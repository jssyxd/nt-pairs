#!/usr/bin/env python
"""采集数据分析：用真实 L2 快照标定 `p_fill`、队列与成交后 markout，并算出真实 EV。

输入：`data/collector/<VENUE>/<YYYYMMDD>.jsonl`（`scripts/collect_l2.py` 的输出）。
逻辑：
1. 把两个场所的快照按时间对齐，重建基差序列（用 mid）与 z-score 带；
2. 在 leader 侧按规则挂一个**合成**被动单（贴价、数量 q）：
   - 队列 = 挂单时该价位的 resting 量；
   - 成交判定 = 之后任一快照里 leader 的**对手价穿过我价**（买侧：best ask ≤ my bid）；
   - 成交价 = 我价（maker），并记录 fill 后的基差 markout（+1/+5/+15 分钟）；
3. 汇总：`p_fill`、成交等待时间、队列分布、markout 均值/分位、以及扣成本后的真实 EV。

用法：
    python scripts/analyze_collector.py --days 3 --qty 100
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from nt_pairs.sessions import classify  # noqa: E402


def load_venue(root: str, venue: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    d = os.path.join(root, venue)
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("symbol"):
                    out[r["symbol"]].append(r)
    for sym in out:
        out[sym].sort(key=lambda x: x.get("t_epoch", 0.0))
    return out


def mid_of(snap: dict) -> float | None:
    b = snap.get("bids") or []
    a = snap.get("asks") or []
    if not b or not a:
        return None
    return (float(b[0][0]) + float(a[0][0])) / 2


def touch(snap: dict, side: str) -> tuple[float, float] | None:
    """返回 (价格, 同档 resting 数量)。side='buy' → 买一；'sell' → 卖一。"""
    lv = snap.get("bids") if side == "buy" else snap.get("asks")
    if not lv:
        return None
    p, q = float(lv[0][0]), float(lv[0][1])
    n = int(lv[0][2]) if len(lv[0]) > 2 else 1
    return p, q * max(n, 1)


def zscore(series: list[float], idx: int, W: int) -> tuple[float, float, float] | None:
    if idx < W:
        return None
    win = series[idx - W:idx]
    med = statistics.median(win)
    mad = statistics.median(abs(x - med) for x in win)
    sig = max(1.4826 * mad, 1e-9)
    return med, sig, (series[idx] - med) / sig


def _session(epoch_s: float) -> str:
    try:
        return classify(int(epoch_s * 1000)).session
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/collector")
    ap.add_argument("--qty", type=float, default=100.0)
    ap.add_argument("--entry-z", type=float, default=1.5)
    ap.add_argument("--exit-z", type=float, default=0.3)
    ap.add_argument("--window", type=int, default=240, help="滚动窗口（分钟）")
    ap.add_argument("--max-hold", type=int, default=120, help="最长持有（分钟）")
    ap.add_argument("--fee-bps", type=float, default=7.0, help="双腿往返固定费（bps）")
    ap.add_argument("--capture-bps", type=float, default=17.0, help="基差捕获（bps）")
    ap.add_argument("--out", default="data/fill_calibration.json", help="输出 JSON 路径")
    args = ap.parse_args()

    hl = load_venue(args.root, "HYPERLIQUID")
    lt = load_venue(args.root, "LIGHTER")
    okx = load_venue(args.root, "OKX")
    print(f"loaded: HL {sum(len(v) for v in hl.values())} snaps / "
          f"Lighter {sum(len(v) for v in lt.values())} / OKX {sum(len(v) for v in okx.values())}", file=sys.stderr)

    report = []
    for sym in sorted(set(hl) & (set(lt) | set(okx))):
        follower_set = lt if sym in lt else okx
        follower_venue = "LIGHTER" if sym in lt else "OKX"
        a_snaps, b_snaps = hl[sym], follower_set[sym]
        # 按分钟对齐（取每分钟最后一个快照）
        def minute_map(snaps):
            m = {}
            for s in snaps:
                key = int(s.get("t_epoch", 0) * 60)
                m[key] = s
            return m
        am, bm = minute_map(a_snaps), minute_map(b_snaps)
        keys = sorted(set(am) & set(bm))
        if len(keys) < args.window + 10:
            print(f"{sym}: only {len(keys)} aligned minutes, skip", file=sys.stderr)
            continue
        basis = []
        for k in keys:
            ma, mb = mid_of(am[k]), mid_of(bm[k])
            basis.append((mb / ma - 1) * 1e4 if ma and mb else None)
        series = [x if x is not None else basis[-1] for x in basis]

        fills: list[dict] = []
        order: dict | None = None
        for i, k in enumerate(keys):
            # 1) 检查挂单是否被穿价成交（用**挂单所在场所**的对手价）
            if order is not None:
                venue_snap = am[k] if order["venue"] == "HYPERLIQUID" else bm[k]
                lv = venue_snap.get("asks") if order["side"] == "buy" else venue_snap.get("bids")
                crossed = False
                if lv:
                    best = float(lv[0][0])
                    crossed = best <= order["px"] if order["side"] == "buy" else best >= order["px"]
                if crossed:
                    fills.append({**order, "fill_idx": i, "fill_min": k,
                                  "markout_1": (series[min(i + 1, len(series) - 1)] - order["basis"]),
                                  "markout_5": (series[min(i + 5, len(series) - 1)] - order["basis"]),
                                  "markout_15": (series[min(i + 15, len(series) - 1)] - order["basis"]),
                                  "session": _session(k)})
                    order = None
                elif i - order["idx"] >= args.max_hold:
                    order = None
            # 2) 无挂单且带外 → 在**偏离方向的那一侧**挂 maker（fade）
            #    basis>0（follower 贵）→ 卖 follower；basis<0（HL 贵）→ 买 HL
            if order is None:
                zs = zscore(series, i, args.window)
                if zs:
                    med, sig, z = zs
                    if abs(z) >= args.entry_z:
                        if z > 0:                      # basis 高于带 → 卖贵的那一侧
                            side = "sell"
                            maker_venue = follower_venue
                        else:                          # basis 低于带 → 买便宜的那一侧
                            side = "buy"
                            maker_venue = "HYPERLIQUID"
                        snap = am[k] if maker_venue == "HYPERLIQUID" else bm[k]
                        t = touch(snap, side)
                        if t:
                            px, queue = t
                            order = {"side": side, "venue": maker_venue, "px": px, "qty": args.qty,
                                     "queue": queue, "idx": i, "min": k,
                                     "basis": series[i], "z": z, "basis_ref": med}
        n_min = len(keys)
        p_fill = len(fills) / n_min
        waits = [f["fill_min"] - f["min"] for f in fills]
        mo1 = [f["markout_1"] for f in fills]
        mo5 = [f["markout_5"] for f in fills]
        mo15 = [f["markout_15"] for f in fills]
        net = args.capture_bps - args.fee_bps + (statistics.mean(mo5) if mo5 else 0.0)
        rec = {"symbol": sym, "follower_venue": follower_venue, "minutes": n_min,
               "fills": len(fills), "p_fill_per_min": round(p_fill, 5),
               "median_wait_min": statistics.median(waits) if waits else None,
               "queue_at_touch_median": statistics.median([f["queue"] for f in fills]) if fills else None,
               "markout_1_mean": round(statistics.mean(mo1), 2) if mo1 else None,
               "markout_5_mean": round(statistics.mean(mo5), 2) if mo5 else None,
               "markout_15_mean": round(statistics.mean(mo15), 2) if mo15 else None,
               "net_bps_per_fill": round(net, 2),
               "net_bps_per_minute": round(net * p_fill, 4),
               "by_session": {s: sum(1 for f in fills if f["session"] == s) for s in ("rth", "pre", "post", "closed")}}
        report.append(rec)
        print(json.dumps(rec, ensure_ascii=False), file=sys.stderr)

    out = args.out
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {out} ({len(report)} symbols)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())