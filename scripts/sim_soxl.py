#!/usr/bin/env python
"""SOXL 完整执行模拟：HL `xyz:SOXL` ↔ Lighter `OPENAI`（market 197）。

做四件事：
1. 两腿画像（tick/数量/费率/深度/24h 量/funding）；
2. 基差序列（5m，30 天）+ 会话分层；
3. 回归（fade）规则的**事件级**回放：信号 → 下一根 open 入场 → 回到带内平仓，
   并按成交概率情景（p_fill ∈ {0.2, 0.5, 0.8}）折算期望；
4. 滑点曲线与容量（用实时盘口做 L2 行走 VWAP）。

结论必须区分「统计事实」与「模型假设」。
"""

from __future__ import annotations

import argparse
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

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
HL_COIN, LT_ID = "xyz:SOXL", 197
OUT_JSON, OUT_MD = "data/soxl_sim.json", "docs/SOXL_SIM.md"


def http(url: str, payload: dict | None = None) -> Any:
    if payload is None:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as fh:
        return json.load(fh)


def hl_candles(coin: str, interval: str, days: int) -> dict[int, tuple[float, float]]:
    now = int(time.time() * 1000)
    start = now - days * 86400 * 1000
    d = http(HL, {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                    "startTime": start, "endTime": now}})
    step_ms = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}[interval]
    return {int(c["t"]) // step_ms * step_ms: (float(c["o"]), float(c["c"])) for c in d
            if isinstance(c, dict) and "t" in c}


def lt_candles(mid: int, resolution: str, days: int, chunk_bars: int = 400) -> dict[int, tuple[float, float]]:
    """分页抓 Lighter K 线（单次最多约 500 根，长窗口必须切片）。"""
    step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[resolution]
    now = int(time.time())
    start = now - days * 86400
    out: dict[int, tuple[float, float]] = {}
    cur = start
    while cur < now:
        end = min(cur + chunk_bars * step, now)
        u = (f"{LT}/candles?market_id={mid}&resolution={resolution}"
             f"&start_timestamp={cur}&end_timestamp={end}&count_back=2")
        try:
            d = http(u)
        except Exception as exc:  # noqa: BLE001
            print(f"[lt] {resolution} window {cur} failed: {exc}", file=sys.stderr)
            break
        for c in d.get("c", []):
            t = int(c["t"])
            out[t] = (float(c["o"]), float(c["c"]))
        if end <= cur:
            break
        cur = end
    return out


def hl_book(coin: str) -> dict:
    d = http(HL, {"type": "l2Book", "coin": coin})
    return {"bids": [(float(x["px"]), float(x["sz"])) for x in d["levels"][0]],
            "asks": [(float(x["px"]), float(x["sz"])) for x in d["levels"][1]]}


def lt_book(mid: int) -> dict:
    d = http(f"{LT}/orderBookOrders?market_id={mid}&limit=200")
    return {"bids": sorted(((float(x["price"]), float(x["remaining_base_amount"])) for x in d.get("bids", [])), reverse=True),
            "asks": sorted(((float(x["price"]), float(x["remaining_base_amount"])) for x in d.get("asks", [])))}


def infer_tick(book: dict) -> float:
    """从盘口价位反推 tick（HL 5 位有效数字规则会随价位跳变）。"""
    px = sorted({p for p, _ in book["bids"]} | {p for p, _ in book["asks"]})
    diffs = [round(b - a, 10) for a, b in zip(px, px[1:]) if b > a]
    return min(diffs) if diffs else 0.01


def walk(book: dict, side: str, qty: float) -> tuple[float, float, bool]:
    lv = book["asks"] if side == "buy" else book["bids"]
    rem, notional = qty, 0.0
    for px, sz in lv:
        take = min(rem, sz)
        notional += take * px
        rem -= take
        if rem <= 1e-12:
            return notional / qty, qty, True
    filled = qty - rem
    return (notional / filled if filled else 0.0), filled, False


def slip_curve(book: dict, mid: float, tiers: list[float]) -> dict:
    out = {}
    for qty in tiers:
        buy_vwap, buy_filled, buy_ok = walk(book, "buy", qty)
        sell_vwap, sell_filled, sell_ok = walk(book, "sell", qty)
        out[f"{qty:.0f}"] = {
            "buy_slip_bps": (buy_vwap - mid) / mid * 1e4 if buy_filled else None,
            "sell_slip_bps": (mid - sell_vwap) / mid * 1e4 if sell_filled else None,
            "buy_fillable": buy_ok, "sell_fillable": sell_ok,
            "buy_filled_qty": buy_filled, "sell_filled_qty": sell_filled,
        }
    return out


def band_events(basis: list[float], ts: list[int], W: int, entry_z: float, exit_z: float,
                hold: int) -> list[dict]:
    events = []
    for i in range(W, len(basis) - 1):
        win = basis[i - W:i]
        med = statistics.median(win)
        mad = statistics.median(abs(x - med) for x in win)
        sig = max(1.4826 * mad, 1e-9)
        z = (basis[i] - med) / sig
        if abs(z) < entry_z:
            continue
        side = -1 if z > 0 else 1          # fade
        exit_i, exit_b = None, None
        for j in range(i + 1, min(i + hold, len(basis))):
            if abs((basis[j] - med) / sig) <= exit_z:
                exit_i, exit_b = j, basis[j]
                break
        if exit_i is None:
            exit_i = min(i + hold, len(basis) - 1)
            exit_b = basis[exit_i]
        events.append({"i": i, "ts": ts[i], "side": side, "entry_z": z,
                       "entry_basis": basis[i], "exit_basis": exit_b, "hold_min": exit_i - i,
                       "capture_bps": (exit_b - basis[i]) * side})
    return events


def summarize(events: list[dict], bstar: float) -> dict:
    if not events:
        return {}
    caps = [e["capture_bps"] for e in events]
    out = {"n": len(events), "mean_capture_bps": statistics.mean(caps),
           "median_capture_bps": statistics.median(caps),
           "win_rate": 100 * sum(1 for c in caps if c > 0) / len(caps),
           "median_hold_min": statistics.median([e["hold_min"] for e in events]),
           "b_star_bps": round(bstar, 2),
           "net_mean_bps": statistics.mean(caps) - bstar}
    for p in (0.2, 0.5, 0.8):
        out[f"exp_net_bps_pfill_{p}"] = statistics.mean(caps) * p - bstar * p
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SOXL 完整执行模拟（只用公开端点）")
    ap.add_argument("--out-json", default=OUT_JSON, help="输出 JSON 路径")
    ap.add_argument("--out-md", default=OUT_MD, help="输出 Markdown 路径")
    args = ap.parse_args()
    print("candles ...", file=sys.stderr)
    h5 = hl_candles(HL_COIN, "5m", 2)
    l5 = lt_candles(LT_ID, "5m", 2)
    ts = sorted(set(h5) & set(l5))
    basis = [(l5[t][1] / h5[t][1] - 1) * 1e4 for t in ts]
    opens = [(l5[t][0] / h5[t][0] - 1) * 1e4 for t in ts]

    print("books ...", file=sys.stderr)
    hb, lb = hl_book(HL_COIN), lt_book(LT_ID)
    time.sleep(0.2)

    def mid_of(book):
        return (book["bids"][0][0] + book["asks"][0][0]) / 2

    hl_mid, lt_mid = mid_of(hb), mid_of(lb)
    hl_spread = (hb["asks"][0][0] - hb["bids"][0][0]) / hl_mid * 1e4
    lt_spread = (lb["asks"][0][0] - lb["bids"][0][0]) / lt_mid * 1e4

    # 成本（与 MATRIX_SPEC 一致）
    fees_hl, fees_lt = 1.5 + 4.5, 0.0
    tick_hl = infer_tick(hb)
    slip_hl = 2 * tick_hl / hl_mid * 1e4
    tick_lt = infer_tick(lb)
    slip_lt = 2 * tick_lt / lt_mid * 1e4
    carry = 0.5          # HL funding ≈0.5 bps/h × 1h 预期持仓
    leg_risk, recon = 0.3, 0.5
    bstar = fees_hl + fees_lt + hl_spread / 2 + lt_spread / 2 + slip_hl + slip_lt + carry + leg_risk + recon

    print("events ...", file=sys.stderr)
    events = band_events(basis, ts, W=48, entry_z=1.5, exit_z=0.3, hold=288) if len(basis) > 60 else []
    # 用下一根 open 入场（可执行口径）
    for e in events:
        j = e["i"] + 1
        e["entry_exec_basis"] = opens[j] if j < len(opens) else e["entry_basis"]
        e["capture_exec_bps"] = (e["exit_basis"] - e["entry_exec_basis"]) * e["side"]

    by_sess: dict[str, list[dict]] = {}
    for e in events:
        by_sess.setdefault(classify(e["ts"]).session, []).append(e)

    tiers = [100, 500, 1000, 5000, 10000]
    depth_cap = 0.10 * min(
        sum(p * q for p, q in hb["bids"] if p >= hl_mid * (1 - 25 / 1e4)),
        sum(p * q for p, q in lb["bids"] if p >= lt_mid * (1 - 25 / 1e4)),
    )

    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "pair": "xyz:SOXL|OPENAI(LIGHTER:197)",
        "legs": {
            "A_hyperliquid": {"mid": hl_mid, "spread_bps": hl_spread, "tick_assumed": tick_hl,
                              "slip_curve": slip_curve(hb, hl_mid, tiers)},
            "B_lighter": {"mid": lt_mid, "spread_bps": lt_spread, "tick": tick_lt,
                          "slip_curve": slip_curve(lb, lt_mid, tiers)},
        },
        "basis_5m_30d": {"n": len(basis), "mean": statistics.mean(basis),
                         "sd": statistics.pstdev(basis),
                         "p95_abs": sorted(abs(x) for x in basis)[int(0.95 * (len(basis) - 1))]},
        "costs": {"fees_hl": fees_hl, "fees_lt": fees_lt, "half_spread_hl": hl_spread / 2,
                  "half_spread_lt": lt_spread / 2, "slip_hl": slip_hl, "slip_lt": slip_lt,
                  "carry": carry, "leg_risk": leg_risk, "recon": recon, "b_star": round(bstar, 2)},
        "overall": summarize([{**e, "capture_bps": e["capture_exec_bps"]} for e in events], bstar),
        "by_session": {k: summarize([{**e, "capture_bps": e["capture_exec_bps"]} for e in v], bstar)
                       for k, v in by_sess.items()},
        "capacity": {"cap_usd_10pct_of_depth25": round(depth_cap, 2)},
        "assumptions": [
            "成交价用 5m 桶 open（下一根），避免用信号桶 close 的同桶偏差。",
            "p_fill 情景为假设值，未用实盘 fill 日志标定（尚无实盘）。",
            "carry 用 HL 1h funding 近似 0.5 bps；Lighter funding 周期未确认。",
            "盘口深度为抓取瞬间快照，单点采样。",
            "1m 口径下 Lighter K 线极稀疏（30 天仅约 500 根 5m），日内统计不稳健。",
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    json.dump(result, open(args.out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = ["# SOXL 执行模拟（HL `xyz:SOXL` ↔ Lighter `OPENAI`）", "",
             f"生成：{result['generated_utc']}", "",
             "## 1. 两腿画像", "",
             "| 腿 | mid | 点差(bps) | tick | 24h 量 |", "|---|---:|---:|---:|---:|",
             f"| HL xyz:SOXL | {hl_mid:.2f} | {hl_spread:.2f} | {tick_hl} | — |",
             f"| Lighter OPENAI | {lt_mid:.2f} | {lt_spread:.2f} | 0.01 | — |", "",
             "## 2. 滑点曲线（L2 行走 VWAP，实时盘口）", "",
             "| 名义 | HL 买/卖滑点(bps) | Lighter 买/卖滑点(bps) | 可成交 |", "|---:|---|---|:--:|"]
    for q in tiers:
        k = f"{q:.0f}"
        a = result["legs"]["A_hyperliquid"]["slip_curve"][k]
        b = result["legs"]["B_lighter"]["slip_curve"][k]
        ok = "✅" if (a["buy_fillable"] and b["buy_fillable"]) else "⚠️"
        lines.append(f"| ${q} | {a['buy_slip_bps']:.2f} / {a['sell_slip_bps']:.2f} | "
                     f"{b['buy_slip_bps']:.2f} / {b['sell_slip_bps']:.2f} | {ok} |")
    lines += ["", "## 3. 成本与回归事件统计", "",
              f"`b* = {result['costs']['b_star']} bps`（六项：{result['costs']}）", "",
              "| 口径 | n | 平均捕获 | 中位 | 胜率 | 中位持仓 | 扣成本净 | p_fill=0.2 | p_fill=0.5 | p_fill=0.8 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label, st in [("全部", result["overall"])] + [(f"session={k}", v) for k, v in result["by_session"].items()]:
        if not st:
            continue
        lines.append(
            f"| {label} | {st['n']} | {st['mean_capture_bps']:+.1f} | {st['median_capture_bps']:+.1f} | "
            f"{st['win_rate']:.0f}% | {st['median_hold_min']:.0f} min | {st['net_mean_bps']:+.1f} | "
            f"{st['exp_net_bps_pfill_0.2']:+.1f} | {st['exp_net_bps_pfill_0.5']:+.1f} | {st['exp_net_bps_pfill_0.8']:+.1f} |")
    lines += ["", f"容量（±25bps 深度的 10%）：**${result['capacity']['cap_usd_10pct_of_depth25']:.0f}**", "",
              "## 4. 模型假设（必须与实测区分）", ""] + [f"- {a}" for a in result["assumptions"]]
    os.makedirs(os.path.dirname(os.path.abspath(args.out_md)), exist_ok=True)
    open(args.out_md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(json.dumps({"overall": result["overall"], "capacity": result["capacity"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())