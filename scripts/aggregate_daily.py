#!/usr/bin/env python3
"""把逐日回放结果聚合成「逐日稳定性」报告。

背景
----
「跑两周」的验收标准是**逐日净 EV 都要为正**，不是聚合平均好看。
`scripts/chd_screen.py --end "<date> 23" --hours 24` 每次跑出的是：
  * 该日期的**完整 24 小时**（00:00–23:00）
  * 外加**前一天 23:00 那一小时**的碎片（因为窗口是 `[end-23h, end]`）

所以聚合时必须**按「跑的是哪一天」取值**，而不是把所有 by_day 条目直接拼起来 ——
否则同一天会出现两行（一份完整、一份 1 小时碎片），数字对不上。

用法
----
    python scripts/aggregate_daily.py --glob '/tmp/daily_2026-11-*.json' \\
        --out data/daily_stability.json --report docs/DAILY_STABILITY.md
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from pathlib import Path
from typing import Any

SESSION_ORDER = ("rth", "pre", "post", "closed")


def anchor_date(path: str) -> str:
    """从文件名推出这次跑的是哪一天：`daily_2026-11-03.json` → `2026-11-03`。"""
    base = os.path.basename(path)
    stem = base[:-5] if base.endswith(".json") else base
    parts = stem.split("_")
    for p in reversed(parts):
        if len(p) == 10 and p.count("-") == 2:
            return p
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glob", default="/tmp/daily_*.json")
    ap.add_argument("--out", default="data/daily_stability.json")
    ap.add_argument("--report", default="docs/DAILY_STABILITY.md")
    ap.add_argument("--pair", default="hyperliquid_futures:XYZ:SOXL~lighter:SOXL")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(args.glob))
    if not files:
        print(f"no files matched {args.glob}", file=__import__("sys").stderr)
        return 2

    daily: list[dict[str, Any]] = []
    for f in files:
        d = anchor_date(f)
        if not d:
            continue
        try:
            payload = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for r in payload.get("stage_b") or []:
            if r.get("key") != args.pair:
                continue
            rep = r.get("replay") or {}
            by_day = rep.get("by_day") or {}
            # 取**这一天自己那次跑**的条目（by_day 里同一天可能有完整 + 1h碎片两份，
            # 取事件数多的那份 = 完整 24 小时）
            cands = [(k, v) for k, v in by_day.items()
                     if not k.startswith("_") and isinstance(v, dict) and "events" in v]
            for day, v in cands:
                if day != d:
                    continue
                sess = rep.get("by_session") or {}
                daily.append({
                    "date": day,
                    "events": v["events"],
                    "net_bps_per_event": v["net_bps_per_event"],
                    "gross_capture_bps_per_event": v["gross_capture_bps_per_event"],
                    "win_rate": v["win_rate"],
                    "mean_p_fill": v["mean_p_fill"],
                    "net_bps_total": v.get("net_bps_total"),
                    "sessions": {k: (sess.get(k) or {}).get("net_bps_per_event")
                                 for k in SESSION_ORDER if k in sess},
                    "b_star_bps": r.get("b_star_bps"),
                    "coverage_ratio": r.get("coverage_ratio"),
                    "depth_min_usd": r.get("depth_min_usd"),
                })
                break  # 一天只取一条（事件数最多的）

    daily.sort(key=lambda x: x["date"])
    # 去重（同一天可能多文件）
    seen = {}
    for row in daily:
        cur = seen.get(row["date"])
        if cur is None or row["events"] > cur["events"]:
            seen[row["date"]] = row
    daily = [seen[k] for k in sorted(seen)]

    if not daily:
        print("no daily rows matched the pair", file=__import__("sys").stderr)
        return 2

    pos = [r for r in daily if r["net_bps_per_event"] > 0]
    payload = {
        "pair": args.pair,
        "days": len(daily),
        "positive_days": len(pos),
        "positive_day_ratio": round(len(pos) / len(daily), 3),
        "total_net_bps": round(sum((r["net_bps_total"] or 0) for r in daily), 2),
        "mean_net_bps_per_event": round(statistics.fmean([r["net_bps_per_event"] for r in daily]), 3),
        "worst_day": min(daily, key=lambda r: r["net_bps_per_event"])["date"],
        "worst_day_net": min(r["net_bps_per_event"] for r in daily),
        "daily": daily,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    L = ["# 逐日稳定性报告（跑两周的验收口径）\n",
         f"标的对：`{args.pair}`\n",
         f"天数：{payload['days']}　正 EV 天数：**{payload['positive_days']}**"
         f"（{payload['positive_day_ratio']:.0%}）\n",
         f"合计净：{payload['total_net_bps']:+.1f} bps　"
         f"日均净/事件：{payload['mean_net_bps_per_event']:+.3f} bps　"
         f"最差日：{payload['worst_day']}（{payload['worst_day_net']:+.2f}）\n",
         "\n| 日期 | 事件数 | 净 EV/事件 bps | 毛捕获 bps | 胜率 | mean p_fill | RTH | pre | post | closed |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in daily:
        g = lambda k: (f"{r['sessions'][k]:+.2f}" if r["sessions"].get(k) is not None else "-")
        L.append(f"| {r['date']} | {r['events']} | {r['net_bps_per_event']:+.2f} | "
                 f"{r['gross_capture_bps_per_event']:.2f} | {r['win_rate']:.0%} | {r['mean_p_fill']:.3f} | "
                 f"{g('rth')} | {g('pre')} | {g('post')} | {g('closed')} |")
    L.append("\n## 口径\n")
    L.append("- 每天 = `--end \"<date> 23\" --hours 24` 的 24 小时盘口回放（`--end` 是逐日回放的必要条件，"
             "否则窗口永远锚定在「现在」）。")
    L.append("- 净 EV 已扣 `b*`（费率 + 半价差 + 滑点 + carry + 腿风险 + 对账），"
             "`p_fill` 用的是排队模型（实测标定见 `docs/FILL_CALIBRATION.md`，模型偏保守）。")
    L.append("- 每格会话数字是该会话内的净 EV/事件；`closed` 含周末与非美股时段。")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {args.out} and {args.report}")
    print(f"正 EV 天数 {payload['positive_days']}/{payload['days']}"
          f"　最差日 {payload['worst_day']} {payload['worst_day_net']:+.2f} bps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
