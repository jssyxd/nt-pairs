#!/usr/bin/env python3
"""用 CryptoHFTData 的历史 L2 做成交率标定（Lighter 公共 API 被风控时的替代路径）。

为什么需要它
------------
实时采集 L2 撞上了 Lighter 的 Human Verification 闸门（`/api/v1/markets` → 403，
`/api/v1/orderBookOrders` → 405 + 验证页），本机拿不到 Lighter 的实时快照。
但 CryptoHFTData 有 Lighter 的**逐小时 L2 增量 + L0 成交**历史（可回溯到 2026-09-01）。

做法
----
把 CHD 的盘口增量流重建成 1 秒粒度的盘口快照（与 `scripts/collect_l2.py` 的输出同构），
再喂给 `scripts/calibrate_fill_model.py` 里那套**已经写好的**标定数学
（`src/nt_pairs/fill_calib.py`）：
  * 贴价档位上**逐笔采样队列（≥3 次取中位数）**
  * TTL 窗口内**穿过我价的成交量** → 实测成交判定
  * 模型 p_fill（Poisson 队列消耗）vs 实测 → 分档偏差

与实时采集的差别（必须写进报告）
--------------------------------
  * CHD 的 L2 增量流在**每小时的边界**会重置（`event_type='snapshot'` 组），
    所以采样窗口不能跨小时；
  * 成交判定用的是 L0 成交价与被动价的穿越关系（与实时采集同口径）；
  * 盘口快照是**重建值**，不是交易所逐笔快照 —— 重建误差（增量丢失/乱序）会被计入偏差。

用法
----
    python scripts/calibrate_fill_chd.py --date 2026-11-13 --hours 12,13,14 \\
        --symbol SOXL --out data/fill_calibration.json --report docs/FILL_CALIBRATION.md
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from nt_pairs import fill_calib as fc  # noqa: E402
from nt_pairs.chd_client import ChdClient  # noqa: E402

VENUE_CHD = {"hyperliquid_futures": "hyperliquid_futures", "lighter": "lighter"}
HL_SYM = "XYZ:SOXL"
LT_SYM = "SOXL"


def rebuild_snapshots(path: Path, venue: str, symbol: str, max_age_ms: int = 10_000) -> list[fc.Snap]:
    """CHD 的 L2 增量流 → 1 秒粒度盘口快照（collector 同构格式）。"""
    import pyarrow.parquet as pq

    df = pq.read_table(path, columns=["event_time", "event_type", "side", "price", "quantity"]).to_pandas()
    df = df.sort_values("event_time", kind="stable")

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    last_t = None
    out: list[fc.Snap] = []
    cur_t = None
    cur_bids: list[list[float]] = []
    cur_asks: list[list[float]] = []

    def flush(t_ms: int, b: dict[float, float], a: dict[float, float]) -> None:
        if t_ms is None or not b or not a:
            return
        out.append(fc.Snap(t=t_ms / 1000.0, venue=venue, symbol=symbol,
                           bids=dict(b), asks=dict(a), trades=(), gap=False))

    events = df.to_dict("records")
    i = 0
    n = len(events)
    while i < n:
        ev = events[i]
        t = int(ev["event_time"])
        j = i
        group: list[dict[str, Any]] = []
        while j < n and int(events[j]["event_time"]) == t:
            group.append(events[j])
            j += 1
        if any(g["event_type"] == "snapshot" for g in group):
            bids = {}
            asks = {}
        for g in group:
            try:
                p = float(g["price"]); q = float(g["quantity"])
            except (TypeError, ValueError):
                continue
            if p <= 0:
                continue
            if g["side"] == "bid":
                if q <= 0:
                    bids.pop(p, None)
                else:
                    bids[p] = q
            else:
                if q <= 0:
                    asks.pop(p, None)
                else:
                    asks[p] = q
        # 每秒输出一帧
        sec = t // 1000
        if cur_t is None:
            cur_t = sec
        elif sec != cur_t:
            flush(int(cur_t) * 1000, bids, asks)
            cur_t = sec
        i = j
    flush(int(cur_t) * 1000 if cur_t else 0, bids, asks)
    return out


def load_trades(path: Path) -> list[tuple[int, float, float, str]]:
    import pyarrow.parquet as pq

    df = pq.read_table(path, columns=["trade_time", "price", "quantity", "is_buyer_maker"]).to_pandas()
    out = []
    for ts, p, q, buyer_maker in df.itertuples(index=False):
        try:
            pf = float(p); qf = float(q)
        except (TypeError, ValueError):
            continue
        # 主动方是买方 → 成交发生在 ask 侧（穿过我的买价的是卖方主动单）
        side = "sell" if (not buyer_maker) else "buy"
        out.append((int(ts), pf, qf, side))
    out.sort(key=lambda x: x[0])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="2026-11-13", help="起始日期 YYYY-MM-DD（UTC）")
    ap.add_argument("--hours", default="12,13,14,15,16,17,18,19,20,21")
    ap.add_argument("--symbol", default="SOXL")
    ap.add_argument("--cache-dir", default="data/chd_cache")
    ap.add_argument("--ttls", default="30,60,120,180")
    ap.add_argument("--notionals", default="100,200,500,1000")
    ap.add_argument("--probe-interval", type=float, default=30.0)
    ap.add_argument("--trail-s", type=float, default=60.0)
    ap.add_argument("--maker-venue", default="LIGHTER", choices=["LIGHTER", "HYPERLIQUID"])
    ap.add_argument("--out", default="data/fill_calibration.json")
    ap.add_argument("--report", default="docs/FILL_CALIBRATION.md")
    args = ap.parse_args(argv)

    client = ChdClient(cache_dir=args.cache_dir)
    date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    hours = [int(x) for x in args.hours.split(",") if x.strip()]

    legs = {
        "HYPERLIQUID": ("hyperliquid_futures", HL_SYM),
        "LIGHTER": ("lighter", LT_SYM),
    }

    snaps_by_venue: dict[str, list[fc.Snap]] = {}
    trades_by_venue: dict[str, list[tuple]] = {}
    for venue, (chd_ex, chd_sym) in legs.items():
        snaps: list[fc.Snap] = []
        trades: list[tuple] = []
        for h in hours:
            try:
                ob = client.download(chd_ex, date.strftime("%Y-%m-%d"), h, chd_sym, "orderbook")
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {venue} orderbook {h}: {exc}")
                continue
            if ob:
                snaps.extend(rebuild_snapshots(Path(ob), venue, chd_sym))
            try:
                tr = client.download(chd_ex, date.strftime("%Y-%m-%d"), h, chd_sym, "trades")
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] {venue} trades {h}: {exc}")
                continue
            if tr:
                trades.extend(load_trades(Path(tr)))
        snaps.sort(key=lambda s: s.t)
        trades.sort(key=lambda t: t[0])
        snaps_by_venue[venue] = snaps
        trades_by_venue[venue] = trades
        print(f"[load] {venue}: {len(snaps)} snapshots, {len(trades)} trades")

    maker = args.maker_venue
    taker = "HYPERLIQUID" if maker == "LIGHTER" else "LIGHTER"
    maker_snaps = snaps_by_venue[maker]
    taker_snaps = snaps_by_venue[taker]
    maker_trades = trades_by_venue[maker]
    taker_trades = trades_by_venue[taker]
    if not maker_snaps or not taker_snaps:
        print("no snapshots; abort", file=sys.stderr)
        return 2

    # 同刻对齐：按 1 秒栅格取两所 mid
    def mids(snaps: list[fc.Snap]) -> dict[int, float]:
        out: dict[int, float] = {}
        for s in snaps:
            m = fc.mid_of(s)
            if m:
                out[int(s.t)] = m
        return out

    mm = mids(maker_snaps)
    mt = mids(taker_snaps)
    common = sorted(set(mm) & set(mt))
    print(f"[align] common seconds: {len(common)}")

    # 逐秒在 maker 侧贴价挂合成单，用 taker 侧的 mid 变化做「价差收敛」判定
    ttls = [int(x) for x in args.ttls.split(",")]
    notionals = [float(x) for x in args.notionals.split(",")]
    # tick 从 maker 侧盘口价位反推（成交价必须精确落在贴价档位上）
    _pxs = []
    for _s in maker_snaps[:2000]:
        _pxs.extend(list(_s.bids.keys())[:5])
        _pxs.extend(list(_s.asks.keys())[:5])
    from nt_pairs import market_micro as mm
    tick = mm.infer_tick(_pxs) if _pxs else 0.0
    print(f"[tick] inferred tick = {tick}")

    rows: list[dict[str, Any]] = []
    last_probe_t = None
    for i, ts in enumerate(common):
        # 探针按**时间间隔**取（不是索引间隔）—— 否则 common 序列稀疏时探针数会很少
        if last_probe_t is not None and (ts - last_probe_t) < args.probe_interval:
            continue
        last_probe_t = ts
        snap = next((s for s in maker_snaps if int(s.t) == ts), None)
        if snap is None:
            continue
        for side in ("buy", "sell"):
            bl = fc.best_level(snap, side)
            if bl is None:
                continue
            px, resting = bl
            samples, q_med = fc.sample_queue(maker_snaps, i, side, px, n_samples=3, min_gap_s=0.2)
            if q_med is None or q_med <= 0:
                continue
            for notional in notionals:
                our_size = notional / px if px > 0 else 0.0
                t0_ms = ts * 1000
                rec: dict[str, Any] = {
                    "t": ts, "px": px, "side": side, "notional": notional,
                    "queue_median": q_med, "queue_samples": samples, "our_size": our_size,
                }
                for ttl in ttls:
                    th = fc.through_trades(maker_trades, t0_ms, t0_ms + ttl * 1000, px, side)
                    cum = fc.cum_volume(th)
                    n = len(th)
                    avg = cum / n if n else 0.0
                    lam = n / ttl if ttl > 0 else 0.0
                    # 成交判定：**按贴价档位精确归属**（成交价 == px 消耗本档队列；
                    # 成交价穿过 px 说明市价扫穿 → 全成交）。用 fill_outcome（按总量）
                    # 会把更低价位的量算到我们头上。
                    outcome = fc.fill_outcome_at_touch(q_med, our_size, th, px, side,
                                                      tick=tick)
                    rec[str(ttl)] = {
                        "n_through": n, "lam": lam, "avg_size": avg, "cum": cum,
                        "outcome": outcome,
                        "p_model_full": fc.model_p_fill(q_med, lam, ttl, our_size, avg),
                        "p_model_any": fc.model_p_fill_any(q_med, lam, ttl, avg),
                        "p_compound_full": fc.compound_p_fill(q_med + our_size, lam, ttl,
                                                             [t[2] for t in th]) if n else 0.0,
                        "p_compound_any": fc.compound_p_fill(q_med, lam, ttl,
                                                            [t[2] for t in th]) if n else 0.0,
                    }
                rows.append(rec)

    print(f"[probe] {len(rows)} probes")

    # 汇总：每个 (TTL, 名义) 的实测 vs 模型
    summary: list[dict[str, Any]] = []
    for ttl in ttls:
        for notional in notionals:
            sel = [r for r in rows if r["notional"] == notional and str(ttl) in r]
            if not sel:
                continue
            # 实测成交 = outcome 为 full 或 partial（队列被吃穿 → 我们至少部分成交）
            emp = sum(1 for r in sel if r[str(ttl)]["outcome"] in ("full", "partial")) / len(sel)
            for key, label in (("p_model_full", "model_full"), ("p_model_any", "model_any"),
                               ("p_compound_full", "compound_full"), ("p_compound_any", "compound_any")):
                mv = [r[str(ttl)][key] for r in sel]
                summary.append({
                    "ttl_s": ttl, "notional": notional, "model": label,
                    "probes": len(sel),
                    "empirical_p_fill": round(emp, 4),
                    "model_p_fill": round(statistics.fmean(mv), 4) if mv else 0.0,
                    "bias": round((statistics.fmean(mv) - emp), 4) if mv else 0.0,
                    "p_model_p10": round(sorted(mv)[max(0, int(0.1 * len(mv)) - 1)], 4) if mv else 0.0,
                    "p_model_p90": round(sorted(mv)[max(0, int(0.9 * len(mv)) - 1)], 4) if mv else 0.0,
                })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "CryptoHFTData 历史 L2（Lighter 公共 API 被 Human Verification 挡住的替代路径）",
        "date": args.date, "hours": hours, "maker_venue": maker, "taker_venue": taker,
        "symbol": args.symbol,
        "aligned_seconds": len(common),
        "probe_count": len(rows),
        "summary": summary,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    L = ["# 成交率标定（模型 vs 实测）\n",
         f"生成：{payload['generated_at']}\n",
         f"数据源：{payload['source']}\n",
         f"窗口：{args.date} {hours}（UTC）　maker={maker} / taker={taker}　标的 {args.symbol}\n",
         f"对齐秒数：{len(common)}　探针数：{len(rows)}\n",
         "\n## 1. 实测 vs 模型（每个 TTL × 名义档）\n",
         "| TTL | 名义 | 模型 | 探针数 | 实测 p_fill | 模型 p_fill | 偏差 |",
         "|---:|---:|---|---:|---:|---:|---:|"]
    for s in summary:
        L.append(f"| {s['ttl_s']}s | ${s['notional']:.0f} | {s['model']} | {s['probes']} | "
                 f"{s['empirical_p_fill']:.3f} | {s['model_p_fill']:.3f} | {s['bias']:+.3f} |")
    L.append("\n## 2. 口径与局限（必须读）\n")
    L.append("- 实测 p_fill = 合成被动单在 TTL 内**被成交量穿过**的比例（贴价排队尾口径）。")
    L.append("- CHD 的 L2 增量流**每小时边界重置**（`event_type='snapshot'`），采样不跨小时。")
    L.append("- 盘口是**重建值**，不是交易所逐帧快照 → 增量丢失/乱序会计入偏差。")
    L.append("- 本报告的 `p_fill` 仍是**上界**（排队尾 + 重建盘口），实盘前需用真实成交日志复核。")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {args.out} and {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
