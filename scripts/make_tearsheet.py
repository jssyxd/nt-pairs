#!/usr/bin/env python
"""可视化座舱：把回测结果渲染成**自包含 HTML** tearsheet。

支持三种输入：

1. ``--native PATH`` —— pickle 的 Nautilus ``BacktestResult``（或 ``BacktestEngine``）
   → 直接走 ``nautilus_trader.analysis.create_tearsheet``。
2. ``--stats PATH`` / 位置参数 —— **仓库自有回测 JSON**
   （例如 ``scripts/run_pair_backtest.py`` 的输出）→ 由本模块的适配器把统计映射成
   ``create_tearsheet_from_stats`` 需要的 ``stats_pnls / stats_returns / stats_general /
   returns`` 四件套，图表仍然是 Nautilus 原生图表（不是自己画的假图）。
3. 数据不足以映射时 → 输出**降级版 HTML**：只用 plotly 画净值 / 回撤 / 月度收益热力图，
   并在页首标注「降级」与缺失项。**不编造任何数据。**

用法::

    python scripts/make_tearsheet.py data/cockpit/backtest_stats.json \\
        --out reports/tearsheet/soxl_hl_lighter_14d.html --theme nautilus_dark --currency USD
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from html import escape as html_escape
from typing import Any

import pandas as pd
from nautilus_trader.model import Currency

from nautilus_trader.analysis import create_tearsheet, create_tearsheet_from_stats
from nautilus_trader.analysis.config import (
    TearsheetConfig,
    TearsheetDistributionChart,
    TearsheetDrawdownChart,
    TearsheetEquityChart,
    TearsheetMonthlyReturnsChart,
    TearsheetRunInfoChart,
    TearsheetStatsTableChart,
    TearsheetYearlyReturnsChart,
)
from nautilus_trader.analysis.themes import list_themes

REQUIRED_STATS_KEYS = ("stats_pnls", "stats_returns", "stats_general")


# ---------------------------------------------------------------------------
# 输入适配
# ---------------------------------------------------------------------------


def load_native(path: str) -> Any:
    with open(path, "rb") as fh:
        return pickle.load(fh)


def adapt_repo_json(payload: dict[str, Any]) -> dict[str, Any]:
    """把仓库自有回测 JSON 映射成 tearsheet 需要的四件套。

    映射规则（只做搬运，不造数）：

    * ``stats_pnls``   ← 原样（原生是 ``{"USD": {...}}``）
    * ``stats_returns``← 原样（回测脚本已把 NaN 换成 null）
    * ``stats_general``← 原样
    * ``returns``       ← ``[[ts_ns, r], ...]`` → ``pd.Series``
    """
    missing = [k for k in REQUIRED_STATS_KEYS if k not in payload]
    if missing:
        raise KeyError(f"repo JSON 缺少字段: {', '.join(missing)}")
    returns = payload.get("returns") or []
    series = pd.Series(
        {int(ts): float(value) for ts, value in returns},
        dtype="float64",
    )
    series = series.sort_index()
    return {
        "stats_pnls": payload["stats_pnls"],
        "stats_returns": payload["stats_returns"],
        "stats_general": payload["stats_general"],
        "returns": series,
        "run_info": payload.get("run_info") or {},
        "account_info": payload.get("account_info") or {},
    }


def load_benchmark(path: str) -> pd.Series | None:
    """从 CSV 读基准收益序列。

    期望两列：第一列是时间戳（可解析为日期，或本来就是 epoch 秒/纳秒），第二列是收益率。
    """
    frame = pd.read_csv(path)
    if frame.shape[1] < 2:
        raise ValueError(f"{path}: 至少需要两列（时间戳, 收益率）")
    ts_col, val_col = frame.columns[0], frame.columns[1]
    index = pd.to_datetime(frame[ts_col], unit="s", errors="coerce")
    if index.isna().all():
        index = pd.to_datetime(frame[ts_col], errors="coerce")
    index = pd.DatetimeIndex(index)
    values = pd.to_numeric(frame[val_col], errors="coerce")
    series = pd.Series(values.values, index=index, dtype="float64").dropna()
    return series


def align_benchmark(benchmark: pd.Series, returns: pd.Series) -> pd.Series | None:
    """把基准重采样到收益序列的时间轴上（不外推、不填充缺失）。"""
    if benchmark is None or returns.empty:
        return None
    bench = benchmark.copy()
    bench.index = bench.index.tz_localize(None) if bench.index.tz is not None else bench.index
    target = pd.DatetimeIndex(
        [pd.Timestamp(int(ts), unit="ns") for ts in returns.index],
    )
    daily = bench.resample("1D").last().dropna()
    # 逐点映射到收益时间轴；基准缺的日子留空后丢弃，**不做前向填充**。
    values = daily.reindex(target.normalize()).to_numpy(dtype="float64")
    return pd.Series(values, index=target, dtype="float64").dropna()


# ---------------------------------------------------------------------------
# 降级渲染（数据不足时）
# ---------------------------------------------------------------------------


def render_degraded(payload: dict[str, Any], out_path: str, title: str, reason: str) -> str:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=2, subplot_titles=(
        "Equity (indexed)", "Drawdown", "Monthly returns", "Returns distribution"))

    equity = payload.get("equity_curve") or []
    returns = payload.get("returns") or []
    if equity:
        ts = [pd.Timestamp(int(t), unit="ns") for t, _ in equity]
        vals = [float(v) for _, v in equity]
        base = vals[0] or 1.0
        fig.add_trace(go.Scatter(x=ts, y=[v / base for v in vals], name="Equity"),
                      row=1, col=1)
        peak = []
        running = -1e18
        for v in vals:
            running = max(running, v)
            peak.append(v / running - 1.0)
        fig.add_trace(go.Scatter(x=ts, y=peak, name="Drawdown"),
                      row=1, col=2)
    if returns:
        idx = [pd.Timestamp(int(t), unit="ns") for t, _ in returns]
        vals = [float(v) for _, v in returns]
        fig.add_trace(go.Histogram(x=vals, nbinsx=60, name="Returns"), row=2, col=2)
        monthly = pd.Series(vals, index=idx).resample("ME").apply(lambda s: (1 + s).prod() - 1)
        fig.add_trace(go.Bar(x=list(monthly.index), y=list(monthly.values), name="Monthly"),
                      row=2, col=1)
    fig.update_layout(title=f"{title} — DEGRADED VIEW", template="plotly_white")
    fig.add_annotation(
        text=reason,
        xref="paper", yref="paper", x=0, y=1.08,
        showarrow=False, align="left", font={"size": 13, "color": "#b00"},
    )
    html = fig.to_html(full_html=True, include_plotlyjs=True)
    # plotly 会把非 ASCII 转义成 \uXXXX 放进 JSON 里，降级原因因此在源文件里搜不到。
    # 这里额外插一段可见的横幅（人眼可读 + 可 grep）。
    banner = (
        '<div style="margin:12px 16px;padding:10px 12px;border:1px solid #b00;'
        'border-radius:4px;color:#b00;font:14px/1.5 system-ui,sans-serif;">'
        f"<b>DEGRADED VIEW</b> — {html_escape(reason)}</div>"
    )
    html = html.replace("<body>", f"<body>\n{banner}", 1)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_config(theme: str, title: str, benchmark_name: str, include_benchmark: bool) -> TearsheetConfig:
    return TearsheetConfig(
        title=title,
        theme=theme,
        include_benchmark=include_benchmark,
        benchmark_name=benchmark_name,
        charts=[
            TearsheetRunInfoChart(),
            TearsheetStatsTableChart(),
            TearsheetEquityChart(),
            TearsheetDrawdownChart(),
            TearsheetMonthlyReturnsChart(),
            TearsheetYearlyReturnsChart(),
            TearsheetDistributionChart(),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", default=None,
                        help="仓库自有回测 JSON（或 --native 指定的 pickle）")
    parser.add_argument("--native", default=None, help="pickle 的 Nautilus BacktestResult")
    parser.add_argument("--stats", default=None, help="仓库自有回测 JSON")
    parser.add_argument("--out", default="reports/tearsheet/tearsheet.html")
    parser.add_argument("--theme", default="nautilus", choices=list_themes())
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--benchmark", default=None, help="基准 CSV（时间戳, 收益率）")
    parser.add_argument("--benchmark-name", default="Benchmark")
    parser.add_argument("--title", default="nt-pairs — two-leg basis backtest")
    parser.add_argument("--force-degraded", action="store_true",
                        help="即使数据齐全也走降级渲染（用于对比/排障）")
    args = parser.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # --- 1. 原生 Nautilus 结果 ------------------------------------------------
    if args.native:
        obj = load_native(args.native)
        benchmark = load_benchmark(args.benchmark) if args.benchmark else None
        currency = None
        if args.currency:
            try:
                currency = Currency.from_str(args.currency)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: 无法解析 --currency {args.currency}: {exc}")
        create_tearsheet(
            engine=obj,
            output_path=out_path,
            title=args.title,
            currency=currency,
            config=build_config(args.theme, args.title, args.benchmark_name, benchmark is not None),
            benchmark_returns=benchmark,
            benchmark_name=args.benchmark_name,
        )
        print(f"wrote {out_path} (native)")
        return 0

    # --- 2. 仓库自有 JSON ----------------------------------------------------
    source = args.stats or args.input
    if not source:
        parser.error("需要一个输入：--native <pkl> 或 --stats/位置参数 <json>")
    with open(source, encoding="utf-8") as fh:
        payload = json.load(fh)

    try:
        adapted = adapt_repo_json(payload)
    except KeyError as exc:
        reason = f"降级原因：{exc}"
        path = render_degraded(payload, out_path, args.title, reason)
        print(f"wrote {path} (degraded) — {reason}")
        return 0

    returns = adapted["returns"]
    if returns.empty:
        reason = "降级原因：JSON 里没有可用的 returns 序列（无法映射到 tearsheet）"
        path = render_degraded(payload, out_path, args.title, reason)
        print(f"wrote {path} (degraded) — {reason}")
        return 0

    if args.force_degraded:
        reason = "降级原因：--force-degraded（仅用 equity/returns 画净值、回撤、月度收益）"
        path = render_degraded(payload, out_path, args.title, reason)
        print(f"wrote {path} (degraded) — {reason}")
        return 0

    stats_pnls = adapted["stats_pnls"]
    if args.currency and isinstance(stats_pnls, dict) and args.currency in stats_pnls:
        stats_pnls = {args.currency: stats_pnls[args.currency]}
    elif args.currency and isinstance(stats_pnls, dict) and stats_pnls:
        print(f"warning: --currency {args.currency} 不在 stats_pnls 里"
              f"（可用: {', '.join(stats_pnls)}），保留全部")

    benchmark = None
    if args.benchmark:
        raw = load_benchmark(args.benchmark)
        benchmark = align_benchmark(raw, returns)
        if benchmark is None:
            print("warning: 基准无法对齐到收益时间轴，已忽略基准")

    create_tearsheet_from_stats(
        stats_pnls=stats_pnls,
        stats_returns=adapted["stats_returns"],
        stats_general=adapted["stats_general"],
        returns=returns,
        output_path=out_path,
        title=args.title,
        config=build_config(args.theme, args.title, args.benchmark_name, benchmark is not None),
        benchmark_returns=benchmark,
        benchmark_name=args.benchmark_name,
        run_info=adapted["run_info"] or None,
        account_info=adapted["account_info"] or None,
    )
    size = os.path.getsize(out_path)
    print(f"wrote {out_path} ({size} bytes, {len(returns)} return points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
