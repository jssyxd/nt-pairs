#!/usr/bin/env python
"""候选标的的事件级 fade 回放（captain 2026-11-12 实测脚本，固化以便复现）。

对每个 HL↔Lighter 同名对：
- 取 5m K 线（最近 2 天，Lighter 侧分页）；
- 用滚动 4h 中位数 + MAD 构造带，`|z| ≥ 1.5` 反向（fade）入场，
  `|z| ≤ 0.3` 或 24h 离场，入场价用**下一根 open**；
- 用实时盘口算两腿的 `b*`（六项），并按会话（RTH/closed/pre/post）分层输出净边际。

用法：python scripts/screen_candidates.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from nt_pairs.sessions import classify  # noqa: E402

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
CAND = [("SOXL", "xyz:SOXL", 197), ("EWY", "xyz:EWY", 166), ("MRVL", "xyz:MRVL", 174),
        ("BABA", "xyz:BABA", 177), ("RKLB", "xyz:RKLB", 186), ("KORU", "xyz:KORU", 226),
        ("KIOXIA", "xyz:KIOXIA", 225), ("INTC", "xyz:INTC", 137), ("NVDA", "xyz:NVDA", 110),
        ("SNDK", "xyz:SNDK", 139), ("MSTR", "xyz:MSTR", 122), ("CRCL", "xyz:CRCL", 121)]


def post(p: dict) -> Any:
    r = urllib.request.Request(HL, data=json.dumps(p).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=45) as fh:
        return json.load(fh)


def get(u: str) -> Any:
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=45) as fh:
        return json.load(fh)


def hl_candles(coin: str, days: int = 2) -> dict[int, tuple[float, float]]:
    now = int(time.time() * 1000)
    d = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "5m",
                                                "startTime": now - days * 86400 * 1000, "endTime": now}})
    return {int(c["t"]) // 300000 * 300000: (float(c["o"]), float(c["c"])) for c in d if isinstance(c, dict)}


def lt_candles(mid: int, days: int = 2) -> dict[int, tuple[float, float]]:
    now = int(time.time())
    start = now - days * 86400
    out: dict[int, tuple[float, float]] = {}
    cur = start
    while cur < now:
        end = min(cur + 400 * 300, now)
        d = get(f"{LT}/candles?market_id={mid}&resolution=5m&start_timestamp={cur}&end_timestamp={end}&count_back=2")
        for c in d.get("c", []):
            out[int(c["t"]) // 300000 * 300000] = (float(c["o"]), float(c["c"]))
        if end <= cur:
            break
        cur = end
    return out


def books(coin: str, mid: int):
    d = post({"type": "l2Book", "coin": coin})
    hb = [(float(x["px"]), float(x["sz"])) for x in d["levels"][0]]
    ha = [(float(x["px"]), float(x["sz"])) for x in d["levels"][1]]
    d2 = get(f"{LT}/orderBookOrders?market_id={mid}&limit=100")
    lb = sorted(((float(x["price"]), float(x["remaining_base_amount"])) for x in d2.get("bids", [])), reverse=True)
    la = sorted(((float(x["price"]), float(x["remaining_base_amount"])) for x in d2.get("asks", [])))
    return hb, ha, lb, la


def tick_of(bids, asks) -> float:
    px = sorted({p for p, _ in bids} | {p for p, _ in asks})
    ds = [round(y - x, 10) for x, y in zip(px, px[1:]) if y > x]
    return min(ds) if ds else 0.01


def main() -> int:
    print(f"{'sym':8s}{'n':>4s}{'b*':>7s}{'cap':>8s}{'net':>7s}{'win':>6s}{'rth':>8s}{'closed':>8s}{'depthLT25':>11s}")
    for name, coin, mid in CAND:
        try:
            h, l = hl_candles(coin), lt_candles(mid)
            ts = sorted(set(h) & set(l))
            if len(ts) < 80:
                print(f"{name:8s} too few bars ({len(ts)})")
                continue
            basis = [(l[t][1] / h[t][1] - 1) * 1e4 for t in ts]
            opens = [(l[t][0] / h[t][0] - 1) * 1e4 for t in ts]
            hb, ha, lb, la = books(coin, mid)
            hm = (hb[0][0] + ha[0][0]) / 2
            lm = (lb[0][0] + la[0][0]) / 2
            hs = (ha[0][0] - hb[0][0]) / hm * 1e4
            ls = (la[0][0] - lb[0][0]) / lm * 1e4
            bstar = (6.0 + hs / 2 + ls / 2 + 2 * tick_of(hb, ha) / hm * 1e4
                     + 2 * tick_of(lb, la) / lm * 1e4 + 0.5 + 0.3 + 0.5)
            depth_lt = sum(p * q for p, q in lb if p >= lm * (1 - 25 / 1e4))
            ev = []
            W = 48
            for i in range(W, len(basis) - 1):
                win = basis[i - W:i]
                med = statistics.median(win)
                mad = statistics.median(abs(x - med) for x in win)
                sig = max(1.4826 * mad, 1e-9)
                z = (basis[i] - med) / sig
                if abs(z) < 1.5:
                    continue
                side = -1 if z > 0 else 1
                for j in range(i + 1, min(i + 288, len(basis))):
                    if abs((basis[j] - med) / sig) <= 0.3:
                        ev.append({"cap": (basis[j] - opens[i + 1]) * side, "sess": classify(ts[i]).session})
                        break
            if not ev:
                print(f"{name:8s} no events")
                continue
            caps = [e["cap"] for e in ev]
            rth = [e["cap"] for e in ev if e["sess"] == "rth"]
            clo = [e["cap"] for e in ev if e["sess"] == "closed"]
            print(f"{name:8s}{len(ev):4d}{bstar:7.1f}{statistics.mean(caps):8.1f}"
                  f"{statistics.mean(caps) - bstar:7.1f}{100 * sum(1 for x in caps if x > 0) / len(caps):6.0f}"
                  f"{((statistics.mean(rth) - bstar) if rth else float('nan')):8.1f}"
                  f"{((statistics.mean(clo) - bstar) if clo else float('nan')):8.1f}{depth_lt:11.0f}")
        except Exception as exc:  # noqa: BLE001
            print(f"{name:8s} ERR {type(exc).__name__} {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())