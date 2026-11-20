"""座舱 HTML 渲染：把 :class:`nt_pairs.cockpit.CockpitMetrics` 渲染成**自包含**单文件页面。

* plotly.js **内联**（``get_plotlyjs()``）→ 离线可打开，无 CDN 依赖；
* 缺数据的板块渲染成显眼的「无数据」占位，**不画 0、不画空图**；
* 纯逻辑（指标 + 停机判据）在 :mod:`nt_pairs.cockpit`，本模块只负责画。
"""
from __future__ import annotations

import html
import statistics
import time
from typing import Any, Mapping

from .cockpit import (
    GREEN,
    RED,
    STOP_RULES,
    UNKNOWN,
    YELLOW,
    CockpitMetrics,
    status_of,
)

__all__ = ["render_html"]

_STATUS_COLOR = {
    GREEN: "#2ea043",
    YELLOW: "#d29922",
    RED: "#f85149",
    UNKNOWN: "#6e7681",
}

_PAGE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 48px 0;
  background: #0d1117; color: #c9d1d9;
  font: 14px/1.6 -apple-system, "Segoe UI", "Noto Sans CJK SC", "PingFang SC", system-ui, sans-serif;
}
header { padding: 24px 32px 12px 32px; border-bottom: 1px solid #21262d; }
h1 { margin: 0 0 6px 0; font-size: 22px; color: #e6edf3; }
h2 { margin: 0 0 4px 0; font-size: 16px; color: #e6edf3; }
.sub { color: #8b949e; font-size: 13px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-weight: 600; font-size: 12px; color: #0d1117; }
main { padding: 0 32px; }
section { margin: 28px 0; }
.card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px 18px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 20px;
  align-items: start; }
.empty { border: 1px dashed #30363d; border-radius: 8px; padding: 26px 18px;
  text-align: center; color: #8b949e; background: #10151c; }
.empty b { color: #d29922; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border-bottom: 1px solid #21262d; padding: 8px 10px; text-align: left; vertical-align: top; }
th { color: #8b949e; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; min-width: 62px; text-align: center; padding: 2px 8px;
  border-radius: 4px; font-weight: 700; font-size: 12px; color: #0d1117; }
.kv { color: #8b949e; }
.kv b { color: #e6edf3; font-weight: 600; }
ul.notes { margin: 8px 0 0 0; padding-left: 20px; color: #8b949e; }
code { background: #21262d; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _figure_div(fig: Any) -> str:
    # 默认高度必须写死：plotly 的 div 默认是 height:100%，父容器高度不定时会溢出卡片。
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        default_height="320px",
        default_width="100%",
        config={"displaylogo": False},
    )


def _empty_panel(text: str, hint: str = "") -> str:
    extra = f'<div class="sub">{_esc(hint)}</div>' if hint else ""
    return f'<div class="empty"><b>{_esc(text)}</b>{extra}</div>'


def _card(title: str, body: str, note: str = "") -> str:
    note_html = f'<div class="sub">{_esc(note)}</div>' if note else ""
    return (
        f'<div class="card"><h2>{_esc(title)}</h2>{note_html}{body}</div>'
    )


def _fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "无数据"
    return f"{value:,.{digits}f}{suffix}"


def _ts_label(epoch: float) -> str:
    """epoch 秒 → UTC 字符串（图表横轴用，别给用户看裸时间戳）。"""
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(epoch))


# ---------------------------------------------------------------------------
# 各板块
# ---------------------------------------------------------------------------


def _pnl_section(metrics: CockpitMetrics) -> str:
    if not metrics.live_pnl_by_day:
        return _card(
            "今日 / 近 N 日 P&L",
            _empty_panel(
                "无实盘数据",
                "没有真实成交记录（data/cockpit/live_fills.jsonl 缺失或为空）—— 不显示 0，避免把「没成交」误读成「没赚到钱」",
            ),
        )
    import plotly.graph_objects as go

    days = sorted(metrics.live_pnl_by_day)
    values = [metrics.live_pnl_by_day[day] for day in days]
    fig = go.Figure(
        go.Bar(x=days, y=values, marker_color=["#2ea043" if v >= 0 else "#f85149" for v in values])
    )
    fig.update_layout(
        title=None,
        xaxis_title="交易日 (UTC)",
        yaxis_title="已实现盈亏 (USD)",
        template="plotly_dark",
        height=320,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    total = metrics.live_pnl_total if metrics.live_pnl_total is not None else 0.0
    body = (
        f'<div class="kv">今日/最近一日：<b>{_fmt(values[-1], 4, " USD")}</b> · '
        f'区间合计：<b>{_fmt(total, 4, " USD")}</b> · 成交笔数：<b>{metrics.live_fill_count}</b></div>'
        f"{_figure_div(fig)}"
    )
    return _card("今日 / 近 N 日 P&L", body, "来源：实盘已实现盈亏事件（live_fills.jsonl）")


def _p_fill_section(metrics: CockpitMetrics) -> str:
    import plotly.graph_objects as go

    model = metrics.p_fill_model
    measured = metrics.p_fill_measured
    if model is None and measured is None:
        return _card(
            "p_fill：模型预测 vs 实测",
            _empty_panel(
                "无数据",
                "Stage B 回放（模型）与成交率标定（实测）都没有 → 成交率塌陷这条停机条件判为「无数据」",
            ),
        )

    rows = []
    for label, estimate, extra in (
        ("模型（Stage B 回放）", model, "每轮机会的成交概率"),
        ("实测（采集器标定）", measured, "每分钟成交概率"),
    ):
        if estimate is None:
            rows.append(f"<tr><td>{_esc(label)}</td><td class='num'>无数据</td><td class='num'>—</td><td>{_esc(extra)}</td></tr>")
        else:
            rows.append(
                f"<tr><td>{_esc(label)}</td><td class='num'>{estimate.value:.4f}</td>"
                f"<td class='num'>{_esc(estimate.unit)}</td><td>{_esc(estimate.source)}</td></tr>"
            )
    table = (
        "<table><tr><th>来源</th><th>p_fill</th><th>口径</th><th>出处</th></tr>"
        + "".join(rows)
        + "</table>"
    )

    parts = [table]
    if metrics.p_fill_model_per_min is not None and metrics.p_fill_measured_per_min is not None:
        ratio = (
            metrics.p_fill_measured_per_min / metrics.p_fill_model_per_min
            if metrics.p_fill_model_per_min
            else None
        )
        parts.append(
            '<div class="kv" style="margin-top:10px">归一化口径（每分钟成交次数）：'
            f"模型 <b>{metrics.p_fill_model_per_min:.4f}</b> / 实测 <b>{metrics.p_fill_measured_per_min:.4f}</b>"
            + (f" → 实测 ÷ 模型 = <b>{ratio:.2f}</b>" if ratio is not None else "")
            + "</div>"
        )

    if model is not None:
        fig = go.Figure(
            go.Bar(x=["模型 p_fill（每轮机会）"], y=[model.value], marker_color="#58a6ff")
        )
        fig.update_layout(
            template="plotly_dark",
        height=320,
            yaxis_title="p_fill",
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            yaxis={"range": [0, max(0.001, model.value * 1.3)]},
        )
        parts.append(_figure_div(fig))
    if measured is not None:
        fig = go.Figure(
            go.Bar(x=["实测 p_fill（每分钟）"], y=[measured.value], marker_color="#a371f7")
        )
        fig.update_layout(
            template="plotly_dark",
        height=320,
            yaxis_title="p_fill / 分钟",
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            yaxis={"range": [0, max(0.001, measured.value * 1.3)]},
        )
        parts.append(_figure_div(fig))

    parts.append(
        '<div class="sub">两种口径不同（每轮机会 vs 每分钟），不能直接相除；'
        "停机判据用的是归一化后的「每分钟成交次数」。</div>"
    )
    return _card("p_fill：模型预测 vs 实测", "".join(parts))


def _exposure_section(metrics: CockpitMetrics) -> str:
    holds = [h for h in metrics.leg_exposure_seconds if h is not None]
    if not holds:
        return _card(
            "单腿暴露时长分布",
            _empty_panel("无数据", "Stage B 回放里没有事件级持仓时长样本"),
        )
    import plotly.graph_objects as go

    fig = go.Figure(go.Histogram(x=holds, nbinsx=40, marker_color="#58a6ff"))
    fig.update_layout(
        template="plotly_dark",
        height=320,
        xaxis_title="单腿暴露时长 (s)",
        yaxis_title="事件数",
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
    )
    exposure_values = [v for v in metrics.leg_exposure_usd_samples if v is not None]
    current = (
        "当前最大单腿暴露：<b>无实盘数据</b>"
        if metrics.naked_leg_exposure_usd is None
        else f"当前最大单腿暴露：<b>${metrics.naked_leg_exposure_usd:,.0f}</b>"
    )
    stats = (
        f'<div class="kv">样本 <b>{len(holds)}</b> 个事件 · 中位 <b>{statistics.median(holds):.0f}s</b> · '
        f"p95 <b>{sorted(holds)[max(0, int(len(holds) * 0.95) - 1)]:.0f}s</b> · "
        f"最长 <b>{max(holds):.0f}s</b> · 曝光金额中位 <b>"
        + (
            f"${statistics.median(exposure_values):,.0f}</b>"
            if exposure_values
            else "无数据</b>"
        )
        + "</div>"
    )
    body = stats + f'<div class="kv">{current}</div>' + _figure_div(fig)
    return _card(
        "单腿暴露时长分布 + 当前最大暴露",
        body,
        "来源：Stage B 事件级回放（hold_s / leg_exposure_usd_s）；实盘当前暴露需要运行中的持仓状态",
    )


def _depth_section(metrics: CockpitMetrics) -> str:
    legs = [leg for leg in metrics.legs if leg.depth_multiple is not None]
    if not legs:
        return _card(
            "两腿深度（±25bps） vs 目标名义",
            _empty_panel("无数据", "没有盘口深度样本（采集器或 Stage B 回放都没有）"),
        )
    import plotly.graph_objects as go

    threshold = 10.0
    fig = go.Figure(
        go.Bar(
            x=[leg.label for leg in legs],
            y=[leg.depth_multiple for leg in legs],
            marker_color=[
                "#f85149" if (leg.depth_multiple or 0) < threshold else "#2ea043" for leg in legs
            ],
        )
    )
    fig.add_hline(y=threshold, line_dash="dash", line_color="#d29922",
                  annotation_text="闸门④ 10× 目标名义", annotation_position="top left")
    fig.update_layout(
        template="plotly_dark",
        height=320,
        yaxis_title="深度 ÷ 目标名义（×）",
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
    )
    rows = "".join(
        f"<tr><td>{_esc(leg.label)}</td>"
        f"<td class='num'>{_fmt(leg.depth_usd, 0, ' USD')}</td>"
        f"<td class='num'>{_fmt(leg.depth_multiple, 1, '×')}</td>"
        f"<td class='num'>{_fmt(leg.repeat_close_ratio * 100 if leg.repeat_close_ratio is not None else None, 1, '%')}</td></tr>"
        for leg in metrics.legs
    )
    table = (
        "<table><tr><th>腿</th><th>±25bps 深度</th><th>× 目标名义</th><th>重复收盘率</th></tr>"
        + rows
        + "</table>"
    )
    target = (
        f"目标名义 ${metrics.target_notional_usd:,.0f}"
        if metrics.target_notional_usd
        else "目标名义未知"
    )
    body = table + f'<div class="kv" style="margin-top:8px">{_esc(target)}</div>' + _figure_div(fig)
    return _card("两腿深度（±25bps） vs 目标名义", body, "深度不足（< 10×）时显示红色")


def _basis_section(metrics: CockpitMetrics) -> str:
    points = metrics.basis_points
    if not points:
        return _card(
            "基差偏离曲线 + z-score",
            _empty_panel(
                "无数据",
                "缺少两个场所的 L2 快照（无法对齐基差序列）—— 基差曲线是运行期最核心的一块，缺采集器就看不到",
            ),
        )
    import plotly.graph_objects as go

    xs = [_ts_label(point.ts) for point in points]
    ys = [point.basis_bps for point in points]
    zs = [point.z for point in points]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ys, name="基差 (bps)", line={"color": "#58a6ff"}))
    z_series = [(x, z) for x, z in zip(xs, zs) if z is not None]
    if z_series:
        fig.add_trace(
            go.Scatter(
                x=[x for x, _ in z_series],
                y=[z for _, z in z_series],
                name="z-score",
                yaxis="y2",
                line={"color": "#a371f7"},
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=320,
        xaxis_title="时间 (UTC)",
        yaxis={"title": "基差 (bps)"},
        yaxis2={"title": "z-score", "overlaying": "y", "side": "right"},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    current = metrics.current_z
    if current is None:
        verdict = "当前 z-score：无数据（窗口不足）"
    else:
        if abs(current) <= metrics.exit_z:
            verdict = f"当前 z = {current:+.2f} → **已触发出场条件**（|z| ≤ {metrics.exit_z}）"
        elif abs(current) >= metrics.entry_z:
            verdict = f"当前 z = {current:+.2f} → 满足入场条件（|z| ≥ {metrics.entry_z}）"
        else:
            verdict = f"当前 z = {current:+.2f} → 带内，无动作"
    body = f'<div class="kv">{verdict}</div>' + _figure_div(fig)
    return _card(
        "基差偏离曲线 + 当前 z-score",
        body,
        f"入场 |z| ≥ {metrics.entry_z}，出场 |z| ≤ {metrics.exit_z}；z 用滚动 MAD 口径",
    )


def _stop_section(conditions: Mapping[str, str]) -> str:
    rows = []
    for rule in STOP_RULES:
        condition = conditions.get(rule.key, f"{UNKNOWN}: 未判定")
        status = status_of(condition)
        reason = condition.split(":", 1)[1].strip() if ":" in condition else condition
        threshold = (
            f"红 ≤ {rule.red:g} / 黄 ≤ {rule.yellow:g}"
            if rule.direction == "below"
            else f"红 ≥ {rule.red:g} / 黄 ≥ {rule.yellow:g}"
        )
        rows.append(
            f"<tr><td><b>{_esc(rule.title)}</b><div class='sub'>{_esc(rule.compare)}</div></td>"
            f"<td><span class='pill' style='background:{_STATUS_COLOR.get(status, '#6e7681')}'>"
            f"{_esc(status)}</span></td>"
            f"<td class='num'>{_esc(threshold)}<div class='sub'>{_esc(rule.unit)}</div></td>"
            f"<td>{_esc(reason)}<div class='sub'>{_esc(rule.rationale)}</div></td></tr>"
        )
    table = (
        "<table><tr><th>条件</th><th>状态</th><th>阈值</th><th>原因 / 依据</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return _card("停机判据", table, "阈值集中在 nt_pairs.cockpit.STOP_RULES；UNKNOWN = 无数据，不等于通过")


def _backtest_section(metrics: CockpitMetrics) -> str:
    if not metrics.backtest_equity:
        return ""
    import plotly.graph_objects as go

    xs = [_ts_label(ts) for ts, _ in metrics.backtest_equity]
    ys = [value for _, value in metrics.backtest_equity]
    fig = go.Figure(
        go.Scatter(x=xs, y=ys, name="权益 (USD)", line={"color": "#2ea043"})
    )
    if metrics.backtest_drawdown:
        fig.add_trace(
            go.Scatter(
                x=[_ts_label(ts) for ts, _ in metrics.backtest_drawdown],
                y=[value for _, value in metrics.backtest_drawdown],
                name="回撤",
                yaxis="y2",
                line={"color": "#f85149"},
            )
        )
    fig.update_layout(
        template="plotly_dark",
        height=320,
        xaxis_title="时间 (UTC)",
        yaxis={"title": "权益 (USD)"},
        yaxis2={"title": "回撤", "overlaying": "y", "side": "right"},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    stats = ""
    if metrics.backtest_stats:
        keep = [
            "Sharpe Ratio (252 days)",
            "Max Drawdown",
            "Total Return",
            "Profit Factor",
        ]
        cells = "".join(
            f"<tr><td>{_esc(k)}</td><td class='num'>{_esc(metrics.backtest_stats[k])}</td></tr>"
            for k in keep
            if k in metrics.backtest_stats
        )
        if cells:
            stats = f"<table><tr><th>回测统计</th><th>值</th></tr>{cells}</table>"
    return _card("回测净值 / 回撤（可选）", stats + _figure_div(fig))


def _sources_section(metrics: CockpitMetrics) -> str:
    rows = "".join(
        f"<tr><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"
        for key, value in metrics.inputs.items()
    ) or "<tr><td colspan='2'>没有任何输入被载入</td></tr>"
    notes = ""
    if metrics.notes:
        notes = "<ul class='notes'>" + "".join(f"<li>{_esc(n)}</li>" for n in metrics.notes) + "</ul>"
    body = (
        "<table><tr><th>输入</th><th>状态</th></tr>" + rows + "</table>" + notes
    )
    return _card("数据来源与缺失说明", body)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------


def render_html(
    metrics: CockpitMetrics,
    conditions: Mapping[str, str],
    *,
    title: str = "nt-pairs · 运行期座舱",
    subtitle: str = "",
) -> str:
    """渲染自包含 HTML（plotly.js 内联）。"""
    from plotly.offline import get_plotlyjs

    statuses = [status_of(value) for value in conditions.values()]
    overall = (
        RED
        if RED in statuses
        else YELLOW
        if YELLOW in statuses
        else GREEN
        if statuses and all(s == GREEN for s in statuses)
        else UNKNOWN
    )
    legend = f"<span class='pill' style='background:{_STATUS_COLOR[overall]}'>{overall}</span>"
    sections = [
        _pnl_section(metrics),
        _p_fill_section(metrics),
        f'<div class="grid">{_exposure_section(metrics)}{_depth_section(metrics)}</div>',
        _basis_section(metrics),
        _stop_section(conditions),
        _backtest_section(metrics),
        _sources_section(metrics),
    ]
    body = "".join(section for section in sections if section)
    head = f"""
<header>
  <h1>{_esc(title)}</h1>
  <div class="sub">{_esc(subtitle)}</div>
  <div class="sub">生成时间 (UTC)：{_esc(metrics.generated_utc)} · 整体状态 {legend}</div>
</header>
"""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{_esc(title)}</title>"
        f"<style>{_PAGE_CSS}</style>"
        # plotly.js 必须排在图形脚本之前，否则内联 newPlot 拿不到 Plotly。
        f"<script>{get_plotlyjs()}</script>"
        "</head><body>"
        f"{head}<main>{body}</main>"
        "</body></html>\n"
    )
