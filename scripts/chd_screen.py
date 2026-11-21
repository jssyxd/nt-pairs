#!/usr/bin/env python3
"""CryptoHFTData 全市场扫描 + 事件级 EV 回测管线（主入口）。

流程（与 DECISION.md 的五道闸门一一对应）：

  a. 拉四家场所的符号清单 → 取股票/ETF/pre-IPO 候选 → 构造**同名跨场所**候选对
     （含 Hyperliquid 内部跨 deployer 配对）。
  b. Stage A（**trades**，便宜）：重建固定时间网格（不把缺行当连续时间），
     算单位一致性（闸门①）、重复收盘/陈旧（闸门②）、p95|basis|、半衰期、ESS→k。
  c. Stage B（**orderbook**，贵）：只对短名单对下载盘口，重建盘口快照，
     算真实 ±25bps 深度（闸门④）、按规模吃盘的滑点、`b*`（闸门③）。
  d. 事件级回放（闸门⑤）：**同刻定价**（同一毫秒两腿 mid）、入场/出场都在
     盘口上取价，主动腿吃盘口、被动腿用排队成交概率；记录净 bps、持仓时长、
     单腿最大暴露。
  e. 输出 `data/chd_screen.json` + `docs/CHD_SCREEN.md`，含**按会话分层的日均机会次数**。

用法：
    python scripts/chd_screen.py --hours 24 --qty 500
    python scripts/chd_screen.py --pairs hyperliquid_futures:XYZ:SOXL,lighter:SOXL
    python scripts/chd_screen.py --hours 6 --replay-hours 2 --max-replay-pairs 6
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from nt_pairs import market_micro as mm  # noqa: E402
from nt_pairs.chd_client import (BookPoint, ChdClient, hour_range, rebuild_book_series,  # noqa: E402
                                 utcnow)
from nt_pairs.costs import LegSpec  # noqa: E402
from nt_pairs.equity_universe import EQUITY_BASES_ALL, ETF_BASES, base_of  # noqa: E402
from nt_pairs.funding_fee import VenueFees, b_star, venue_fees  # noqa: E402
from nt_pairs.sessions import classify  # noqa: E402

VENUES = ("hyperliquid_futures", "lighter", "okx_futures", "binance_futures")
HL_INFO = "https://api.hyperliquid.xyz/info"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _quantile(xs: Sequence[float], q: float) -> float:
    vals = sorted(x for x in xs if isinstance(x, (int, float)) and math.isfinite(x))
    if not vals:
        return float("nan")
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def _median(xs: Sequence[float]) -> float:
    vals = [x for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    return statistics.median(vals) if vals else float("nan")


def _autocorr(xs: Sequence[float], lag: int = 1) -> float:
    vals = [x for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    n = len(vals)
    if n < lag + 5:
        return 0.0
    mean = sum(vals) / n
    num = sum((vals[i] - mean) * (vals[i + lag] - mean) for i in range(n - lag))
    den = sum((v - mean) ** 2 for v in vals)
    if den <= 0:
        return 0.0
    return num / den


def _ess(n: int, rho: float) -> float:
    if n <= 0:
        return 0.0
    rho = min(max(rho, -0.999), 0.999)
    return max(1.0, n * (1 - rho) / (1 + rho))


def _pd_numeric(col):
    import pandas as pd
    return pd.to_numeric(col, errors="coerce")


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Leg:
    venue: str
    symbol: str
    base: str
    prefix: str = ""            # HL deployer 前缀（xyz / io / cash / ...）


@dataclass
class Pair:
    key: str
    a: Leg
    b: Leg
    base: str


@dataclass
class LegSeries:
    """某条腿在固定网格上的成交价序列（缺失格子为 None，不前向填充）。"""

    leg: Leg
    grid_ts: list[int]
    price: list[float | None]
    n_trades: list[int]
    qty_sum: list[float]
    price_counts: dict[float, int] = field(default_factory=dict)

    def ff(self) -> tuple[list[float], float]:
        """前向填充后的价格序列 + 无成交格子占比（%）。"""
        out: list[float] = []
        last: float | None = None
        missing = 0
        for p in self.price:
            if p is None:
                missing += 1
                out.append(last if last is not None else float("nan"))
            else:
                last = p
                out.append(p)
        n = len(self.price) or 1
        return out, 100.0 * missing / n

    @property
    def span_s(self) -> float:
        if len(self.grid_ts) < 2:
            return 0.0
        return (self.grid_ts[-1] - self.grid_ts[0]) / 1000.0

    @property
    def trade_rate(self) -> float:
        """整条腿的成交笔率（笔/秒）。"""
        if self.span_s <= 0:
            return 0.0
        return sum(self.n_trades) / self.span_s

    @property
    def avg_trade_size(self) -> float:
        n = sum(self.n_trades)
        return (sum(self.qty_sum) / n) if n else 0.0


@dataclass
class PairStats:
    pair: Pair
    n_overlap: int
    ratio_mean: float
    ratio_cv: float
    basis_p95_dev: float
    basis_center: float
    autocorr1: float
    half_life_s: float
    ess: float
    k: float
    repeat_a: float
    repeat_b: float
    stale_a: float
    stale_b: float
    unit_ok: bool
    fresh_ok: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class Event:
    ts_ms: int
    session: str
    entry_basis: float
    exit_basis: float
    capture_bps: float
    cost_bps: float
    net_bps: float
    p_fill: float
    hold_s: float
    converged: bool
    leg_exposure_usd_s: float
    maker_leg: str
    taker_leg: str


# --------------------------------------------------------------------------
# Stage A：trades → 固定网格
# --------------------------------------------------------------------------

def hl_meta(dex: str) -> dict:
    """Hyperliquid `meta`（按 dex）：拿每个资产的 deployerFeeScale / growthMode。

    HIP-3 的真实费率 = base × (scale<1 ? scale+1 : scale*2) × (growthMode ? 0.1 : 1)
    （见 `nt_pairs.costs.hl_fee_bps`）。未知时用基础档（保守上界）。
    """
    req = urllib.request.Request(
        HL_INFO, data=json.dumps({"type": "meta", "dex": dex}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as fh:
        return json.load(fh)


def fees_for(leg: Leg, hl_cache: dict[str, dict]) -> VenueFees:
    """取一条腿的费率（HL HIP-3 按 deployerFeeScale 换算）。"""
    if leg.venue != "hyperliquid_futures" or ":" not in leg.symbol:
        return venue_fees(leg.venue)
    dex, base = leg.symbol.split(":", 1)
    # HL 的 dex 名是**小写**（xyz / io / para / cash / km / flx / vntl / mkts / hyna），
    # 符号表里却是大写（`XYZ:SOXL`）。不归一化会查到一个不存在的 dex →
    # 费率退回基础档（1.5/4.5），比 HIP-3 实际（growth mode 下 0.3/0.9）高 5 倍。
    dex_key = dex.lower()
    if dex_key not in hl_cache:
        try:
            hl_cache[dex_key] = hl_meta(dex_key)
        except Exception:  # noqa: BLE001
            hl_cache[dex_key] = {}
    for a in (hl_cache[dex_key] or {}).get("universe") or []:
        name = str(a.get("name", ""))
        if ":" in name:
            name = name.split(":", 1)[1]
        if name.upper() != base.upper():
            continue
        try:
            scale: float | None = float(a.get("deployerFeeScale"))
        except (TypeError, ValueError):
            scale = None
        gm = a.get("growthMode")
        growth = True if gm == "enabled" else (False if gm == "disabled" else None)
        return venue_fees(leg.venue, deployer_fee_scale=scale, growth_mode=growth)
    return venue_fees(leg.venue)


def last_rth_hours(now: datetime, n: int) -> list[tuple[str, int]]:
    """最近一个 RTH 时段里的最后 `n` 个小时（用于回放窗口覆盖美股盘中）。"""
    from datetime import timedelta
    cur = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    start = None
    for _ in range(24 * 10):
        if classify(int(cur.timestamp() * 1000)).session == "rth":
            start = cur
            break
        cur -= timedelta(hours=1)
    if start is None:
        return []
    block = []
    c = start
    while classify(int(c.timestamp() * 1000)).session == "rth":
        block.append(c)
        c += timedelta(hours=1)
    block = block[-max(1, n):]
    return [(d.strftime("%Y-%m-%d"), d.hour) for d in block]


def prefetch(client: ChdClient, jobs: Sequence[tuple[str, str, int, str, str]],
             workers: int = 12, verbose: bool = False) -> dict[tuple[str, str, str, str], Path]:
    """并行下载 (venue, day, hour, symbol, data_type) 列表，返回可用文件路径表。"""
    from concurrent.futures import ThreadPoolExecutor

    out: dict[tuple[str, str, str, str], Path] = {}
    if not jobs:
        return out

    def one(job):
        venue, day, hour, symbol, dtype = job
        try:
            return job, client.download(venue, day, hour, symbol, dtype)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  [warn] 下载失败 {venue}/{symbol}/{day}-{hour}/{dtype}: {exc}")
            return job, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for job, path in ex.map(one, jobs):
            if path is not None:
                venue, day, hour, symbol, dtype = job
                out[(venue, symbol, f"{day} {hour:02d}", dtype)] = path
    return out


def load_leg_series(leg: Leg, hours: Sequence[tuple[str, int]],
                     grid_s: int,
                     paths: dict[tuple[str, str, str, str], Path]) -> LegSeries | None:
    """把某条腿的 trades 折叠到固定时间网格（缺格保留为 None，不前向填充）。"""
    cells: dict[int, list[float]] = {}
    price_counts: dict[float, int] = defaultdict(int)
    for day, hour in hours:
        path = paths.get((leg.venue, leg.symbol, f"{day} {hour:02d}", "trades"))
        if path is None:
            continue
        df = ChdClient.read_parquet(path)
        if df is None or len(df) == 0:
            continue
        step = grid_s * 1000
        ts = (df["event_time"].astype("int64") // step) * step
        px = _pd_numeric(df["price"])
        qty = _pd_numeric(df["quantity"])
        for t, p, q in zip(ts, px, qty):
            if p != p:
                continue
            cell = cells.get(int(t))
            if cell is None:
                cell = [float(p), 0, 0.0]
                cells[int(t)] = cell
            cell[0] = float(p)      # 格子内最后一笔成交价
            cell[1] += 1
            cell[2] += float(q) if q == q else 0.0
            price_counts[round(float(p), 8)] += 1
    if not cells:
        return None
    grid_ts = sorted(cells)
    return LegSeries(
        leg=leg, grid_ts=grid_ts,
        price=[cells[t][0] for t in grid_ts],
        n_trades=[int(cells[t][1]) for t in grid_ts],
        qty_sum=[cells[t][2] for t in grid_ts],
        price_counts=dict(price_counts),
    )


def load_trade_detail(leg: Leg, hours: Sequence[tuple[str, int]],
                       paths: dict[tuple[str, str, str, str], Path]) -> list[tuple[int, float, float]]:
    """逐笔成交明细 `(ts_ms, price, qty)`（按时间升序），用于被动腿的成交模型。"""
    out: list[tuple[int, float, float]] = []
    for day, hour in hours:
        path = paths.get((leg.venue, leg.symbol, f"{day} {hour:02d}", "trades"))
        if path is None:
            continue
        df = ChdClient.read_parquet(path)
        if df is None or len(df) == 0:
            continue
        ts = df["event_time"].astype("int64").tolist()
        px = _pd_numeric(df["price"]).tolist()
        qty = _pd_numeric(df["quantity"]).tolist()
        for t, p, q in zip(ts, px, qty):
            if p != p:
                continue
            out.append((int(t), float(p), float(q) if q == q else 0.0))
    out.sort(key=lambda x: x[0])
    return out


def volume_through(trades: Sequence[tuple[int, float, float]], t0: int, t1: int,
                   price: float, side: str) -> tuple[float, int]:
    """窗口 `[t0, t1]` 内**穿过我们价位**的成交量与笔数。

    被动买入（挂在 bid）：市价卖单成交价 ≤ 我们的买价才算打到我们；
    被动卖出（挂在 ask）：市价买单成交价 ≥ 我们的卖价才算打到我们。

    返回 `(成交量, 笔数)`；笔数用于估成交流率 λ（笔/秒），成交量用于估均单量。
    """
    vol = 0.0
    n = 0
    for ts, px, qty in trades:
        if ts < t0:
            continue
        if ts > t1:
            break
        if (side == "buy" and px <= price) or (side == "sell" and px >= price):
            vol += qty
            n += 1
    return vol, n


def hourly_repeat_pct(series: LegSeries) -> float:
    """**1 小时**口径的重复收盘率（与仓库 `RepeatedCloseDetector` 同口径）。

    每小时取最后一笔成交价；整点无成交的小时沿用上一小时的价（= 陈旧打印）。
    相邻小时价完全相同的比例 >10% → 该腿的「回归」是机械假象（闸门②）。
    """
    if series is None or not series.grid_ts:
        return 100.0
    by_hour: dict[int, float] = {}
    for t, p in zip(series.grid_ts, series.price):
        if p is None:
            continue
        by_hour[t // 3_600_000] = p          # grid 升序 → 最后一笔覆盖
    if not by_hour:
        return 100.0
    keys = sorted(by_hour)
    closes: list[float] = []
    last = by_hour[keys[0]]
    for k in range(keys[0], keys[-1] + 1):
        if k in by_hour:
            last = by_hour[k]
        closes.append(last)
    if len(closes) < 2:
        return 0.0
    same = sum(1 for a, b in zip(closes, closes[1:]) if a == b)
    return 100.0 * same / (len(closes) - 1)


def pair_stats(pair: Pair, a: LegSeries, b: LegSeries, grid_s: int) -> PairStats:
    """**同刻定价**的基差统计（同一网格格子的两腿成交价，不跨瞬间）。"""
    bmap = {t: p for t, p in zip(b.grid_ts, b.price) if p is not None}
    ratios: list[float] = []
    for t, pa in zip(a.grid_ts, a.price):
        if pa is None:
            continue
        pb = bmap.get(t)
        if pb is None or pb <= 0 or pa <= 0:
            continue
        ratios.append(pa / pb)
    if len(ratios) < 20:
        return PairStats(pair, len(ratios), float("nan"), float("nan"), float("nan"),
                         float("nan"), 0.0, 0.0, 0.0, float("inf"), 0.0, 0.0, 0.0, 0.0,
                         False, False, ["同刻重叠样本不足（<20 格）"])
    rmean = statistics.fmean(ratios)
    rsd = statistics.pstdev(ratios)
    basis = [(r - 1.0) * 10000.0 for r in ratios]
    center = _median(basis)
    dev = [x - center for x in basis]
    p95 = _quantile([abs(x) for x in dev], 0.95)
    rho = _autocorr(dev, 1)
    hl_s = (math.log(2) / -math.log(rho)) * grid_s if 0 < rho < 1 else float("inf")
    ess = _ess(len(dev), rho)
    k = 1.0 + 1.0 / math.sqrt(ess) if ess > 0 else float("inf")
    _ff_a, stale_a = a.ff()
    _ff_b, stale_b = b.ff()
    rep_a = hourly_repeat_pct(a)
    rep_b = hourly_repeat_pct(b)
    cv = rsd / rmean if rmean else float("inf")
    unit_ok = abs(rmean - 1.0) <= 0.05 and cv <= 0.05
    fresh_ok = rep_a <= 10.0 and rep_b <= 10.0
    notes: list[str] = []
    if not unit_ok:
        notes.append(f"单位不一致：ratio_mean={rmean:.4f}, cv={cv:.4f}")
    if rep_a > 10.0:
        notes.append(f"A 腿重复收盘 {rep_a:.1f}%（陈旧打印）")
    if rep_b > 10.0:
        notes.append(f"B 腿重复收盘 {rep_b:.1f}%（陈旧打印）")
    return PairStats(pair, len(ratios), rmean, cv, p95, center, rho, hl_s, ess, k,
                     rep_a, rep_b, stale_a, stale_b, unit_ok, fresh_ok, notes)


# --------------------------------------------------------------------------
# Stage B：盘口 → tick / 深度 / 滑点
# --------------------------------------------------------------------------

@dataclass
class BookLeg:
    leg: Leg
    points: list[BookPoint]
    tick: float
    spread_bps: float
    depth_bid_usd: float
    depth_ask_usd: float
    slip_bps: float
    fee: VenueFees


def build_book_leg(leg: Leg, hours: Sequence[tuple[str, int]],
                   qty_usd: float, band_bps: float, fee: VenueFees,
                   paths: dict[tuple[str, str, str, str], Path]) -> BookLeg | None:
    """orderbook → 重建盘口 → tick / 点差 / 带内深度 / 按规模吃单滑点。"""
    all_points: list[BookPoint] = []
    for day, hour in hours:
        path = paths.get((leg.venue, leg.symbol, f"{day} {hour:02d}", "orderbook"))
        if path is None:
            continue
        df = ChdClient.read_parquet(path)
        if df is None or len(df) == 0:
            continue
        all_points.extend(rebuild_book_series(df, band_bps=band_bps, sample_ms=1000))
    if len(all_points) < 10:
        return None
    all_points.sort(key=lambda p: p.ts_ms)
    tick = mm.infer_tick([p.bid for p in all_points] + [p.ask for p in all_points])
    sp = [(p.ask - p.bid) / p.mid * 10000.0 for p in all_points if p.mid > 0]
    spread = statistics.median(sp) if sp else 0.0
    bids = [p.bid_usd for p in all_points if p.bid_usd > 0]
    asks = [p.ask_usd for p in all_points if p.ask_usd > 0]
    mid = statistics.median([p.mid for p in all_points if p.mid > 0])
    qty = qty_usd / mid if mid > 0 else 0.0
    slip = _walk_slippage(all_points, qty)
    return BookLeg(leg, all_points, tick, spread,
                   statistics.median(bids) if bids else 0.0,
                   statistics.median(asks) if asks else 0.0,
                   slip, fee)


def _walk_slippage(points: Sequence[BookPoint], qty: float) -> float:
    """在盘口序列上按目标数量吃单，取滑点中位数（bps，相对 mid）。

    滑点**只来自盘口档位**（`market_micro.walk_book`），不用价格变动反推。
    """
    if qty <= 0:
        return 0.0
    step = max(1, len(points) // 200)
    samples = points[::step]
    out: list[float] = []
    for p in samples:
        _, slip_buy, filled_buy = mm.walk_book("buy", p.ask_levels, qty, mid=p.mid)
        _, slip_sell, filled_sell = mm.walk_book("sell", p.bid_levels, qty, mid=p.mid)
        best = max(slip_buy, slip_sell)
        if filled_buy > 0 and filled_sell > 0:
            out.append(best)
        elif filled_buy > 0 or filled_sell > 0:
            out.append(slip_buy if filled_buy > 0 else slip_sell)
    return statistics.median(out) if out else 0.0


# --------------------------------------------------------------------------
# 事件级回放（闸门⑤）
# --------------------------------------------------------------------------

@dataclass
class ReplayConfig:
    grid_ms: int = 2000             # 同刻定价网格（两腿用同一毫秒）
    max_age_ms: int = 10000         # 盘口最大陈旧时长
    signal_s: int = 60              # 信号时钟（秒）：带与 σ 在粗时钟上估计
    signal_window: int = 30         # b_hat 粗时钟滚动窗口（桶）
    band_window: int = 450          # （兼容字段，信号窗口由 signal_* 决定）
    entry_z: float = 1.5
    exit_z: float = 0.3
    min_hold_s: int = 30            # 最短持仓（低于 h* 的捕获不可信）
    max_hold_s: int = 900
    ttl_s: int = 60                 # 被动腿等待上限
    drift_z: float = 2.0
    band_bps: float = 25.0


@dataclass
class ReplayResult:
    events: list[Event]
    n_grid: int
    n_valid: int
    stale_frac: float
    maker_leg: str
    taker_leg: str
    notes: list[str] = field(default_factory=list)


@dataclass
class _Grid:
    mid: list[float]
    bid: list[float]
    ask: list[float]
    bid_qty: list[float]      # 买一档挂单量（排队用）
    ask_qty: list[float]
    age_ms: list[int]


def _grid_from_points(points: Sequence[BookPoint], t0: int, t1: int,
                      grid_ms: int, max_age_ms: int) -> _Grid:
    n = int((t1 - t0) / grid_ms) + 1
    mid = [float("nan")] * n
    bid = [float("nan")] * n
    ask = [float("nan")] * n
    bq = [0.0] * n
    aq = [0.0] * n
    age = [10 ** 9] * n
    idx = 0
    for i in range(n):
        ts = t0 + i * grid_ms
        while idx < len(points) and points[idx].ts_ms <= ts:
            idx += 1
        j = idx - 1
        if j < 0:
            continue
        p = points[j]
        d = ts - p.ts_ms
        if d > max_age_ms:
            continue
        mid[i] = p.mid
        bid[i] = p.bid
        ask[i] = p.ask
        bq[i] = p.bid_levels[0][1] if p.bid_levels else 0.0
        aq[i] = p.ask_levels[0][1] if p.ask_levels else 0.0
        age[i] = d
    return _Grid(mid, bid, ask, bq, aq, age)


def replay_pair(a: BookLeg, b: BookLeg, trades_a: LegSeries | None,
                trades_b: LegSeries | None, cfg: ReplayConfig,
                qty_usd: float,
                trade_detail: Sequence[tuple[int, float, float]] = (),
                maker_is_a: bool | None = None,
                trade_detail_b: Sequence[tuple[int, float, float]] = ()) -> ReplayResult:
    """**同刻定价**的事件级回放（两腿同一毫秒的盘口 mid）。

    `maker_is_a=None` → 两种腿角色都跑一遍，取**期望净 EV 更高**的一种
    （腿选择规则：费率 → 点差 → 深度 → 成交概率；成交概率只能靠回放测出来）。

    * 入场：`|basis − b_hat| ≥ entry_z·σ`（fade 方向：basis 偏高 → 卖 A 买 B）；
    * 主动腿吃盘口（滑点来自 walk_book），被动腿按排队模型给 p_fill（我们排队尾）；
    * 出场：`|basis − b_hat| ≤ exit_z·σ`（收敛）或时间止损 `max_hold`；
    * 成本：`funding_fee.b_star`（四笔成交 + carry + 腿风险 + 对账），口径与仓库一致。
    """
    if maker_is_a is None:
        r1 = replay_pair(a, b, trades_a, trades_b, cfg, qty_usd, trade_detail,
                         maker_is_a=True, trade_detail_b=trade_detail_b)
        r2 = replay_pair(a, b, trades_a, trades_b, cfg, qty_usd, trade_detail,
                         maker_is_a=False, trade_detail_b=trade_detail_b)
        s1 = sum(e.net_bps for e in r1.events)
        s2 = sum(e.net_bps for e in r2.events)
        best = r1 if s1 >= s2 else r2
        best.notes = list(best.notes) + [
            f"两种腿角色：A做被动净 {s1:.2f} bps / B做被动净 {s2:.2f} bps → 取更优"]
        return best
    notes: list[str] = []
    # 腿角色：**费率优先**（DECISION.md：费率 → 点差 → 深度 → 成交概率）
    maker, taker = (a, b) if maker_is_a else (b, a)
    maker_series = trades_a if maker_is_a else trades_b
    maker_detail = trade_detail if maker_is_a else trade_detail_b

    t0 = min(a.points[0].ts_ms, b.points[0].ts_ms)
    t1 = max(a.points[-1].ts_ms, b.points[-1].ts_ms)
    if t1 <= t0:
        return ReplayResult([], 0, 0, 1.0, maker.leg.venue, taker.leg.venue, ["盘口窗口为空"])
    n = int((t1 - t0) / cfg.grid_ms) + 1
    if n > 200_000:
        notes.append("窗口过长，已截断")
        n = 200_000

    ga = _grid_from_points(a.points, t0, t0 + (n - 1) * cfg.grid_ms, cfg.grid_ms, cfg.max_age_ms)
    gb = _grid_from_points(b.points, t0, t0 + (n - 1) * cfg.grid_ms, cfg.grid_ms, cfg.max_age_ms)

    ratio: list[float] = []
    for i in range(n):
        ma, mb = ga.mid[i], gb.mid[i]
        ratio.append(ma / mb if (ma == ma and mb == mb and ma > 0 and mb > 0) else float("nan"))
    valid = sum(1 for r in ratio if r == r)
    stale_frac = 1.0 - (valid / n if n else 0.0)
    if valid < 60:
        return ReplayResult([], n, valid, stale_frac, maker.leg.venue, taker.leg.venue,
                            notes + ["有效同刻样本不足（<60 格）"])

    # 信号在**粗时钟**上估计（1 分钟桶的最后一笔），执行仍在细网格上：
    # 否则 σ 会被 2 秒级微观噪声主导 → 阈值过紧 → 「机会很多但每次只抓 3 bps」。
    import pandas as pd
    idx = pd.date_range(start=pd.Timestamp(t0, unit="ms", tz="UTC"),
                        periods=n, freq=f"{cfg.grid_ms}ms")
    rser = pd.Series(ratio, index=idx)
    coarse = rser.resample(f"{cfg.signal_s}s").last().dropna()
    window = max(5, min(cfg.signal_window, len(coarse) // 3 or 5))
    bh_c = coarse.rolling(window, min_periods=max(3, window // 3)).median()
    sig_c = (coarse - bh_c).abs().rolling(window, min_periods=max(3, window // 3)).median() * 1.4826
    drift_c = (bh_c - bh_c.shift(max(1, window // 2))).abs()
    bh = bh_c.reindex(rser.index.union(bh_c.index)).ffill().reindex(rser.index)
    sig = sig_c.reindex(rser.index.union(sig_c.index)).ffill().reindex(rser.index)
    drift = drift_c.reindex(rser.index.union(drift_c.index)).ffill().reindex(rser.index)

    lam_leg = maker_series.trade_rate if maker_series else 0.0
    avg_size = maker_series.avg_trade_size if maker_series else 0.0
    fill_notes: set[str] = set()

    events: list[Event] = []
    i = 0
    while i < n:
        r = ratio[i]
        bhat = bh.iloc[i]
        s = sig.iloc[i]
        if r != r or bhat != bhat or s != s or s <= 0 or r <= 0:
            i += 1
            continue
        dev_bps = (r - float(bhat)) * 10000.0
        sig_bps = float(s) * 10000.0
        if abs(dev_bps) < cfg.entry_z * sig_bps:
            i += 1
            continue
        d = drift.iloc[i]
        if d == d and float(d) * 10000.0 > cfg.drift_z * sig_bps:
            i += 1
            continue
        entry_basis = (r - 1.0) * 10000.0
        limit = min(n, i + int(cfg.max_hold_s * 1000 / cfg.grid_ms))
        min_exit = min(limit, i + max(1, int(cfg.min_hold_s * 1000 / cfg.grid_ms)))
        exit_i = None
        converged = False
        j = min_exit
        while j < limit:
            rj, bhj, sj = ratio[j], bh.iloc[j], sig.iloc[j]
            if rj != rj or bhj != bhj or sj != sj or sj <= 0:
                j += 1
                continue
            if abs((rj - float(bhj)) * 10000.0) <= cfg.exit_z * float(sj) * 10000.0:
                exit_i = j
                converged = True
                break
            j += 1
        if exit_i is None:
            exit_i = limit - 1
        if exit_i <= i or ratio[exit_i] != ratio[exit_i]:
            i = max(i + 1, exit_i)
            continue
        exit_basis = (ratio[exit_i] - 1.0) * 10000.0
        capture = (entry_basis - exit_basis) if dev_bps > 0 else (exit_basis - entry_basis)
        hold_s = (exit_i - i) * cfg.grid_ms / 1000.0

        # 被动腿：贴价挂单 → 排队成交概率（我们排在队尾）
        m_grid = ga if maker_is_a else gb
        m_mid = m_grid.mid[i]
        m_bid, m_ask = m_grid.bid[i], m_grid.ask[i]
        # 方向：A 的持仓 s = -1（basis 偏高时卖 A）
        s_a = -1.0 if dev_bps > 0 else 1.0
        maker_side = "buy" if (s_a > 0) == maker_is_a else "sell"
        touch_px = m_bid if maker_side == "buy" else m_ask
        queue_ahead = m_grid.bid_qty[i] if maker_side == "buy" else m_grid.ask_qty[i]
        qty_base = (qty_usd / m_mid) if m_mid else 0.0
        # TTL 内**穿过我们价位**的成交量 → 排队成交概率
        vol, n_trades = volume_through(maker_detail, t0 + i * cfg.grid_ms,
                                       t0 + i * cfg.grid_ms + cfg.ttl_s * 1000,
                                       touch_px, maker_side)
        if n_trades > 0:
            lam = n_trades / cfg.ttl_s          # 笔/秒
            avg_size = vol / n_trades          # 均单量（base unit）
            p_fill = mm.fill_probability(queue_ahead, lam, cfg.ttl_s, qty_base, avg_size)
        else:
            p_fill = 0.0
            fill_notes.add("TTL 内无成交穿过被动腿价位 → p_fill=0")
        if avg_size <= 0:
            fill_notes.add("被动腿无成交样本")

        maker_mid = m_mid if m_mid and m_mid == m_mid else 1.0
        taker_grid = gb if maker_is_a else ga
        taker_mid = taker_grid.mid[i] if taker_grid.mid[i] == taker_grid.mid[i] else 1.0
        cost = _round_cost(maker, taker, qty_usd, hold_s, maker_mid, taker_mid)
        net = (capture - cost) * p_fill
        exposure = qty_usd * hold_s * (1.0 - p_fill)   # USD·s（被动腿没成交时的裸腿暴露）
        sess = classify(t0 + i * cfg.grid_ms).session
        events.append(Event(t0 + i * cfg.grid_ms, sess, entry_basis, exit_basis,
                            capture, float(cost), float(net), p_fill, hold_s,
                            converged, float(exposure),
                            f"{maker.leg.venue}:{maker.leg.symbol}",
                            f"{taker.leg.venue}:{taker.leg.symbol}"))
        i = exit_i + 1
    notes.extend(sorted(fill_notes))
    if not events:
        notes.append("窗口内无入场事件")
    return ReplayResult(events, n, valid, stale_frac, maker.leg.venue, taker.leg.venue,
                        notes)


def _round_cost(maker: BookLeg, taker: BookLeg, qty_usd: float, hold_s: float,
                maker_mid: float, taker_mid: float) -> float:
    m_spec = LegSpec(venue=maker.leg.venue, maker_bps=maker.fee.maker_bps,
                      taker_bps=maker.fee.taker_bps,
                      half_spread_bps=Decimal(str(maker.spread_bps / 2)),
                      price=Decimal(str(maker_mid or 1)), tick=Decimal(str(maker.tick or 0)),
                      depth_bid_usd=Decimal(str(maker.depth_bid_usd)),
                      depth_ask_usd=Decimal(str(maker.depth_ask_usd)))
    t_spec = LegSpec(venue=taker.leg.venue, maker_bps=taker.fee.maker_bps,
                      taker_bps=taker.fee.taker_bps,
                      half_spread_bps=Decimal(str(taker.spread_bps / 2)),
                      price=Decimal(str(taker_mid or 1)), tick=Decimal(str(taker.tick or 0)),
                      depth_bid_usd=Decimal(str(taker.depth_bid_usd)),
                      depth_ask_usd=Decimal(str(taker.depth_ask_usd)))
    return float(b_star(m_spec, t_spec, qty_usd=Decimal(str(qty_usd)),
                        hold_hours=Decimal(str(hold_s / 3600.0)),
                        leader_slip_bps=Decimal(str(maker.slip_bps)),
                        follower_slip_bps=Decimal(str(taker.slip_bps))))


# --------------------------------------------------------------------------
# 会话分层 / 机会次数
# --------------------------------------------------------------------------

# 每个会话在**一个交易日**里的典型小时数（NYSE 日历，见 nt_pairs.sessions）
SESSION_HOURS_PER_DAY = {"rth": 6.5, "pre": 5.5, "post": 4.0, "closed": 24.0}


def summarize(events: Sequence[Event], days: int) -> dict[str, Any]:
    """按会话分层：事件数、胜率、净 EV、**日均机会次数**。

    「日均机会次数」= 观测窗口内该会话的事件数，按**该会话覆盖小时数**折算到
    一个完整交易日（窗口短时外推，样本小 → 报告里必须同时给出覆盖小时数）。
    """
    buckets: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        buckets[e.session].append(e)
    out: dict[str, Any] = {}
    for sess, evs in sorted(buckets.items()):
        n = len(evs)
        wins = sum(1 for e in evs if e.net_bps > 0)
        net_sum = sum(e.net_bps for e in evs)
        gross_sum = sum(e.capture_bps for e in evs)
        covered_h = len({e.ts_ms // 3_600_000 for e in evs})
        typical_h = SESSION_HOURS_PER_DAY.get(sess, 24.0)
        scale = (typical_h / covered_h) if covered_h else 0.0
        out[sess] = {
            "events": n,
            "covered_session_hours": covered_h,
            "opportunities_per_day": n * scale,
            "win_rate": wins / n if n else 0.0,
            "net_bps_total": net_sum,
            "net_bps_per_event": net_sum / n if n else 0.0,
            "gross_capture_bps_per_event": gross_sum / n if n else 0.0,
            "mean_hold_s": sum(e.hold_s for e in evs) / n if n else 0.0,
            "mean_p_fill": sum(e.p_fill for e in evs) / n if n else 0.0,
            "max_leg_exposure_usd_s": max((e.leg_exposure_usd_s for e in evs), default=0.0),
        }
    return out


def summarize_by_day(events: Sequence[Event]) -> dict[str, Any]:
    """按**交易日**拆分：逐日净 EV、胜率、事件数。

    「跑两周」的验收标准是**逐日稳定**，不是聚合平均好看：
    聚合为正但只有 1 天在赚、其余 13 天小亏，是不可接受的。
    所以这里按交易日（NYSE 日历）拆分，并给出「正 EV 天数占比」。
    """
    buckets: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        buckets[classify(e.ts_ms).trading_date.isoformat()].append(e)
    out: dict[str, Any] = {}
    for day, evs in sorted(buckets.items()):
        n = len(evs)
        net_sum = sum(e.net_bps for e in evs)
        out[day] = {
            "events": n,
            "win_rate": sum(1 for e in evs if e.net_bps > 0) / n if n else 0.0,
            "net_bps_total": round(net_sum, 2),
            "net_bps_per_event": round(net_sum / n, 2) if n else 0.0,
            "gross_capture_bps_per_event": round(sum(e.capture_bps for e in evs) / n, 2) if n else 0.0,
            "mean_p_fill": round(sum(e.p_fill for e in evs) / n, 3) if n else 0.0,
        }
    pos_days = sum(1 for v in out.values() if v["net_bps_total"] > 0)
    out["_summary"] = {
        "days": len(out),
        "positive_days": pos_days,
        "positive_day_ratio": round(pos_days / len(out), 3) if out else 0.0,
    }
    return out


def count_trading_days(t0_ms: int, t1_ms: int) -> int:
    days = set()
    t = t0_ms
    while t <= t1_ms:
        days.add(classify(t).trading_date)
        t += 3600_000
    return max(1, len({d for d in days if d.weekday() < 5}))


def pfill_scenarios(sess: dict[str, Any]) -> dict[str, Any]:
    """p_fill 情景：保守（0.5×）、中性（1×）、乐观（1.5×，上限 1）。"""
    out: dict[str, Any] = {}
    for name, mult in (("conservative", 0.5), ("neutral", 1.0), ("optimistic", 1.5)):
        total = 0.0
        per_session = {}
        for k, v in sess.items():
            p = min(1.0, v["mean_p_fill"] * mult)
            net = (v["gross_capture_bps_per_event"] - v["net_bps_per_event"] / max(v["mean_p_fill"], 1e-9)) \
                if v["mean_p_fill"] > 0 else 0.0
            # 净 EV 随 p_fill 线性：用「每单位 p_fill 的净」重算
            unit_net = v["net_bps_per_event"] / v["mean_p_fill"] if v["mean_p_fill"] > 0 else 0.0
            per_session[k] = unit_net * p
            total += unit_net * p * v["events"]
        out[name] = {"per_session_net_bps_per_event": per_session, "total_net_bps": total}
    return out


# --------------------------------------------------------------------------
# 候选构造
# --------------------------------------------------------------------------

def build_candidates(venues: Sequence[str],
                     symbols_by_venue: dict[str, list[str]],
                     extra_bases: Sequence[str] = ()) -> list[Pair]:
    """同名跨场所配对（含 HL 内部跨 deployer）。

    `extra_bases` 里的 base **额外**允许进入候选（默认空 → 行为不变）。
    """
    allow = {b.strip().upper() for b in extra_bases if b.strip()}
    by_base: dict[str, list[Leg]] = defaultdict(list)
    for venue in venues:
        for sym in symbols_by_venue.get(venue, []):
            base = base_of(sym)
            if allow:
                if base not in allow:
                    continue
            elif base not in EQUITY_BASES_ALL:
                continue
            prefix = sym.split(":", 1)[0] if ":" in sym else ""
            by_base[base].append(Leg(venue, sym, base, prefix))
    pairs: list[Pair] = []
    for base, legs in sorted(by_base.items()):
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                x, y = legs[i], legs[j]
                if x.venue == y.venue and x.prefix == y.prefix:
                    continue
                pairs.append(Pair(f"{x.venue}:{x.symbol}~{y.venue}:{y.symbol}", x, y, base))
    return pairs


# --------------------------------------------------------------------------
# 输出行
# --------------------------------------------------------------------------

def _row_a(st: PairStats) -> dict[str, Any]:
    return {
        "key": st.pair.key, "base": st.pair.base,
        "a": f"{st.pair.a.venue}:{st.pair.a.symbol}",
        "b": f"{st.pair.b.venue}:{st.pair.b.symbol}",
        "n_overlap": st.n_overlap, "ratio_mean": st.ratio_mean, "ratio_cv": st.ratio_cv,
        "basis_p95_dev": st.basis_p95_dev, "basis_center": st.basis_center,
        "autocorr1": st.autocorr1, "half_life_s": st.half_life_s, "ess": st.ess, "k": st.k,
        "repeat_a_pct": st.repeat_a, "repeat_b_pct": st.repeat_b,
        "stale_a_pct": st.stale_a, "stale_b_pct": st.stale_b,
        "gate1_unit": st.unit_ok, "gate2_fresh": st.fresh_ok, "notes": st.notes,
    }


def _row(p: Pair, st: PairStats | None, ba: BookLeg | None, bb: BookLeg | None,
         rep: ReplayResult | None, qty_usd: float, err: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"key": p.key, "base": p.base,
                            "a": f"{p.a.venue}:{p.a.symbol}", "b": f"{p.b.venue}:{p.b.symbol}"}
    if st is not None:
        row["stage_a"] = _row_a(st)
    if err:
        row["error"] = err
        return row
    if ba is None or bb is None:
        row["error"] = "盘口数据缺失"
        return row
    row["book"] = {
        "a": {"tick": ba.tick, "spread_bps": ba.spread_bps, "depth_bid_usd": ba.depth_bid_usd,
              "depth_ask_usd": ba.depth_ask_usd, "slip_bps": ba.slip_bps,
              "fee_maker_bps": float(ba.fee.maker_bps), "fee_taker_bps": float(ba.fee.taker_bps)},
        "b": {"tick": bb.tick, "spread_bps": bb.spread_bps, "depth_bid_usd": bb.depth_bid_usd,
              "depth_ask_usd": bb.depth_ask_usd, "slip_bps": bb.slip_bps,
              "fee_maker_bps": float(bb.fee.maker_bps), "fee_taker_bps": float(bb.fee.taker_bps)},
    }
    depth_min = min(min(ba.depth_bid_usd, ba.depth_ask_usd),
                    min(bb.depth_bid_usd, bb.depth_ask_usd))
    row["depth_min_usd"] = depth_min
    row["gate4_capacity"] = bool(depth_min >= 10 * qty_usd)
    bst = _b_star_for(ba, bb, qty_usd)
    row["b_star_bps"] = bst
    if st is not None and bst:
        row["gate3_cost_cover"] = bool(st.basis_p95_dev >= st.k * bst)
        row["coverage_ratio"] = (st.basis_p95_dev / (st.k * bst)) if bst > 0 else None
    if rep is not None:
        days = count_trading_days(ba.points[0].ts_ms, ba.points[-1].ts_ms)
        sess = summarize(rep.events, days)
        row["replay"] = {
            "n_events": len(rep.events), "grid_points": rep.n_grid,
            "valid_points": rep.n_valid, "stale_frac": rep.stale_frac,
            "maker_leg": rep.maker_leg, "taker_leg": rep.taker_leg,
            "trading_days": days, "by_session": sess,
            "by_day": summarize_by_day(rep.events),
            "pfill_scenarios": pfill_scenarios(sess),
            "notes": rep.notes,
            "events": [
                {"ts_ms": e.ts_ms, "session": e.session, "entry_basis": e.entry_basis,
                 "exit_basis": e.exit_basis, "capture_bps": e.capture_bps,
                 "cost_bps": e.cost_bps, "net_bps": e.net_bps, "p_fill": e.p_fill,
                 "hold_s": e.hold_s, "converged": e.converged,
                 "leg_exposure_usd_s": e.leg_exposure_usd_s}
                for e in rep.events
            ],
        }
        row["gate5_net_ev"] = any(v["net_bps_per_event"] > 0 for v in sess.values())
        row["net_ev_bps_total"] = sum(v["net_bps_total"] for v in sess.values())
        row["opportunities_per_day_total"] = len(rep.events) / days if days else 0.0
    return row


def _b_star_for(ba: BookLeg, bb: BookLeg, qty_usd: float) -> float:
    m_spec = LegSpec(venue=ba.leg.venue, maker_bps=ba.fee.maker_bps, taker_bps=ba.fee.taker_bps,
                      half_spread_bps=Decimal(str(ba.spread_bps / 2)),
                      price=Decimal("1"), tick=Decimal(str(ba.tick)),
                      depth_bid_usd=Decimal(str(ba.depth_bid_usd)),
                      depth_ask_usd=Decimal(str(ba.depth_ask_usd)))
    t_spec = LegSpec(venue=bb.leg.venue, maker_bps=bb.fee.maker_bps, taker_bps=bb.fee.taker_bps,
                      half_spread_bps=Decimal(str(bb.spread_bps / 2)),
                      price=Decimal("1"), tick=Decimal(str(bb.tick)),
                      depth_bid_usd=Decimal(str(bb.depth_bid_usd)),
                      depth_ask_usd=Decimal(str(bb.depth_ask_usd)))
    return float(b_star(m_spec, t_spec, qty_usd=Decimal(str(qty_usd)),
                         hold_hours=Decimal("0.25"),
                         leader_slip_bps=Decimal(str(ba.slip_bps)),
                         follower_slip_bps=Decimal(str(bb.slip_bps))))


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def write_report(payload: dict[str, Any], path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    L.append("# CHD 全市场扫描 + 事件级 EV 回测报告\n")
    L.append(f"生成时间：{payload['generated_at']}　窗口：{payload['window']['start']} → "
             f"{payload['window']['end']}　单笔名义：${payload['config']['qty_usd']:.0f}/腿　"
             f"回放窗口：{payload['window']['replay_hours']} 小时\n")
    a_rows = payload["stage_a"]
    L.append("## 1. 候选宇宙\n")
    L.append(f"- 候选对（股票/ETF/pre-IPO 同名跨场所 + HL 内部跨 deployer）："
             f"**{payload['n_candidate_pairs']}** 对")
    L.append(f"- Stage A 有同刻重叠样本的对："
             f"**{sum(1 for r in a_rows if r.get('n_overlap', 0) > 0)}** 对")
    g1 = [r for r in a_rows if r.get("gate1_unit")]
    g12 = [r for r in a_rows if r.get("gate1_unit") and r.get("gate2_fresh")]
    L.append(f"- 过闸门①（单位一致 \\|ratio−1\\|≤5%）：**{len(g1)}** 对")
    L.append(f"- 过闸门①②（+ 重复收盘率 ≤10%）：**{len(g12)}** 对\n")

    L.append("## 2. Stage B（盘口）与事件级 EV\n")
    rows = payload.get("stage_b") or []
    if not rows:
        L.append("（未跑 Stage B 或无候选进入）\n")
    else:
        L.append("| 对 | b* bps | p95覆盖倍数 | 最小深度 US$ | 事件数 | 日均机会/会话 | 胜率 | 净EV/事件 bps | 判定 |")
        L.append("|---|---:|---:|---:|---:|---|---:|---:|---|")
        for r in rows:
            rep = r.get("replay") or {}
            sess = rep.get("by_session") or {}
            ev = rep.get("n_events", 0)
            win = max([v["win_rate"] for v in sess.values()], default=0.0)
            net = max([v["net_bps_per_event"] for v in sess.values()], default=0.0)
            opp = "，".join(f"{k}:{v['opportunities_per_day']:.2f}" for k, v in sess.items())
            bad = []
            if r.get("gate3_cost_cover") is False:
                bad.append("③成本覆盖不过")
            if r.get("gate4_capacity") is False:
                bad.append("④容量不过")
            if r.get("gate5_net_ev") is False:
                bad.append("⑤净EV≤0")
            if r.get("error"):
                bad.append(r["error"])
            L.append(f"| {r['key']} | {r.get('b_star_bps') or 0:.1f} | "
                     f"{(r.get('coverage_ratio') or 0):.2f} | {r.get('depth_min_usd', 0):,.0f} | "
                     f"{ev} | {opp} | {win:.0%} | {net:.2f} | {'；'.join(bad) or '通过'} |")
        L.append("")
        L.append("### 2.1 p_fill 情景（净 EV 对 p_fill 的线性敏感性）\n")
        L.append("| 对 | 保守(0.5×) | 中性(1×) | 乐观(1.5×) |")
        L.append("|---|---:|---:|---:|")
        for r in rows:
            sc = (r.get("replay") or {}).get("pfill_scenarios") or {}
            if not sc:
                continue
            L.append(f"| {r['key']} | {sc.get('conservative', {}).get('total_net_bps', 0):.1f} | "
                     f"{sc.get('neutral', {}).get('total_net_bps', 0):.1f} | "
                     f"{sc.get('optimistic', {}).get('total_net_bps', 0):.1f} |")
        L.append("")
        L.append("### 2.2 逐交易日（跑两周的验收标准是逐日稳定，不是聚合平均）\n")
        any_day = False
        for r in rows:
            by_day = (r.get("replay") or {}).get("by_day") or {}
            summary = by_day.get("_summary") or {}
            days = {k: v for k, v in by_day.items() if not k.startswith("_")}
            if not days:
                continue
            any_day = True
            L.append(f"**{r['key']}** —— 正 EV 天数 {summary.get('positive_days', 0)}/"
                     f"{summary.get('days', 0)}（{summary.get('positive_day_ratio', 0):.0%}）\n")
            L.append("| 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |")
            L.append("|---|---:|---:|---:|---:|---:|")
            for day, v in days.items():
                L.append(f"| {day} | {v['events']} | {v['win_rate']:.0%} | "
                         f"{v['net_bps_per_event']:+.2f} | {v['gross_capture_bps_per_event']:.2f} | "
                         f"{v['mean_p_fill']:.3f} |")
            L.append("")
        if not any_day:
            L.append("（本次窗口不足一个完整交易日，或回放未产生事件。）\n")
    L.append("## 3. 口径与警告\n")
    L.append("- 两腿**同刻定价**（同一毫秒盘口 mid），不使用 bar open/close 跨瞬间取价。")
    L.append("- 缺行**不当连续时间**：固定网格 + 记录 stale 占比。")
    L.append("- 滑点来自**盘口档位**（`market_micro.walk_book`），tick 由 `infer_tick(book)` 反推。")
    L.append("- 胜率 >90% 的行必须对照重复收盘率与成交后 markout 再谈 EV。")
    L.append("- 被动腿 p_fill 是**排队模型估计**（排队尾），未经真实 fill 日志标定 → "
             "报告里的 EV 是**上界**。")
    p.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CryptoHFTData 全市场扫描 + 事件级 EV 回测")
    ap.add_argument("--hours", type=int, default=24, help="Stage A 扫描窗口（最近 N 小时）")
    ap.add_argument("--replay-hours", type=int, default=2, help="Stage B 盘口回放窗口（小时）")
    ap.add_argument("--pairs", type=str, default="",
                    help="只看指定对，逗号分隔，如 hyperliquid_futures:XYZ:SOXL,lighter:SOXL")
    ap.add_argument("--qty", type=float, default=500.0, help="目标单笔名义（USD/腿）")
    ap.add_argument("--venues", type=str, default=",".join(VENUES))
    ap.add_argument("--bases", type=str, default="",
                    help="只扫这些 base（逗号分隔，**白名单**，覆盖 EQUITY_BASES_ALL 名单）；"
                         "默认空 = 原名单 EQUITY_BASES_ALL（行为不变）")
    ap.add_argument("--grid-s", type=int, default=60, help="Stage A 网格（秒）")
    ap.add_argument("--band-bps", type=float, default=25.0, help="深度统计带宽（±bps）")
    ap.add_argument("--entry-z", type=float, default=1.5,
                    help="入场阈值：|basis−b̂| ≥ entry_z·σ（越大机会越少、捕获越大）")
    ap.add_argument("--exit-z", type=float, default=0.3,
                    help="出场阈值：|basis−b̂| ≤ exit_z·σ 视为收敛")
    ap.add_argument("--ttl-s", type=int, default=60, help="被动腿等待上限（秒）")
    ap.add_argument("--max-hold-s", type=int, default=900, help="时间止损（秒）")
    ap.add_argument("--max-replay-pairs", type=int, default=8, help="Stage B 最多回放多少对")
    ap.add_argument("--cache-dir", type=str, default="data/chd_cache")
    ap.add_argument("--out", type=str, default="data/chd_screen.json")
    ap.add_argument("--report", type=str, default="docs/CHD_SCREEN.md")
    ap.add_argument("--no-orderbook", action="store_true", help="只跑 Stage A")
    ap.add_argument("--workers", type=int, default=12, help="并行下载线程数")
    ap.add_argument("--replay-rth", action="store_true",
                    help="回放窗口强制用最近一个 RTH 时段（默认：窗口无 RTH 时自动切换）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    venues = [v.strip() for v in args.venues.split(",") if v.strip()]
    client = ChdClient(cache_dir=args.cache_dir)
    now = utcnow()
    # 数据按整点发布，当前小时尚未落盘 → 窗口右端取上一小时
    hours_all = hour_range(now - timedelta(hours=1), args.hours)
    hours_replay = hours_all[-max(1, args.replay_hours):]
    if args.replay_rth:
        rth = last_rth_hours(now, args.replay_hours)
        if rth:
            hours_replay = rth
    elif not any(classify(int(datetime.strptime(f"{d} {h:02d}:00", "%Y-%m-%d %H:%M")
                                .replace(tzinfo=timezone.utc).timestamp() * 1000)).session == "rth"
                 for d, h in hours_replay):
        rth = last_rth_hours(now, args.replay_hours)
        if rth:
            hours_replay = rth
            print(f"      最近的窗口没有 RTH → 回放窗口改用最近 RTH 时段：{rth[0]} … {rth[-1]}")

    print(f"[1/5] 拉取场所符号清单 …（{now:%Y-%m-%d %H:%M} UTC 起，最近 {args.hours} 小时）")
    symbols_by_venue: dict[str, list[str]] = {}
    for v in venues:
        try:
            symbols_by_venue[v] = client.symbols(v, "orderbook")
            print(f"      {v}: {len(symbols_by_venue[v])} 个符号")
        except Exception as exc:  # noqa: BLE001
            print(f"      [warn] {v} 符号清单失败：{exc}")
            symbols_by_venue[v] = []

    pairs = build_candidates(venues, symbols_by_venue, extra_bases=args.bases.split(","))
    if args.pairs:
        want = {p.strip() for p in args.pairs.split(",") if p.strip()}
        want_legs = {w for w in want if any(w.startswith(f"{v}:") for v in VENUES)}
        want_bases = want - want_legs

        def match(p: Pair) -> bool:
            la = f"{p.a.venue}:{p.a.symbol}"
            lb = f"{p.b.venue}:{p.b.symbol}"
            if p.base in want_bases or p.key in want:
                return True
            if la in want_legs and lb in want_legs:
                return True
            if (la in want_legs and p.b.base in want_bases) or \
                    (lb in want_legs and p.a.base in want_bases):
                return True
            return False

        pairs = [p for p in pairs if match(p)]
    print(f"[2/5] 候选对：{len(pairs)}（股票/ETF/pre-IPO 同名跨场所 + HL 内部跨 deployer）")

    # ------------------------------------------------------------- Stage A
    t_start = time.time()
    legs = sorted({(p.a.venue, p.a.symbol) for p in pairs} |
                  {(p.b.venue, p.b.symbol) for p in pairs})
    jobs = [(venue, day, hour, symbol, "trades")
            for (venue, symbol) in legs for (day, hour) in hours_all]
    print(f"      下载 trades：{len(jobs)} 个文件（{len(legs)} 条腿 × {len(hours_all)} 小时）…")
    trade_paths = prefetch(client, jobs, workers=args.workers, verbose=args.verbose)
    print(f"      下载完成 {len(trade_paths)}/{len(jobs)}，用时 {time.time() - t_start:.0f}s")

    # 回放窗口可能落在 hours_all 之外（例如「最近一个 RTH 时段」是几天前），
    # 那时被动腿的逐笔成交明细会缺失 → p_fill 恒为 0 → 净 EV 恒为 0（假阴性）。
    # 所以回放窗口的 trades 必须单独补齐。
    replay_jobs = [(venue, day, hour, symbol, "trades")
                   for (venue, symbol) in legs
                   for (day, hour) in hours_replay
                   if (venue, symbol, f"{day} {hour:02d}", "trades") not in trade_paths]
    if replay_jobs:
        print(f"      补下载回放窗口 trades：{len(replay_jobs)} 个文件 …")
        trade_paths.update(prefetch(client, replay_jobs, workers=args.workers,
                                    verbose=args.verbose))
        print(f"      回放窗口 trades 补齐，共 {len(trade_paths)} 个文件")

    series_cache: dict[tuple[str, str], LegSeries | None] = {}
    stats_list: list[PairStats] = []
    for idx, p in enumerate(pairs):
        ka, kb = (p.a.venue, p.a.symbol), (p.b.venue, p.b.symbol)
        if ka not in series_cache:
            series_cache[ka] = load_leg_series(p.a, hours_all, args.grid_s, trade_paths)
        if kb not in series_cache:
            series_cache[kb] = load_leg_series(p.b, hours_all, args.grid_s, trade_paths)
        sa, sb = series_cache[ka], series_cache[kb]
        if sa is None or sb is None:
            stats_list.append(PairStats(p, 0, float("nan"), float("nan"), float("nan"),
                                        float("nan"), 0.0, 0.0, 0.0, float("inf"),
                                        0.0, 0.0, 0.0, 0.0, False, False,
                                        ["缺 trades 数据"]))
            continue
        stats_list.append(pair_stats(p, sa, sb, args.grid_s))
        if (idx + 1) % 25 == 0:
            print(f"      Stage A {idx + 1}/{len(pairs)} …")
    print(f"[3/5] Stage A 完成：{len(stats_list)} 对，用时 {time.time() - t_start:.0f}s")
    ok12 = [s for s in stats_list if s.unit_ok and s.fresh_ok]
    print(f"      过闸门①②：{len(ok12)}")

    # ------------------------------------------------------------- Stage B
    results: list[dict[str, Any]] = []
    if not args.no_orderbook and ok12:
        ranked = sorted(ok12, key=lambda s: -(s.basis_p95_dev if math.isfinite(s.basis_p95_dev) else 0.0))
        shortlist = ranked[: args.max_replay_pairs]
        print(f"[4/5] Stage B：盘口回放 {len(shortlist)} 对（最近 {len(hours_replay)} 小时）")
        hl_cache: dict[str, dict] = {}
        ob_legs = sorted({(st.pair.a.venue, st.pair.a.symbol) for st in shortlist} |
                         {(st.pair.b.venue, st.pair.b.symbol) for st in shortlist})
        ob_jobs = [(venue, day, hour, symbol, "orderbook")
                   for (venue, symbol) in ob_legs for (day, hour) in hours_replay]
        print(f"      下载 orderbook：{len(ob_jobs)} 个文件 …")
        ob_paths = prefetch(client, ob_jobs, workers=args.workers, verbose=args.verbose)
        print(f"      下载完成 {len(ob_paths)}/{len(ob_jobs)}")
        for st in shortlist:
            p = st.pair
            try:
                fa = fees_for(p.a, hl_cache)
                fb = fees_for(p.b, hl_cache)
                ba = build_book_leg(p.a, hours_replay, args.qty, args.band_bps, fa, ob_paths)
                bb = build_book_leg(p.b, hours_replay, args.qty, args.band_bps, fb, ob_paths)
                if ba is None or bb is None:
                    results.append(_row(p, st, None, None, None, args.qty, "盘口数据缺失"))
                    continue
                cfg = ReplayConfig(band_bps=args.band_bps, entry_z=args.entry_z,
                                   exit_z=args.exit_z, ttl_s=args.ttl_s,
                                   max_hold_s=args.max_hold_s)
                detail_a = load_trade_detail(p.a, hours_replay, trade_paths)
                detail_b = load_trade_detail(p.b, hours_replay, trade_paths)
                rep = replay_pair(ba, bb, series_cache.get((p.a.venue, p.a.symbol)),
                                  series_cache.get((p.b.venue, p.b.symbol)), cfg, args.qty,
                                  trade_detail=detail_a, trade_detail_b=detail_b)
                results.append(_row(p, st, ba, bb, rep, args.qty, None))
                print(f"      {p.key}: 事件 {len(rep.events)}，"
                      f"净EV/事件 {results[-1].get('replay', {}).get('by_session', {})}")
            except Exception as exc:  # noqa: BLE001
                results.append(_row(p, st, None, None, None, args.qty, repr(exc)))
                if args.verbose:
                    import traceback
                    traceback.print_exc()
    elif args.no_orderbook:
        print("[4/5] Stage B 跳过（--no-orderbook）")

    # --------------------------------------------------------------- 输出
    payload = {
        "generated_at": now.isoformat(),
        "window": {"hours": args.hours, "replay_hours": args.replay_hours,
                   "start": f"{hours_all[0][0]} {hours_all[0][1]:02d}:00",
                   "end": f"{hours_all[-1][0]} {hours_all[-1][1]:02d}:00"},
        "config": {"qty_usd": args.qty, "grid_s": args.grid_s, "band_bps": args.band_bps,
                   "venues": venues, "max_replay_pairs": args.max_replay_pairs},
        "n_candidate_pairs": len(pairs),
        "stage_a": [_row_a(s) for s in stats_list],
        "stage_b": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"[5/5] 已写出 {out_path} 与 {args.report}")
    write_report(payload, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
