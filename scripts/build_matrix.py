#!/usr/bin/env python
"""三场所价差矩阵（HL ↔ Lighter 全窗口 + OKX/Binance 截面快照）。

数据来源：
- Hyperliquid：`POST https://api.hyperliquid.xyz/info`（perpDexs / metaAndAssetCtxs / candleSnapshot / l2Book）
- Lighter：`https://mainnet.zklighter.elliot.ai/api/v1/*`（orderBookDetails / candles / orderBookOrders）
- OKX / Binance：**本机直连被出口拦截**，使用 ant-cross-arb 工作区里 2026-11-11 的落盘快照
  （`W:/ant-cross-arb/.herd-dispatch/jobs/universe-map/{okx_swap_inst,okx_swap_tickers,binance_fapi,binance_fapi_tickers24h}.json`），
  这些是**截面**数据（单点），因此 OKX/Binance 只参与「当前截面」行，不参与多窗口统计。

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
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from nt_pairs.sessions import classify  # noqa: E402

HL_INFO = "https://api.hyperliquid.xyz/info"
LT_BASE = "https://mainnet.zklighter.elliot.ai/api/v1"
SNAP_DIR = r"W:\ant-cross-arb\.herd-dispatch\jobs\universe-map"
OUT_JSON = "data/matrix.json"
OUT_MD = "docs/MATRIX.md"

DEXES = ("xyz", "io", "para", "vntl", "flx", "km", "mkts", "cash", "hyna")
EQUITY_HINT = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "LTC", "BCH", "ADA", "AVAX", "LINK", "SUI", "TON",
               "TRX", "DOT", "MATIC", "ARB", "OP", "APT", "NEAR", "ATOM", "UNI", "AAVE", "PEPE", "SHIB", "WIF",
               "JUP", "TIA", "INJ", "STX", "IMX", "GRT", "SAND", "MANA", "GALA", "CHZ", "CRO", "FIL", "ICP",
               "XLM", "HBAR", "EGLD", "FTM", "THETA", "RNDR", "FET", "DYDX", "ENA", "PUMP", "HYPE", "ZEC", "XMR")

SYMBOL_ALIAS = {
    "OAI": "OPENAI", "ANTH": "ANTHROPIC", "ANT": "ANTHROPIC", "SKHX": "SKHYNIX", "SKHY": "SKHYNIX",
    "SMSN": "SAMSUNG", "005930": "SAMSUNG", "285A": "KIOXIA", "KIOXIA": "KIOXIA", "SMCI": "SMCI",
}


def norm(sym: str) -> str:
    s = sym.upper().split(":")[-1]
    s = SYMBOL_ALIAS.get(s, s)
    return s


def http_json(url: str, payload: dict | None = None, timeout: int = 40) -> Any:
    if payload is None:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


def get_hl(payload: dict, retries: int = 4) -> Any:
    last = None
    for i in range(retries):
        try:
            return http_json(HL_INFO, payload)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8 * (i + 1))
    raise last  # type: ignore[misc]


def http_json_retry(url: str, payload: dict | None = None, retries: int = 4) -> Any:
    last = None
    for i in range(retries):
        try:
            return http_json(url, payload)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.6 * (i + 1))
    raise last  # type: ignore[misc]


def sleep(ms: int = 120) -> None:
    time.sleep(ms / 1000.0)


# ---------------------------------------------------------------- venues
def load_hl() -> list[dict]:
    rows = []
    for dex in DEXES:
        try:
            d = get_hl({"type": "metaAndAssetCtxs", "dex": dex})
        except Exception as exc:  # noqa: BLE001
            print(f"[hl] {dex} failed: {exc}", file=sys.stderr)
            continue
        for u, c in zip(d[0]["universe"], d[1]):
            mid = float(c.get("midPx") or c.get("markPx") or 0)
            vol = float(c.get("dayNtlVlm") or 0)
            if mid <= 0:
                continue
            rows.append({"venue": "HYPERLIQUID", "dex": dex, "coin": u["name"],
                         "sym": norm(u["name"]), "raw": u["name"].split(":")[-1],
                         "mid": mid, "vol24h": vol,
                         "oi": float(c.get("openInterest") or 0),
                         "funding_h": float(c.get("funding") or 0) * 1e4,
                         "sz_dec": int(u.get("szDecimals") or 2),
                         "lev": int(u.get("maxLeverage") or 0),
                         "book": None})
        sleep()
    return rows


def load_lighter() -> list[dict]:
    d = http_json(f"{LT_BASE}/orderBookDetails")
    out = []
    for m in d.get("order_book_details", []):
        if m.get("market_type") != "perp" or m.get("status") != "active":
            continue
        px = float(m.get("last_trade_price") or m.get("mark_price") or 0)
        if px <= 0:
            continue
        out.append({"venue": "LIGHTER", "market_id": int(m["market_id"]), "sym": norm(m["symbol"]),
                    "raw": m["symbol"].upper(), "mid": px,
                    "vol24h": float(m.get("daily_quote_token_volume") or 0),
                    "oi": float(m.get("open_interest") or 0),
                    "maker_bps": float(m.get("maker_fee") or 0) * 1e4,
                    "taker_bps": float(m.get("taker_fee") or 0) * 1e4,
                    "min_qty": float(m.get("min_base_amount") or 0),
                    "tick": 10 ** -int(m.get("supported_price_decimals") or 2),
                    "book": None})
    return out


def load_snapshots() -> tuple[list[dict], list[dict]]:
    okx: list[dict] = []
    binance: list[dict] = []
    try:
        inst = json.load(open(os.path.join(SNAP_DIR, "okx_swap_inst.json"), encoding="utf-8"))["data"]
        tick = {t["instId"]: t for t in json.load(open(os.path.join(SNAP_DIR, "okx_swap_tickers.json"), encoding="utf-8"))["data"]}
        for x in inst:
            iid = x["instId"]
            if not iid.endswith("-USDT-SWAP"):
                continue
            t = tick.get(iid, {})
            px = float(t.get("last") or 0)
            if px <= 0:
                continue
            okx.append({"venue": "OKX", "inst": iid, "sym": norm(iid), "raw": iid[:-len("-USDT-SWAP")],
                        "mid": px, "bid": float(t.get("bidPx") or 0), "ask": float(t.get("askPx") or 0),
                        "bid_sz": float(t.get("bidSz") or 0), "ask_sz": float(t.get("askSz") or 0),
                        "vol24h": float(t.get("volCcy24h") or 0),
                        "maker_bps": 2.0, "taker_bps": 5.0, "ct_val": float(x.get("ctVal") or 1),
                        "tick": float(x.get("tickSz") or 0.01), "snapshot": "2026-11-11"})
    except Exception as exc:  # noqa: BLE001
        print(f"[okx snapshot] {exc}", file=sys.stderr)
    try:
        ex = json.load(open(os.path.join(SNAP_DIR, "binance_fapi.json"), encoding="utf-8"))
        tk = {t["symbol"]: t for t in json.load(open(os.path.join(SNAP_DIR, "binance_fapi_tickers24h.json"), encoding="utf-8"))}
        for s in ex.get("symbols", []):
            if s.get("contractType") != "TRADIFI_PERPETUAL" or s.get("status") != "TRADING":
                continue
            t = tk.get(s["symbol"], {})
            px = float(t.get("lastPrice") or 0)
            if px <= 0:
                continue
            binance.append({"venue": "BINANCE", "inst": s["symbol"], "sym": norm(s["symbol"]),
                             "raw": s["symbol"].replace("USDT", ""), "mid": px,
                             "vol24h": float(t.get("quoteVolume") or 0),
                             "maker_bps": 2.0, "taker_bps": 4.5, "snapshot": "2026-11-11"})
    except Exception as exc:  # noqa: BLE001
        print(f"[binance snapshot] {exc}", file=sys.stderr)
    return okx, binance


# ---------------------------------------------------------------- data
def hl_candles(coin: str, interval: str, days: int) -> dict[int, float]:
    now = int(time.time() * 1000)
    start = now - days * 86400 * 1000
    d = get_hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": now}})
    return {int(c["t"]) // 3600000 * 3600000: float(c["c"]) for c in d if isinstance(c, dict) and "t" in c}


def lt_candles(market_id: int, resolution: str, days: int, chunk_bars: int = 400) -> dict[int, float]:
    """分页抓 Lighter K 线（单次约上限 500 根，长窗口必须切片）。"""
    step = {"1m": 60, "5m": 300, "1h": 3600}[resolution]
    now = int(time.time())
    start = now - days * 86400
    out: dict[int, float] = {}
    cur = start
    while cur < now:
        end = min(cur + chunk_bars * step, now)
        u = (f"{LT_BASE}/candles?market_id={market_id}&resolution={resolution}"
             f"&start_timestamp={cur}&end_timestamp={end}&count_back=2")
        try:
            d = http_json(u)
        except Exception:  # noqa: BLE001
            break
        for c in d.get("c", []):
            out[int(c["t"]) // 3600000 * 3600000] = float(c["c"])
        if end <= cur:
            break
        cur = end
    return out


def infer_tick(book: dict | None) -> float:
    if not book:
        return 0.01
    px = sorted({p for p, _ in book.get("bids", [])} | {p for p, _ in book.get("asks", [])})
    diffs = [round(b - a, 10) for a, b in zip(px, px[1:]) if b > a]
    return min(diffs) if diffs else 0.01


def hl_book(coin: str) -> dict:
    d = get_hl({"type": "l2Book", "coin": coin})
    bids = [(float(x["px"]), float(x["sz"])) for x in d["levels"][0]]
    asks = [(float(x["px"]), float(x["sz"])) for x in d["levels"][1]]
    return {"bids": bids, "asks": asks}


def lt_book(market_id: int) -> dict:
    d = http_json(f"{LT_BASE}/orderBookOrders?market_id={market_id}&limit=100")
    bids = sorted(((float(x["price"]), float(x["remaining_base_amount"])) for x in d.get("bids", [])), reverse=True)
    asks = sorted(((float(x["price"]), float(x["remaining_base_amount"])) for x in d.get("asks", [])))
    return {"bids": bids, "asks": asks}


# ---------------------------------------------------------------- stats
def stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)

    def q(p: float) -> float:
        i = p * (n - 1)
        lo = int(i)
        hi = min(lo + 1, n - 1)
        f = i - lo
        return ordered[lo] * (1 - f) + ordered[hi] * f

    rho = None
    half = None
    if n > 3:
        xs, ys = values[:-1], values[1:]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if vx > 0:
            rho = cov / vx
            if 0 < abs(rho) < 0.999:
                half = -math.log(2) / math.log(abs(rho))
    return {"n": n, "mean": statistics.mean(values), "sd": statistics.pstdev(values),
            "p50": q(0.50), "p90": q(0.90), "p95": q(0.95), "p99": q(0.99),
            "min": ordered[0], "max": ordered[-1], "rho": rho, "half_life_h": half}


def ess_k(n: int, rho: float | None) -> tuple[float, float]:
    if not n:
        return 0.0, 1.0
    r = abs(rho or 0.0)
    e = max(n * (1 - r) / (1 + r), 1.0)
    return e, 1 + 1 / math.sqrt(e)


def book_stats(book: dict | None) -> dict:
    if not book or not book["bids"] or not book["asks"]:
        return {"spread_bps": None, "depth25_bid": None, "depth25_ask": None, "mid": None}
    bid, ask = book["bids"][0][0], book["asks"][0][0]
    mid = (bid + ask) / 2
    lo, hi = mid * (1 - 25 / 1e4), mid * (1 + 25 / 1e4)
    db = sum(p * q for p, q in book["bids"] if p >= lo)
    da = sum(p * q for p, q in book["asks"] if p <= hi)
    return {"spread_bps": (ask - bid) / mid * 1e4 if mid else None,
            "depth25_bid": db, "depth25_ask": da, "mid": mid}


def main() -> int:
    print("[1/5] venues ...", file=sys.stderr)
    cache_path = "data/cache/venues.json"
    cache = None
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache = None
    if cache and cache.get("generated_utc"):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cache["generated_utc"])).total_seconds()
        if age < 7200:
            print(f"  using cache ({age/60:.0f} min old)", file=sys.stderr)
            hl, lt = cache["hl"], cache["lt"]
            okx, binance = load_snapshots()
        else:
            cache = None
    if cache is None:
        hl = load_hl()
        lt = load_lighter()
        okx, binance = load_snapshots()
        os.makedirs("data/cache", exist_ok=True)
        json.dump({"generated_utc": datetime.now(timezone.utc).isoformat(), "hl": hl, "lt": lt},
                  open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  HL {len(hl)} | Lighter {len(lt)} | OKX {len(okx)} | Binance {len(binance)}", file=sys.stderr)

    by_sym_hl: dict[str, list[dict]] = {}
    for r in hl:
        if r["vol24h"] <= 0:
            continue
        if r["raw"] in EQUITY_HINT:
            continue
        by_sym_hl.setdefault(r["sym"], []).append(r)
    by_sym_lt: dict[str, list[dict]] = {}
    for r in lt:
        if r["raw"] in EQUITY_HINT:
            continue
        by_sym_lt.setdefault(r["sym"], []).append(r)
    by_sym_okx = {r["sym"]: r for r in okx if r["raw"] not in EQUITY_HINT}
    by_sym_bn = {r["sym"]: r for r in binance if r["raw"] not in EQUITY_HINT}

    pairs = []
    for sym in sorted(set(by_sym_hl) & set(by_sym_lt)):
        for a in by_sym_hl[sym]:
            for b in by_sym_lt[sym]:
                pairs.append((a, b))
    for sym in sorted(set(by_sym_hl) & set(by_sym_okx)):
        a = max(by_sym_hl[sym], key=lambda x: x["vol24h"])
        pairs.append((a, by_sym_okx[sym]))
    for sym in sorted(set(by_sym_hl) & set(by_sym_bn)):
        a = max(by_sym_hl[sym], key=lambda x: x["vol24h"])
        pairs.append((a, by_sym_bn[sym]))
    # 按「两腿 24h 量乘积」排序，只保留流动性最好的 N 对（默认 30），避免薄市场拖慢与误导
    def liq(pair):
        a, b = pair
        return (a.get("vol24h") or 0) * (b.get("vol24h") or 0)
    pairs.sort(key=liq, reverse=True)
    cap = int(os.environ.get("MATRIX_MAX_PAIRS", "30"))
    if len(pairs) > cap:
        print(f"  cap {cap} of {len(pairs)} pairs (env MATRIX_MAX_PAIRS)", file=sys.stderr)
        pairs = pairs[:cap]
    print(f"[2/5] pairs: {len(pairs)}", file=sys.stderr)

    out_pairs = []
    for i, (a, b) in enumerate(pairs):
        rec: dict[str, Any] = {
            "pair": f"{a.get('coin') or a.get('inst')}|{b.get('inst') or b.get('market_id') or b.get('coin')}",
            "sym": a["sym"],
            "A": {k: v for k, v in a.items() if k != "book"},
            "B": {k: v for k, v in b.items() if k != "book"},
            "windows": {}, "reasons": [],
        }
        if a["venue"] == "HYPERLIQUID" and b["venue"] == "LIGHTER":
            try:
                h1 = hl_candles(a["coin"], "1h", 30)
                l1 = lt_candles(b["market_id"], "1h", 30)
                sleep()
            except Exception as exc:  # noqa: BLE001
                print(f"  [{rec['pair']}] candles failed: {exc}", file=sys.stderr)
                rec["reasons"].append(f"candles_1h_failed:{exc}")
                h1, l1 = {}, {}
            ts = sorted(set(h1) & set(l1))
            basis = [(l1[t] / h1[t] - 1) * 1e4 for t in ts]
            ratios = [l1[t] / h1[t] for t in ts]
            r_mean = statistics.mean(ratios) if ratios else 0.0
            r_sd = statistics.pstdev(ratios) if len(ratios) > 1 else 0.0
            r_cv = (r_sd / r_mean) if r_mean else 1.0
            rec["ratio_evidence"] = {"mean": round(r_mean, 6), "sd": round(r_sd, 6),
                                     "cv": round(r_cv, 6), "n": len(ratios)}
            rec["unit_fail"] = bool(r_cv > 0.005 or abs(r_mean - 1) > 0.05)
            if rec["unit_fail"]:
                rec["reasons"].append(f"unit_mismatch:ratio_mean={r_mean:.4f} cv={r_cv:.5f}")
            rec["windows"]["30d"] = stats(basis)
            rec["windows"]["14d"] = stats([x for t, x in zip(ts, basis) if t >= ts[-1] - 14 * 86400 * 1000]) if ts else {}
            rec["windows"]["7d"] = stats([x for t, x in zip(ts, basis) if t >= ts[-1] - 7 * 86400 * 1000]) if ts else {}
            by_sess: dict[str, list[float]] = {"pre": [], "rth": [], "post": [], "closed": []}
            for t, x in zip(ts, basis):
                by_sess[classify(t).session].append(x)
            rec["sessions_30d"] = {k: stats(v) for k, v in by_sess.items()}
            try:
                hb = hl_book(a["coin"])
                sleep()
                lb = lt_book(b["market_id"])
                sleep()
            except Exception as exc:  # noqa: BLE001
                rec["reasons"].append(f"book_failed:{exc}")
                hb = lb = None
            rec["A"]["book"] = book_stats(hb)
            rec["B"]["book"] = book_stats(lb)
            rec["A"]["book_raw"] = hb
            rec["B"]["book_raw"] = lb
        else:
            rec["reasons"].append("snapshot_only:second_venue_series_unavailable")
            rec["snapshot_basis_bps"] = (b["mid"] / a["mid"] - 1) * 1e4
            r = b["mid"] / a["mid"]
            rec["ratio_evidence"] = {"mean": round(r, 6), "sd": 0.0, "cv": 0.0, "n": 1}
            rec["unit_fail"] = bool(abs(r - 1) > 0.05)
            if rec["unit_fail"]:
                rec["reasons"].append(f"unit_mismatch:ratio={r:.4f}")
            if b.get("bid") and b.get("ask"):
                rec["B"]["book"] = {"spread_bps": (b["ask"] - b["bid"]) / b["mid"] * 1e4,
                                    "depth25_bid": b["bid"] * b["bid_sz"] * b.get("ct_val", 1),
                                    "depth25_ask": b["ask"] * b["ask_sz"] * b.get("ct_val", 1)}
        out_pairs.append(rec)
        print(f"  {i+1}/{len(pairs)} {rec['pair']}", file=sys.stderr)

    print("[3/5] scoring ...", file=sys.stderr)
    for rec in out_pairs:
        if rec.get("unit_fail"):
            rec["costs"] = {"b_star_bps": None, "k": None, "ess": None, "k_b_star": None}
            rec["capacity"] = {"cap_usd": None, "depth25_a": None, "depth25_b": None}
            rec["score"] = {"edge_ratio": None, "capacity_ok": False, "session_split": None, "rank": None}
            rec["tradable"] = False
            continue
        a, b = rec["A"], rec["B"]
        fee_a = (a.get("maker_bps", 1.5) + a.get("taker_bps", 4.5))
        fee_b = (b.get("maker_bps", 0) + b.get("taker_bps", 0))
        hs_a = (a.get("book") or {}).get("spread_bps") or 0.0
        hs_b = (b.get("book") or {}).get("spread_bps") or 0.0
        px_a = a["mid"]
        tick_a = infer_tick(a.get("book_raw"))
        slip_a = 2 * tick_a / px_a * 1e4
        tick_b = infer_tick(b.get("book_raw")) if b.get("book_raw") else (b.get("tick") or 0.01)
        slip_b = 2 * tick_b / b["mid"] * 1e4
        carry = abs(a.get("funding_h", 0)) * 1.0
        leg = 0.3
        bstar = fee_a + fee_b + hs_a / 2 + hs_b / 2 + slip_a + slip_b + carry + leg + 0.5
        w30 = rec.get("windows", {}).get("30d") or {}
        rho = w30.get("rho")
        ess, k = ess_k(w30.get("n", 0), rho)
        cap_a = (a.get("book") or {}).get("depth25_bid") or 0.0
        cap_b = (b.get("book") or {}).get("depth25_bid") or 0.0
        cap = 0.10 * min(cap_a, cap_b)
        p95 = w30.get("p95", 0.0)
        edge = (p95 - k * bstar) / (k * bstar) if k * bstar else None
        rth = (rec.get("sessions_30d") or {}).get("rth") or {}
        closed = (rec.get("sessions_30d") or {}).get("closed") or {}
        split = None
        if rth.get("mean") is not None and closed.get("mean") is not None and w30.get("sd"):
            split = abs(rth["mean"] - closed["mean"]) / w30["sd"]
        rank = (edge or -9) * min(1.0, cap / 5000.0) / (1 + (split or 0))
        rec["costs"] = {"b_star_bps": round(bstar, 2), "k": round(k, 3), "ess": round(ess, 1),
                        "k_b_star": round(k * bstar, 2), "fees_a": fee_a, "fees_b": fee_b,
                        "half_spread_a": round(hs_a / 2, 2), "half_spread_b": round(hs_b / 2, 2),
                        "slip_a": round(slip_a, 2), "slip_b": round(slip_b, 2),
                        "carry": round(carry, 2), "leg_risk": leg, "recon": 0.5}
        rec["capacity"] = {"cap_usd": round(cap, 2), "depth25_a": cap_a, "depth25_b": cap_b}
        rec["score"] = {"edge_ratio": round(edge, 3) if edge is not None else None,
                        "capacity_ok": cap >= 1000.0, "session_split": round(split, 3) if split else None,
                        "rank": round(rank, 3) if edge is not None else None}
        rec["tradable"] = bool(edge is not None and edge > 0 and cap >= 1000.0)

    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(), "pairs": out_pairs}
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[4/5] wrote {OUT_JSON}", file=sys.stderr)

    rows = [p for p in out_pairs if p["score"].get("rank") is not None]
    rows.sort(key=lambda p: -p["score"]["rank"])
    lines = ["# 三场所价差矩阵（2026-11-12 实测）", "",
             f"生成：{payload['generated_utc']}；HL↔Lighter 为 30 天 1h 序列（会话分层），",
             "OKX / Binance 为 2026-11-11 截面快照（本机直连被出口拦截，无历史序列）。", "",
             "| # | pair | 30d mean | p95 | b* | k·b* | 覆盖 | cap($) | 会话分裂 | 单位校验 | 可交易 |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---|:--:|:--:|"]
    for i, p in enumerate(rows[:40], 1):
        w = p["windows"].get("30d") or {}
        c = p["costs"]
        s = p["score"]
        def fmt(v, spec=".1f"):
            return format(v, spec) if isinstance(v, (int, float)) else "n/a"
        lines.append(
            f"| {i} | {p['pair']} | {fmt(w.get('mean'), '+.1f')} | {fmt(w.get('p95'))} | {fmt(c.get('b_star_bps'))} | "
            f"{fmt(c.get('k_b_star'))} | {fmt(s.get('edge_ratio'), '.2f')} | {fmt(p['capacity'].get('cap_usd'), '.0f')} | "
            f"{fmt(s.get('session_split'), '.2f')} | "
            f"{'❌unit' if p.get('unit_fail') else '✅'} | {'✅' if p['tradable'] else '❌'} |")
    for sess in ("rth", "pre", "post", "closed"):
        sub = []
        for p in out_pairs:
            st = (p.get("sessions_30d") or {}).get(sess) or {}
            if st.get("n"):
                sub.append((st.get("p95", 0), p, st))
        sub.sort(key=lambda x: -x[0])
        lines += ["", f"### 会话 {sess} top10（按该会话 p95）", "",
                  "| pair | n | mean | p95 | sd |", "|---|---:|---:|---:|---:|"]
        for _, p, st in sub[:10]:
            lines.append(f"| {p['pair']} | {st['n']} | {st['mean']:+.1f} | {st['p95']:.1f} | {st['sd']:.1f} |")
    os.makedirs("docs", exist_ok=True)
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[5/5] wrote {OUT_MD}; tradable={sum(1 for p in out_pairs if p['tradable'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())