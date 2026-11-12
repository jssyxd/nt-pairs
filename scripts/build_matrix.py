#!/usr/bin/env python
"""三场所价差矩阵：Hyperliquid(HIP-3) ↔ Lighter ↔ OKX。

- 基差：同一时间桶的 close（1h，30 天；对齐缺桶计数）。
- 单位校验：ratio_mean 偏离 1 > 5% 或 CV > 0.005 → `unit_fail`（同名字不同标的）。
- 成本 `b*` 六项：费（按场所）+ 半价差 + 按规模滑点 + carry + 腿风险 + 对账 0.5。
- 深度/容量：实时盘口 ±25bps 深度 × 10% 作为单笔上限。
- 会话：RTH / pre / post / closed（`nt_pairs.sessions`，2026 NYSE 日历）。
- 排序：`rank = edge_ratio × min(1, cap/5000) / (1 + session_split)`；另给「费率优先」的腿组合建议。

数据源（只用公开端点）：
- HL：`POST api.hyperliquid.xyz/info`
- Lighter：`mainnet.zklighter.elliot.ai/api/v1`
- OKX：`www.okx.com/api/v5` —— **必须用 urllib**（curl 在本机被拦，见 field guide）

输出：`data/matrix.json`、`docs/MATRIX.md`。
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from nt_pairs.sessions import classify  # noqa: E402

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
OKX = "https://www.okx.com/api/v5"
DEXES = ("xyz", "io", "para", "vntl", "flx", "km", "mkts", "cash", "hyna")
SNAP = r"W:\ant-cross-arb\.herd-dispatch\jobs\universe-map"
UA = {"User-Agent": "Mozilla/5.0"}
EQUITY_HINT = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "LTC", "BCH", "ADA", "AVAX", "LINK", "SUI", "TON",
               "TRX", "DOT", "MATIC", "ARB", "OP", "APT", "NEAR", "ATOM", "UNI", "AAVE", "PEPE", "SHIB", "WIF",
               "JUP", "TIA", "INJ", "STX", "IMX", "GRT", "SAND", "MANA", "GALA", "CHZ", "CRO", "FIL", "ICP",
               "XLM", "HBAR", "EGLD", "FTM", "THETA", "RNDR", "FET", "DYDX", "ENA", "PUMP", "HYPE", "ZEC", "XMR")
ALIAS = {"OAI": "OPENAI", "ANTH": "ANTHROPIC", "ANT": "ANTHROPIC", "SKHX": "SKHYNIX", "SKHY": "SKHYNIX",
         "SMSN": "SAMSUNG", "005930": "SAMSUNG", "285A": "KIOXIA"}


def norm(sym: str) -> str:
    s = sym.upper().split(":")[-1]
    return ALIAS.get(s, s)


def http_json(url: str, payload: dict | None = None, retries: int = 3) -> Any:
    last = None
    for i in range(retries):
        try:
            if payload is None:
                req = urllib.request.Request(url, headers=UA)
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 * (i + 1))
    raise last  # type: ignore[misc]


# ------------------------------------------------------------------ venue loaders
def load_hl() -> list[dict]:
    rows = []
    for dex in DEXES:
        try:
            d = http_json(HL, {"type": "metaAndAssetCtxs", "dex": dex})
        except Exception as exc:  # noqa: BLE001
            print(f"[hl] {dex}: {exc}", file=sys.stderr)
            continue
        for u, c in zip(d[0]["universe"], d[1]):
            mid = float(c.get("midPx") or c.get("markPx") or 0)
            if mid <= 0:
                continue
            rows.append({"venue": "HYPERLIQUID", "ident": u["name"], "sym": norm(u["name"]),
                         "raw": u["name"].split(":")[-1], "mid": mid,
                         "vol24h": float(c.get("dayNtlVlm") or 0),
                         "oi": float(c.get("openInterest") or 0),
                         "funding_bps_h": float(c.get("funding") or 0) * 1e4,
                         "maker_bps": 1.5, "taker_bps": 4.5, "tick": None})
        time.sleep(0.1)
    return rows


def load_lighter() -> list[dict]:
    d = http_json(f"{LT}/orderBookDetails")
    out = []
    for m in d.get("order_book_details", []):
        if m.get("market_type") != "perp" or m.get("status") != "active":
            continue
        px = float(m.get("last_trade_price") or m.get("mark_price") or 0)
        if px <= 0:
            continue
        out.append({"venue": "LIGHTER", "ident": str(m["market_id"]), "sym": norm(m["symbol"]),
                    "raw": m["symbol"].upper(), "mid": px,
                    "vol24h": float(m.get("daily_quote_token_volume") or 0),
                    "oi": float(m.get("open_interest") or 0),
                    "funding_bps_h": 0.0,
                    "maker_bps": float(m.get("maker_fee") or 0) * 1e4,
                    "taker_bps": float(m.get("taker_fee") or 0) * 1e4,
                    "tick": 10 ** -int(m.get("supported_price_decimals") or 2)})
    return out


def load_okx(universe: set[str]) -> list[dict]:
    """OKX 股票类永续（用 instruments 快照筛出符号，再逐个取 ticker）。"""
    try:
        inst = json.load(open(os.path.join(SNAP, "okx_swap_inst.json"), encoding="utf-8"))["data"]
    except Exception as exc:  # noqa: BLE001
        print(f"[okx] snapshot: {exc}", file=sys.stderr)
        return []
    ids = [x["instId"] for x in inst
           if x["instId"].endswith("-USDT-SWAP") and norm(x["instId"]) in universe]
    out = []
    for iid in ids:
        try:
            t = http_json(f"{OKX}/market/ticker?instId={iid}")["data"][0]
        except Exception:  # noqa: BLE001
            continue
        px = float(t.get("last") or 0)
        if px <= 0:
            continue
        out.append({"venue": "OKX", "ident": iid, "sym": norm(iid), "raw": iid[:-len("-USDT-SWAP")],
                    "mid": px, "vol24h": float(t.get("volCcy24h") or 0),
                    "oi": 0.0, "funding_bps_h": 0.0,
                    "maker_bps": 2.0, "taker_bps": 5.0, "tick": 0.01})
        time.sleep(0.05)
    return out


# ------------------------------------------------------------------ series / books
def hl_series(ident: str, days: int = 30) -> dict[int, float]:
    now = int(time.time() * 1000)
    d = http_json(HL, {"type": "candleSnapshot", "req": {"coin": ident, "interval": "1h",
                                                          "startTime": now - days * 86400 * 1000,
                                                          "endTime": now}})
    return {int(c["t"]) // 3600000 * 3600000: float(c["c"]) for c in d if isinstance(c, dict)}


def lt_series(ident: str, days: int = 30) -> dict[int, float]:
    mid = int(ident)
    now = int(time.time())
    start = now - days * 86400
    out: dict[int, float] = {}
    cur = start
    while cur < now:
        end = min(cur + 400 * 3600, now)
        d = http_json(f"{LT}/candles?market_id={mid}&resolution=1h&start_timestamp={cur}&end_timestamp={end}&count_back=2")
        for c in d.get("c", []):
            out[int(c["t"]) // 3600000 * 3600000] = float(c["c"])
        if end <= cur:
            break
        cur = end
    return out


def okx_series(ident: str, days: int = 30) -> dict[int, float]:
    now = int(time.time() * 1000)
    start = now - days * 86400 * 1000
    out: dict[int, float] = {}
    after = None
    for _ in range(8):
        u = f"{OKX}/market/history-candles?instId={ident}&bar=1H&limit=300"
        if after:
            u += f"&after={after}"
        try:
            rows = http_json(u).get("data", [])
        except Exception:  # noqa: BLE001
            break
        if not rows:
            break
        for r in rows:
            out[int(r[0]) // 3600000 * 3600000] = float(r[4])
        after = min(int(r[0]) for r in rows)
        if after <= start or len(rows) < 300:
            break
    return out


def book_of(leg: dict) -> dict | None:
    try:
        if leg["venue"] == "HYPERLIQUID":
            d = http_json(HL, {"type": "l2Book", "coin": leg["ident"]})
            return {"bids": [(float(x["px"]), float(x["sz"]), int(x.get("n", 1))) for x in d["levels"][0]],
                    "asks": [(float(x["px"]), float(x["sz"]), int(x.get("n", 1))) for x in d["levels"][1]]}
        if leg["venue"] == "LIGHTER":
            d = http_json(f"{LT}/orderBookOrders?market_id={leg['ident']}&limit=100")
            return {"bids": sorted(((float(x["price"]), float(x["remaining_base_amount"]), 1)
                                   for x in d.get("bids", [])), reverse=True),
                    "asks": sorted(((float(x["price"]), float(x["remaining_base_amount"]), 1)
                                   for x in d.get("asks", [])))}
        d = http_json(f"{OKX}/market/books?instId={leg['ident']}&sz=50")["data"][0]
        return {"bids": [(float(p), float(s), 1) for p, s, *_ in d.get("bids", [])],
                "asks": [(float(p), float(s), 1) for p, s, *_ in d.get("asks", [])]}
    except Exception:  # noqa: BLE001
        return None


def tick_of(book: dict | None) -> float:
    if not book:
        return 0.01
    px = sorted({p for p, _, _ in book["bids"]} | {p for p, _, _ in book["asks"]})
    ds = [round(y - x, 10) for x, y in zip(px, px[1:]) if y > x]
    return min(ds) if ds else 0.01


def book_stats(book: dict | None) -> dict:
    if not book or not book["bids"] or not book["asks"]:
        return {"spread_bps": None, "depth25_bid": None, "depth25_ask": None}
    bid, ask = book["bids"][0][0], book["asks"][0][0]
    mid = (bid + ask) / 2
    lo, hi = mid * (1 - 25 / 1e4), mid * (1 + 25 / 1e4)
    return {"spread_bps": (ask - bid) / mid * 1e4,
            "depth25_bid": sum(p * q for p, q, _ in book["bids"] if p >= lo),
            "depth25_ask": sum(p * q for p, q, _ in book["asks"] if p <= hi)}


SERIES: dict[str, Callable[[str, int], dict[int, float]]] = {
    "HYPERLIQUID": hl_series, "LIGHTER": lt_series, "OKX": okx_series,
}


def repeat_close_pct(series: dict[int, float]) -> float:
    """相邻桶 close 完全相同的比例 → **陈旧打印**（薄市场的关键数据质量指标）。

    实测：Lighter RKLB 31%、KORU 36%（且 KORU 30 天只有 189 根），
    MRVL 4% / EWY 5% / SOXL 1%；HL 侧全部 ≤1%。
    """
    vals = [series[t] for t in sorted(series)]
    if len(vals) < 2:
        return 0.0
    same = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
    return round(100.0 * same / (len(vals) - 1), 2)


def stats(values: list[float]) -> dict:
    if not values:
        return {}
    o = sorted(values)
    n = len(o)

    def q(p: float) -> float:
        i = p * (n - 1)
        lo = int(i)
        hi = min(lo + 1, n - 1)
        f = i - lo
        return o[lo] * (1 - f) + o[hi] * f

    rho = None
    if n > 3:
        xs, ys = values[:-1], values[1:]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        if vx > 0:
            rho = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx
    return {"n": n, "mean": statistics.mean(values), "sd": statistics.pstdev(values),
            "p50": q(.5), "p90": q(.9), "p95": q(.95), "p99": q(.99),
            "rho": rho,
            "half_life_h": (-math.log(2) / math.log(abs(rho))) if rho and 0 < abs(rho) < 0.999 else None}


def main() -> int:
    print("[1/5] venues ...", file=sys.stderr)
    hl = load_hl()
    lt = load_lighter()
    syms = {r["sym"] for r in hl} | {r["sym"] for r in lt}
    okx = load_okx(syms)
    print(f"  HL {len(hl)} | Lighter {len(lt)} | OKX {len(okx)}", file=sys.stderr)

    by: dict[str, dict[str, list[dict]]] = {}
    for r in hl + lt + okx:
        if r["raw"] in EQUITY_HINT:
            continue
        by.setdefault(r["sym"], {}).setdefault(r["venue"], []).append(r)

    pairs = []
    for sym, venues in sorted(by.items()):
        v = sorted(venues)
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                for a in venues[v[i]]:
                    for b in venues[v[j]]:
                        if a["mid"] <= 0 or b["mid"] <= 0:
                            continue
                        pairs.append((a, b))
    # 按「两腿 24h 量乘积」排序，只保留流动性最好的 N 对（默认 60），避免薄市场拖慢与误导
    pairs.sort(key=lambda ab: (ab[0].get("vol24h") or 0) * (ab[1].get("vol24h") or 0), reverse=True)
    cap_n = int(os.environ.get("MATRIX_MAX_PAIRS", "60"))
    if len(pairs) > cap_n:
        print(f"  cap {cap_n} of {len(pairs)} pairs (MATRIX_MAX_PAIRS)", file=sys.stderr)
        pairs = pairs[:cap_n]
    print(f"[2/5] candidate pairs: {len(pairs)}", file=sys.stderr)

    out_pairs = []
    for idx, (a, b) in enumerate(pairs):
        rec: dict[str, Any] = {
            "pair": f"{a['ident']}|{b['ident']}", "sym": a["sym"],
            "A": {k: v for k, v in a.items()}, "B": {k: v for k, v in b.items()},
            "windows": {}, "reasons": [], "sessions_30d": {},
        }
        try:
            sa = SERIES[a["venue"]](a["ident"], 30)
            sb = SERIES[b["venue"]](b["ident"], 30)
        except Exception as exc:  # noqa: BLE001
            rec["reasons"].append(f"series_failed:{exc}")
            sa, sb = {}, {}
        ts = sorted(set(sa) & set(sb))
        if ts:
            basis = [(sb[t] / sa[t] - 1) * 1e4 for t in ts]
            ratios = [sb[t] / sa[t] for t in ts]
            rm = statistics.mean(ratios)
            rsd = statistics.pstdev(ratios) if len(ratios) > 1 else 0.0
            rcv = rsd / rm if rm else 1.0
            rec["ratio_evidence"] = {"mean": round(rm, 6), "sd": round(rsd, 6), "cv": round(rcv, 6), "n": len(ratios)}
            # 单位失配（硬失败）只看**量级**：同名字不同标的会差 10x/100x。
            # CV 高往往来自**陈旧报价**（薄市场的打印间隔），不是单位问题 → 软标记。
            rec["unit_fail"] = bool(abs(rm - 1) > 0.05)
            rec["repeat_close_pct_A"] = repeat_close_pct(sa)
            rec["repeat_close_pct_B"] = repeat_close_pct(sb)
            rec["bars_A"], rec["bars_B"] = len(sa), len(sb)
            rec["data_quality_cv"] = round(rcv, 6)
            rec["stale_print_suspect"] = bool(rcv > 0.005
                                              or max(repeat_close_pct(sa), repeat_close_pct(sb)) > 10.0)
            rec["windows"]["30d"] = stats(basis)
            rec["windows"]["14d"] = stats([x for t, x in zip(ts, basis) if t >= ts[-1] - 14 * 86400 * 1000])
            rec["windows"]["7d"] = stats([x for t, x in zip(ts, basis) if t >= ts[-1] - 7 * 86400 * 1000])
            sess: dict[str, list[float]] = {"pre": [], "rth": [], "post": [], "closed": []}
            for t, x in zip(ts, basis):
                sess[classify(t).session].append(x)
            rec["sessions_30d"] = {k: stats(v) for k, v in sess.items()}
        else:
            rec["reasons"].append("no_common_buckets")
        ba, bb = book_of(a), book_of(b)
        rec["A"]["book"] = book_stats(ba)
        rec["B"]["book"] = book_stats(bb)
        out_pairs.append(rec)
        print(f"  {idx+1}/{len(pairs)} {rec['pair']} n={len(rec.get('windows', {}).get('30d', {}))}", file=sys.stderr)

    print("[3/5] scoring ...", file=sys.stderr)
    for rec in out_pairs:
        a, b = rec["A"], rec["B"]
        if rec.get("unit_fail") or not rec.get("windows", {}).get("30d"):
            rec["costs"] = {"b_star_bps": None, "k": None, "k_b_star": None}
            rec["capacity"] = {"cap_usd": None}
            rec["score"] = {"edge_ratio": None, "capacity_ok": False, "session_split": None, "rank": None}
            rec["tradable"] = False
            continue
        fee_a, fee_b = a["maker_bps"] + a["taker_bps"], b["maker_bps"] + b["taker_bps"]
        hs_a = (a.get("book") or {}).get("spread_bps") or 0.0
        hs_b = (b.get("book") or {}).get("spread_bps") or 0.0
        slip_a = 2 * tick_of(ba) / a["mid"] * 1e4
        slip_b = 2 * tick_of(bb) / b["mid"] * 1e4
        carry = max(abs(a.get("funding_bps_h", 0)), abs(b.get("funding_bps_h", 0))) * 1.0
        bstar = fee_a + fee_b + hs_a / 2 + hs_b / 2 + slip_a + slip_b + carry + 0.3 + 0.5
        w = rec["windows"]["30d"]
        rho = w.get("rho")
        ess = max(w["n"] * (1 - abs(rho or 0)) / (1 + abs(rho or 0)), 1.0)
        k = 1 + 1 / math.sqrt(ess)
        cap = 0.10 * min((a.get("book") or {}).get("depth25_bid") or 0.0,
                         (b.get("book") or {}).get("depth25_bid") or 0.0)
        edge = (w["p95"] - k * bstar) / (k * bstar) if k * bstar else None
        rth = (rec.get("sessions_30d") or {}).get("rth") or {}
        closed = (rec.get("sessions_30d") or {}).get("closed") or {}
        split = (abs(rth.get("mean", 0) - closed.get("mean", 0)) / w["sd"]
                 if rth.get("mean") is not None and closed.get("mean") is not None and w.get("sd") else None)
        rank = edge * min(1.0, cap / 5000.0) / (1 + (split or 0)) if edge is not None else None
        rec["costs"] = {"b_star_bps": round(bstar, 2), "k": round(k, 3), "ess": round(ess, 1),
                        "k_b_star": round(k * bstar, 2), "fees_a": fee_a, "fees_b": fee_b,
                        "fee_total": fee_a + fee_b}
        rec["capacity"] = {"cap_usd": round(cap, 2)}
        rec["score"] = {"edge_ratio": round(edge, 3) if edge is not None else None,
                        "capacity_ok": cap >= 1000.0,
                        "session_split": round(split, 3) if split is not None else None,
                        "rank": round(rank, 3) if rank is not None else None}
        rec["tradable"] = bool(edge is not None and edge > 0 and cap >= 1000.0
                               and not rec.get("stale_print_suspect"))

    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(), "pairs": out_pairs}
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open("data/matrix.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    rows = [p for p in out_pairs if p["score"].get("rank") is not None]
    rows.sort(key=lambda p: -p["score"]["rank"])
    lines = ["# 三场所价差矩阵（2026-11-12 实测）", "",
             f"生成：{payload['generated_utc']}；HL / Lighter / OKX，30 天 1h，会话分层。", "",
             "| # | pair | 30d mean | p95 | b* | 其中费 | k·b* | 覆盖 | cap($) | 会话分裂 | CV | 可交易 |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|"]
    for i, p in enumerate(rows[:60], 1):
        w = p["windows"]["30d"]
        c = p["costs"]
        s = p["score"]
        lines.append(
            f"| {i} | {p['pair']} | {w['mean']:+.1f} | {w['p95']:.1f} | {c['b_star_bps']:.1f} | {c['fee_total']:.1f} | "
            f"{c['k_b_star']:.1f} | {s['edge_ratio']:.2f} | {p['capacity']['cap_usd']:.0f} | "
            f"{s['session_split'] if s['session_split'] is not None else 'n/a'} | "
            f"{p.get('data_quality_cv', 0):.5f} | {'✅' if p['tradable'] else '❌'} |")
    # 每个标的的最佳腿组合（费率优先）
    best: dict[str, dict] = {}
    for p in out_pairs:
        if p["score"].get("edge_ratio") is None:
            continue
        cur = best.get(p["sym"])
        if cur is None or (p["costs"]["fee_total"], -(p["score"]["edge_ratio"] or 0)) < \
                (cur["costs"]["fee_total"], -(cur["score"]["edge_ratio"] or 0)):
            best[p["sym"]] = p
    lines += ["", "## 每个标的的最佳腿组合（费率优先 → 再看覆盖倍数）", "",
              "| 标的 | 最佳对 | b* | 其中费 | 覆盖 | cap($) | 会话分裂 |", "|---|---|---:|---:|---:|---:|---:|"]
    for sym, p in sorted(best.items()):
        s, c = p["score"], p["costs"]
        lines.append(f"| {sym} | {p['pair']} | {c['b_star_bps']:.1f} | {c['fee_total']:.1f} | "
                     f"{s['edge_ratio']:.2f} | {p['capacity']['cap_usd']:.0f} | "
                     f"{s['session_split'] if s['session_split'] is not None else 'n/a'} |")
    for sess in ("rth", "pre", "post", "closed"):
        sub = []
        for p in out_pairs:
            st = (p.get("sessions_30d") or {}).get(sess) or {}
            if st.get("n"):
                sub.append((st.get("p95", 0), p, st))
        sub.sort(key=lambda x: -x[0])
        lines += ["", f"### 会话 {sess} top10（p95）", "", "| pair | n | mean | p95 | sd |", "|---|---:|---:|---:|---:|"]
        for _, p, st in sub[:10]:
            lines.append(f"| {p['pair']} | {st['n']} | {st['mean']:+.1f} | {st['p95']:.1f} | {st['sd']:.1f} |")
    os.makedirs("docs", exist_ok=True)
    open("docs/MATRIX.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[4/5] tradable={sum(1 for p in out_pairs if p['tradable'])} of {len(out_pairs)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())