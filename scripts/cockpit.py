#!/usr/bin/env python
"""运行期座舱：把回放 / 采集 / 标定 / 回测数据汇成一块可盯的自包含 HTML 面板。

输入（**全部可缺省**，缺了就跳过对应板块，不编造数据）：

  --chd               data/chd_screen.json          Stage B 回放（逐日 EV、p_fill、深度、b*、k）
  --collector         data/collector                L2 采集器输出（队列、陈旧率、基差序列）
  --fill-calibration  data/fill_calibration.json    成交率标定（实测 p_fill、markout）
  --backtest          data/cockpit/backtest_stats.json  回测统计（净值 / 回撤，可选）
  --live-fills        data/cockpit/live_fills.jsonl 实盘已实现盈亏事件（可选）

输出：

  --out               reports/cockpit/cockpit.html  自包含 HTML（plotly.js 内联，可离线打开）
  --metrics-out       data/cockpit/metrics.json    机器可读：指标 + 停机判据（CI / cron 用）

退出码：

  0  正常
  3  ``--strict`` 且停机判据出现 **红色**（供 CI / cron 直接使用）

用法::

    python scripts/cockpit.py
    python scripts/cockpit.py --metrics-only --metrics-out ci-out/cockpit_metrics.json --strict
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from nt_pairs.cockpit import (  # noqa: E402
    RED,
    STOP_RULES,
    build_metrics,
    evaluate_stop_conditions,
    load_backtest_stats,
    load_chd_screen,
    load_collector,
    load_fill_calibration,
    load_live_fills,
    metrics_to_dict,
    overall_status,
    status_of,
)

EXIT_STOP_RED = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chd", default="data/chd_screen.json", help="Stage B 回放 JSON")
    parser.add_argument("--collector", default="data/collector", help="L2 采集器根目录")
    parser.add_argument("--fill-calibration", default="data/fill_calibration.json",
                        help="成交率标定 JSON")
    parser.add_argument("--backtest", default="data/cockpit/backtest_stats.json",
                        help="回测统计 JSON（可选）")
    parser.add_argument("--live-fills", default="data/cockpit/live_fills.jsonl",
                        help="实盘已实现盈亏 JSONL（可选）")
    parser.add_argument("--out", default="reports/cockpit/cockpit.html", help="HTML 输出路径")
    parser.add_argument("--metrics-out", default=None, help="机器可读指标 JSON 输出路径")
    parser.add_argument("--metrics-only", action="store_true",
                        help="只算指标与停机判据，不渲染 HTML（CI 用，免 plotly 依赖）")
    parser.add_argument("--strict", action="store_true",
                        help="出现红色停机条件时退出码 3")
    parser.add_argument("--title", default="nt-pairs · 运行期座舱")
    parser.add_argument("--subtitle", default="两周运行监控：回放 / 采集 / 标定 / 实盘合并视图")
    parser.add_argument("--pair-key", default=None, help="选 Stage B 里的某一对（默认第一对）")
    parser.add_argument("--exit-z", type=float, default=0.3)
    parser.add_argument("--entry-z", type=float, default=1.5)
    args = parser.parse_args(argv)

    chd = load_chd_screen(args.chd, pair_key=args.pair_key)
    collector = load_collector(args.collector) if args.collector else ()
    calibrations = load_fill_calibration(args.fill_calibration)
    backtest = load_backtest_stats(args.backtest)
    live_fills = load_live_fills(args.live_fills)

    metrics = build_metrics(
        chd=chd,
        collector=collector,
        calibrations=calibrations,
        backtest=backtest,
        live_fills=live_fills,
        pair_key=args.pair_key,
        exit_z=args.exit_z,
        entry_z=args.entry_z,
    )
    conditions = evaluate_stop_conditions(metrics)
    overall = overall_status(conditions)

    if args.metrics_out:
        payload = metrics_to_dict(metrics)
        payload["stop_conditions"] = dict(conditions)
        payload["overall_status"] = overall
        payload["rules"] = [
            {
                "key": rule.key,
                "title": rule.title,
                "threshold": {"red": rule.red, "yellow": rule.yellow, "unit": rule.unit},
                "direction": rule.direction,
                "rationale": rule.rationale,
                "status": status_of(conditions.get(rule.key, "")),
            }
            for rule in STOP_RULES
        ]
        out_path = os.path.abspath(args.metrics_out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"wrote {out_path}")

    if not args.metrics_only:
        from nt_pairs.cockpit_html import render_html

        html = render_html(metrics, conditions, title=args.title, subtitle=args.subtitle)
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes)")

    for key, value in conditions.items():
        print(f"[{status_of(value):7s}] {key}: {value.split(':', 1)[-1].strip()}")
    print(f"overall: {overall}")

    if args.strict and any(status_of(value) == RED for value in conditions.values()):
        return EXIT_STOP_RED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
