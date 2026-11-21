#!/usr/bin/env python3
"""把 Stage A / Stage B 的扫描结果汇总成「全宇宙排序表 + 第二对建议」。

输入：
  * data/universe_stage_a.json —— `scripts/chd_screen.py --no-orderbook` 的输出
  * data/universe_stage_b.json —— `scripts/chd_screen.py`（含盘口回放）的输出
输出：
  * docs/UNIVERSE_RANKING.md

只做**汇总与排序**，不改动任何判定逻辑：闸门结论直接取自 Stage A/B 的原始字段。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from nt_pairs.chd_client import ChdClient  # noqa: E402


def _f(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def leg_trade_rates(cache_dir: str, legs: list[tuple[str, str]],
                    hours: list[tuple[str, int]]) -> dict[tuple[str, str], dict[str, float]]:
    """每条腿的成交笔数/分钟 + 覆盖小时数（从缓存 parquet 直接数行）。"""
    out: dict[tuple[str, str], dict[str, float]] = {}
    for venue, symbol in legs:
        n_trades = 0
        covered = 0
        for day, hour in hours:
            p = (Path(cache_dir) / venue / day / f"{hour:02d}"
                 / f"{symbol.replace(':', '%3A')}_trades.parquet")
            if not p.exists() or p.stat().st_size == 0:
                continue
            try:
                df = ChdClient.read_parquet(p)
            except Exception:  # noqa: BLE001
                continue
            if df is None or len(df) == 0:
                continue
            n_trades += len(df)
            covered += 1
        mins = max(1, len(hours) * 60)
        out[(venue, symbol)] = {
            "trades": n_trades,
            "covered_hours": covered,
            "trades_per_min": n_trades / mins,
        }
    return out


def collect(stage_a: dict, stage_b: dict, bases: list[str]) -> dict[str, Any]:
    a_rows = {r["key"]: r for r in stage_a.get("stage_a", [])}
    b_rows = stage_b.get("stage_b", [])

    # ---- 每个 base 的 Stage A 汇总 ----
    per_base: dict[str, dict[str, Any]] = {}
    for r in stage_a.get("stage_a", []):
        b = r.get("base") or r["key"].split(":")[0]
        d = per_base.setdefault(b, {
            "pairs": 0, "gate1": 0, "gate12": 0, "best_p95": 0.0,
            "best_p95_key": "", "notes": set(), "pairs_list": [],
        })
        d["pairs"] += 1
        if r.get("gate1_unit"):
            d["gate1"] += 1
        if r.get("gate1_unit") and r.get("gate2_fresh"):
            d["gate12"] += 1
            p95 = _f(r.get("basis_p95_dev"), 0.0)
            d["pairs_list"].append((p95, r["key"]))
            if p95 > d["best_p95"]:
                d["best_p95"] = p95
                d["best_p95_key"] = r["key"]
        for n in r.get("notes") or []:
            d["notes"].add(n.split("：")[0].split(":")[0])

    # ---- Stage B 明细 ----
    replay: dict[str, dict[str, Any]] = {}
    for r in b_rows:
        rep = r.get("replay") or {}
        by_day = {k: v for k, v in (rep.get("by_day") or {}).items() if not k.startswith("_")}
        summ = (rep.get("by_day") or {}).get("_summary") or {}
        sess = rep.get("by_session") or {}
        days = sorted(by_day)
        pos_days = sum(1 for v in by_day.values() if _f(v.get("net_bps_total")) > 0)
        net_total = sum(_f(v.get("net_bps_total")) for v in by_day.values())
        ev = rep.get("n_events", 0)
        replay[r["key"]] = {
            "key": r["key"], "base": r.get("base"),
            "b_star_bps": _f(r.get("b_star_bps")),
            "coverage_ratio": _f(r.get("coverage_ratio")),
            "depth_min_usd": _f(r.get("depth_min_usd")),
            "n_events": ev,
            "maker": rep.get("maker_leg"), "taker": rep.get("taker_leg"),
            "pos_days": pos_days, "n_days": len(days), "days": days,
            "pos_day_ratio": (pos_days / len(days)) if days else 0.0,
            "net_total_bps": net_total,
            "net_per_event": (net_total / ev) if ev else 0.0,
            "opportunities_per_day": (ev / len(days)) if days else 0.0,
            "win_rate": max([_f(v.get("win_rate")) for v in sess.values()], default=0.0),
            "mean_p_fill": (sum(_f(v.get("mean_p_fill")) for v in sess.values()) / len(sess))
            if sess else 0.0,
            "stale_frac": _f(rep.get("stale_frac")),
            "by_day": by_day,
            "by_session": sess,
            "notes": rep.get("notes") or [],
            "error": r.get("error"),
        }

    # ---- 排序：逐日净 EV 为正的天数占比 → 净 EV/事件 → 日均机会 ----
    ranked = sorted(
        replay.values(),
        key=lambda r: (-r["pos_day_ratio"], -r["net_per_event"], -r["opportunities_per_day"]),
    )
    return {"per_base": per_base, "a_rows": a_rows, "replay": replay, "ranked": ranked}


def write_md(out: dict, stage_a: dict, stage_b: dict, bases: list[str],
             rates: dict, path: str) -> None:
    L: list[str] = []
    per_base = out["per_base"]
    ranked = out["ranked"]
    L.append("# 全宇宙候选池排序与「第二对」建议\n")
    L.append(f"生成时间：{stage_b.get('generated_at')}　"
             f"Stage A 窗口：{stage_a['window']['start']} → {stage_a['window']['end']}"
             f"（{stage_a['window']['hours']} 小时）　"
             f"Stage B 回放窗口：{stage_b['window']['replay_hours']} 小时　"
             f"单笔名义 ${stage_b['config']['qty_usd']:.0f}/腿\n")
    L.append(f"候选对总数：**{stage_a['n_candidate_pairs']}**（56 个 base × 各场所同名腿的组合）　"
             f"Stage B 回放对数：**{len(ranked)}**\n")

    # ---------------- 1. Stage B 排序表 ----------------
    L.append("## 1. Stage B 回放排序（主表）\n")
    L.append("排序依据：**逐交易日净 EV 为正的天数占比** → 净 EV/事件 → 日均机会次数\n")
    L.append("| # | 对 | base | 腿（maker/被动 · taker/主动） | b* bps | p95覆盖× | 最小深度 US$ | 事件数 | 日均机会 | 胜率 | 正EV天数 | 净EV/事件 bps |")
    L.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|:---:|---:|")
    for i, r in enumerate(ranked, 1):
        L.append(f"| {i} | `{r['key']}` | {r['base']} | {r['maker']} · {r['taker']} | "
                 f"{r['b_star_bps']:.1f} | {r['coverage_ratio']:.2f} | {r['depth_min_usd']:,.0f} | "
                 f"{r['n_events']} | {r['opportunities_per_day']:.2f} | {r['win_rate']:.0%} | "
                 f"{r['pos_days']}/{r['n_days']} ({r['pos_day_ratio']:.0%}) | {r['net_per_event']:+.2f} |")
    L.append("")

    # ---------------- 2. 逐日分布 ----------------
    L.append("## 2. 前 5 对的逐交易日净 EV\n")
    for r in ranked[:5]:
        L.append(f"**{r['key']}** —— 正 EV {r['pos_days']}/{r['n_days']} 天"
                 f"（{r['pos_day_ratio']:.0%}），净 EV/事件 {r['net_per_event']:+.2f} bps，"
                 f"日均机会 {r['opportunities_per_day']:.2f} 次\n")
        L.append("| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for day, v in r["by_day"].items():
            L.append(f"| {day} | {v['events']} | {_f(v.get('win_rate')):.0%} | "
                     f"{_f(v.get('net_bps_per_event')):+.2f} | {_f(v.get('gross_capture_bps_per_event')):.2f} | "
                     f"{_f(v.get('mean_p_fill')):.3f} |")
        L.append("")

    # ---------------- 3. 56 base 完整表 ----------------
    L.append("## 3. 全部 56 个 base 的 Stage A 结果\n")
    L.append("`闸门①` = 单位一致（|ratio−1|≤5% 且 cv≤5%）；"
             "`闸门②` = 重复收盘率 ≤10%（非陈旧打印）。\n")
    L.append("| base | 候选对 | 过① | 过①② | 最佳对的 p95 偏离 bps | 最佳对 | 状态 |")
    L.append("|---|---:|---:|---:|---:|---|---|")
    for base in bases:
        d = per_base.get(base)
        if not d:
            L.append(f"| {base} | 0 | 0 | 0 | – | – | 无候选对 |")
            continue
        if d["gate12"]:
            state = "过闸门①② → 进入 Stage B 候选"
        elif d["gate1"]:
            state = "闸门①过、②不过（陈旧打印）"
        else:
            state = "闸门①不过（单位不一致/样本不足）"
        L.append(f"| {base} | {d['pairs']} | {d['gate1']} | {d['gate12']} | "
                 f"{d['best_p95']:.1f} | `{d['best_p95_key']}` | {state} |")
    L.append("")

    # ---------------- 4. 每条腿的成交笔数/分钟 ----------------
    L.append("## 4. 每条腿的成交笔数/分钟（Stage A 窗口，摊到全窗口）\n")
    L.append("| 腿 | 成交笔数 | 覆盖小时 | 笔/分钟 |")
    L.append("|---|---:|---:|---:|")
    for (venue, symbol), v in sorted(rates.items(), key=lambda kv: -kv[1]["trades_per_min"]):
        L.append(f"| {venue}:{symbol} | {v['trades']} | {v['covered_hours']} | "
                 f"{v['trades_per_min']:.1f} |")
    L.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="汇总全宇宙 Stage A/B 结果 → 排序表")
    ap.add_argument("--stage-a", default="data/universe_stage_a.json")
    ap.add_argument("--stage-b", default="data/universe_stage_b.json")
    ap.add_argument("--bases", default="data/universe_bases.txt")
    ap.add_argument("--cache-dir", default="/home/da/桌面/delta/data/chd_cache")
    ap.add_argument("--out", default="docs/UNIVERSE_RANKING.md")
    args = ap.parse_args(argv)

    stage_a = json.loads(Path(args.stage_a).read_text(encoding="utf-8"))
    stage_b = json.loads(Path(args.stage_b).read_text(encoding="utf-8"))
    bases = [b.strip() for b in Path(args.bases).read_text(encoding="utf-8").split(",") if b.strip()]

    out = collect(stage_a, stage_b, bases)

    # 腿清单（从 Stage A 的对里取）
    legs: list[tuple[str, str]] = []
    seen = set()
    for r in stage_a.get("stage_a", []):
        for side in ("a", "b"):
            leg = r.get(side)
            if not leg:
                continue
            venue, symbol = leg.split(":", 1)
            key = (venue, symbol)
            if key not in seen:
                seen.add(key)
                legs.append(key)

    hours: list[tuple[str, int]] = []
    from datetime import datetime, timedelta
    start = datetime.strptime(stage_a["window"]["start"], "%Y-%m-%d %H:%M")
    end = datetime.strptime(stage_a["window"]["end"], "%Y-%m-%d %H:%M")
    cur = start
    while cur <= end:
        hours.append((cur.strftime("%Y-%m-%d"), cur.hour))
        cur += timedelta(hours=1)

    print(f"[1/2] 统计 {len(legs)} 条腿的成交笔数/分钟 …")
    rates = leg_trade_rates(args.cache_dir, legs, hours)
    print(f"[2/2] 写 {args.out}")
    write_md(out, stage_a, stage_b, bases, rates, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
