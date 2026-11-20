"""运行期座舱（cockpit）的纯逻辑内核。

职责：

1. 把四类**可选**输入（Stage B 回放 / L2 采集 / 成交率标定 / 回测统计）汇成一份
   有类型的 :class:`CockpitMetrics`；
2. 用 :func:`evaluate_stop_conditions` 把 metrics 判成逐条停机条件（绿 / 黄 / 红 / 无数据）。

约束：

* 本模块**只依赖标准库** —— CI 与 cron 都要能单独 import 它（停机判据是纯函数）。
* **缺数据就是「无数据」，绝不拿 0 顶替**：0 与「没有」在停机判据里含义完全相反
  （0 深度 = 腿不可用；没有深度数据 = 不知道）。
* 所有阈值集中在 :data:`STOP_RULES` 一张表里，判定代码里不再出现裸数字阈值。

数据来源（都可缺省，缺了就跳过对应板块）：

* ``data/chd_screen.json``      —— Stage B 事件级回放：逐日 EV、p_fill、盘口深度、b*、k；
* ``data/collector/<VENUE>/<YYYYMMDD>.jsonl`` —— L2 采集器输出：队列、陈旧率、基差序列；
* ``data/fill_calibration.json`` —— P0-2 成交率标定（实测 p_fill、markout）；
* ``data/cockpit/backtest_stats.json`` —— 回测统计（净值 / 回撤，可选）。
"""
from __future__ import annotations

import gzip
import json
import math
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "GREEN",
    "YELLOW",
    "RED",
    "UNKNOWN",
    "DEFAULT_TARGET_NOTIONAL_USD",
    "STOP_RULES",
    "STOP_RULES_BY_KEY",
    "BasisPoint",
    "CockpitMetrics",
    "ChdScreen",
    "CollectorSeries",
    "LegMetric",
    "PFillEstimate",
    "StopRule",
    "evaluate_stop_conditions",
    "overall_status",
    "status_of",
    "build_metrics",
    "load_chd_screen",
    "load_collector",
    "load_fill_calibration",
    "load_backtest_stats",
    "load_live_fills",
    "metrics_to_dict",
    "metrics_from_dict",
]

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"
UNKNOWN = "UNKNOWN"

#: 两周运行纪律里的目标名义（docs/2026-11-14-CHD_SCREENING.md §6：$100/腿）。
#: 只在 Stage B 回放缺席时作为**显式默认值**使用，并在 notes 里标注来源。
DEFAULT_TARGET_NOTIONAL_USD = 100.0

#: 严重度排序（数字越大越糟）。UNKNOWN 最低 —— 「不知道」不等于「好」。
SEVERITY: dict[str, int] = {GREEN: 1, UNKNOWN: 0, YELLOW: 2, RED: 3}


@dataclass(frozen=True, kw_only=True)
class StopRule:
    """一条停机规则的阈值与依据。

    ``direction``:

    * ``"above"`` —— 值越大越糟（裸腿时长、重复收盘率、连续负 EV 天数）；
    * ``"below"`` —— 值越小越糟（深度倍数、成本覆盖倍数、成交率倍数）。
    """

    key: str
    title: str
    direction: str
    red: float
    yellow: float
    unit: str
    compare: str
    rationale: str


#: **唯一的阈值表**。改阈值只改这里。
STOP_RULES: tuple[StopRule, ...] = (
    StopRule(
        key="rolling_net_ev",
        title="滚动净 EV 转负",
        direction="above",
        red=3.0,
        yellow=2.0,
        unit="连续交易日",
        compare="连续 3 个交易日净 EV ≤ 0",
        rationale=(
            "docs/2026-11-14-CHD_SCREENING.md §6 两周运行纪律 3：「周均净 EV 转负连续 3 天 → "
            "暂停并复盘」；3 天是确认，不是噪音"
        ),
    ),
    StopRule(
        key="depth",
        title="两腿深度不足",
        direction="below",
        red=10.0,
        yellow=20.0,
        unit="× 目标名义",
        compare="任一腿 ±25bps 深度 ≥ 10 × 目标名义",
        rationale=(
            "闸门④（scripts/chd_screen.py `gate4_capacity`：depth_min ≥ 10 × qty_usd；"
            "docs/EV_AND_MONITORING.md §5 深度与滑点公式）"
        ),
    ),
    StopRule(
        key="cost_cover",
        title="成本覆盖失效",
        direction="below",
        red=1.0,
        yellow=1.5,
        unit="× k·b*",
        compare="当前基差 p95 ≥ k·b*",
        rationale=(
            "闸门③（scripts/chd_screen.py `gate3_cost_cover`：basis_p95_dev ≥ k × b*；"
            "docs/EV_AND_MONITORING.md §5 可交易判据）"
        ),
    ),
    StopRule(
        key="fill_rate",
        title="成交率塌陷",
        direction="below",
        red=0.6,
        yellow=0.8,
        unit="实测 ÷ 模型",
        compare="实测 p_fill ≥ 60% × 模型 p_fill",
        rationale=(
            "docs/2026-11-14-CHD_SCREENING.md §6「必须先做的两件事」①：maker 腿实际成交率低于 "
            "60% 时，$100 档的边际翻负；标定产物 data/fill_calibration.json"
        ),
    ),
    StopRule(
        key="naked_leg",
        title="裸腿超时",
        direction="above",
        red=180.0,
        yellow=60.0,
        unit="秒",
        compare="单腿暴露 ≤ 180s 未对冲",
        rationale=(
            "nt_pairs.risk.RiskLimits：`naked_flatten_s=180`（授权平仓）/ `naked_warn_s=60`（告警）；"
            "docs/EV_AND_MONITORING.md §4.2 裸腿年龄"
        ),
    ),
    StopRule(
        key="stale_quote",
        title="陈旧报价",
        direction="above",
        red=0.10,
        yellow=0.05,
        unit="重复收盘率",
        compare="任一腿重复收盘率 ≤ 10%",
        rationale=(
            "闸门②（scripts/chd_screen.py `gate2_fresh`：repeat_a/b ≤ 10%）；"
            "RKLB / KORU / KIOXIA 的陈旧打印假象教训"
        ),
    ),
)

STOP_RULES_BY_KEY: dict[str, StopRule] = {rule.key: rule for rule in STOP_RULES}


# ---------------------------------------------------------------------------
# 有类型的指标容器
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PFillEstimate:
    """一个 p_fill 估计值。``unit`` 决定它能不能和另一个估计值直接比。"""

    value: float
    unit: str
    source: str


@dataclass(frozen=True, kw_only=True)
class LegMetric:
    """一条腿（场所）的运行期指标。"""

    label: str
    depth_usd: float | None = None
    depth_multiple: float | None = None
    repeat_close_ratio: float | None = None
    basis_p95_bps: float | None = None


@dataclass(frozen=True, kw_only=True)
class BasisPoint:
    """基差曲线上的一个点。"""

    ts: float
    basis_bps: float
    z: float | None = None


@dataclass(frozen=True, kw_only=True)
class CockpitMetrics:
    """面板要渲染的全部指标。

    约定：``None`` 一律表示「无数据」，**不是 0**。
    """

    generated_utc: str
    inputs: dict[str, str] = field(default_factory=dict)
    net_ev_by_day: dict[str, float] = field(default_factory=dict)
    net_ev_source: str = "none"
    net_ev_unit: str = "bps/轮"
    legs: tuple[LegMetric, ...] = ()
    target_notional_usd: float | None = None
    basis_p95_bps: float | None = None
    k: float | None = None
    b_star_bps: float | None = None
    p_fill_model: PFillEstimate | None = None
    p_fill_measured: PFillEstimate | None = None
    #: 归一化到「每分钟成交次数」后的模型 / 实测 p_fill（口径不同时的可比基准）。
    p_fill_model_per_min: float | None = None
    p_fill_measured_per_min: float | None = None
    naked_leg_age_s: float | None = None
    naked_leg_exposure_usd: float | None = None
    leg_exposure_seconds: tuple[float, ...] = ()
    leg_exposure_usd_samples: tuple[float, ...] = ()
    basis_points: tuple[BasisPoint, ...] = ()
    current_z: float | None = None
    exit_z: float = 0.3
    entry_z: float = 1.5
    live_pnl_by_day: dict[str, float] = field(default_factory=dict)
    live_pnl_total: float | None = None
    live_fill_count: int = 0
    backtest_equity: tuple[tuple[float, float], ...] = ()
    backtest_drawdown: tuple[tuple[float, float], ...] = ()
    backtest_stats: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 输入：Stage B 回放
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ChdScreen:
    """``data/chd_screen.json`` 的强类型视图（只取面板需要的字段）。"""

    path: str
    generated_at: str
    pair_key: str
    target_notional_usd: float | None = None
    k: float | None = None
    b_star_bps: float | None = None
    basis_p95_bps: float | None = None
    net_ev_by_day: dict[str, float] = field(default_factory=dict)
    p_fill_by_session: dict[str, float] = field(default_factory=dict)
    mean_p_fill: float | None = None
    opportunities_per_day: float | None = None
    hold_seconds: tuple[float, ...] = ()
    leg_exposure_usd: tuple[float, ...] = ()
    max_leg_exposure_usd: float | None = None
    leg_labels: tuple[str, ...] = ()
    leg_depth_usd: dict[str, float] = field(default_factory=dict)
    repeat_close_ratio: dict[str, float] = field(default_factory=dict)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def load_chd_screen(path: str, pair_key: str | None = None) -> ChdScreen | None:
    """读 Stage B 回放结果。文件不存在或结构不符时返回 ``None``（不编造数据）。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    rows = payload.get("stage_b")
    if not isinstance(rows, list) or not rows:
        return None
    row = next((r for r in rows if isinstance(r, dict) and r.get("key") == pair_key), None)
    if row is None:
        row = next((r for r in rows if isinstance(r, dict)), None)
    if row is None:
        return None

    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    stage_a = row.get("stage_a") if isinstance(row.get("stage_a"), dict) else {}
    book = row.get("book") if isinstance(row.get("book"), dict) else {}
    replay = row.get("replay") if isinstance(row.get("replay"), dict) else {}
    events = replay.get("events") if isinstance(replay.get("events"), list) else []

    leg_labels: list[str] = []
    leg_depth: dict[str, float] = {}
    for side, label in (("a", "A"), ("b", "B")):
        leg_book = book.get(side) if isinstance(book.get(side), dict) else {}
        depths = [_as_float(leg_book.get("depth_bid_usd")), _as_float(leg_book.get("depth_ask_usd"))]
        depths = [d for d in depths if d is not None]
        leg_labels.append(label)
        if depths:
            leg_depth[label] = min(depths)

    repeat: dict[str, float] = {}
    for side, label in (("a", "A"), ("b", "B")):
        value = _as_float(stage_a.get(f"repeat_{side}_pct"))
        if value is not None:
            repeat[label] = value / 100.0

    net_ev_by_day: dict[str, float] = {}
    by_day = replay.get("by_day") if isinstance(replay.get("by_day"), dict) else {}
    for day, stats in by_day.items():
        if not isinstance(stats, dict):
            continue
        value = _as_float(stats.get("net_bps_per_event"))
        if value is not None:
            net_ev_by_day[str(day)] = value

    p_fill_by_session: dict[str, float] = {}
    by_session = replay.get("by_session") if isinstance(replay.get("by_session"), dict) else {}
    for session, stats in by_session.items():
        if not isinstance(stats, dict):
            continue
        value = _as_float(stats.get("mean_p_fill"))
        if value is not None:
            p_fill_by_session[str(session)] = value

    event_p_fills = [e.get("p_fill") for e in events if isinstance(e, dict)]
    event_p_fills = [v for v in (_as_float(x) for x in event_p_fills) if v is not None]
    mean_p_fill = statistics.fmean(event_p_fills) if event_p_fills else None

    holds = tuple(
        v for v in (_as_float(e.get("hold_s")) for e in events if isinstance(e, dict)) if v is not None
    )
    exposures = tuple(
        v
        for v in (_as_float(e.get("leg_exposure_usd_s")) for e in events if isinstance(e, dict))
        if v is not None
    )
    max_exposure = max(exposures) if exposures else None

    return ChdScreen(
        path=path,
        generated_at=str(payload.get("generated_at") or ""),
        pair_key=str(row.get("key") or ""),
        target_notional_usd=_as_float(config.get("qty_usd")),
        k=_as_float(stage_a.get("k")),
        b_star_bps=_as_float(row.get("b_star_bps")),
        basis_p95_bps=_as_float(stage_a.get("basis_p95_dev")),
        net_ev_by_day=net_ev_by_day,
        p_fill_by_session=p_fill_by_session,
        mean_p_fill=mean_p_fill,
        opportunities_per_day=_as_float(row.get("opportunities_per_day_total")),
        hold_seconds=holds,
        leg_exposure_usd=exposures,
        max_leg_exposure_usd=max_exposure,
        leg_labels=tuple(leg_labels),
        leg_depth_usd=leg_depth,
        repeat_close_ratio=repeat,
    )


# ---------------------------------------------------------------------------
# 输入：L2 采集器
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CollectorSample:
    """一帧盘口快照（已裁剪到面板需要的字段）。"""

    t_epoch: float
    mid: float
    depth_bid_usd: float
    depth_ask_usd: float


@dataclass(frozen=True, kw_only=True)
class CollectorSeries:
    """一个场所 + 一个标的的快照序列。"""

    venue: str
    symbol: str
    samples: tuple[CollectorSample, ...] = ()
    repeat_close_ratio: float | None = None


def canonical_symbol(venue: str, symbol: str) -> str:
    """把各场所的 instrument id 归一到同一个基础标的（与 analyze_collector.py 同口径）。"""
    text = symbol.strip()
    if venue == "HYPERLIQUID":
        for prefix in ("xyz:", "io:", "cash:", "flx:", "vntl:"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
    elif venue == "OKX":
        for suffix in ("-USDT-SWAP", "-USDC-SWAP", "-USD-SWAP"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
    return text.upper()


def _depth_within(
    levels: Sequence[Sequence[Any]], mid: float, band_bps: float, side: str
) -> float:
    """±band_bps 带内的深度（USD）。数量取盘口原始 qty（不乘订单数，避免重复计数）。
    买侧取 ``mid × (1 − band)`` 之上、卖侧取 ``mid × (1 + band)`` 之下的档位。
    """
    if mid <= 0:
        return 0.0
    band = band_bps / 10_000.0
    total = 0.0
    for level in levels:
        if not level:
            continue
        try:
            price = float(level[0])
            qty = float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price <= 0 or qty <= 0:
            continue
        inside = price >= mid * (1.0 - band) if side == "bid" else price <= mid * (1.0 + band)
        if inside:
            total += price * qty
    return total


def _book_mid(bids: Sequence[Any], asks: Sequence[Any]) -> float | None:
    if not bids or not asks:
        return None
    try:
        bid = float(bids[0][0])
        ask = float(asks[0][0])
    except (TypeError, ValueError, IndexError):
        return None
    if bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def load_collector(
    root: str,
    *,
    max_files: int = 2,
    max_rows: int = 40000,
    band_bps: float = 25.0,
) -> tuple[CollectorSeries, ...]:
    """读 ``data/collector/<VENUE>/<YYYYMMDD>.jsonl(.gz)``。

    只保留每帧的 mid 与 ±band_bps 深度（不保留整本盘口）——VPS 只有 960MB，
    历史上全量读 5 天 330MB 的 JSONL 被 OOM kill 过。
    """
    if not root or not os.path.isdir(root):
        return ()
    buckets: dict[tuple[str, str], list[CollectorSample]] = {}
    for venue in sorted(os.listdir(root)):
        venue_dir = os.path.join(root, venue)
        if not os.path.isdir(venue_dir):
            continue
        names = sorted(
            name for name in os.listdir(venue_dir) if name.endswith((".jsonl", ".jsonl.gz"))
        )
        for name in names[-max_files:]:
            path = os.path.join(venue_dir, name)
            opener = gzip.open if name.endswith(".gz") else open
            try:
                with opener(path, "rt", encoding="utf-8", errors="replace") as fh:  # type: ignore[operator]
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        symbol = row.get("symbol")
                        if not symbol:
                            continue
                        mid = _book_mid(row.get("bids") or [], row.get("asks") or [])
                        if mid is None:
                            continue
                        key = (venue, canonical_symbol(venue, str(symbol)))
                        samples = buckets.setdefault(key, [])
                        if len(samples) >= max_rows:
                            continue
                        samples.append(
                            CollectorSample(
                                t_epoch=float(row.get("t_epoch") or 0.0),
                                mid=mid,
                                depth_bid_usd=_depth_within(
                                    row.get("bids") or [], mid, band_bps, "bid"
                                ),
                                depth_ask_usd=_depth_within(
                                    row.get("asks") or [], mid, band_bps, "ask"
                                ),
                            )
                        )
            except (OSError, EOFError, gzip.BadGzipFile):
                continue

    series: list[CollectorSeries] = []
    for (venue, symbol), samples in sorted(buckets.items()):
        samples.sort(key=lambda s: s.t_epoch)
        ratio = _hourly_repeat_ratio(samples)
        series.append(
            CollectorSeries(
                venue=venue,
                symbol=symbol,
                samples=tuple(samples),
                repeat_close_ratio=ratio,
            )
        )
    return tuple(series)


def _hourly_repeat_ratio(samples: Sequence[CollectorSample]) -> float | None:
    """重复收盘率 = 相邻**小时收盘**未变的比例（闸门②口径）。
    与 ``scripts/chd_screen.py`` 的 ``hourly_repeat_pct`` 同口径：取每小时最后一帧的 mid，
    中间缺小时就沿用上一个收盘价（空档也算「没动」）。
 """
    if len(samples) < 2:
        return None
    by_hour: dict[int, tuple[float, float]] = {}
    for sample in samples:
        hour = int(sample.t_epoch // 3600)
        current = by_hour.get(hour)
        if current is None or sample.t_epoch >= current[0]:
            by_hour[hour] = (sample.t_epoch, sample.mid)
    if len(by_hour) < 2:
        return None
    hours = sorted(by_hour)
    closes: list[float] = []
    carried: float | None = None
    for hour in range(hours[0], hours[-1] + 1):
        if hour in by_hour:
            carried = by_hour[hour][1]
        if carried is not None:
            closes.append(carried)
    if len(closes) < 2:
        return None
    repeats = sum(1 for a, b in zip(closes, closes[1:]) if a == b)
    return repeats / (len(closes) - 1)


# ---------------------------------------------------------------------------
# 输入：成交率标定 / 回测统计 / 实盘成交
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class FillCalibration:
    """``data/fill_calibration.json`` 的强类型视图（每标的一条）。"""

    symbol: str
    minutes: float | None
    fills: float | None
    p_fill_per_minute: float | None
    markout_5_bps: float | None
    net_bps_per_fill: float | None
    by_session: dict[str, float] = field(default_factory=dict)


def load_fill_calibration(path: str) -> tuple[FillCalibration, ...]:
    """读成交率标定结果。文件不存在或不是预期结构时返回空元组。"""
    if not path or not os.path.isfile(path):
        return ()
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return ()
    rows = payload if isinstance(payload, list) else [payload]
    out: list[FillCalibration] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not symbol:
            continue
        by_session = row.get("by_session")
        out.append(
            FillCalibration(
                symbol=str(symbol),
                minutes=_as_float(row.get("minutes")),
                fills=_as_float(row.get("fills")),
                p_fill_per_minute=(
                    _as_float(row.get("p_fill_per_minute"))
                    if row.get("p_fill_per_minute") is not None
                    else _as_float(row.get("p_fill_per_min"))
                ),
                markout_5_bps=_as_float(row.get("markout_5_mean")),
                net_bps_per_fill=_as_float(row.get("net_bps_per_fill")),
                by_session={
                    str(k): v
                    for k, v in (by_session.items() if isinstance(by_session, dict) else [])
                    if isinstance(v, (int, float))
                },
            )
        )
    return tuple(out)


def load_backtest_stats(path: str) -> dict[str, Any] | None:
    """读回测统计 JSON（可选输入）。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def load_live_fills(path: str) -> tuple[tuple[str, float], ...]:
    """读实盘已实现盈亏事件（JSONL：``{"ts": ISO8601, "pnl_usd": float}``）。

    没有这个文件 → 空元组 → 面板显示「无实盘数据」（**不显示 0**）。
    """
    if not path or not os.path.isfile(path):
        return ()
    out: list[tuple[str, float]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                pnl = _as_float(row.get("pnl_usd"))
                if pnl is None:
                    continue
                out.append((str(row.get("ts") or ""), pnl))
    except OSError:
        return ()
    return tuple(out)


# ---------------------------------------------------------------------------
# 基差曲线（采集器 → 基差 → z-score）
# ---------------------------------------------------------------------------


def _minute_series(samples: Sequence[CollectorSample]) -> dict[int, CollectorSample]:
    """按分钟对齐：每分钟取最后一帧。"""
    out: dict[int, CollectorSample] = {}
    for sample in samples:
        key = int(sample.t_epoch // 60) * 60
        current = out.get(key)
        if current is None or sample.t_epoch >= current.t_epoch:
            out[key] = sample
    return out


def build_basis_points(
    leader: CollectorSeries,
    follower: CollectorSeries,
    *,
    window: int = 240,
) -> tuple[BasisPoint, ...]:
    """构造基差序列与滚动 z-score（MAD 口径，同 analyze_collector.py）。

    ``basis_bps = (leader_mid / follower_mid − 1) × 1e4``（与 Stage B 回放同口径）。
    """
    left = _minute_series(leader.samples)
    right = _minute_series(follower.samples)
    keys = sorted(set(left) & set(right))
    if not keys:
        return ()
    raw: list[tuple[int, float]] = []
    for key in keys:
        a, b = left[key].mid, right[key].mid
        if a <= 0 or b <= 0:
            continue
        raw.append((key, (a / b - 1.0) * 10_000.0))
    points: list[BasisPoint] = []
    for index, (key, basis) in enumerate(raw):
        z: float | None = None
        if index >= window:
            sample = [value for _, value in raw[index - window:index]]
            median = statistics.median(sample)
            mad = statistics.median(abs(value - median) for value in sample)
            sigma = max(1.4826 * mad, 1e-9)
            z = (basis - median) / sigma
        points.append(BasisPoint(ts=float(key), basis_bps=basis, z=z))
    return tuple(points)


# ---------------------------------------------------------------------------
# 指标组装
# ---------------------------------------------------------------------------


def _match_calibration(
    calibrations: Sequence[FillCalibration], symbols: Iterable[str]
) -> FillCalibration | None:
    wanted = {s.upper() for s in symbols}
    for calibration in calibrations:
        if calibration.symbol.upper() in wanted:
            return calibration
    return None


def build_metrics(
    *,
    chd: ChdScreen | None = None,
    collector: Sequence[CollectorSeries] = (),
    calibrations: Sequence[FillCalibration] = (),
    backtest: Mapping[str, Any] | None = None,
    live_fills: Sequence[tuple[str, float]] = (),
    pair_key: str | None = None,
    exit_z: float = 0.3,
    entry_z: float = 1.5,
    basis_window: int = 240,
) -> CockpitMetrics:
    """把各类输入汇成一份 :class:`CockpitMetrics`。

    缺输入不报错，只是对应字段为 ``None``（= 无数据）。
    """
    inputs: dict[str, str] = {}
    notes: list[str] = []

    legs: list[LegMetric] = []
    target_notional = chd.target_notional_usd if chd else None
    if target_notional is None:
        target_notional = DEFAULT_TARGET_NOTIONAL_USD
        notes.append(
            f"Stage B 回放缺席，目标名义按两周运行纪律默认 ${DEFAULT_TARGET_NOTIONAL_USD:,.0f}/腿"
            "（docs/2026-11-14-CHD_SCREENING.md §6）。"
        )
    basis_p95 = chd.basis_p95_bps if chd else None

    # --- 两腿深度 / 重复收盘率：优先采集器实测，回放作为补充 -------------------
    leader = next((s for s in collector if s.venue == "HYPERLIQUID"), None)
    follower = next((s for s in collector if s.venue in ("LIGHTER", "OKX")), None)

    if collector:
        for series in collector:
            depth = min(
                (s.depth_bid_usd for s in series.samples if s.depth_bid_usd > 0),
                default=None,
            )
            multiple = (depth / target_notional) if (depth and target_notional) else None
            legs.append(
                LegMetric(
                    label=f"{series.venue}:{series.symbol}",
                    depth_usd=depth,
                    depth_multiple=multiple,
                    repeat_close_ratio=series.repeat_close_ratio,
                )
            )
    elif chd:
        for label in chd.leg_labels:
            depth = chd.leg_depth_usd.get(label)
            multiple = (depth / target_notional) if (depth and target_notional) else None
            legs.append(
                LegMetric(
                    label=label,
                    depth_usd=depth,
                    depth_multiple=multiple,
                    repeat_close_ratio=chd.repeat_close_ratio.get(label),
                )
            )

    # --- 净 EV 逐日 -----------------------------------------------------------
    net_ev_by_day = dict(chd.net_ev_by_day) if chd else {}
    net_ev_source = "replay" if chd and chd.net_ev_by_day else "none"

    # --- p_fill：模型 vs 实测 -------------------------------------------------
    p_fill_model: PFillEstimate | None = None
    if chd and chd.mean_p_fill is not None:
        p_fill_model = PFillEstimate(
            value=chd.mean_p_fill,
            unit="per_event",
            source=f"Stage B 回放（{chd.pair_key or 'n/a'}）",
        )
    p_fill_measured: PFillEstimate | None = None
    symbols = [s.symbol for s in collector] + ([chd.pair_key.split(":")[0]] if chd else [])
    calibration = _match_calibration(calibrations, symbols)
    if calibration and calibration.p_fill_per_minute is not None:
        p_fill_measured = PFillEstimate(
            value=calibration.p_fill_per_minute,
            unit="per_minute",
            source=f"成交率标定（{calibration.symbol}）",
        )

    # 归一化到「每分钟成交次数」：模型侧 = 单次机会成交概率 × 每分钟机会数；
    # 实测侧 = 成交数 ÷ 对齐分钟数。两者口径一致，才谈得上「实测 ÷ 模型」。
    model_per_min: float | None = None
    if chd and chd.mean_p_fill is not None and chd.opportunities_per_day:
        model_per_min = chd.mean_p_fill * chd.opportunities_per_day / 1440.0
    measured_per_min: float | None = (
        calibration.p_fill_per_minute if calibration else None
    )

    # --- 基差曲线 -------------------------------------------------------------
    basis_points: tuple[BasisPoint, ...] = ()
    current_z: float | None = None
    if leader and follower:
        basis_points = build_basis_points(leader, follower, window=basis_window)
        for point in reversed(basis_points):
            if point.z is not None:
                current_z = point.z
                break

    # --- 实盘 P&L -------------------------------------------------------------
    pnl_by_day: dict[str, float] = {}
    for ts, pnl in live_fills:
        day = ts[:10] if len(ts) >= 10 else "unknown"
        pnl_by_day[day] = pnl_by_day.get(day, 0.0) + pnl
    live_total = sum(pnl_by_day.values()) if pnl_by_day else None

    # --- 回测统计（可选） ------------------------------------------------------
    equity: tuple[tuple[float, float], ...] = ()
    drawdown: tuple[tuple[float, float], ...] = ()
    backtest_stats: dict[str, Any] = {}
    if backtest:
        curve = backtest.get("equity_curve")
        if isinstance(curve, list) and curve:
            equity = tuple((float(ts), float(v)) for ts, v in curve)
            peak = -math.inf
            dd: list[tuple[float, float]] = []
            for ts, value in equity:
                peak = max(peak, value)
                dd.append((ts, value / peak - 1.0 if peak > 0 else 0.0))
            drawdown = tuple(dd)
        stats = backtest.get("stats_returns")
        if isinstance(stats, dict):
            backtest_stats = {str(k): v for k, v in stats.items()}

    if chd:
        inputs["chd_screen"] = chd.path
    if collector:
        inputs["collector"] = f"{len(collector)} 条序列"
    if calibrations:
        inputs["fill_calibration"] = f"{len(calibrations)} 个标的"
    if backtest:
        inputs["backtest_stats"] = "已载入"
    if live_fills:
        inputs["live_fills"] = f"{len(live_fills)} 条已实现盈亏"
    else:
        notes.append("无实盘成交数据 → P&L 板块显示「无实盘数据」（不显示 0）。")
    if not collector:
        notes.append("无 L2 采集数据 → 基差曲线 / 深度 / 陈旧率板块无数据。")
    if not calibrations:
        notes.append("无成交率标定（data/fill_calibration.json）→ 成交率对比板块无数据。")

    return CockpitMetrics(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        inputs=inputs,
        net_ev_by_day=net_ev_by_day,
        net_ev_source=net_ev_source,
        legs=tuple(legs),
        target_notional_usd=target_notional,
        basis_p95_bps=basis_p95,
        k=chd.k if chd else None,
        b_star_bps=chd.b_star_bps if chd else None,
        p_fill_model=p_fill_model,
        p_fill_measured=p_fill_measured,
        p_fill_model_per_min=model_per_min,
        p_fill_measured_per_min=measured_per_min,
        naked_leg_age_s=None,
        naked_leg_exposure_usd=None,
        leg_exposure_seconds=chd.hold_seconds if chd else (),
        leg_exposure_usd_samples=chd.leg_exposure_usd if chd else (),
        basis_points=basis_points,
        current_z=current_z,
        exit_z=exit_z,
        entry_z=entry_z,
        live_pnl_by_day=pnl_by_day,
        live_pnl_total=live_total,
        live_fill_count=len(live_fills),
        backtest_equity=equity,
        backtest_drawdown=drawdown,
        backtest_stats=backtest_stats,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# 停机判据（纯函数）
# ---------------------------------------------------------------------------


def _status_line(status: str, reason: str) -> str:
    return f"{status}: {reason}"


def _rule_net_ev(rule: StopRule, metrics: CockpitMetrics) -> str:
    if not metrics.net_ev_by_day:
        return _status_line(UNKNOWN, "无净 EV 日序列（Stage B 回放或实盘统计都没有）")
    days = sorted(metrics.net_ev_by_day)
    trailing = 0
    for day in reversed(days):
        if metrics.net_ev_by_day[day] <= 0:
            trailing += 1
        else:
            break
    latest = metrics.net_ev_by_day[days[-1]]
    source = "实盘" if metrics.net_ev_source == "live" else "Stage B 回放"
    context = f"（{source}，单位 {metrics.net_ev_unit}；最近一日 {latest:+.2f}）"
    if trailing >= rule.red:
        return _status_line(RED, f"连续 {trailing} 个交易日净 EV ≤ 0{context}")
    if trailing >= rule.yellow:
        return _status_line(YELLOW, f"连续 {trailing} 个交易日净 EV ≤ 0，未达 {int(rule.red)} 日确认{context}")
    if trailing == 1:
        return _status_line(GREEN, f"仅最近 1 日净 EV ≤ 0，未达确认阈值{context}")
    return _status_line(GREEN, f"最近 {len(days)} 个交易日净 EV 均为正{context}")


def _rule_depth(rule: StopRule, metrics: CockpitMetrics) -> str:
    legs = [leg for leg in metrics.legs if leg.depth_multiple is not None]
    if not legs:
        return _status_line(UNKNOWN, "无 ±25bps 深度数据")
    worst = min(legs, key=lambda leg: leg.depth_multiple or 0.0)
    detail = "，".join(
        f"{leg.label} {leg.depth_multiple:.1f}×" for leg in legs if leg.depth_multiple is not None
    )
    target = (
        f"目标名义 ${metrics.target_notional_usd:,.0f}"
        if metrics.target_notional_usd
        else "目标名义未知"
    )
    if (worst.depth_multiple or 0.0) < rule.red:
        return _status_line(RED, f"{worst.label} 深度仅 {worst.depth_multiple:.1f}× 目标名义（阈值 {rule.red:.0f}×，{target}）：{detail}")
    if (worst.depth_multiple or 0.0) < rule.yellow:
        return _status_line(YELLOW, f"最薄腿 {worst.label} {worst.depth_multiple:.1f}×，逼近 {rule.red:.0f}× 阈值：{detail}")
    return _status_line(GREEN, f"两腿深度均 ≥ {rule.red:.0f}× 目标名义（{target}）：{detail}")


def _rule_cost_cover(rule: StopRule, metrics: CockpitMetrics) -> str:
    if metrics.basis_p95_bps is None or metrics.k is None or metrics.b_star_bps is None:
        return _status_line(UNKNOWN, "缺基差 p95 / k / b*（无法判断成本覆盖）")
    threshold = metrics.k * metrics.b_star_bps
    ratio = metrics.basis_p95_bps / threshold if threshold > 0 else None
    if ratio is None:
        return _status_line(UNKNOWN, "k·b* ≤ 0，无法判断")
    detail = f"基差 p95 {metrics.basis_p95_bps:.1f}bps vs k·b* {threshold:.1f}bps（k={metrics.k:.3f}, b*={metrics.b_star_bps:.2f}）"
    if ratio < rule.red:
        return _status_line(RED, f"成本覆盖失效：{detail}")
    if ratio < rule.yellow:
        return _status_line(YELLOW, f"成本覆盖仅 {ratio:.2f}×k·b*：{detail}")
    return _status_line(GREEN, f"成本覆盖 {ratio:.2f}×k·b*：{detail}")


def _rule_fill_rate(rule: StopRule, metrics: CockpitMetrics) -> str:
    model = metrics.p_fill_model
    measured = metrics.p_fill_measured
    if model is None or measured is None:
        missing = "模型" if model is None else "实测"
        return _status_line(UNKNOWN, f"缺{missing} p_fill，无法比较")

    # 优先用归一化口径（每分钟成交次数）；退化到同单位直接比较。
    if metrics.p_fill_model_per_min and metrics.p_fill_measured_per_min is not None:
        model_value = metrics.p_fill_model_per_min
        measured_value = metrics.p_fill_measured_per_min
        basis = "每分钟成交次数（模型 = 单次机会成交概率 × 每分钟机会数；实测 = 成交数 ÷ 对齐分钟数）"
    elif model.unit == measured.unit:
        model_value = model.value
        measured_value = measured.value
        basis = f"同为 {model.unit} 口径"
    else:
        return _status_line(
            UNKNOWN,
            f"口径不同（模型 {model.unit} vs 实测 {measured.unit}）且无法归一化，无法比较："
            f"模型 {model.value:.3f}（{model.source}）实测 {measured.value:.3f}（{measured.source}）",
        )
    if model_value <= 0:
        return _status_line(UNKNOWN, "模型 p_fill 为 0，无法比较")
    ratio = measured_value / model_value
    detail = (
        f"实测 {measured_value:.4f} ÷ 模型 {model_value:.4f} = {ratio:.2f}"
        f"（{basis}；模型：{model.source}；实测：{measured.source}）"
    )
    if ratio < rule.red:
        return _status_line(RED, f"成交率塌陷：{detail}")
    if ratio < rule.yellow:
        return _status_line(YELLOW, f"成交率偏低：{detail}")
    return _status_line(GREEN, f"成交率健康：{detail}")


def _rule_naked_leg(rule: StopRule, metrics: CockpitMetrics) -> str:
    if metrics.naked_leg_age_s is None:
        return _status_line(UNKNOWN, "无实盘裸腿状态（没有运行中的持仓）")
    age = metrics.naked_leg_age_s
    exposure = (
        f"，敞口 ${metrics.naked_leg_exposure_usd:,.0f}"
        if metrics.naked_leg_exposure_usd is not None
        else ""
    )
    if age > rule.red:
        return _status_line(RED, f"单腿暴露 {age:.0f}s 未对冲（阈值 {rule.red:.0f}s{exposure}）")
    if age > rule.yellow:
        return _status_line(YELLOW, f"单腿暴露 {age:.0f}s 未对冲{exposure}")
    return _status_line(GREEN, f"单腿暴露 {age:.0f}s{exposure}")


def _rule_stale_quote(rule: StopRule, metrics: CockpitMetrics) -> str:
    ratios = {
        leg.label: leg.repeat_close_ratio
        for leg in metrics.legs
        if leg.repeat_close_ratio is not None
    }
    if not ratios:
        return _status_line(UNKNOWN, "无重复收盘率数据")
    worst_label, worst = max(ratios.items(), key=lambda item: item[1])
    detail = "，".join(f"{label} {value * 100:.1f}%" for label, value in ratios.items())
    if worst > rule.red:
        return _status_line(RED, f"{worst_label} 重复收盘率 {worst * 100:.1f}%（阈值 {rule.red * 100:.0f}%）：{detail}")
    if worst > rule.yellow:
        return _status_line(YELLOW, f"{worst_label} 重复收盘率 {worst * 100:.1f}%：{detail}")
    return _status_line(GREEN, f"重复收盘率正常：{detail}")


_EVALUATORS = {
    "rolling_net_ev": _rule_net_ev,
    "depth": _rule_depth,
    "cost_cover": _rule_cost_cover,
    "fill_rate": _rule_fill_rate,
    "naked_leg": _rule_naked_leg,
    "stale_quote": _rule_stale_quote,
}


def evaluate_stop_conditions(metrics: CockpitMetrics) -> dict[str, str]:
    """逐条判定停机条件。

    返回 ``{rule_key: "STATUS: 原因"}``；``STATUS ∈ {GREEN, YELLOW, RED, UNKNOWN}``。
    ``UNKNOWN`` = 缺数据，**不等于 GREEN**。

    纯函数：同样的 metrics 永远得到同样的结论；没有 IO、没有时钟依赖。
    """
    return {rule.key: _EVALUATORS[rule.key](rule, metrics) for rule in STOP_RULES}


def status_of(condition: str) -> str:
    """从 ``"RED: 原因"`` 里取出状态。"""
    return condition.split(":", 1)[0].strip().upper()


def overall_status(conditions: Mapping[str, str]) -> str:
    """整体状态：取最差的一条（UNKNOWN 排在 GREEN 之下）。"""
    if not conditions:
        return UNKNOWN
    return max((status_of(value) for value in conditions.values()), key=lambda s: SEVERITY.get(s, 0))


# ---------------------------------------------------------------------------
# 序列化（给 CI / 定时任务用）
# ---------------------------------------------------------------------------


def metrics_to_dict(metrics: CockpitMetrics) -> dict[str, Any]:
    """把 metrics 转成可 JSON 序列化的字典（CI 读这个文件）。"""
    return {
        "generated_utc": metrics.generated_utc,
        "inputs": dict(metrics.inputs),
        "net_ev_by_day": dict(metrics.net_ev_by_day),
        "net_ev_source": metrics.net_ev_source,
        "net_ev_unit": metrics.net_ev_unit,
        "legs": [
            {
                "label": leg.label,
                "depth_usd": leg.depth_usd,
                "depth_multiple": leg.depth_multiple,
                "repeat_close_ratio": leg.repeat_close_ratio,
            }
            for leg in metrics.legs
        ],
        "target_notional_usd": metrics.target_notional_usd,
        "basis_p95_bps": metrics.basis_p95_bps,
        "k": metrics.k,
        "b_star_bps": metrics.b_star_bps,
        "p_fill_model": (
            {"value": metrics.p_fill_model.value, "unit": metrics.p_fill_model.unit,
             "source": metrics.p_fill_model.source}
            if metrics.p_fill_model else None
        ),
        "p_fill_measured": (
            {"value": metrics.p_fill_measured.value, "unit": metrics.p_fill_measured.unit,
             "source": metrics.p_fill_measured.source}
            if metrics.p_fill_measured else None
        ),
        "p_fill_model_per_min": metrics.p_fill_model_per_min,
        "p_fill_measured_per_min": metrics.p_fill_measured_per_min,
        "naked_leg_age_s": metrics.naked_leg_age_s,
        "naked_leg_exposure_usd": metrics.naked_leg_exposure_usd,
        "leg_exposure_seconds": list(metrics.leg_exposure_seconds),
        "basis_points": [
            {"ts": p.ts, "basis_bps": p.basis_bps, "z": p.z} for p in metrics.basis_points
        ],
        "current_z": metrics.current_z,
        "exit_z": metrics.exit_z,
        "entry_z": metrics.entry_z,
        "live_pnl_by_day": dict(metrics.live_pnl_by_day),
        "live_pnl_total": metrics.live_pnl_total,
        "live_fill_count": metrics.live_fill_count,
        "backtest_equity": [list(item) for item in metrics.backtest_equity],
        "backtest_drawdown": [list(item) for item in metrics.backtest_drawdown],
        "backtest_stats": dict(metrics.backtest_stats),
        "notes": list(metrics.notes),
    }


def metrics_from_dict(payload: Mapping[str, Any]) -> CockpitMetrics:
    """从（CI 写出的）字典还原 metrics。"""
    def optional_float(value: Any) -> float | None:
        return _as_float(value)

    model = payload.get("p_fill_model")
    measured = payload.get("p_fill_measured")
    legs_raw = payload.get("legs") or []

    def pfill(raw: Any) -> PFillEstimate | None:
        if not isinstance(raw, Mapping):
            return None
        value = _as_float(raw.get("value"))
        if value is None:
            return None
        return PFillEstimate(
            value=value,
            unit=str(raw.get("unit") or ""),
            source=str(raw.get("source") or ""),
        )

    return CockpitMetrics(
        generated_utc=str(payload.get("generated_utc") or ""),
        inputs={str(k): str(v) for k, v in (payload.get("inputs") or {}).items()},
        net_ev_by_day={
            str(k): float(v) for k, v in (payload.get("net_ev_by_day") or {}).items()
            if isinstance(v, (int, float))
        },
        net_ev_source=str(payload.get("net_ev_source") or "none"),
        net_ev_unit=str(payload.get("net_ev_unit") or "bps/轮"),
        legs=tuple(
            LegMetric(
                label=str(leg.get("label") or ""),
                depth_usd=optional_float(leg.get("depth_usd")),
                depth_multiple=optional_float(leg.get("depth_multiple")),
                repeat_close_ratio=optional_float(leg.get("repeat_close_ratio")),
            )
            for leg in legs_raw
            if isinstance(leg, Mapping)
        ),
        target_notional_usd=optional_float(payload.get("target_notional_usd")),
        basis_p95_bps=optional_float(payload.get("basis_p95_bps")),
        k=optional_float(payload.get("k")),
        b_star_bps=optional_float(payload.get("b_star_bps")),
        p_fill_model=pfill(model),
        p_fill_measured=pfill(measured),
        p_fill_model_per_min=optional_float(payload.get("p_fill_model_per_min")),
        p_fill_measured_per_min=optional_float(payload.get("p_fill_measured_per_min")),
        naked_leg_age_s=optional_float(payload.get("naked_leg_age_s")),
        naked_leg_exposure_usd=optional_float(payload.get("naked_leg_exposure_usd")),
        leg_exposure_seconds=tuple(
            float(v) for v in (payload.get("leg_exposure_seconds") or []) if isinstance(v, (int, float))
        ),
        basis_points=tuple(
            BasisPoint(
                ts=float(point.get("ts") or 0.0),
                basis_bps=float(point.get("basis_bps") or 0.0),
                z=optional_float(point.get("z")),
            )
            for point in (payload.get("basis_points") or [])
            if isinstance(point, Mapping)
        ),
        current_z=optional_float(payload.get("current_z")),
        exit_z=optional_float(payload.get("exit_z")) or 0.3,
        entry_z=optional_float(payload.get("entry_z")) or 1.5,
        live_pnl_by_day={
            str(k): float(v) for k, v in (payload.get("live_pnl_by_day") or {}).items()
            if isinstance(v, (int, float))
        },
        live_pnl_total=optional_float(payload.get("live_pnl_total")),
        live_fill_count=int(payload.get("live_fill_count") or 0),
        notes=tuple(str(n) for n in (payload.get("notes") or [])),
    )
