#!/usr/bin/env python
"""候选标的的 fade 事件级回放（**OKX 腿**），与 `screen_candidates.py`（Lighter 腿）对照。

关键差异：OKX 腿的基差**自相关更低**（ρ≈0.2–0.7 vs Lighter 0.89）、点差更小、但费率更高
（maker 2 + taker 5 bps vs Lighter 0）。

数据：
- HL：`candleSnapshot`（1h 30 天 / 5m 2 天）
- OKX：`market/history-candles`（1h 分页 `after` / 5m 2 天）

用法：python scripts/screen_candidates_okx.py [--intervals 1h,5m]
"""

from __future__ import annotations

import argparse
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
OKX = "https://www.okx.com/api/v5"
CAND = [("SOXL", "xyz:SOXL", "SOXL-USDT-SWAP"), ("RKLB", "xyz:RKLB", "RKLB-USDT-SWAP"),
        ("MRVL", "xyz:MRVL", "MRVL-USDT-SWAP"), ("EWY", "xyz:EWY", "EWY-USDT-SWAP"),
        ("INTC", "xyz:INTC", "INTC-USDT-SWAP"), ("NVDA", "xyz:NVDA", "NVDA-USDT-SWAP"),
        ("SNDK", "xyz:SNDK", "SNDK-USDT-SWAP"), ("MSTR", "xyz:MSTR", "MSTR-USDT-SWAP")]
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, payload: dict | None = None) -> Any:
    if payload is None:
        req = urllib.request.Request(url, headers=UA)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as fh:
        return json.load(fh)


def hl_series(coin: str, bar: str, days: int) -> dict[int, float]:
    now = int(time.time() * 1000)
    d = _get(HL, {"type": "candleSnapshot", "req": {"coin": coin, "interval": bar,
                                                   "startTime": now - days * 86400 * 1000, "endTime": now}})
    step = {"1m": 60000, "5m": 300000, "1h": 3600000}[bar]
    return {int(c["t"]) // step * step: float(c["c"]) for c in d if isinstance(c, dict)}


def okx_series(inst: str, bar: str, days: int) -> dict[int, float]:
    okx_bar = {"1m": "1m", "5m": "5m", "1h": "1H"}[bar]
    now = int(time.time() * 1000)
    start = now - days * 86400 * 1000
    out: dict[int, float] = {}
    after = None
    for _ in range(12):                     # 30d@1h ≈ 720 根 → 3 页
        u = f"{OKX}/market/history-candles?instId={inst}&bar={okx_bar}&limit=300"
        if after:
            u += f"&after={after}"
        try:
            rows = _get(u).get("data", [])
        except Exception:  # noqa: BLE001
            break
        if not rows:
            break
        for r in rows:
            t = int(r[0])
            out[t // 3600000 * 3600000] = float(r[4])
        after = min(int(r[0]) for r in rows)
        if after <= start or len(rows) < 300:
            break
    return out


def okx_book(inst: str) -> tuple[list, list]:
    b = _get(f"{OKX}/market/books?instId={inst}&sz=50")["data"][0]
    return ([(float(p), float(s)) for p, s, *_ in b.get("bids", [])],
            [(float(p), float(s)) for p, s, *_ in b.get("asks", [])])


def hl_book(coin: str) -> tuple[list, list]:
    d = _get(HL, {"type": "l2Book", "coin": coin})
    return ([(float(x["px"]), float(x["sz"])) for x in d["levels"][0]],
            [(float(x["px"]), float(x["sz"])) for x in d["levels"][1]])


def fade_events(basis: list[float], ts: list[int], W: int, entry_z: float, exit_z: float,
                hold: int) -> list[dict]:
    ev = []
    for i in range(W, len(basis) - 1):
        win = basis[i - W:i]
        med = statistics.median(win)
        mad = statistics.median(abs(x - med) for x in win)
        sig = max(1.4826 * mad, 1e-9)
        z = (basis[i] - med) / sig
        if abs(z) < entry_z:
            continue
        side = -1 if z > 0 else 1
        for j in range(i + 1, min(i + hold, len(basis))):
            if abs((basis[j] - med) / sig) <= exit_z:
                ev.append({"cap": (basis[j] - basis[i]) * side, "sess": classify(ts[i]).session,
                           "hold": j - i})
                break
        else:
            ev.append({"cap": (basis[min(i + hold, len(basis) - 1)] - basis[i]) * side,
                       "sess": classify(ts[i]).session, "hold": hold})
    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="1h")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    for bar in args.intervals.split(","):
        days = args.days if bar == "1h" else 2
        W = 48 if bar == "1h" else 48
        hold = 24 * 7 if bar == "1h" else 288
        print(f"\n=== bar={bar} days={days} 窗口={W}根 hold={hold}根 "
              f"(fade: |z|>=1.5 入, |z|<=0.3 出)")
        print(f"{'sym':7s}{'n':>5s}{'b*':>7s}{'cap':>8s}{'net':>8s}{'win':>6s}{'rth':>8s}{'closed':>8s}"
              f"{'okx_spr':>9s}{'hl_spr':>7s}{'okx_d25':>10s}")
        for name, coin, inst in CAND:
            try:
                h = hl_series(coin, bar, days)
                o = okx_series(inst, bar, days)
                ts = sorted(set(h) & set(o))
                if len(ts) < 80:
                    print(f"{name:7s} few bars {len(ts)}")
                    continue
                basis = [(o[t] / h[t] - 1) * 1e4 for t in ts]
                hb, ha = hl_book(coin)
                ob, oa = okx_book(inst)
                hm = (hb[0][0] + ha[0][0]) / 2
                om = (ob[0][0] + oa[0][0]) / 2
                hs = (ha[0][0] - hb[0][0]) / hm * 1e4
                os_ = (oa[0][0] - ob[0][0]) / om * 1e4
                depth_okx = sum(p * q for p, q in ob if p >= om * (1 - 25 / 1e4))
                bstar = (6.0 + 7.0 + hs / 2 + os_ / 2 + 2 * 0.001 / hm * 1e4
                         + 2 * 0.01 / om * 1e4 + 0.5 + 0.3 + 0.5)
                ev = fade_events(basis, ts, W, 1.5, 0.3, hold)
                if not ev:
                    print(f"{name:7s} no events")
                    continue
                caps = [e["cap"] for e in ev]
                rth = [e["cap"] for e in ev if e["sess"] == "rth"]
                clo = [e["cap"] for e in ev if e["sess"] == "closed"]
                print("%-7s%5d%7.1f%8.1f%8.1f%6.0f%8s%8s%9.2f%7.2f%10.0f" % (
                    name, len(ev), bstar, statistics.mean(caps), statistics.mean(caps) - bstar,
                    100 * sum(1 for x in caps if x > 0) / len(caps),
                    f"{statistics.mean(rth)-bstar:+.1f}" if rth else "n/a",
                    f"{statistics.mean(clo)-bstar:+.1f}" if clo else "n/a",
                    os_, hs, depth_okx))
            except Exception as exc:  # noqa: BLE001
                print(f"{name:7s} ERR {type(exc).__name__} {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())