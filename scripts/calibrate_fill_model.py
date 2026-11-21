#!/usr/bin/env python
"""成交率标定：把排队模型（`market_micro.fill_probability`）与 L2 回放实测并排比较。

输入：`data/collector/<VENUE>/<YYYYMMDD>.jsonl`（`scripts/collect_l2.py` 的输出，含盘口 + 逐笔成交）。
输出：`data/fill_calibration.json`（机读）+ `docs/FILL_CALIBRATION.md`（人读）。

做法（与 `scripts/chd_screen.py` 同一口径，保证可比）：
1. **合成挂单**：在决策时刻贴价挂一笔 maker 单（买一或卖一），排队在**队尾**；
2. **贴价队列**：向前逐笔采样 ≥3 次（间隔 ≥200ms）取**中位数**（`docs/FILL_MODEL.md` §3 硬要求）；
3. **成交判定**：窗口内穿过我们价位的成交量 ≥ 队头 + 我们的量 → 全成交；
   ≥ 队头 → 被碰到（部分成交）；否则未成交；
4. **模型对照**：同一窗口实测的 λ（笔/秒）、实测均单量、实测中位队列代入
   `market_micro.fill_probability`；
5. **偏差诊断**：区分「λ 口径」「队列口径」「模型形式」三种来源；
6. **EV 阶梯**：用标定后的 p_fill 重算 $100/$200/$500/$1000 的净 EV。

用法：
    python scripts/calibrate_fill_model.py --root data/collector --symbols SOXL
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from nt_pairs import fill_calib as fc  # noqa: E402
from nt_pairs import market_micro as mm  # noqa: E402
from nt_pairs.costs import LegSpec  # noqa: E402
from nt_pairs.funding_fee import b_star, venue_fees  # noqa: E402
from nt_pairs.sessions import classify  # noqa: E402

HL_VENUE = "HYPERLIQUID"
LT_VENUE = "LIGHTER"
# 场所名 → 仓库成本模型里的 venue key
COST_VENUE = {HL_VENUE: "hyperliquid_futures", LT_VENUE: "lighter"}
# HL HIP-3 的实测费率（deployerFeeScale=1.0, growthMode=enabled → maker 0.30 / taker 0.90）
HL_FEE = venue_fees("hyperliquid_futures", deployer_fee_scale=1.0, growth_mode=True)
LT_FEE = venue_fees("lighter")


# ---------------------------------------------------------------- 盘口 → 成本腿

def leg_spec(venue: str, snap: fc.Snap, qty_usd: float) -> tuple[LegSpec, float]:
    """从快照构造 `costs.LegSpec` + 该名义下的吃单滑点（bps）。

    口径与 `scripts/chd_screen.py` 一致：tick 由 `infer_tick` 反推、半价差取价差一半、
    深度取 ±25bps 带内名义、滑点用 `walk_book` 吃单（**不是** 2×tick 经验代理）。
    """
    mids = [p for p in list(snap.bids) + list(snap.asks)]
    mid = fc.mid_of(snap)
    if not mid or not mids:
        return None, 0.0  # type: ignore[return-value]
    tick = Decimal(str(mm.infer_tick(mids)))
    spread = mm.spread_bps({"bids": [[p, q] for p, q in snap.bids.items()],
                            "asks": [[p, q] for p, q in snap.asks.items()]})
    book = mm.book_depth({"bids": [[p, q] for p, q in snap.bids.items()],
                           "asks": [[p, q] for p, q in snap.asks.items()]}, mid, 25.0)
    fee = HL_FEE if venue == HL_VENUE else LT_FEE
    spec = LegSpec(venue=COST_VENUE[venue], maker_bps=fee.maker_bps, taker_bps=fee.taker_bps,
                   half_spread_bps=Decimal(str(spread / 2.0)), price=Decimal(str(mid)),
                   tick=tick, depth_bid_usd=Decimal(str(book["bid_usd"])),
                   depth_ask_usd=Decimal(str(book["ask_usd"])))
    # 吃单滑点（两腿都要：maker 腿平仓也是 taker）
    qty_base = qty_usd / mid
    bids = [[p, q] for p, q in snap.bids.items()]
    asks = [[p, q] for p, q in snap.asks.items()]
    _, slip_buy, _ = mm.walk_book("buy", asks, qty_base, mid=mid)
    _, slip_sell, _ = mm.walk_book("sell", bids, qty_base, mid=mid)
    slip = max(slip_buy, slip_sell)
    return spec, slip


# ---------------------------------------------------------------- 探针（合成挂单）

def build_probes(snaps: list[fc.Snap], trades: list[tuple], side: str, ttl_list: list[int],
                 notional: float, probe_interval: float, trail_s: float) -> list[dict]:
    """在贴价档位上周期性挂「合成单」，逐 TTL 记录实测与模型概率。"""
    out: list[dict] = []
    last_t = None
    for i, s in enumerate(snaps):
        if last_t is not None and (s.t - last_t) < probe_interval:
            continue
        bl = fc.best_level(s, side)
        if bl is None:
            continue
        px, _resting = bl
        samples, q_med = fc.sample_queue(snaps, i, side, px, n_samples=3, min_gap_s=0.2)
        if q_med is None or q_med <= 0:
            continue
        last_t = s.t
        our_size = notional / px if px > 0 else 0.0
        t0_ms = int(s.t * 1000)
        rec: dict[str, Any] = {
            "t": s.t, "px": px, "side": side, "queue_samples": samples, "queue_median": q_med,
            "our_size": our_size, "notional": notional,
        }
        tr_trail = fc.through_trades(trades, t0_ms - int(trail_s * 1000), t0_ms, px, side)
        trail_n = len(tr_trail)
        trail_cum = fc.cum_volume(tr_trail)
        trail_avg = trail_cum / trail_n if trail_n else 0.0
        rec["trail_lam"] = trail_n / trail_s if trail_s > 0 else 0.0
        rec["trail_avg_size"] = trail_avg
        for ttl in ttl_list:
            th = fc.through_trades(trades, t0_ms, t0_ms + ttl * 1000, px, side)
            cum = fc.cum_volume(th)
            n = len(th)
            avg = cum / n if n else 0.0
            lam = n / ttl if ttl > 0 else 0.0
            outcome = fc.fill_outcome(q_med, our_size, cum)
            sizes = [t[2] for t in th]
            rec[str(ttl)] = {
                "n_through": n, "lam": lam, "avg_size": avg, "cum": cum,
                "outcome": outcome,
                "p_model_full": fc.model_p_fill(q_med, lam, ttl, our_size, avg),
                "p_model_any": fc.model_p_fill_any(q_med, lam, ttl, avg),
                "p_model_trail": fc.model_p_fill(q_med, rec["trail_lam"], ttl, our_size, trail_avg),
                "p_compound_full": fc.compound_p_fill(q_med + our_size, lam, ttl, sizes) if n else 0.0,
                "p_compound_any": fc.compound_p_fill(q_med, lam, ttl, sizes) if n else 0.0,
                "wait_to_full_s": (time_to_reach(trades, t0_ms, ttl * 1000, px, side,
                                                 q_med + our_size)),
            }
        out.append(rec)
    return out


def time_to_reach(trades: list[tuple], t0_ms: int, window_ms: int, px: float, side: str,
                  need: float) -> float | None:
    """累计穿过量首次达到 `need` 的时刻（秒，相对 t0）。"""
    cum = 0.0
    for t in trades:
        if t[0] < t0_ms or t[0] > t0_ms + window_ms:
            continue
        if not ((side == "buy" and t[1] <= px) or (side == "sell" and t[1] >= px)):
            continue
        cum += t[2]
        if cum >= need:
            return (t[0] - t0_ms) / 1000.0
    return None


def summarize_probes(probes: list[dict], ttl: int, key: str = "queue_median") -> dict:
    """一组探针的实测/模型概率汇总（偏差在这里算）。"""
    if not probes:
        return {"n": 0}
    n = len(probes)
    full = sum(1 for p in probes if p[str(ttl)]["outcome"] == "full")
    anyf = sum(1 for p in probes if p[str(ttl)]["outcome"] in ("full", "partial"))
    p_emp_full = full / n
    p_emp_any = anyf / n
    mean = lambda f: statistics.mean(f(p) for p in probes)  # noqa: E731
    p_model_full = mean(lambda p: p[str(ttl)]["p_model_full"])
    p_model_any = mean(lambda p: p[str(ttl)]["p_model_any"])
    p_model_trail = mean(lambda p: p[str(ttl)]["p_model_trail"])
    p_comp_full = mean(lambda p: p[str(ttl)]["p_compound_full"])
    p_comp_any = mean(lambda p: p[str(ttl)]["p_compound_any"])
    waits = [p[str(ttl)]["wait_to_full_s"] for p in probes if p[str(ttl)]["wait_to_full_s"] is not None]
    # 队列噪声：3 次采样值的相对离散度
    spreads = []
    for p in probes:
        vals = [v for v in p["queue_samples"] if v is not None]
        if len(vals) >= 2:
            spreads.append((max(vals) - min(vals)) / max(statistics.median(vals), 1e-9))
    # 隐含需求：让模型复现实测 p_fill 所需的 need（比值 >1 → 真实队列比采样值更大）
    mean_need = mean(lambda p: p[key] + p["our_size"])
    mean_hits = mean(lambda p: p[str(ttl)]["lam"] * ttl * p[str(ttl)]["avg_size"]
                     / max(p[key] + p["our_size"], 1e-9))
    # 「队列口径」诊断：把采样队列整体乘以 k，看 k=几时模型能复现实测 p_fill。
    #   k<1 → 采样队列偏大（模型偏保守）；k>1 → 采样队列偏小（模型偏乐观）。
    q_scale = None
    for k in (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0, 8.0):
        pk = statistics.mean(fc.model_p_fill(p[key] * k, p[str(ttl)]["lam"], ttl,
                                              p["our_size"], p[str(ttl)]["avg_size"])
                             for p in probes)
        if abs(pk - p_emp_full) <= 0.02:
            q_scale = k
            break
    if q_scale is None:
        pk1 = statistics.mean(fc.model_p_fill(p[key], p[str(ttl)]["lam"], ttl,
                                              p["our_size"], p[str(ttl)]["avg_size"])
                              for p in probes)
        q_scale = ("<0.1" if pk1 > p_emp_full else ">8")
    return {
        "n": n,
        "p_emp_full": round(p_emp_full, 4), "p_emp_any": round(p_emp_any, 4),
        "p_model_full": round(p_model_full, 4), "p_model_any": round(p_model_any, 4),
        "p_model_trail_lam": round(p_model_trail, 4),
        "p_compound_full": round(p_comp_full, 4), "p_compound_any": round(p_comp_any, 4),
        "bias_model_minus_emp_full": round(p_model_full - p_emp_full, 4),
        "bias_model_trail_minus_emp_full": round(p_model_trail - p_emp_full, 4),
        "bias_compound_minus_emp_full": round(p_comp_full - p_emp_full, 4),
        "bias_model_any_minus_emp_any": round(p_model_any - p_emp_any, 4),
        "bias_compound_any_minus_emp_any": round(p_comp_any - p_emp_any, 4),
        "queue_median_mean": round(mean_need - mean(lambda p: p["our_size"]), 3),
        "model_mean_hits": round(mean_hits, 3),
        "queue_scale_to_match_emp": q_scale,
        "queue_sample_spread_rel_median": round(statistics.median(spreads), 3) if spreads else None,
        "median_wait_to_full_s": round(statistics.median(waits), 1) if waits else None,
        "n_through_total": sum(p[str(ttl)]["n_through"] for p in probes),
        "lam_mean": round(mean(lambda p: p[str(ttl)]["lam"]), 4),
        "avg_size_mean": round(mean(lambda p: p[str(ttl)]["avg_size"]), 4),
    }


def bias_by_bucket(probes: list[dict], ttl: int) -> dict:
    """按贴价队列分档看偏差。"""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in probes:
        buckets[fc.queue_bucket(p["queue_median"])].append(p)
    order = ["0", "<=5", "6-20", "21-50", "51-200", ">200"]
    return {b: summarize_probes(buckets.get(b, []), ttl) for b in order if buckets.get(b)}


# ---------------------------------------------------------------- 基差 / 信号

def mid_series(snaps: list[fc.Snap]) -> dict[int, float]:
    out: dict[int, float] = {}
    for s in snaps:
        m = fc.mid_of(s)
        if m:
            out[int(s.t)] = m
    return out


def grid_ratio(mid_a: dict[int, float], mid_b: dict[int, float], t0: int, t1: int,
               grid_s: int = 2, max_age_s: int = 10) -> list[float]:
    """两腿同刻 mid 之比（缺失时按最近值前向填充，最多 max_age_s）。"""
    keys_a = sorted(mid_a)
    keys_b = sorted(mid_b)
    out: list[float] = []
    ia = ib = 0
    la = lb = None
    for t in range(t0, t1 + 1, grid_s):
        while ia < len(keys_a) and keys_a[ia] <= t:
            la = mid_a[keys_a[ia]]
            ia += 1
        while ib < len(keys_b) and keys_b[ib] <= t:
            lb = mid_b[keys_b[ib]]
            ib += 1
        if la is None or lb is None or t - la > max_age_s or t - lb > max_age_s:
            out.append(float("nan"))
        else:
            out.append(la / lb)
    return out


def rolling_signal(ratio: list[float], grid_s: int, bucket_s: int = 60,
                   window: int = 30) -> tuple[list[float], list[float]]:
    """粗时钟（bucket_s 秒桶）滚动中位数带 b̂ 与 σ（1.4826·MAD），逐格前向填充。

    与 `scripts/chd_screen.py` 同口径：信号在粗时钟上估计，执行在细网格上。
    """
    nb = max(1, len(ratio) * grid_s // bucket_s)
    buckets: list[float] = []
    for b in range(nb):
        seg = ratio[b * (bucket_s // grid_s):(b + 1) * (bucket_s // grid_s)]
        seg = [x for x in seg if x == x]
        buckets.append(seg[-1] if seg else float("nan"))
    win = max(5, min(window, max(5, len(buckets) // 3)))
    bh_b: list[float] = []
    sig_b: list[float] = []
    for i in range(len(buckets)):
        seg = [x for x in buckets[max(0, i - win + 1):i + 1] if x == x]
        if len(seg) < max(3, win // 3):
            bh_b.append(float("nan"))
            sig_b.append(float("nan"))
            continue
        med = statistics.median(seg)
        bh_b.append(med)
        sig_b.append(1.4826 * statistics.median(abs(x - med) for x in seg))
    bh: list[float] = []
    sig: list[float] = []
    cur_bh = cur_sig = float("nan")
    for i in range(len(buckets)):
        if bh_b[i] == bh_b[i]:
            cur_bh = bh_b[i]
        if sig_b[i] == sig_b[i]:
            cur_sig = sig_b[i]
        bh.append(cur_bh)
        sig.append(cur_sig)
    out_bh = [float("nan")] * len(ratio)
    out_sig = [float("nan")] * len(ratio)
    for i in range(len(ratio)):
        b = min(len(buckets) - 1, i * grid_s // bucket_s)
        out_bh[i] = bh[b]
        out_sig[i] = sig[b]
    return out_bh, out_sig


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/collector")
    ap.add_argument("--symbols", default="SOXL", help="逗号分隔的标的（按采集器里的 symbol 匹配）")
    ap.add_argument("--venues", default=f"{HL_VENUE},{LT_VENUE}")
    ap.add_argument("--ttls", default="30,60,120,180")
    ap.add_argument("--notionals", default="100,200,500,1000")
    ap.add_argument("--probe-interval", type=float, default=30.0, help="探针间隔（秒）")
    ap.add_argument("--trail-s", type=float, default=60.0, help="用于「事前 λ」的回看窗口（秒）")
    ap.add_argument("--entry-z", type=float, default=2.5)
    ap.add_argument("--exit-z", type=float, default=0.3)
    ap.add_argument("--min-hold-s", type=int, default=30)
    ap.add_argument("--max-hold-s", type=int, default=900)
    ap.add_argument("--signal-window", type=int, default=30)
    ap.add_argument("--maker-venue", default=LT_VENUE, choices=[HL_VENUE, LT_VENUE],
                    help="被动腿（maker）在哪一侧")
    ap.add_argument("--max-rows", type=int, default=200000)
    ap.add_argument("--out", default="data/fill_calibration.json")
    ap.add_argument("--report", default="docs/FILL_CALIBRATION.md")
    ap.add_argument("--screening", default="data/chd_screen.json",
                    help="用于对照的既有事件级 EV 报告（捕获幅度来源）")
    args = ap.parse_args()

    syms = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    venues = [v.strip() for v in args.venues.split(",") if v.strip()]
    ttls = [int(x) for x in args.ttls.split(",") if x.strip()]
    notionals = [float(x) for x in args.notionals.split(",") if x.strip()]

    data: dict[str, dict[str, list[fc.Snap]]] = {}
    for v in venues:
        data[v] = fc.load_venue(args.root, v, syms, max_rows=args.max_rows)
        n = sum(len(x) for x in data[v].values())
        print(f"[load] {v}: {n} snapshots", file=sys.stderr)

    report: dict[str, Any] = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "root": args.root, "symbols": sorted(syms), "ttls": ttls, "notionals": notionals,
        "probe_interval_s": args.probe_interval, "trail_s": args.trail_s,
        "config": {"entry_z": args.entry_z, "exit_z": args.exit_z,
                   "min_hold_s": args.min_hold_s, "max_hold_s": args.max_hold_s,
                   "maker_venue": args.maker_venue},
    }

    # ---- 1. 探针：每个场所 × 每一边 × 每个名义
    probes: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    flat_trades: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
    for v in venues:
        for sym, snaps in data[v].items():
            tr: list[tuple] = []
            for s in snaps:
                tr.extend(s.trades)
            tr.sort(key=lambda x: x[0])
            flat_trades[v][sym] = tr
            gaps = sum(1 for s in snaps if s.gap)
            win_ms = sum((s.win[1] - s.win[0]) for s in snaps if s.win)
            elapsed_ms = (snaps[-1].t - snaps[0].t) * 1000.0 if len(snaps) > 1 else 0.0
            report.setdefault("data_quality", {})[f"{v}:{sym}"] = {
                "snapshots": len(snaps), "trades": len(tr),
                "trade_gap_snapshots": gaps,
                "trade_coverage": round(win_ms / elapsed_ms, 3) if elapsed_ms > 0 else None,
                "span_s": round(snaps[-1].t - snaps[0].t, 1) if snaps else 0.0,
                "median_sample_interval_s": (round(statistics.median(
                    [b.t - a.t for a, b in zip(snaps, snaps[1:])]), 2) if len(snaps) > 1 else None),
            }
            for side in ("buy", "sell"):
                for n_usd in notionals:
                    key = f"{side}:{int(n_usd)}"
                    probes[v][key] = build_probes(snaps, tr, side, ttls, n_usd,
                                                  args.probe_interval, args.trail_s)

    cal: dict[str, Any] = {}
    for v in venues:
        cal[v] = {}
        for key, pr in probes[v].items():
            side, n_usd = key.split(":")
            cal[v][key] = {
                "side": side, "notional": float(n_usd),
                "by_ttl": {str(ttl): summarize_probes(pr, ttl) for ttl in ttls},
                "by_ttl_bucket": {str(ttl): bias_by_bucket(pr, ttl) for ttl in ttls},
            }
    report["calibration"] = cal

    # ---- 2. 信号事件（策略口径）：捕获、成本、EV
    ev = replay_events(args, data, flat_trades, probes, ttls, notionals)
    report["events"] = {"summary": ev["summary"], "detail": ev["detail"]}
    report["ev_ladder"] = ev["ladder"]
    report["screening_reference"] = screening_inputs(args.screening)
    report["sizing"] = sizing_analysis(ev, report["screening_reference"], notionals, max(ttls))
    report["data_quality"] = report.get("data_quality", {})

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    print(f"wrote {args.out}", file=sys.stderr)

    write_report(args, report, ev)
    print(f"wrote {args.report}", file=sys.stderr)
    return 0


def replay_events(args, data, flat_trades, probes, ttls, notionals) -> dict:
    """信号驱动的策略事件：捕获 → 成本 → 用实测 p_fill 的净 EV。"""
    # 配对：HL ↔ Lighter（同标的）
    syms = sorted(set(data.get(HL_VENUE, {})) & set(data.get(LT_VENUE, {})))
    out_detail: list[dict] = []
    out_summary: dict[str, Any] = {}
    ladder: dict[str, Any] = {}

    if not syms:
        return {"summary": {}, "detail": [], "ladder": {}}

    for sym in syms:
        a_snaps, b_snaps = data[HL_VENUE][sym], data[LT_VENUE][sym]
        mid_a, mid_b = mid_series(a_snaps), mid_series(b_snaps)
        t0 = int(max(min(mid_a), min(mid_b)))
        t1 = int(min(max(mid_a), max(mid_b)))
        if t1 - t0 < 600:
            out_summary[sym] = {"note": "两腿重叠窗口不足 10 分钟"}
            continue
        grid_s = 2
        ratio = grid_ratio(mid_a, mid_b, t0, t1, grid_s=grid_s)
        bh, sig = rolling_signal(ratio, grid_s, bucket_s=60, window=args.signal_window)
        maker_venue = args.maker_venue
        events: list[dict] = []
        i = 0
        n = len(ratio)
        while i < n:
            r = ratio[i]
            if r != r or bh[i] != bh[i] or sig[i] != sig[i] or sig[i] <= 0:
                i += 1
                continue
            dev = (r - bh[i]) * 1e4
            sig_bps = sig[i] * 1e4
            if abs(dev) < args.entry_z * sig_bps:
                i += 1
                continue
            limit = min(n, i + int(args.max_hold_s / grid_s))
            min_exit = min(limit, i + max(1, int(args.min_hold_s / grid_s)))
            exit_i = None
            for j in range(min_exit, limit):
                if ratio[j] != ratio[j] or bh[j] != bh[j] or sig[j] != sig[j]:
                    continue
                if abs((ratio[j] - bh[j]) * 1e4) <= args.exit_z * sig[j] * 1e4:
                    exit_i = j
                    break
            if exit_i is None:
                exit_i = limit - 1
            if exit_i <= i or ratio[exit_i] != ratio[exit_i]:
                i = max(i + 1, exit_i)
                continue
            entry_basis = (ratio[i] - 1.0) * 1e4
            exit_basis = (ratio[exit_i] - 1.0) * 1e4
            capture = (entry_basis - exit_basis) if dev > 0 else (exit_basis - entry_basis)
            hold_s = (exit_i - i) * grid_s
            # 腿角色：basis（HL/LT）偏高 → HL 贵 → 卖 HL / 买 Lighter
            maker_is_a = (maker_venue == HL_VENUE)
            s_a = -1.0 if dev > 0 else 1.0
            maker_side = "buy" if (s_a > 0) == maker_is_a else "sell"
            taker_side = "sell" if maker_side == "buy" else "buy"
            ts = t0 + i * grid_s
            # 取事件时刻两边盘口
            snap_a = nearest(a_snaps, ts)
            snap_b = nearest(b_snaps, ts)
            if snap_a is None or snap_b is None:
                i = max(i + 1, exit_i)
                continue
            ev_rec: dict[str, Any] = {
                "ts": ts, "session": classify(ts * 1000).session,
                "dev_bps": round(dev, 2), "capture_bps": round(capture, 2),
                "hold_s": hold_s, "maker_venue": maker_venue, "maker_side": maker_side,
            }
            for n_usd in notionals:
                spec_a, slip_a = leg_spec(HL_VENUE, snap_a, n_usd)
                spec_b, slip_b = leg_spec(LT_VENUE, snap_b, n_usd)
                if spec_a is None or spec_b is None:
                    continue
                maker_spec, taker_spec = (spec_a, spec_b) if maker_is_a else (spec_b, spec_a)
                maker_slip, taker_slip = (slip_a, slip_b) if maker_is_a else (slip_b, slip_a)
                cost = float(b_star(maker_spec, taker_spec,
                                    qty_usd=Decimal(str(n_usd)),
                                    hold_hours=Decimal(str(hold_s / 3600.0)),
                                    leader_slip_bps=Decimal(str(maker_slip)),
                                    follower_slip_bps=Decimal(str(taker_slip))))
                # 实测 p_fill：探针总体（同场所/同边/同名义/同 TTL）
                ttl_key = str(max(ttls))
                pr = probes.get(maker_venue, {}).get(f"{maker_side}:{int(n_usd)}", [])
                summ = summarize_probes(pr, max(ttls))
                p_emp = summ.get("p_emp_full", 0.0)
                p_model = summ.get("p_model_full", 0.0)
                ev_rec[f"n{int(n_usd)}"] = {
                    "cost_bps": round(cost, 2),
                    "p_emp": p_emp, "p_model": p_model,
                    "net_emp_bps": round((capture - cost) * p_emp, 3),
                    "net_model_bps": round((capture - cost) * p_model, 3),
                    "net_p1_bps": round(capture - cost, 3),
                }
            events.append(ev_rec)
            i = max(i + 1, exit_i)
        out_detail = events
        # 汇总
        for n_usd in notionals:
            nets = [e[f"n{int(n_usd)}"]["net_emp_bps"] for e in events if f"n{int(n_usd)}" in e]
            costs = [e[f"n{int(n_usd)}"]["cost_bps"] for e in events if f"n{int(n_usd)}" in e]
            caps = [e["capture_bps"] for e in events]
            if not nets:
                continue
            ladder[f"{sym}:{int(n_usd)}"] = {
                "events": len(nets),
                "capture_mean_bps": round(statistics.mean(caps), 2) if caps else None,
                "capture_median_bps": round(statistics.median(caps), 2) if caps else None,
                "cost_mean_bps": round(statistics.mean(costs), 2) if costs else None,
                "net_emp_mean_bps": round(statistics.mean(nets), 3),
                "net_model_mean_bps": round(statistics.mean(
                    [e[f"n{int(n_usd)}"]["net_model_bps"] for e in events
                     if f"n{int(n_usd)}" in e]), 3),
                "net_p1_mean_bps": round(statistics.mean(
                    [e[f"n{int(n_usd)}"]["net_p1_bps"] for e in events
                     if f"n{int(n_usd)}" in e]), 3),
            }
        out_summary[sym] = {
            "events": len(events),
            "maker_venue": maker_venue,
            "window_s": t1 - t0,
            "capture_mean_bps": round(statistics.mean([e["capture_bps"] for e in events]), 2) if events else None,
            "sessions": {s: sum(1 for e in events if e["session"] == s)
                         for s in ("rth", "pre", "post", "closed")},
        }
        # sizing：每个名义的成本 / 实测 p_fill / 在既有捕获幅度下的净 EV
        for n_usd in notionals:
            key = f"{sym}:{int(n_usd)}"
            evs = [e for e in events if f"n{int(n_usd)}" in e]
            if not evs:
                continue
            costs = sorted(e[f"n{int(n_usd)}"]["cost_bps"] for e in evs)
            p_emp = statistics.mean(e[f"n{int(n_usd)}"]["p_emp"] for e in evs)
            p_model = statistics.mean(e[f"n{int(n_usd)}"]["p_model"] for e in evs)
            caps = sorted(e["capture_bps"] for e in evs)
            ladder[key].update({
                "cost_median_bps": costs[len(costs) // 2],
                "p_emp": round(p_emp, 4), "p_model": round(p_model, 4),
                "capture_median_bps": caps[len(caps) // 2],
            })
    return {"summary": out_summary, "detail": out_detail, "ladder": ladder}


def sizing_analysis(ev: dict, screen: dict, notionals: list[float], ttl: int) -> dict:
    """实盘 sizing：用**实测 p_fill** + 实测成本，重算各名义档的净 EV。

    净 EV = (捕获 − 成本) × p_fill。
    捕获有两个来源：
      * `own`：本次 L2 回放里同一策略口径下的实测捕获（样本小）；
      * `screening_rth`：既有事件级回测（`data/chd_screen.json`）的 RTH 捕获幅度。
    """
    rows: list[dict] = []
    lad = ev["ladder"]
    syms = sorted({k.split(":")[0] for k in lad})
    scr_caps = (screen or {}).get("gross_capture_bps") or {}
    cap_scr_rth = scr_caps.get("rth")
    cap_scr_all = statistics.mean(list(scr_caps.values())) if scr_caps else None
    for sym in syms:
        for n_usd in notionals:
            v = lad.get(f"{sym}:{int(n_usd)}")
            if not v:
                continue
            row = {"symbol": sym, "notional": n_usd, "ttl_s": ttl,
                   "cost_median_bps": v.get("cost_median_bps"),
                   "p_emp": v.get("p_emp"), "p_model": v.get("p_model"),
                   "capture_own_median_bps": v.get("capture_median_bps"),
                   "capture_screening_rth_bps": cap_scr_rth,
                   "capture_screening_mean_bps": cap_scr_all}
            for label, cap in (("own", v.get("capture_median_bps")),
                               ("scr_rth", cap_scr_rth), ("scr_mean", cap_scr_all)):
                if cap is None:
                    continue
                cost = v.get("cost_median_bps") or 0.0
                p = v.get("p_emp") or 0.0
                row[f"net_{label}_emp"] = round((cap - cost) * p, 3)
                row[f"net_{label}_p1"] = round(cap - cost, 3)
            rows.append(row)
    def max_positive(label: str) -> float | None:
        best = None
        for r in rows:
            val = r.get(f"net_{label}_emp")
            if val is None or val <= 0:
                continue
            if best is None or r["notional"] > best:
                best = r["notional"]
        return best
    return {
        "rows": rows,
        "max_positive_notional_own_capture": max_positive("own"),
        "max_positive_notional_screening_rth": max_positive("scr_rth"),
        "max_positive_notional_screening_mean": max_positive("scr_mean"),
        "note": ("净 EV > 0 ⟺ 捕获 > 成本（p_fill>0 时）；p_fill 只决定**幅度**与"
                 "事件频率，不改变符号。"),
    }


def screening_inputs(path: str) -> dict:
    """读既有事件级 EV 报告（`data/chd_screen.json`）：捕获幅度与 b* 的来源。"""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    sb = (d.get("stage_b") or [{}])[0]
    rep = sb.get("replay") or {}
    by_sess = rep.get("by_session") or {}
    return {
        "pair": sb.get("key"),
        "b_star_bps": sb.get("b_star_bps"),
        "n_events": rep.get("n_events"),
        "maker_leg": rep.get("maker_leg"),
        "mean_p_fill": {k: v.get("mean_p_fill") for k, v in by_sess.items()},
        "gross_capture_bps": {k: v.get("gross_capture_bps_per_event") for k, v in by_sess.items()},
        "window": d.get("window"),
    }


def nearest(snaps: list[fc.Snap], ts: int) -> fc.Snap | None:
    best = None
    bestd = None
    for s in snaps:
        d = abs(int(s.t) - ts)
        if bestd is None or d < bestd:
            best, bestd = s, d
        if s.t > ts + 30:
            break
    if best is None or (bestd is not None and bestd > 30):
        return None
    return best


def write_report(args, report: dict, ev: dict) -> None:
    """人读报告（Markdown）。"""
    L: list[str] = []
    A = L.append
    A("# 成交率标定：排队模型 vs L2 回放实测")
    A("")
    A(f"生成：{report['generated_at']}　数据：`{report['root']}`　"
      f"标的：{', '.join(report['symbols'])}")
    A("")
    A("> 本文的 `p_fill` **实测**来自 L2 + 逐笔成交回放：合成挂单贴在买一/卖一，"
      "**队尾**排队；成交判定 = 窗口内穿过我们价位的累计成交量 ≥ 队头 + 我们的量。")
    A("> `p_fill` **模型** = `market_micro.fill_probability(queue_ahead, λ, dt, our_size, avg_size)`，"
      "λ / 均单量 / 队列取**同一窗口的实测值**（与 `scripts/chd_screen.py` 口径一致）。")
    A("")

    # 数据质量
    A("## 1. 数据质量")
    A("")
    A("| 场所:标的 | 快照数 | 跨度 | 采样间隔中位数 | 逐笔成交 | 覆盖缺口快照 |")
    A("|---|---:|---:|---:|---:|---:|")
    for k, q in report.get("data_quality", {}).items():
        A(f"| {k} | {q['snapshots']} | {q['span_s']:.0f}s | "
          f"{q['median_sample_interval_s']}s | {q['trades']} | {q['trade_gap_snapshots']} |")
    A("")

    # 主表
    A("## 2. 实测 vs 模型（按场所 / 边 / TTL）")
    A("")
    for venue, per_side in report["calibration"].items():
        if not per_side:
            A(f"### {venue}：本次采集没有数据。")
            A("")
            continue
        for key, blk in per_side.items():
            side, n_usd = key.split(":")
            A(f"### {venue} · {side} 侧 · 名义 ${n_usd}")
            A("")
            A("| TTL | 探针数 | 实测 p_fill(全成交) | 实测 p_fill(被碰到) | 模型 p_fill(同窗 λ) | "
              "偏差 model−emp | 模型 p_fill(事前 λ) | 偏差 | 复合 Poisson p_fill | 偏差 | 队头均值 | "
              "模型强度 hits | 队列缩放至复现实测 | 队列采样离散度 |")
            A("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
            for ttl, s in blk["by_ttl"].items():
                if not s.get("n"):
                    continue
                A(f"| {ttl}s | {s['n']} | {s['p_emp_full']:.3f} | {s['p_emp_any']:.3f} | "
                  f"{s['p_model_full']:.3f} | **{s['bias_model_minus_emp_full']:+.3f}** | "
                  f"{s['p_model_trail_lam']:.3f} | {s['bias_model_trail_minus_emp_full']:+.3f} | "
                  f"{s['p_compound_full']:.3f} | {s['bias_compound_minus_emp_full']:+.3f} | "
                  f"{s['queue_median_mean']:.1f} | {s['model_mean_hits']} | "
                  f"{s['queue_scale_to_match_emp']} | "
                  f"{s['queue_sample_spread_rel_median']} |")
            A("")

    # 分档
    A("## 3. 偏差随贴价队列分档（模型形式固定，只看队列）")
    A("")
    for venue, per_side in report["calibration"].items():
        if not per_side:
            continue
        key = sorted(per_side)[0]
        blk = per_side[key]
        side, n_usd = key.split(":")
        ttl = max(int(t) for t in blk["by_ttl"])
        A(f"### {venue} · {side} · ${n_usd} · TTL {ttl}s")
        A("")
        A("| 队列档 | 探针数 | 实测 p_fill | 模型 p_fill | 偏差 | 复合 Poisson p_fill | 偏差 |")
        A("|---|---:|---:|---:|---:|---:|---:|")
        for b, s in blk["by_ttl_bucket"][str(ttl)].items():
            if not s.get("n"):
                continue
            A(f"| {b} | {s['n']} | {s['p_emp_full']:.3f} | {s['p_model_full']:.3f} | "
              f"**{s['bias_model_minus_emp_full']:+.3f}** | {s['p_compound_full']:.3f} | "
              f"{s['bias_compound_minus_emp_full']:+.3f} |")
        A("")

    # 事件与 EV
    A("## 4. 策略事件（捕获 / 成本 / 净 EV）")
    A("")
    for sym, s in report.get("events", {}).get("summary", {}).items():
        if "note" in s:
            A(f"- {sym}: {s['note']}")
            continue
        A(f"### {sym}（maker = {s['maker_venue']}）")
        A("")
        A(f"- 窗口 {s['window_s']}s，事件 {s['events']} 个，"
          f"平均捕获 {s['capture_mean_bps']} bps，会话分布 {s['sessions']}")
        A("")
    A("")
    A("| 名义 | 事件数 | 平均捕获 bps | 平均成本 bps | 净 EV(实测 p_fill) | 净 EV(模型 p_fill) | 净 EV(p_fill=1) |")
    A("|---:|---:|---:|---:|---:|---:|---:|")
    for k, v in report.get("ev_ladder", {}).items():
        A(f"| ${k.split(':')[1]} | {v['events']} | {v['capture_mean_bps']} | {v['cost_mean_bps']} | "
          f"**{v['net_emp_mean_bps']}** | {v['net_model_mean_bps']} | {v['net_p1_mean_bps']} |")
    A("")
    # 实盘规模
    sizing = report.get("sizing") or {}
    scr = report.get("screening_reference") or {}
    A("## 5. 实盘规模：用实测 p_fill 重算净 EV")
    A("")
    if scr:
        A(f"捕获幅度的参照来源：`{args.screening}`（{scr.get('pair')}，"
          f"{scr.get('n_events')} 个事件，maker 腿 {scr.get('maker_leg')}，"
          f"b\* = {scr.get('b_star_bps')} bps）。")
        A("")
        A("| 会话 | 毛捕获 bps | 既有 mean p_fill |")
        A("|---|---:|---:|")
        for sess, cap in (scr.get("gross_capture_bps") or {}).items():
            A(f"| {sess} | {cap} | {(scr.get('mean_p_fill') or {}).get(sess)} |")
        A("")
    A("| 标的 | 名义 | 成本 bps | 实测 p_fill | 模型 p_fill | 捕获(本次回放) | 净EV(实测 p_fill) | "
      "捕获(screening RTH) | 净EV(实测 p_fill) |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in sizing.get("rows", []):
        A(f"| {r['symbol']} | ${int(r['notional'])} | {r.get('cost_median_bps')} | "
          f"{r.get('p_emp')} | {r.get('p_model')} | {r.get('capture_own_median_bps')} | "
          f"**{r.get('net_own_emp')}** | {r.get('capture_screening_rth_bps')} | "
          f"**{r.get('net_scr_rth_emp')}** |")
    A("")
    A(f"- 本次回放捕获下净 EV 仍为正的最大名义：**${sizing.get('max_positive_notional_own_capture')}**")
    A(f"- screening RTH 捕获幅度下净 EV 仍为正的最大名义：**${sizing.get('max_positive_notional_screening_rth')}**")
    A(f"- screening 各会话平均捕获下净 EV 仍为正的最大名义：**${sizing.get('max_positive_notional_screening_mean')}**")
    A(f"- {sizing.get('note')}")
    A("")
    out = os.path.dirname(args.report)
    if out:
        os.makedirs(out, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
