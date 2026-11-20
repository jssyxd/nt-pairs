#!/usr/bin/env python
"""双腿基差「回归(fade)」回测 —— NautilusTrader 2.0 原生引擎 + 真实公开行情。

用途：给可视化座舱（``scripts/make_tearsheet.py``）产出**真实的 Nautilus BacktestResult**。

数据（只用公开端点，无任何凭据）：
  - leader   : Hyperliquid ``xyz:SOXL`` 5m K 线（``api.hyperliquid.xyz/info``）
  - follower : Lighter    ``SOXL``(market_id=197) 5m K 线（``mainnet.zklighter.elliot.ai``）

策略（与仓库 ``nt_pairs`` 的带状回归同构，简化版）：
  - b(t) = (leader/follower - 1) × 1e4  (bps)
  - z(t) = (b(t) - mean_N(b)) / std_N(b)        # N 根滚动窗口
  - z > entry_z → **做空基差**（卖 leader / 买 follower）；z < -entry_z → 做多基差
  - |z| < exit_z → 平掉两腿

成本：两腿各自的 maker/taker 费率（HL HIP-3 口径见 ``nt_pairs.costs``）+ 每笔 1 tick 滑点。

用法::

    python scripts/run_pair_backtest.py --days 14 --out data/cockpit/backtest_stats.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from decimal import Decimal
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.execution import MakerTakerFeeModel, OneTickSlippageFillModel
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarAggregation,
    BarSpecification,
    BarType,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    OmsType,
    OrderSide,
    Price,
    PriceType,
    Quantity,
    Symbol,
    TimeInForce,
    Venue,
)
from nautilus_trader.trading import Strategy

USD = Currency.from_str("USD")
VENUE_LEADER = Venue("HYPERLIQUID")
VENUE_FOLLOWER = Venue("LIGHTER")
IID_LEADER = InstrumentId.from_str("SOXL-PERP.HYPERLIQUID")
IID_FOLLOWER = InstrumentId.from_str("SOXL-PERP.LIGHTER")

HL_INFO = "https://api.hyperliquid.xyz/info"
LIGHTER_CANDLES = "https://mainnet.zklighter.elliot.ai/api/v1/candles"

#: HL HIP-3 实际费率（deployerFeeScale=1.0、growthMode=on → base×2×0.1）
HL_MAKER_BPS = Decimal("0.30")
HL_TAKER_BPS = Decimal("0.90")
#: Lighter 费率按 0 计（field-guide 实测 Lighter maker/taker = 0）
LIGHTER_MAKER_BPS = Decimal("0")
LIGHTER_TAKER_BPS = Decimal("0")


def _http(url: str, payload: dict | None = None) -> Any:
    if payload is None:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
    with urllib.request.urlopen(req, timeout=45) as fh:
        return json.load(fh)


def fetch_hl_candles(coin: str, interval: str, days: int) -> dict[int, tuple]:
    now = int(time.time() * 1000)
    start = now - days * 86400 * 1000
    rows = _http(HL_INFO, {"type": "candleSnapshot",
                           "req": {"coin": coin, "interval": interval,
                                   "startTime": start, "endTime": now}})
    step = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}[interval]
    out: dict[int, tuple] = {}
    for c in rows:
        if isinstance(c, dict) and "t" in c:
            ts = int(c["t"]) // step * step // 1000  # 统一成「秒」
            out[ts] = (float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c.get("v", 0.0)))
    return out


def fetch_lighter_candles(market_id: int, resolution: str, days: int, chunk: int = 400) -> dict[int, tuple]:
    step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[resolution]
    now = int(time.time())
    start = now - days * 86400
    out: dict[int, tuple] = {}
    cursor = start
    while cursor < now:
        end = min(cursor + chunk * step, now)
        url = (f"{LIGHTER_CANDLES}?market_id={market_id}&resolution={resolution}"
               f"&start_timestamp={cursor}&end_timestamp={end}&count_back=2")
        payload = _http(url)
        rows = payload if isinstance(payload, list) else (payload.get("c") or payload.get("data") or [])
        if not rows:
            break
        for c in rows:
            ts = int(c["t"]) // 1000 // step * step
            out[ts] = (float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c.get("v", 0.0)))
        cursor = end + 1
    return out


def make_instrument(instrument_id: InstrumentId) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol("SOXL"),
        base_currency=USD,
        quote_currency=USD,
        settlement_currency=USD,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price(0.01, 2),
        size_increment=Quantity(0.001, 3),
        ts_event=0,
        ts_init=0,
    )


class FadeConfig(StrategyConfig):
    """回归策略配置。

    2.0 注意事项：``StrategyConfig`` 是 Cython 类，Python 子类**不能**转发 kwargs 给
    ``super().__init__()``（会炸 ``object.__init__() takes exactly one argument``）；
    自定义字段在 ``super().__init__()`` 之后按属性赋值即可。``strategy_id`` 留空，
    由 trader 自动分配（S-000）。
    """

    def __init__(self, *, leader: str, follower: str, qty: str = "5",
                 window: int = 288, entry_z: float = 1.5, exit_z: float = 0.3) -> None:
        super().__init__()
        self.leader = leader
        self.follower = follower
        self.qty = qty
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z


class FadeStrategy(Strategy):
    """两腿基差回归（fade）：z 超阈值入场，回到带内平仓。"""

    def __init__(self, config: FadeConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self.leader_iid = InstrumentId.from_str(config.leader)
        self.follower_iid = InstrumentId.from_str(config.follower)
        self._closes: dict[str, tuple[Price, int]] = {}
        self._basis: list[float] = []
        self._side: int = 0
        self._pending: bool = False
        self.trades: list[dict[str, Any]] = []
        self.rounds: list[dict[str, Any]] = []
        self._round_open: dict[str, Any] | None = None
        #: (ts_event_ns, 总权益 USD) —— 2.0 的 ``BacktestResult.returns_series`` 退化成
        #: 单个常量 key 的点，座舱必须自己从 portfolio equity 曲线算收益序列。
        self.equity_curve: list[tuple[int, float]] = []

    def on_start(self) -> None:
        self.subscribe_bars(self._bar_type(self.leader_iid))
        self.subscribe_bars(self._bar_type(self.follower_iid))

    def _bar_type(self, instrument_id: InstrumentId) -> BarType:
        return BarType(instrument_id, BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST))

    def on_bar(self, bar: Bar) -> None:
        iid = bar.bar_type.instrument_id
        self._closes[str(iid)] = (bar.close, bar.ts_event)
        if iid != self.follower_iid:
            return
        leader = self._closes.get(str(self.leader_iid))
        if leader is None or leader[1] != bar.ts_event:
            return
        leader_px = float(leader[0])
        follower_px = float(bar.close)
        if leader_px <= 0 or follower_px <= 0:
            return
        basis_bps = (leader_px / follower_px - 1.0) * 10_000.0
        self._record_equity(bar.ts_event)
        self._basis.append(basis_bps)
        if len(self._basis) > self.cfg.window:
            self._basis.pop(0)
        if len(self._basis) < self.cfg.window:
            return
        mean = statistics.fmean(self._basis)
        std = statistics.pstdev(self._basis)
        if std <= 0:
            return
        z = (basis_bps - mean) / std
        qty = Quantity(Decimal(self.cfg.qty), 3)
        if self._side == 0 and not self._pending:
            if z >= self.cfg.entry_z:
                self._enter(OrderSide.SELL, self.leader_iid, qty)
                self._enter(OrderSide.BUY, self.follower_iid, qty)
                self._side = -1
                self._round_open = {"ts": bar.ts_event, "z": z, "basis_bps": basis_bps}
            elif z <= -self.cfg.entry_z:
                self._enter(OrderSide.BUY, self.leader_iid, qty)
                self._enter(OrderSide.SELL, self.follower_iid, qty)
                self._side = 1
                self._round_open = {"ts": bar.ts_event, "z": z, "basis_bps": basis_bps}
        elif self._side != 0 and abs(z) <= self.cfg.exit_z:
            self._enter(OrderSide.BUY if self._side < 0 else OrderSide.SELL, self.leader_iid, qty)
            self._enter(OrderSide.SELL if self._side < 0 else OrderSide.BUY, self.follower_iid, qty)
            self._side = 0
            self._pending = True

    def _enter(self, side: OrderSide, instrument_id: InstrumentId, qty: Quantity) -> None:
        order = self.order_factory.market(
            instrument_id=instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(order)
        self._pending = True

    def on_order_filled(self, event: Any) -> None:
        self._pending = False

    def _record_equity(self, ts_event: int) -> None:
        total = 0.0
        for venue in (VENUE_LEADER, VENUE_FOLLOWER):
            for money in self.portfolio.equity(venue=venue).values():
                total += float(money.as_decimal())
        self.equity_curve.append((int(ts_event), total))


def build_engine(starting_balance: Decimal, slippage: bool) -> BacktestEngine:
    engine = BacktestEngine(config=BacktestEngineConfig(run_analysis=True))
    for venue, iid, maker, taker in (
        (VENUE_LEADER, IID_LEADER, HL_MAKER_BPS, HL_TAKER_BPS),
        (VENUE_FOLLOWER, IID_FOLLOWER, LIGHTER_MAKER_BPS, LIGHTER_TAKER_BPS),
    ):
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_balance, USD)],
            base_currency=USD,
            default_leverage=Decimal("1"),
            fee_model=MakerTakerFeeModel(
                maker_rate=(maker / Decimal("10000")),
                taker_rate=(taker / Decimal("10000")),
            ),
            fill_model=OneTickSlippageFillModel(1.0, 1.0 if slippage else 0.0, 7),
        )
        engine.add_instrument(make_instrument(iid))
    return engine


def run(days: int, qty: str, window: int, entry_z: float, exit_z: float,
        starting_balance: Decimal, slippage: bool, leader_coin: str, follower_market: int) -> Any:
    leader = fetch_hl_candles(leader_coin, "5m", days)
    follower = fetch_lighter_candles(follower_market, "5m", days)
    common = sorted(set(leader) & set(follower))
    if len(common) < window + 10:
        raise SystemExit(f"共同 K 线太少（{len(common)}），无法回测")
    engine = build_engine(starting_balance, slippage)
    bar_leader = BarType(IID_LEADER, BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST))
    bar_follower = BarType(IID_FOLLOWER, BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST))
    bars_leader = [
        Bar(bar_leader, Price(o, 2), Price(h, 2), Price(l, 2), Price(c, 2),
            Quantity(v, 3), ts * 1_000_000_000, ts * 1_000_000_000)
        for ts in common for o, h, l, c, v in [leader[ts]]
    ]
    bars_follower = [
        Bar(bar_follower, Price(o, 2), Price(h, 2), Price(l, 2), Price(c, 2),
            Quantity(v, 3), ts * 1_000_000_000, ts * 1_000_000_000)
        for ts in common for o, h, l, c, v in [follower[ts]]
    ]
    engine.add_data(bars_leader)
    engine.add_data(bars_follower)
    strategy = FadeStrategy(FadeConfig(leader=str(IID_LEADER), follower=str(IID_FOLLOWER),
                                       qty=qty, window=window, entry_z=entry_z, exit_z=exit_z))
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy, len(common)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--qty", default="5")
    parser.add_argument("--window", type=int, default=288)
    parser.add_argument("--entry-z", type=float, default=1.5)
    parser.add_argument("--exit-z", type=float, default=0.3)
    parser.add_argument("--starting-balance", default="100000")
    parser.add_argument("--no-slippage", action="store_true")
    parser.add_argument("--leader-coin", default="xyz:SOXL")
    parser.add_argument("--follower-market", type=int, default=197)
    parser.add_argument("--out", default="data/cockpit/backtest_stats.json")
    args = parser.parse_args()

    engine, strategy, n_bars = run(
        days=args.days, qty=args.qty, window=args.window, entry_z=args.entry_z,
        exit_z=args.exit_z, starting_balance=Decimal(args.starting_balance),
        slippage=not args.no_slippage, leader_coin=args.leader_coin,
        follower_market=args.follower_market,
    )
    result = engine.get_result()
    returns = _returns_from_equity(strategy.equity_curve)
    daily_returns = _daily_returns(strategy.equity_curve)
    computed_returns_stats = _returns_stats(daily_returns, periods_per_year=252)
    native_returns_stats = _jsonable(result.stats_returns)
    stats_returns = dict(native_returns_stats)
    for key, value in computed_returns_stats.items():
        if key not in stats_returns or stats_returns[key] is None:
            stats_returns[key] = value
    stats_returns["Bars Observed (charts)"] = float(len(returns))
    stats_returns["Trading Days Observed"] = float(len(daily_returns))
    payload = {
        "source": "nautilus_backtest",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": {
            "bars": n_bars,
            "interval": "5m",
            "days": args.days,
            "leader": args.leader_coin,
            "follower_market_id": args.follower_market,
            "qty": args.qty,
            "window": args.window,
            "entry_z": args.entry_z,
            "exit_z": args.exit_z,
            "starting_balance": args.starting_balance,
            "slippage_ticks_per_fill": 0 if args.no_slippage else 1,
            "hl_maker_bps": float(HL_MAKER_BPS),
            "hl_taker_bps": float(HL_TAKER_BPS),
            "lighter_maker_bps": float(LIGHTER_MAKER_BPS),
            "lighter_taker_bps": float(LIGHTER_TAKER_BPS),
            "nautilus_version": _nautilus_version(),
        },
        "stats_pnls": _jsonable(result.stats_pnls),
        "stats_returns": _jsonable(stats_returns),
        "stats_general": _jsonable(result.stats_general),
        "native_stats_returns": native_returns_stats,
        "returns": _returns_payload(returns),
        "equity_curve": [[int(ts), float(v)] for ts, v in strategy.equity_curve],
        "run_info": {
            "Strategy": "FadeStrategy (two-leg basis fade)",
            "Leader venue": "HYPERLIQUID",
            "Follower venue": "LIGHTER",
            "Bars": str(n_bars),
            "Bar interval": "5m",
            "Window (bars)": str(args.window),
            "Entry z": str(args.entry_z),
            "Exit z": str(args.exit_z),
            "Quantity per leg": args.qty,
            "Starting balance (USD)": args.starting_balance,
            "Slippage": "1 tick per fill" if not args.no_slippage else "none",
            "NautilusTrader": _nautilus_version(),
            "Returns series (charts)": f"per-bar equity returns ({n_bars} bars), computed by the cockpit adapter",
            "Returns stats (Sharpe etc.)": f"Nautilus native daily-return stats over {len(daily_returns)} trading day(s)",
            "Sharpe caveat": "annualised Sharpe over <1 month of data is NOT statistically meaningful",
        },
    }
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    print(f"wrote {out_path}")
    print("stats_general:", json.dumps(_jsonable(result.stats_general), indent=2, default=str)[:1200])
    print("returns points:", len(returns))
    return 0


def _nautilus_version() -> str:
    from importlib.metadata import version
    try:
        return version("nautilus_trader")
    except Exception:  # noqa: BLE001
        return "unknown"


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _returns_from_equity(equity_curve: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """把权益曲线变成逐 bar 的简单收益序列。
    Nautilus 2.0.0rc9 的 ``BacktestResult.returns_series`` 只有**一个**点（key 是常量
    ``1728000000000000``），没法直接喂给 tearsheet，所以座舱自己从 portfolio 的
    equity 曲线算收益。
    """
    out: list[tuple[int, float]] = []
    prev_ts: int | None = None
    prev_value: float | None = None
    for ts, value in equity_curve:
        if prev_value is not None and prev_value != 0:
            out.append((int(ts), value / prev_value - 1.0))
        prev_ts, prev_value = int(ts), value
    return out


def _daily_returns(equity_curve: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """把权益曲线重采样成**日频**收益（UTC 日界），用于年化统计。"""
    last_of_day: dict[str, tuple[int, float]] = {}
    for ts, value in equity_curve:
        day = time.strftime("%Y-%m-%d", time.gmtime(ts / 1_000_000_000))
        last_of_day[day] = (ts, value)
    ordered = [last_of_day[k] for k in sorted(last_of_day)]
    out: list[tuple[int, float]] = []
    for i in range(1, len(ordered)):
        prev_value = ordered[i - 1][1]
        if prev_value:
            out.append((ordered[i][0], ordered[i][1] / prev_value - 1.0))
    return out


def _returns_stats(returns: list[tuple[int, float]], periods_per_year: int) -> dict[str, float]:
    """由周期收益序列计算年化统计量（口径写在 run_info 里）。"""
    values = [r for _, r in returns]
    if len(values) < 2:
        return {}
    mean = statistics.fmean(values)
    std = statistics.pstdev(values)
    downside = [v for v in values if v < 0]
    dstd = statistics.fmean([v * v for v in downside]) ** 0.5 if downside else 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for v in values:
        equity *= (1.0 + v)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    total = equity - 1.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "Average (Return)": mean,
        "Returns Volatility (252 days)": std * (periods_per_year ** 0.5),
        "Sharpe Ratio (252 days)": (mean / std * (periods_per_year ** 0.5)) if std > 0 else float("nan"),
        "Sortino Ratio (252 days)": (mean / dstd * (periods_per_year ** 0.5)) if dstd > 0 else float("nan"),
        "Profit Factor": (gross_win / gross_loss) if gross_loss > 0 else float("nan"),
        "Max Drawdown": max_dd,
        "Total Return": total,
        "Positive Periods": float(len(wins)),
        "Negative Periods": float(len(losses)),
        "Hit Rate (bars)": (len(wins) / len(values)) if values else float("nan"),
        "Annualisation": float(periods_per_year),
    }


def _returns_payload(series: Any) -> list[list[float]]:
    out: list[list[float]] = []
    items = series.items() if hasattr(series, "items") else series
    for ts, value in items:
        out.append([float(ts), float(value)])
    return out


if __name__ == "__main__":
    raise SystemExit(main())
