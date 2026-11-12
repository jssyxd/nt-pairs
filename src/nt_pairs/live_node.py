"""NautilusTrader 2.0 ``LiveNode`` 装配层（实盘节点接线）。

2.0.0rc6 移除了 1.x 的 ``TradingNode`` / ``TradingNodeConfig``，改为
``nautilus_trader.live`` 里的 ``LiveNode`` / ``LiveNodeBuilder`` / ``LiveNodeConfig``。
本模块只负责**装配**（纯配置 + 节点构建），不启动网络循环，因此可以被单测直接调用。

真实签名（实测 nautilus_trader 2.0.0rc6，见 .herd-dispatch/jobs/live-node/report.md）::

    LiveNodeBuilder.from_config(name, config=None, *, data_factories=None, exec_factories=None)
    LiveNodeBuilder.add_data_client(name, factory, config, routing=None)
    LiveNodeBuilder.add_exec_client(name, factory, config, routing=None)
    LiveNodeBuilder.add_simulated_exec_client(name, factory, config)
    LiveNodeBuilder.build() -> LiveNode
    LiveNode.add_strategy(strategy)            # 策略注册入口（已实测）
    LiveNode.add_actor(actor)

安全约束（CONTRACT §6）：
- ``dry_run=True`` 时**不读取任何私钥**（连 ``os.environ`` 里的密钥都不取），
  执行端一律用 ``add_simulated_exec_client``（沙盒），不可能发出真实订单；
- ``dry_run=False`` 时只从环境变量读密钥，缺密钥直接抛 :class:`MissingCredentials`，
  在任何网络连接之前失败。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidEnvironment,
    HyperliquidExecutionClientConfig,
    HyperliquidExecutionClientFactory,
)
from nautilus_trader.adapters.lighter import (
    LighterDataClientConfig,
    LighterDataClientFactory,
    LighterDeployment,
    LighterEnvironment,
    LighterExecutionClientConfig,
    LighterExecutionClientFactory,
)
from nautilus_trader.adapters.sandbox import (
    SandboxExecutionClientConfig,
    SandboxExecutionClientFactory,
)
from nautilus_trader.common import DataActor
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.live import LiveNode, LiveNodeBuilder, LiveNodeConfig
from nautilus_trader.model import AccountId, Currency, InstrumentId, Money, Venue

from .pair_spec import load_pair
from .strategy import PairHedgeConfig, PairHedgeStrategy

__all__ = [
    "MissingCredentials",
    "LegPlan",
    "LivePlan",
    "SubscriptionPlan",
    "NodeBundle",
    "load_live_plan",
    "build_client_specs",
    "build_node",
    "scan_secret_fields",
]

# ---------------------------------------------------------------------------
# 场所 → 适配器
# ---------------------------------------------------------------------------

VENUE_HYPERLIQUID = "HYPERLIQUID"
VENUE_LIGHTER = "LIGHTER"
SUPPORTED_VENUES = (VENUE_HYPERLIQUID, VENUE_LIGHTER)

#: 触发实盘执行时每个场所必须提供的环境变量（dry-run 下完全不看）。
REQUIRED_ENV = {
    VENUE_HYPERLIQUID: ("HL_PRIVATE_KEY",),
    VENUE_LIGHTER: ("LIGHTER_PRIVATE_KEY",),
}

#: 凭据字段名——装配层自检用（断言这些字段在 dry-run 下全为空）。
SECRET_FIELD_NAMES = (
    "private_key",
    "api_key",
    "secret",
    "api_secret",
    "passphrase",
)


class MissingCredentials(RuntimeError):
    """实盘模式缺少必需的环境变量凭据。

    属性:
        venue: 缺凭据的场所名。
        missing: 缺失的环境变量名列表。
    """

    def __init__(self, venue: str, missing: list[str]) -> None:
        self.venue = venue
        self.missing = list(missing)
        super().__init__(
            f"missing credentials for venue {venue}: {', '.join(self.missing)}"
        )

    def as_dict(self) -> dict[str, Any]:
        """返回结构化错误载荷（供 CLI 打印 JSON）。"""
        return {
            "error": "missing_credentials",
            "venue": self.venue,
            "missing": list(self.missing),
            "hint": "set the listed environment variables before starting a live node",
        }


# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegPlan:
    """一条腿的装配计划。

    Attributes:
        role: ``"leader"``（被动挂单腿）或 ``"follower"``（IOC 对冲腿）。
        venue: 场所名（``HYPERLIQUID`` / ``LIGHTER``）。
        instrument_id: nautilus 风格的 instrument id。
        direction: 开仓方向（``"BUY"`` / ``"SELL"``）。
        max_qty: 单腿数量上限（Decimal）。
    """

    role: str
    venue: str
    instrument_id: str
    direction: str
    max_qty: Decimal
    maker_fee_bps: Decimal = Decimal("0")
    taker_fee_bps: Decimal = Decimal("0")


@dataclass(frozen=True)
class SubscriptionPlan:
    """一条 L2 订阅计划。"""

    instrument_id: str
    book_type: str
    role: str
    venue: str


@dataclass(frozen=True)
class LivePlan:
    """整个 pair 的装配计划（纯数据，无网络、无 IO）。"""

    name: str
    leader: LegPlan
    follower: LegPlan
    ratio: Decimal
    sigma_1m_bps: Decimal
    max_leg_usd: Decimal
    dry_run: bool
    state_dir: str
    book_max_age_ms: int
    subscriptions: tuple[SubscriptionPlan, ...] = field(default=())

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        """两腿 instrument id（订阅顺序：leader 在前）。"""
        return tuple(sub.instrument_id for sub in self.subscriptions)

    @property
    def venues(self) -> tuple[str, ...]:
        """需要连接执行端的场所（按出现顺序去重）。"""
        seen: list[str] = []
        for sub in self.subscriptions:
            if sub.venue not in seen:
                seen.append(sub.venue)
        return tuple(seen)


def _leg_direction(raw: dict[str, Any], role: str, default: str) -> str:
    """从 JSON 取方向，缺省按 CONTRACT §6 的主被动约定给默认值。"""
    value = str(raw.get("direction", default)).upper()
    if value not in ("BUY", "SELL"):
        raise ValueError(f"{role}.direction must be BUY or SELL (got {value!r})")
    return value


def _leg_qty(raw: dict[str, Any], role: str, default: str) -> Decimal:
    """从 JSON 取数量上限（字符串 / 数字皆可，禁止 float）。"""
    value = raw.get("max_qty", default)
    if isinstance(value, float):
        raise ValueError(f"{role}.max_qty must not be a float (got {value!r})")
    return Decimal(str(value))


def load_live_plan(path: str | Path) -> LivePlan:
    """读取 pair 配置并生成 :class:`LivePlan`。

    支持两种 JSON 布局：

    **A. 仓库既有的 pair-spec 布局**（``configs/openai_entropy_lighter.json`` 等）——
    ``leader`` / ``follower`` 段由 :func:`nt_pairs.pair_spec.load_pair` 校验
    （Decimal 精确、缺字段即报错）；实盘开关放在顶层 ``live`` 段：

    .. code-block:: json

        {
          "name": "...",
          "leader": {...}, "follower": {...},
          "ratio": "1", "unit_evidence": "...", "sigma_1m_bps": "15", "max_leg_usd": "500",
          "live": {
            "dry_run": true,
            "state_dir": "state",
            "book_max_age_ms": 1000,
            "leader_direction": "BUY",
            "follower_direction": "SELL",
            "max_leg_qty": "0.05"
          }
        }

    **B. 腿 A/B 布局**（任务书指定的 ``configs/pair.json``）：

    .. code-block:: json

        {
          "name": "...",
          "dry_run": true,
          "leg_a": {"instrument_id": "...", "venue": "HYPERLIQUID",
                    "direction": "BUY", "max_qty": "0.05"},
          "leg_b": {"instrument_id": "...", "venue": "LIGHTER",
                    "direction": "SELL", "max_qty": "0.05"},
          "ratio": "1", "sigma_1m_bps": "15", "max_leg_usd": "500"
        }
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{p.name}: top level must be a JSON object")

    live_raw = raw.get("live", {}) or {}
    if not isinstance(live_raw, dict):
        raise ValueError(f"{p.name}: 'live' must be a JSON object")

    if "leg_a" in raw or "leg_b" in raw:
        return _plan_from_legs(p, raw, live_raw)
    return _plan_from_pair_spec(p, raw, live_raw)


def _plan_from_pair_spec(
    p: Path,
    raw: dict[str, Any],
    live_raw: dict[str, Any],
) -> LivePlan:
    """由仓库既有 pair-spec 布局生成计划。"""
    spec = load_pair(p)

    dry_run = bool(live_raw.get("dry_run", True))
    state_dir = str(live_raw.get("state_dir", "state"))
    book_max_age_ms = int(live_raw.get("book_max_age_ms", 1000))
    max_qty = _leg_qty(live_raw, "live", "0.05")
    leader_direction = _leg_direction(live_raw, "leader", "BUY")
    follower_direction = _leg_direction(live_raw, "follower", "SELL")

    leader = LegPlan(
        role="leader",
        venue=spec.leader.venue.upper(),
        instrument_id=spec.leader.instrument_id,
        direction=leader_direction,
        max_qty=max_qty,
        maker_fee_bps=spec.leader.maker_fee_bps,
        taker_fee_bps=spec.leader.taker_fee_bps,
    )
    follower = LegPlan(
        role="follower",
        venue=spec.follower.venue.upper(),
        instrument_id=spec.follower.instrument_id,
        direction=follower_direction,
        max_qty=max_qty,
        maker_fee_bps=spec.follower.maker_fee_bps,
        taker_fee_bps=spec.follower.taker_fee_bps,
    )
    return _finalize_plan(
        p=p,
        name=spec.name,
        leader=leader,
        follower=follower,
        ratio=spec.ratio,
        sigma_1m_bps=spec.sigma_1m_bps,
        max_leg_usd=spec.max_leg_usd,
        dry_run=dry_run,
        state_dir=state_dir,
        book_max_age_ms=book_max_age_ms,
    )


def _plan_from_legs(
    p: Path,
    raw: dict[str, Any],
    live_raw: dict[str, Any],
) -> LivePlan:
    """由腿 A/B 布局生成计划。"""
    leg_a = raw.get("leg_a") or {}
    leg_b = raw.get("leg_b") or {}
    if not isinstance(leg_a, dict) or not isinstance(leg_b, dict):
        raise ValueError(f"{p.name}: 'leg_a' and 'leg_b' must be JSON objects")
    for key in ("instrument_id", "venue"):
        for leg_name, leg in (("leg_a", leg_a), ("leg_b", leg_b)):
            if not leg.get(key):
                raise ValueError(f"{p.name}: {leg_name}.{key} is required")

    dry_run = bool(raw.get("dry_run", live_raw.get("dry_run", True)))
    state_dir = str(raw.get("state_dir", live_raw.get("state_dir", "state")))
    book_max_age_ms = int(raw.get("book_max_age_ms", live_raw.get("book_max_age_ms", 1000)))
    max_qty = _leg_qty(raw.get("live", {}) or live_raw, "live", "0.05")

    leader = LegPlan(
        role="leader",
        venue=str(leg_a["venue"]).upper(),
        instrument_id=str(leg_a["instrument_id"]),
        direction=_leg_direction(leg_a, "leg_a", "BUY"),
        max_qty=_leg_qty(leg_a, "leg_a", str(max_qty)),
    )
    follower = LegPlan(
        role="follower",
        venue=str(leg_b["venue"]).upper(),
        instrument_id=str(leg_b["instrument_id"]),
        direction=_leg_direction(leg_b, "leg_b", "SELL"),
        max_qty=_leg_qty(leg_b, "leg_b", str(max_qty)),
    )
    return _finalize_plan(
        p=p,
        name=str(raw.get("name", p.stem)),
        leader=leader,
        follower=follower,
        ratio=Decimal(str(raw.get("ratio", "1"))),
        sigma_1m_bps=Decimal(str(raw.get("sigma_1m_bps", "12.86"))),
        max_leg_usd=Decimal(str(raw.get("max_leg_usd", "500"))),
        dry_run=dry_run,
        state_dir=state_dir,
        book_max_age_ms=book_max_age_ms,
    )


def _finalize_plan(
    *,
    p: Path,
    name: str,
    leader: LegPlan,
    follower: LegPlan,
    ratio: Decimal,
    sigma_1m_bps: Decimal,
    max_leg_usd: Decimal,
    dry_run: bool,
    state_dir: str,
    book_max_age_ms: int,
) -> LivePlan:
    """校验场所并生成订阅列表。"""
    for leg in (leader, follower):
        if leg.venue not in SUPPORTED_VENUES:
            raise ValueError(
                f"{p.name}: unsupported venue {leg.venue!r} "
                f"(supported: {', '.join(SUPPORTED_VENUES)})"
            )
        if leg.max_qty <= 0:
            raise ValueError(f"{p.name}: {leg.role}.max_qty must be > 0")

    subscriptions = (
        SubscriptionPlan(leader.instrument_id, "L2_MBP", leader.role, leader.venue),
        SubscriptionPlan(follower.instrument_id, "L2_MBP", follower.role, follower.venue),
    )
    return LivePlan(
        name=name,
        leader=leader,
        follower=follower,
        ratio=ratio,
        sigma_1m_bps=sigma_1m_bps,
        max_leg_usd=max_leg_usd,
        dry_run=dry_run,
        state_dir=state_dir,
        book_max_age_ms=book_max_age_ms,
        subscriptions=subscriptions,
    )


# ---------------------------------------------------------------------------
# 客户端配置装配
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientSpec:
    """一个待注册客户端的装配描述。"""

    name: str
    kind: str  # "data" | "exec" | "sim_exec"
    factory: Any
    config: Any
    venue: str


def _hyperliquid_data_config(environment: HyperliquidEnvironment) -> HyperliquidDataClientConfig:
    return HyperliquidDataClientConfig(environment=environment)


def _lighter_data_config(environment: LighterEnvironment) -> LighterDataClientConfig:
    return LighterDataClientConfig(
        environment=environment,
        deployment=LighterDeployment.LIGHTER,
    )


def _sandbox_exec_config(venue: str, leg: LegPlan) -> SandboxExecutionClientConfig:
    """沙盒执行端（dry-run 唯一允许的执行入口）。

    2.0 要求显式给 fee_model（连零费率模型也必须显式给），否则
    ``SandboxExecutionClientConfig`` 直接抛 ``RuntimeError``。
    费率取自该腿配置的 maker/taker bps。
    """
    usd = Currency.from_str("USD")
    maker_rate = Decimal(leg.maker_fee_bps) / Decimal(10_000)
    taker_rate = Decimal(leg.taker_fee_bps) / Decimal(10_000)
    return SandboxExecutionClientConfig(
        venue=Venue(venue),
        starting_balances=[Money(1_000_000, usd)],
        base_currency=usd,
        fee_model=MakerTakerFeeModel(maker_rate=maker_rate, taker_rate=taker_rate),
    )


def _hyperliquid_exec_config(environment: HyperliquidEnvironment) -> HyperliquidExecutionClientConfig:
    """实盘 HL 执行端——只在这里读取私钥。"""
    private_key = os.environ.get("HL_PRIVATE_KEY")
    account_address = os.environ.get("HL_ACCOUNT_ADDRESS")
    if not private_key:
        raise MissingCredentials(VENUE_HYPERLIQUID, ["HL_PRIVATE_KEY"])
    return HyperliquidExecutionClientConfig(
        account_address=account_address,
        private_key=private_key,
        environment=environment,
    )


def _lighter_exec_config(environment: LighterEnvironment) -> LighterExecutionClientConfig:
    """实盘 Lighter 执行端——只在这里读取私钥。"""
    private_key = os.environ.get("LIGHTER_PRIVATE_KEY")
    account_index = os.environ.get("LIGHTER_ACCOUNT_INDEX")
    if not private_key:
        raise MissingCredentials(VENUE_LIGHTER, ["LIGHTER_PRIVATE_KEY"])
    return LighterExecutionClientConfig(
        account_id=AccountId(os.environ.get("LIGHTER_ACCOUNT_ID", "LIGHTER-001")),
        account_index=int(account_index) if account_index else None,
        api_key_index=int(os.environ.get("LIGHTER_API_KEY_INDEX", "4")),
        private_key=private_key,
        environment=environment,
        deployment=LighterDeployment.LIGHTER,
    )


def _leg_for_venue(plan: LivePlan, venue: str) -> LegPlan:
    """按场所取腿（同一 pair 两腿不同场所）。"""
    for leg in (plan.leader, plan.follower):
        if leg.venue == venue:
            return leg
    raise ValueError(f"no leg configured for venue {venue!r}")


def build_client_specs(
    plan: LivePlan,
    *,
    dry_run: Optional[bool] = None,
    environment: str = "mainnet",
) -> list[ClientSpec]:
    """生成客户端装配列表（不建立网络连接）。

    Args:
        plan: :class:`LivePlan`。
        dry_run: 覆盖 ``plan.dry_run``；``True`` 时不读取任何凭据，执行端用沙盒。
        environment: ``"mainnet"`` 或 ``"testnet"``。

    Returns:
        :class:`ClientSpec` 列表（先数据端、后执行端）。
    """
    use_dry_run = plan.dry_run if dry_run is None else bool(dry_run)
    hl_env = (
        HyperliquidEnvironment.MAINNET
        if environment == "mainnet"
        else HyperliquidEnvironment.TESTNET
    )
    lt_env = (
        LighterEnvironment.MAINNET
        if environment == "mainnet"
        else LighterEnvironment.TESTNET
    )

    specs: list[ClientSpec] = []
    for venue in plan.venues:
        if venue == VENUE_HYPERLIQUID:
            specs.append(
                ClientSpec(
                    name="hyperliquid",
                    kind="data",
                    factory=HyperliquidDataClientFactory(),
                    config=_hyperliquid_data_config(hl_env),
                    venue=venue,
                )
            )
        elif venue == VENUE_LIGHTER:
            specs.append(
                ClientSpec(
                    name="lighter",
                    kind="data",
                    factory=LighterDataClientFactory(),
                    config=_lighter_data_config(lt_env),
                    venue=venue,
                )
            )
        else:  # pragma: no cover - load_live_plan 已校验
            raise ValueError(f"unsupported venue {venue!r}")

    for venue in plan.venues:
        if use_dry_run:
            specs.append(
                ClientSpec(
                    name=f"{venue.lower()}-sim",
                    kind="sim_exec",
                    factory=SandboxExecutionClientFactory(),
                    config=_sandbox_exec_config(venue, _leg_for_venue(plan, venue)),
                    venue=venue,
                )
            )
            continue
        if venue == VENUE_HYPERLIQUID:
            specs.append(
                ClientSpec(
                    name="hyperliquid",
                    kind="exec",
                    factory=HyperliquidExecutionClientFactory(),
                    config=_hyperliquid_exec_config(hl_env),
                    venue=venue,
                )
            )
        elif venue == VENUE_LIGHTER:
            specs.append(
                ClientSpec(
                    name="lighter",
                    kind="exec",
                    factory=LighterExecutionClientFactory(),
                    config=_lighter_exec_config(lt_env),
                    venue=venue,
                )
            )
    return specs


# ---------------------------------------------------------------------------
# 心跳 actor
# ---------------------------------------------------------------------------


class PairHeartbeatActor(DataActor):
    """每 5 秒打一条心跳，并汇报两腿的订阅状态。

    心跳字段：盘口时间戳、两腿 mid、基差（bps）、residual、naked_age（秒）。

    数据流只订阅一次：由 :class:`~nt_pairs.strategy.PairHedgeStrategy` 在
    ``on_start`` 里订阅两腿 L2_MBP。本 actor **不重复订阅**，改为读策略维护的
    ``book_ts``（收到过 deltas 即视为订阅成功）以及 cache 里的盘口，
    避免同一 instrument 出现两条数据流。
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        # 2.0 的 DataActor.__new__ 只接受 0~1 个位置参数，自定义构造参数需在此吞掉。
        return super().__new__(cls)

    def __init__(self, strategy: PairHedgeStrategy, plan: LivePlan) -> None:
        super().__init__()
        self._strategy = strategy
        self._plan = plan
        self._ticks = 0

    # -- 生命周期 ---------------------------------------------------------
    def on_start(self) -> None:
        self.clock.set_timer(
            name="heartbeat",
            interval=timedelta(seconds=5),
            callback=self._on_heartbeat,
        )
        self.log.info(
            f"heartbeat actor started instruments={list(self._plan.instrument_ids)}"
        )

    def on_stop(self) -> None:
        self.log.info("heartbeat actor stopped")

    # -- 心跳 -------------------------------------------------------------
    def _on_heartbeat(self, event: Any = None) -> None:
        self._ticks += 1
        now = self.clock.timestamp_ns() / 1e9
        book_ts = dict(getattr(self._strategy, "book_ts", {}) or {})
        mids: dict[str, Optional[float]] = {}
        for sub in self._plan.subscriptions:
            mids[sub.instrument_id] = self._mid(sub.instrument_id)
        leader_mid = mids.get(self._plan.leader.instrument_id)
        follower_mid = mids.get(self._plan.follower.instrument_id)
        basis_bps: Optional[float] = None
        if leader_mid and follower_mid and leader_mid > 0:
            basis_bps = (follower_mid / leader_mid - 1.0) * 1e4
        residual = self._strategy.ledger.residual()
        naked_since = getattr(self._strategy, "naked_since", None)
        naked_age = None if naked_since is None else round(now - float(naked_since), 3)
        payload = {
            "kind": "heartbeat",
            "tick": self._ticks,
            "ts_event": book_ts,
            "mid": mids,
            "basis_bps": basis_bps,
            "residual": str(residual),
            "naked_age_s": naked_age,
            "subscribed": self.subscription_status(),
        }
        self.log.info(json.dumps(payload, default=str))

    def _mid(self, instrument_id: str) -> Optional[float]:
        try:
            book = self.cache.order_book(InstrumentId.from_str(instrument_id))
        except Exception:  # noqa: BLE001 - cache 尚未有该 book
            return None
        if book is None:
            return None
        bid, ask = book.best_bid_price(), book.best_ask_price()
        if not bid or not ask:
            return None
        return float(bid + ask) / 2.0

    def subscription_status(self) -> dict[str, bool]:
        """每条腿的订阅状态（策略收到过该腿的 deltas 即为 True）。"""
        book_ts = getattr(self._strategy, "book_ts", {}) or {}
        return {
            sub.instrument_id: str(sub.instrument_id) in book_ts
            for sub in self._plan.subscriptions
        }


# ---------------------------------------------------------------------------
# 节点装配
# ---------------------------------------------------------------------------


@dataclass
class NodeBundle:
    """构建好的节点及其附属对象。"""

    node: LiveNode
    plan: LivePlan
    strategy: PairHedgeStrategy
    heartbeat: PairHeartbeatActor
    client_specs: list[ClientSpec]


def build_node(
    plan: LivePlan,
    *,
    dry_run: Optional[bool] = None,
    environment: str = "mainnet",
    node_name: Optional[str] = None,
) -> NodeBundle:
    """按计划组装 ``LiveNode``（不启动网络循环）。

    策略注册入口为 ``LiveNode.add_strategy(instance)``（实测 2.0.0rc6：
    ``LiveNodeConfig`` **没有** strategies 字段，``LiveNodeBuilder`` 也没有
    ``with_strategy``；只有 node 级别的 ``add_strategy`` /
    ``add_strategy_from_config`` / ``add_builtin_strategy``）。
    """
    use_dry_run = plan.dry_run if dry_run is None else bool(dry_run)
    specs = build_client_specs(plan, dry_run=use_dry_run, environment=environment)

    config = LiveNodeConfig(
        environment=None,
        load_state=False,
        save_state=False,
        shutdown_on_error=True,
    )
    builder = LiveNodeBuilder.from_config(node_name or plan.name, config)
    for spec in specs:
        if spec.kind == "data":
            builder.add_data_client(spec.name, spec.factory, spec.config)
        elif spec.kind == "exec":
            builder.add_exec_client(spec.name, spec.factory, spec.config)
        else:
            builder.add_simulated_exec_client(spec.name, spec.factory, spec.config)

    node = builder.build()

    strategy_config = PairHedgeConfig(
        leader_id=plan.leader.instrument_id,
        follower_id=plan.follower.instrument_id,
        ratio=str(plan.ratio),
        sigma_1m_bps=str(plan.sigma_1m_bps),
        max_leg_qty=str(plan.leader.max_qty),
        book_max_age_ms=plan.book_max_age_ms,
        dry_run=use_dry_run,
        state_dir=plan.state_dir,
    )
    strategy = PairHedgeStrategy(strategy_config)
    node.add_strategy(strategy)

    heartbeat = PairHeartbeatActor(strategy, plan)
    node.add_actor(heartbeat)

    return NodeBundle(
        node=node,
        plan=plan,
        strategy=strategy,
        heartbeat=heartbeat,
        client_specs=specs,
    )


# ---------------------------------------------------------------------------
# 凭据自检
# ---------------------------------------------------------------------------


def _iter_config_objects(obj: Any, depth: int = 0):
    """递归遍历配置对象（属性型 pyo3 类）。"""
    if obj is None or depth > 3:
        return
    yield obj
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            value = getattr(obj, name)
        except Exception:  # noqa: BLE001
            continue
        if callable(value):
            continue
        if isinstance(value, (str, int, float, bool, Decimal)):
            continue
        if hasattr(value, "__dict__") or type(value).__module__.startswith("nautilus_trader"):
            yield from _iter_config_objects(value, depth + 1)


def scan_secret_fields(bundle: NodeBundle) -> dict[str, Any]:
    """扫描装配结果里的凭据字段。

    Returns:
        ``{"populated": [...], "fields": {field: value}}``——``populated`` 列出
        **有值**的凭据字段；dry-run 下必须为空。
    """
    populated: list[str] = []
    fields: dict[str, Any] = {}
    targets: list[Any] = [bundle.node]
    targets.extend(spec.config for spec in bundle.client_specs)
    for obj in targets:
        for candidate in _iter_config_objects(obj):
            for field_name in SECRET_FIELD_NAMES:
                if not hasattr(candidate, field_name):
                    continue
                value = getattr(candidate, field_name)
                fields.setdefault(field_name, value)
                if value:
                    populated.append(f"{type(candidate).__name__}.{field_name}")
    return {"populated": populated, "fields": fields}
