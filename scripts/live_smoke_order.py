"""小额真单「链路验证」脚本：HL maker 腿 + Lighter taker 腿。

**本脚本不是策略。** R3 安全审查（``.herd-dispatch/jobs/live-safety/report.md``）
确认 ``src/nt_pairs/strategy.py`` 的开仓路径（B1）与出场路径（B2）都不存在，
且规格 §3 的 11 条风险闸门在运行时 0 条生效。因此本脚本**绕开策略路径**，
独立完成一次「两腿执行链路」的端到端验证：

1. HL（leader）挂 **post-only GTC** maker 单（TTL ≤ 5 s）；
2. 成交后用 Lighter（follower）**IOC 限价**对冲（collar ≤ 50 bps）；
3. 两腿都成交后用 **reduce-only 限价 IOC** 平掉两腿；
4. 任一步异常/超时 → 撤掉本脚本发出的全部未成交订单、打印两腿账户持仓/残差、
   以非 0 退出码结束（绝不吞异常）。

安全约束（硬编码，**不读配置文件**，防误改放大）：

- 默认 **不发单**：只有显式 ``--send`` 才真发；否则用 nautilus
  ``SandboxExecutionClient``（沙盒）跑完整流程；
- 单腿名义默认 **$20**、硬上限 **$50**（``--notional-usd`` 超出直接拒绝退出 2）；
- 单轮、单标的、不可并发（状态目录锁文件 + 当日轮次闸门）；
- 当日轮次 ≤ 1、当日累计亏损 ≤ **−$0.8**（绝对美元，不依赖账户权益——权益未核实）；
- 重复运行复用同一 ``cloid``（HL 适配器把 ``ClientOrderId`` 映射成交易所 cloid），
  交易所侧幂等，重复运行不会重复下单。

用法::

    # 沙盒（默认，不发单）
    python scripts/live_smoke_order.py --dry --state-dir /tmp/nt-smoke

    # 真单（人工前台跑，不注册 systemd）
    python scripts/live_smoke_order.py --send --state-dir /var/lib/nt-pairs

退出码：0 全部成功 / 2 参数或上限被拒 / 3 凭据缺失 / 4 下单或成交失败 /
5 裸腿或未平干净。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from nautilus_trader.adapters.sandbox import (  # noqa: E402
    SandboxExecutionClientConfig,
)
from nautilus_trader.config import StrategyConfig  # noqa: E402
from nautilus_trader.execution import MakerTakerFeeModel  # noqa: E402
from nautilus_trader.live import LiveNodeBuilder, LiveNodeConfig  # noqa: E402
from nautilus_trader.model import (  # noqa: E402
    BookType,
    ClientOrderId,
    Currency,
    InstrumentId,
    Money,
    OrderSide,
    Price,
    Quantity,
    TimeInForce,
    Venue,
)
from nautilus_trader.trading import Strategy  # noqa: E402

from nt_pairs.execution import MAX_COLLAR_BPS, hedge_order  # noqa: E402
from nt_pairs.ledger import LegLedger  # noqa: E402
from nt_pairs.live_node import (  # noqa: E402
    LegPlan,
    LivePlan,
    MissingCredentials,
    SubscriptionPlan,
    build_client_specs,
)
from nt_pairs.risk import RiskLimits, evaluate  # noqa: E402

# ---------------------------------------------------------------------------
# 硬上限（不读配置文件——防误改放大）
# ---------------------------------------------------------------------------

#: 单腿名义硬上限（美元）。``--notional-usd`` 超过即拒绝退出 2。
MAX_NOTIONAL_USD = Decimal("50")
#: 单腿名义默认值（美元）。R3 §4：第一笔真单取 $20/腿（B5 死亡开关未接）。
DEFAULT_NOTIONAL_USD = Decimal("20")
#: maker 挂单 TTL 上限（秒）。
#: 5 秒太短：实测两次真单都在 TTL 内没等到成交（都被交易所接受、只是没被吃单），
#: 链路验证只能验证到「交易所接受订单」。放宽到 60s 才能等到真实成交并走完对冲+平仓。
MAX_TTL_S = 60.0
#: maker 挂单 TTL 默认值（秒）。
DEFAULT_TTL_S = 30.0
#: 对冲 collar 上限（bps）——与 ``nt_pairs.execution.MAX_COLLAR_BPS`` 一致。
MAX_COLLAR_BPS_HARD = Decimal(MAX_COLLAR_BPS)
#: 当日轮次上限（轮）。
MAX_ROUNDS_PER_DAY = 1
#: 当日累计亏损上限（绝对美元，负数）。达到即当日拒绝再跑。
DAILY_LOSS_LIMIT_USD = Decimal("-0.8")
#: 状态目录默认值（VPS 上的生产路径）。
DEFAULT_STATE_DIR = "/var/lib/nt-pairs"
#: 单轮总时限（秒）——超时按失败处理并清理。
DEFAULT_DEADLINE_S = 120.0
#: 单笔订单等待结算的上限（秒）。
ORDER_SETTLE_TIMEOUT_S = 8.0
#: maker 因「穿价被拒」而重挂的最大次数（每次重新按最新盘口定价）。
MAX_MAKER_ATTEMPTS = 3

#: 两所最小下单约束（硬编码；来源 ``configs/rklb_xyz_lighter.json``）。
#: HL RKLB：min_qty 0.01、min_notional $10；Lighter RKLB：min_qty 0.085、min_notional $10。
VENUE_MIN_QTY = {
    "HYPERLIQUID": Decimal("0.01"),
    "LIGHTER": Decimal("0.085"),
}
VENUE_MIN_NOTIONAL_USD = {
    "HYPERLIQUID": Decimal("10"),
    "LIGHTER": Decimal("10"),
}

#: 两所费率（bps）——只用于沙盒 fee model 与审计口径。
# HL 用官方 HIP-3 公式（deployerFeeScale=1.0、growthMode=enabled）：
#   maker 0.30 bps / taker 0.90 bps —— 不是旧口径的 1.5 / 4.5。
#   来源：src/nt_pairs/costs.py:hl_fee_bps 与 commit 0defca8。
VENUE_FEES_BPS = {
    "HYPERLIQUID": {"maker": Decimal("0.30"), "taker": Decimal("0.90")},
    "LIGHTER": {"maker": Decimal("0"), "taker": Decimal("0")},
}

DEFAULT_LEADER_ID = "xyz:RKLB-USD-PERP.HYPERLIQUID"
DEFAULT_FOLLOWER_ID = "RKLB-PERP.LIGHTER"

#: 沙盒账户的计价货币。HL 标的以 USD 计价、Lighter 标的以 USDC 计价；
#: 若沙盒账户用 USD 而标的是 USDC，RiskEngine 会因缺汇率映射直接
#: DENY 订单（"Account currency conversion failed: Quote maps must not be empty"）。
SANDBOX_BASE_CURRENCY = {
    "HYPERLIQUID": "USD",
    "LIGHTER": "USDC",
}

EXIT_OK = 0
EXIT_LIMIT = 2
EXIT_CREDENTIALS = 3
EXIT_ORDER = 4
EXIT_NAKED = 5

_ZERO = Decimal(0)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _floor_tick(px: Decimal, tick: Decimal) -> Decimal:
    return (px / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _ceil_tick(px: Decimal, tick: Decimal) -> Decimal:
    return (px / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _dec(value: Any) -> Decimal:
    """安全转 Decimal（float 一律走 str，避免二进制误差）。"""
    return Decimal(str(value))


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)


# ---------------------------------------------------------------------------
# 每日状态（轮次 + 累计亏损，均为绝对美元）
# ---------------------------------------------------------------------------


@dataclass
class SmokeState:
    """当日轮次 / 亏损 / cloid 的本地状态文件。

    属性:
        path: 状态文件路径。
        day: UTC 日期（``YYYY-MM-DD``）——跨日自动重置计数。
        rounds: 当日**真实**轮次（只有 ``--send`` 消耗）。
        dry_runs: 当日沙盒轮次（记录用，不消耗真实轮次）。
        loss_usd: 当日累计已实现损益（美元，负数为亏）。
        cloids: 当日复用的 cloid（幂等）。
        history: 最近若干轮摘要。
    """

    path: Path
    day: str
    rounds: int = 0
    dry_runs: int = 0
    loss_usd: Decimal = _ZERO
    cloids: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, day: str) -> "SmokeState":
        """读取状态文件；不存在或跨日 → 重置当日计数。"""
        state = cls(path=path, day=day)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return state
        if not isinstance(raw, dict):
            return state
        state.history = list(raw.get("history") or [])[-50:]
        if str(raw.get("day")) != day:
            # 跨日：轮次/亏损/cloid 全部重置（历史保留）。
            return state
        state.rounds = int(raw.get("rounds") or 0)
        state.dry_runs = int(raw.get("dry_runs") or 0)
        try:
            state.loss_usd = _dec(raw.get("loss_usd") or "0")
        except Exception:  # noqa: BLE001 - 状态文件损坏按 0 处理
            state.loss_usd = _ZERO
        cloids = raw.get("cloids") or {}
        if isinstance(cloids, dict):
            state.cloids = {str(k): str(v) for k, v in cloids.items()}
        return state

    def save(self) -> None:
        """原子写回状态文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "day": self.day,
            "rounds": self.rounds,
            "dry_runs": self.dry_runs,
            "loss_usd": str(self.loss_usd),
            "cloids": self.cloids,
            "history": self.history[-50:],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def reserve_round(self) -> None:
        """预留当日真实轮次（先占坑再下单——崩溃也不给第二次机会）。"""
        self.rounds += 1
        self.save()

    def record_round(
        self, *, mode: str, exit_code: int, loss_usd: Decimal, extra: dict[str, Any]
    ) -> None:
        """写入一轮摘要；真实轮次同时累加亏损。"""
        self.history.append(
            {
                "utc": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "exit_code": exit_code,
                "loss_usd": str(loss_usd),
                **extra,
            }
        )
        if mode == "send":
            self.loss_usd += loss_usd
        else:
            self.dry_runs += 1
        self.save()

    def gate_reason(self) -> Optional[str]:
        """返回拒绝原因；``None`` 表示放行。"""
        if self.rounds >= MAX_ROUNDS_PER_DAY:
            return (
                f"当日真实轮次已达上限 {MAX_ROUNDS_PER_DAY}"
                f"（today={self.day}, rounds={self.rounds}）"
            )
        if self.loss_usd <= DAILY_LOSS_LIMIT_USD:
            return (
                f"当日累计亏损已达上限 {DAILY_LOSS_LIMIT_USD} USD"
                f"（today={self.day}, loss_usd={self.loss_usd}）"
            )
        return None


class _RunLock:
    """状态目录内的独占锁——保证同一时刻只有一个实例在跑。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: Optional[int] = None

    def acquire(self) -> Optional[str]:
        """抢锁；成功返回 ``None``，失败返回原因。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (0, 1):
            try:
                self._fd = os.open(
                    str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                os.write(self._fd, str(os.getpid()).encode("utf-8"))
                return None
            except FileExistsError:
                pid = self._read_pid()
                if attempt == 0 and pid and not _pid_alive(pid):
                    # 上一个实例已经死了（崩溃残留）→ 清理后重抢一次。
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                return f"已有实例在运行（锁文件 {self.path}，pid={pid}）"
            except OSError as exc:
                return f"无法创建锁文件 {self.path}：{exc}"
        return f"无法获取锁 {self.path}"

    def _read_pid(self) -> Optional[int]:
        try:
            return int(self.path.read_text(encoding="utf-8").strip() or 0) or None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - Windows 上 os.kill 语义不同
        return True
    return True


# ---------------------------------------------------------------------------
# 策略（脚本自有，不复用 nt_pairs.strategy——那条路径没有开仓/出场代码）
# ---------------------------------------------------------------------------


class SmokeOrderConfig(StrategyConfig):
    """脚本策略配置（2.0 的 StrategyConfig 是可变对象，不要 frozen）。"""

    def __init__(
        self,
        *,
        leader_id: str,
        follower_id: str,
        notional_usd: str,
        ttl_s: float,
        collar_bps: str,
        leader_cloid: str,
        follower_cloid: str,
        close_cloid_base: str,
        deadline_s: float,
        dry_run: bool,
        dry_cross_maker: bool,
        book_max_age_ms: int = 1000,
        force_fill: bool = False,
    ) -> None:
        super().__init__()
        self.leader_id = leader_id
        self.follower_id = follower_id
        self.notional_usd = notional_usd
        self.ttl_s = float(ttl_s)
        self.collar_bps = collar_bps
        self.leader_cloid = leader_cloid
        self.follower_cloid = follower_cloid
        self.close_cloid_base = close_cloid_base
        self.deadline_s = float(deadline_s)
        self.dry_run = bool(dry_run)
        self.dry_cross_maker = bool(dry_cross_maker)
        # 盘口新鲜度上限：超过这个年龄的快照不用于定价
        # （实测踩坑：拿 1.2 秒前的 bid 挂 maker，post-only 被交易所以
        #  "Post only order would have immediately matched" 拒单）。
        self.book_max_age_ms = int(book_max_age_ms)
        # 真单「强制成交」模式：leader 腿改用**穿价 taker**（挂在 ask、不带 post_only），
        # 目的是让链路验证能走到「真成交 → 对冲 → 平仓」这三步。
        # 代价：多付一次 HL 的 taker 费（0.90 bps）与价差，名义仍是 $20/腿。
        self.force_fill = bool(force_fill)


class SmokeOrderStrategy(Strategy):
    """一次性的两腿链路验证状态机。

    阶段：``wait_book`` → ``maker`` → ``hedge_wait`` → ``close_wait`` → ``done``；
    任何异常/超时都走 ``_fail`` 清理路径（撤单 + 打印账户持仓 + 非 0 退出码）。
    """

    def __init__(self, config: SmokeOrderConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self.leader_iid = InstrumentId.from_str(config.leader_id)
        self.follower_iid = InstrumentId.from_str(config.follower_id)
        self.ledger = LegLedger(
            self.leader_iid.venue.value, self.follower_iid.venue.value, Decimal("1")
        )
        self.finished = threading.Event()
        self.result: dict[str, Any] = {
            "phase": "init",
            "exit_code": EXIT_ORDER,
            "exit_reason": "",
            "error": None,
            "legs": {},
            "positions": {},
            "residual": "0",
            "naked_leader_equiv": "0",
            "naked_usd": "0",
            "cash_pnl_usd": "0",
            "fees_usd": "0",
            "round_pnl_usd": "0",
            "risk": {},
            "qty": None,
            "notional_usd": config.notional_usd,
            "cloids": {
                "leader": config.leader_cloid,
                "follower": config.follower_cloid,
                "hedge": f"{config.follower_cloid}-H",
                "close_base": config.close_cloid_base,
            },
        }
        self._phase = "wait_book"
        self._t0 = time.monotonic()
        self._subscribed = False
        self._maker_base_cloid = ClientOrderId(config.leader_cloid)
        self._maker_cloid = self._maker_base_cloid
        # 重挂时换 cloid（同一 cloid 已被交易所拒过，复用可能再被拒）
        self._maker_cloids: list[ClientOrderId] = [self._maker_base_cloid]
        self._maker_attempts = 0
        self._hedge_cloid = ClientOrderId(f"{config.follower_cloid}-H")
        self._submitted: list[ClientOrderId] = []
        self._close_orders: dict[str, list[ClientOrderId]] = {
            "leader": [],
            "follower": [],
        }
        # 链路里程碑（成功判据必须基于「两腿都真的成交过」，不能只看净持仓）
        self._maker_target_qty: Decimal = _ZERO
        self._maker_filled_qty: Decimal = _ZERO
        self._hedge_target_qty: Decimal = _ZERO
        self._hedge_filled_qty: Decimal = _ZERO
        self._hedge_ok = False
        self._post_hedge_positions: dict[str, Decimal] = {}
        self._residual_after_hedge: Optional[Decimal] = None
        self._qty: Optional[Decimal] = None
        self._leader_fill_px: Optional[Decimal] = None
        self._cash: dict[str, Decimal] = {}
        self._fee: dict[str, Decimal] = {}
        self._naked_since: Optional[float] = None
        self._hedge_abandoned_reason: Optional[str] = None
        self._maker_ts: float = time.monotonic()
        self._hedge_ts: float = time.monotonic()

    # -- 生命周期 ---------------------------------------------------------
    def on_start(self) -> None:
        # 订阅**不能**在 on_start 里做：此时行情 WS 客户端还没连上，
        # DataEngine 会报 "no client found for venue=..." 并（shutdown_on_error）
        # 直接把整个节点停掉。改为在 tick 里等 instrument 进 cache 后再订阅。
        self.clock.set_timer(
            name="smoke-tick",
            interval=timedelta(milliseconds=250),
            callback=self._tick,
        )
        self.log.info(
            f"smoke strategy started leader={self.cfg.leader_id} "
            f"follower={self.cfg.follower_id} dry_run={self.cfg.dry_run}"
        )

    def _ensure_subscribed(self) -> bool:
        """等两腿 instrument 进 cache（= 行情客户端已连上）后再订阅盘口。"""
        if self._subscribed:
            return True
        if (
            self._instrument(self.leader_iid) is None
            or self._instrument(self.follower_iid) is None
        ):
            return False
        # managed=True：DataEngine 才会把 OrderBook 放进 cache.order_book()
        self.subscribe_book_deltas(self.leader_iid, BookType.L2_MBP, managed=True)
        self.subscribe_book_deltas(self.follower_iid, BookType.L2_MBP, managed=True)
        # 同时订阅成交：沙盒撮合引擎靠 trade tick 触发成交（不订阅就永远不成交）
        self.subscribe_trades(self.leader_iid)
        self.subscribe_trades(self.follower_iid)
        self._subscribed = True
        self.log.info("subscribed to book deltas + trades on both legs")
        return True

    def on_stop(self) -> None:
        self.log.info("smoke strategy stopped")

    # -- 事件 -------------------------------------------------------------
    def on_order_filled(self, event: Any) -> None:
        """成交流水 → 账本 + 现金流水（账本只用于算两腿残差）。"""
        try:
            venue = str(event.instrument_id.venue.value)
            qty = _dec(event.last_qty)
            px = _dec(event.last_px)
            side = "BUY" if event.is_buy else "SELL"
            commission = getattr(event, "commission", None)
            fee = commission.as_decimal() if commission is not None else None
            self.ledger.on_fill(
                trade_id=str(event.trade_id),
                venue=venue,
                side=side,
                qty=qty,
                px=px,
                fee=fee,
            )
            cash = -qty * px if side == "BUY" else qty * px
            self._cash[venue] = self._cash.get(venue, _ZERO) + cash
            self._fee[venue] = self._fee.get(venue, _ZERO) + (fee or _ZERO)
            if event.instrument_id == self.leader_iid:
                self._leader_fill_px = px
                self._naked_since = time.monotonic()
            self.log.info(f"fill venue={venue} side={side} qty={qty} px={px} fee={fee}")
        except Exception as exc:  # noqa: BLE001 - 成交处理失败必须显式暴露
            self._fail(exc, phase="on_order_filled")

    def on_order_rejected(self, event: Any) -> None:
        # 拒单是**预期内**的业务结果（退出码 4），不能用 ERROR 级别：
        # LiveNodeConfig.shutdown_on_error=True 时 ERROR 日志会直接停掉节点。
        self.log.warning(f"order rejected {event.instrument_id} reason={event.reason}")

    def on_order_denied(self, event: Any) -> None:
        self.log.warning(f"order denied {event.instrument_id} reason={event.reason}")

    def on_order_canceled(self, event: Any) -> None:
        self.log.info(f"order canceled {event.instrument_id}")

    # -- 状态机 -----------------------------------------------------------
    def _tick(self, event: Any = None) -> None:
        if self.finished.is_set():
            return
        try:
            self._drive()
        except Exception as exc:  # noqa: BLE001
            self._fail(exc, phase=self._phase)

    def _drive(self) -> None:
        now = time.monotonic()
        if now - self._t0 > self.cfg.deadline_s:
            self._fail_timeout(f"总时限 {self.cfg.deadline_s}s 超时")
            return

        if self._phase == "wait_book":
            if not self._ensure_subscribed():
                return
            self._maybe_start_maker()
        elif self._phase == "maker":
            self._maybe_hedge(now)
        elif self._phase == "hedge_wait":
            self._maybe_close(now)
        elif self._phase == "close_wait":
            self._maybe_finish()

    # -- 阶段 1：等盘口 + 下 maker 单 --------------------------------------
    def _maybe_start_maker(self) -> None:
        leader_book = self._book(self.leader_iid)
        follower_book = self._book(self.follower_iid)
        if leader_book is None or follower_book is None:
            return
        leader_bid = leader_book.best_bid_price()
        leader_ask = leader_book.best_ask_price()
        follower_bid = follower_book.best_bid_price()
        follower_ask = follower_book.best_ask_price()
        if not all((leader_bid, leader_ask, follower_bid, follower_ask)):
            return
        leader_inst = self._instrument(self.leader_iid)
        follower_inst = self._instrument(self.follower_iid)
        if leader_inst is None or follower_inst is None:
            return

        leader_tick = _dec(leader_inst.price_increment)
        follower_tick = _dec(follower_inst.price_increment)
        grid = max(_dec(leader_inst.size_increment), _dec(follower_inst.size_increment))

        # 盘口新鲜度：快照太旧就别用它定价（否则会挂出穿价单被交易所拒）
        if not self._book_fresh(leader_book):
            return
        if not self._book_fresh(follower_book):
            return
        # 交叉盘口（bid >= ask）说明快照本身有问题，这一轮先不挂
        if _dec(leader_bid) >= _dec(leader_ask):
            return
        if _dec(follower_bid) >= _dec(follower_ask):
            return
        leader_mid = (_dec(leader_bid) + _dec(leader_ask)) / 2
        follower_mid = (_dec(follower_bid) + _dec(follower_ask)) / 2
        if self.cfg.force_fill:
            # 真单强制成交：穿价买入（taker），保证成交，验证对冲与平仓链路。
            # 挂在 ask 之上 10 bps：只按 ask 挂会落在盘口下方（实测 TTL 内不成交），
            # 多付的成本 = 名义 × 10 bps ≈ $0.02，可接受。
            ref_px = max(_dec(leader_ask), _dec(follower_ask))
            maker_px = _ceil_tick(_dec(leader_ask) * Decimal("1.001"), leader_tick)
        elif self.cfg.dry_cross_maker:
            # 仅沙盒：挂在卖价上、且**不带** post_only（沙盒的 post-only 校验会
            # 拒掉穿价单），目的是确定性地把「成交→对冲→平仓」链路走完；
            # 真实下单禁止该模式（--send 下会被拒绝）。
            ref_px = max(_dec(leader_ask), _dec(follower_ask))
            maker_px = _ceil_tick(_dec(leader_ask), leader_tick)
        else:
            ref_px = max(leader_mid, follower_mid)
            # 挂在 leader 最优买价**下一个 tick**：按构造就不会穿价
            maker_px = _floor_tick(_dec(leader_bid) - leader_tick, leader_tick)
        if ref_px <= 0:
            self._reject("盘口价格非法（ref_px<=0）")
            return

        notional = _dec(self.cfg.notional_usd)
        qty = (notional / ref_px / grid).to_integral_value(rounding=ROUND_FLOOR) * grid
        if qty <= 0:
            self._reject(f"名义 {notional} USD 换算出的数量为 0")
            return

        # 两所最小数量 / 最小名义都要满足（硬编码下限与交易所实际下限取更严者）
        for inst, venue in (
            (leader_inst, "HYPERLIQUID"),
            (follower_inst, "LIGHTER"),
        ):
            min_qty = VENUE_MIN_QTY[venue]
            if inst.min_quantity is not None:
                min_qty = max(min_qty, _dec(inst.min_quantity))
            min_notional = VENUE_MIN_NOTIONAL_USD[venue]
            if inst.min_notional is not None:
                min_notional = max(min_notional, _dec(inst.min_notional.as_decimal()))
            if qty < min_qty:
                self._reject(
                    f"{venue} 最小数量 {min_qty} > 本次数量 {qty}（名义 {notional} 太小）"
                )
                return
            if qty * ref_px < min_notional:
                self._reject(
                    f"{venue} 最小名义 {min_notional} USD > 本次名义 {qty * ref_px}"
                )
                return

        # 名义硬上限复核：限价 × 数量 ≤ 硬上限（真实成交价只会更优）
        if qty * maker_px > notional:
            self._reject(
                f"maker 限价 {maker_px} × 数量 {qty} 超过名义上限 {notional}"
            )
            return

        self._maker_cloid = self._cloid_for_attempt(self._maker_attempts)
        self._maker_cloids.append(self._maker_cloid)
        order = self._submit(
            instrument_id=self.leader_iid,
            side="BUY",
            qty=qty,
            price=maker_px,
            tif=TimeInForce.GTC,
            post_only=not (self.cfg.dry_cross_maker or self.cfg.force_fill),
            reduce_only=False,
            client_order_id=self._maker_cloid,
            tag="maker",
        )
        self._qty = qty
        self._maker_target_qty = qty
        self.result["qty"] = str(qty)
        self.result["ref_px"] = str(ref_px)
        self.result["maker_price"] = str(maker_px)
        self.result["phase"] = "maker"
        self._phase = "maker"
        self._maker_ts = time.monotonic()
        self.log.info(
            f"maker submitted cloid={self._maker_cloid} qty={qty} px={maker_px} "
            f"post_only={not (self.cfg.dry_cross_maker or self.cfg.force_fill)} "
            f"tif=GTC ttl={self.cfg.ttl_s}s"
        )
        self.log.info(
            f"maker pricing leader_bid={leader_bid} leader_ask={leader_ask} "
            f"follower_bid={follower_bid} follower_ask={follower_ask} "
            f"book_age_ms={int((time.time_ns() - int(getattr(leader_book, 'ts_event', 0) or 0)) / 1_000_000)} "
            f"force_fill={self.cfg.force_fill} maker_px={maker_px}"
        )
        _ = order

    # -- 阶段 2：maker 成交 → 对冲 ----------------------------------------
    def _maybe_hedge(self, now: float) -> None:
        order = self._order(self._maker_cloid)
        filled = _dec(order.filled_qty) if order is not None else _ZERO
        if filled > 0:
            self._maker_filled_qty = filled
            # 先撤掉 maker 余量（避免半裸腿继续扩大），再对冲已成交部分
            if order is not None and not order.is_closed:
                self.cancel_order(self._maker_cloid)
            self._submit_hedge()
            return
        # TTL 从**下单时刻**算起（不是从策略启动算起）
        if (now - self._maker_ts) >= self.cfg.ttl_s:
            if order is not None and not order.is_closed:
                self.cancel_order(self._maker_cloid)
                self.log.warning(f"maker TTL {self.cfg.ttl_s}s 到期仍未成交 → 撤单退出")
            self.result["phase"] = "maker_timeout"
            self._finish(exit_code=EXIT_ORDER, reason="maker 挂单 TTL 到期未成交")
            return
        if order is not None and order.is_closed:
            # 穿价被拒（post-only）→ 按最新盘口重新定价再挂，最多 MAX_MAKER_ATTEMPTS 次
            if "REJECT" in str(order.status) and self._maker_attempts + 1 < MAX_MAKER_ATTEMPTS:
                self._maker_attempts += 1
                self.log.warning(
                    f"maker 被拒（{order.status}）→ 按最新盘口重挂 "
                    f"第 {self._maker_attempts + 1} 次"
                )
                self._phase = "wait_book"
                self._maker_ts = time.monotonic()
                return
            self.result["phase"] = "maker_closed"
            self._finish(
                exit_code=EXIT_ORDER,
                reason=f"maker 单提前结束（status={order.status}，未成交）",
            )

    def _submit_hedge(self) -> None:
        """用 Lighter IOC 限价对冲已成交的 maker 腿。"""
        follower_book = self._book(self.follower_iid)
        if follower_book is None:
            self._fail(RuntimeError("follower 盘口缺失，无法对冲"), phase="hedge")
            return
        bid = follower_book.best_bid_price()
        ask = follower_book.best_ask_price()
        if not bid or not ask:
            self._fail(RuntimeError("follower 盘口为空，无法对冲"), phase="hedge")
            return
        inst = self._instrument(self.follower_iid)
        if inst is None:
            self._fail(RuntimeError("follower instrument 缺失"), phase="hedge")
            return

        residual = self.ledger.residual()
        collar = _dec(self.cfg.collar_bps)
        if self._leader_fill_px is None:
            self._abandon_hedge("maker 成交价缺失，无法计算对冲基差")
            return
        follower_mid = (_dec(bid) + _dec(ask)) / 2
        drift_bps = abs(follower_mid / self._leader_fill_px - 1) * 10_000
        if drift_bps > collar:
            # collar 之外 → 放弃对冲，记录裸腿（后续走平仓 + 退出码 5）
            self._abandon_hedge(
                f"基差漂移 {drift_bps:.1f} bps 超过 collar {collar} bps"
            )
            return

        spec = hedge_order(
            residual_qty=residual,
            book_bid=_dec(bid),
            book_ask=_dec(ask),
            tick=_dec(inst.price_increment),
            size_increment=_dec(inst.size_increment),
            min_qty=max(
                VENUE_MIN_QTY["LIGHTER"],
                _dec(inst.min_quantity) if inst.min_quantity is not None else _ZERO,
            ),
            collar_bps=collar,
            stage="t3_ioc",
        )
        if spec is None:
            self._abandon_hedge(
                f"对冲数量 {abs(residual)} 低于 Lighter 最小下单量（dust）"
            )
            return

        self._submit(
            instrument_id=self.follower_iid,
            side=spec.side,
            qty=spec.qty,
            price=spec.price,
            tif=TimeInForce.IOC,
            post_only=False,
            reduce_only=False,
            client_order_id=self._hedge_cloid,
            tag="hedge",
        )
        self.result["hedge_price"] = str(spec.price)
        self.result["hedge_collar_bps"] = str(spec.collar_bps)
        self.result["phase"] = "hedge_wait"
        self._phase = "hedge_wait"
        self._hedge_ts = time.monotonic()
        self._hedge_target_qty = spec.qty
        self.log.info(
            f"hedge submitted cloid={self._hedge_cloid} side={spec.side} "
            f"qty={spec.qty} px={spec.price} tif=IOC collar={collar}bps"
        )

    def _abandon_hedge(self, reason: str) -> None:
        """放弃对冲 → 记录裸腿 → 直接进入平仓阶段（退出码 5）。"""
        self._hedge_abandoned_reason = reason
        # 注意：不能用 ERROR 级别（shutdown_on_error=True 会直接停节点）
        self.log.warning(f"放弃对冲：{reason}")
        self.result["phase"] = "close_wait"
        self._phase = "close_wait"
        self._start_close()

    # -- 阶段 3：对冲结算 → 平仓 ------------------------------------------
    def _maybe_close(self, now: float) -> None:
        order = self._order(self._hedge_cloid)
        settled = order is not None and order.is_closed
        hedge_deadline = self._hedge_ts + ORDER_SETTLE_TIMEOUT_S
        if not settled and now < hedge_deadline:
            return
        if not settled:
            self.cancel_order(self._hedge_cloid)
            self.log.warning("对冲单未在时限内结算 → 视为对冲失败")
            self._hedge_abandoned_reason = "对冲单超时未成交"
        # 对冲成交情况（成功判据：两腿都真的成交过）
        if order is not None:
            self._hedge_filled_qty = _dec(order.filled_qty)
        self._hedge_ok = (
            self._hedge_target_qty > 0
            and self._hedge_filled_qty >= self._hedge_target_qty
            and not self._hedge_abandoned_reason
        )
        self._residual_after_hedge = self.ledger.residual()
        self._post_hedge_positions = {
            "leader": self._account_position(self.leader_iid),
            "follower": self._account_position(self.follower_iid),
        }
        self.log.info(
            f"hedge settled filled={self._hedge_filled_qty}/{self._hedge_target_qty} "
            f"ok={self._hedge_ok} residual={self._residual_after_hedge} "
            f"positions={self._post_hedge_positions}"
        )
        self.result["phase"] = "close_wait"
        self._phase = "close_wait"
        self._start_close()

    def _start_close(self) -> None:
        """用 reduce-only 限价 IOC 平掉两腿。"""
        for role, iid, tag in (
            ("leader", self.leader_iid, "L"),
            ("follower", self.follower_iid, "F"),
        ):
            pos = self._account_position(iid)
            if pos == 0:
                continue
            book = self._book(iid)
            inst = self._instrument(iid)
            if book is None or inst is None:
                self.log.warning(f"平仓失败：{iid} 盘口或 instrument 缺失")
                continue
            bid = book.best_bid_price()
            ask = book.best_ask_price()
            if not bid or not ask:
                self.log.warning(f"平仓失败：{iid} 盘口为空")
                continue
            tick = _dec(inst.price_increment)
            collar = _dec(self.cfg.collar_bps)
            if pos > 0:
                side = "SELL"
                price = _floor_tick(_dec(bid) * (1 - collar / 10_000), tick)
            else:
                side = "BUY"
                price = _ceil_tick(_dec(ask) * (1 + collar / 10_000), tick)
            step = _dec(inst.size_increment)
            qty = (abs(pos) / step).to_integral_value(rounding=ROUND_FLOOR) * step
            if qty <= 0:
                self.log.warning(f"平仓失败：{iid} 持仓 {pos} 换算后数量为 0")
                continue
            cloid = ClientOrderId(f"{self.cfg.close_cloid_base}-{tag}")
            self._close_orders[role].append(cloid)
            self._submit(
                instrument_id=iid,
                side=side,
                qty=qty,
                price=price,
                tif=TimeInForce.IOC,
                post_only=False,
                reduce_only=True,
                client_order_id=cloid,
                tag=f"close-{tag}",
            )
            self.log.info(
                f"close submitted cloid={cloid} {iid} {side} qty={qty} px={price} "
                f"reduce_only=True"
            )

    # -- 阶段 4：收尾 ------------------------------------------------------
    def _maybe_finish(self) -> None:
        pending = False
        for role in ("leader", "follower"):
            for cloid in self._close_orders[role]:
                order = self._order(cloid)
                if order is not None and not order.is_closed:
                    pending = True
                    break
        if pending:
            if time.monotonic() - self._t0 > self.cfg.deadline_s - 10:
                for role in ("leader", "follower"):
                    for cloid in self._close_orders[role]:
                        order = self._order(cloid)
                        if order is not None and not order.is_closed:
                            self.cancel_order(cloid)
                self.log.warning("平仓单未在时限内结算 → 强制撤单")
            return
        self._collect()
        leader_pos = self._account_position(self.leader_iid)
        follower_pos = self._account_position(self.follower_iid)
        residual = self.ledger.residual()
        naked = self.ledger.naked_leader_equiv()
        if not self._hedge_ok:
            # 对冲没成交（或只成交一部分）→ 链路未验证；即便现在净持仓是平的，
            # 也要按裸腿处理（退出码 5），不能报成功。
            detail = self._hedge_abandoned_reason or (
                f"对冲只成交 {self._hedge_filled_qty}/{self._hedge_target_qty}"
            )
            self.result["phase"] = "naked"
            self._finish(
                exit_code=EXIT_NAKED,
                reason=f"裸腿/对冲未完成：{detail}",
            )
            return
        if leader_pos != 0 or follower_pos != 0 or naked != 0:
            self.result["phase"] = "not_flat"
            self._finish(
                exit_code=EXIT_NAKED,
                reason=(
                    f"未平干净：leader={leader_pos} follower={follower_pos} "
                    f"residual={residual} naked={naked}"
                ),
            )
            return
        if leader_pos != 0 or follower_pos != 0 or naked != 0:
            self.result["phase"] = "not_flat"
            self._finish(
                exit_code=EXIT_NAKED,
                reason=(
                    f"未平干净：leader={leader_pos} follower={follower_pos} "
                    f"residual={residual} naked={naked}"
                ),
            )
            return
        self.result["phase"] = "done"
        self._finish(exit_code=EXIT_OK, reason="两腿已成交并平掉")

    # -- 工具 --------------------------------------------------------------
    def _book(self, instrument_id: InstrumentId) -> Any:
        try:
            return self.cache.order_book(instrument_id)
        except Exception:  # noqa: BLE001 - cache 里还没有该 book
            return None

    def _cloid_for_attempt(self, attempt: int) -> ClientOrderId:
        """第 0 次用基础 cloid，之后每次重挂加 -A{n} 后缀（不复用被拒的 cloid）。"""
        if attempt <= 0:
            return self._maker_base_cloid
        return ClientOrderId(f"{self._maker_base_cloid}-A{attempt}")

    def _book_fresh(self, book: Any) -> bool:
        """盘口快照是否够新（超过 book_max_age_ms 就不用于定价）。"""
        ts = int(getattr(book, "ts_event", 0) or 0)
        if not ts:
            return False
        age_ms = (time.time_ns() - ts) / 1_000_000
        return age_ms <= max(1, int(self.cfg.book_max_age_ms))

    def _instrument(self, instrument_id: InstrumentId) -> Any:
        try:
            return self.cache.instrument(instrument_id)
        except Exception:  # noqa: BLE001
            return None

    def _order(self, cloid: ClientOrderId) -> Any:
        try:
            return self.cache.order(cloid)
        except Exception:  # noqa: BLE001
            return None

    def _account_position(self, instrument_id: InstrumentId) -> Decimal:
        """从账户侧（portfolio/OMS 状态）读持仓——不是脚本内存账本。"""
        try:
            return _dec(self.portfolio.net_position(instrument_id))
        except Exception:  # noqa: BLE001
            return _ZERO

    def _submit(
        self,
        *,
        instrument_id: InstrumentId,
        side: str,
        qty: Decimal,
        price: Decimal,
        tif: TimeInForce,
        post_only: bool,
        reduce_only: bool,
        client_order_id: ClientOrderId,
        tag: str,
    ) -> Any:
        inst = self._instrument(instrument_id)
        if inst is None:
            raise RuntimeError(f"instrument 缺失：{instrument_id}")
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=Quantity(qty, inst.size_precision),
            price=Price(price, inst.price_precision),
            time_in_force=tif,
            post_only=post_only,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
        self.submit_order(order)
        self._submitted.append(client_order_id)
        return order

    # -- 收尾 / 失败 -------------------------------------------------------
    def _collect(self) -> None:
        """收集两腿订单状态、成交均价、账户持仓与残差。"""
        legs: dict[str, Any] = {}
        for role, iid, cloids in (
            (
                "leader",
                self.leader_iid,
                list(dict.fromkeys([*self._maker_cloids, *self._close_orders["leader"]])),
            ),
            (
                "follower",
                self.follower_iid,
                [self._hedge_cloid, *self._close_orders["follower"]],
            ),
        ):
            entries = []
            for cloid in cloids:
                order = self._order(cloid)
                if order is None:
                    entries.append({"cloid": str(cloid), "status": "UNKNOWN"})
                    continue
                entries.append(
                    {
                        "cloid": str(cloid),
                        "venue_order_id": str(order.venue_order_id),
                        "status": str(order.status),
                        "filled_qty": str(order.filled_qty),
                        "leaves_qty": str(order.leaves_qty),
                        "avg_px": str(order.avg_px),
                        "price": str(order.price),
                        "quantity": str(order.quantity),
                        "post_only": bool(order.is_post_only),
                        "reduce_only": bool(order.is_reduce_only),
                        "tif": str(order.time_in_force),
                    }
                )
            legs[role] = {
                "instrument_id": str(iid),
                "venue": str(iid.venue.value),
                "account_position": str(self._account_position(iid)),
                "orders": entries,
            }
        self.result["legs"] = legs
        self.result["positions"] = {
            "leader": str(self._account_position(self.leader_iid)),
            "follower": str(self._account_position(self.follower_iid)),
            "source": "portfolio.net_position（账户侧状态）",
        }
        self.result["residual"] = str(self.ledger.residual())
        self.result["naked_leader_equiv"] = str(self.ledger.naked_leader_equiv())
        # 链路里程碑：成功判据 = maker 成交 + 对冲成交 + 两腿平掉
        self.result["milestones"] = {
            "maker_target_qty": str(self._maker_target_qty),
            "maker_filled_qty": str(self._maker_filled_qty),
            "hedge_target_qty": str(self._hedge_target_qty),
            "hedge_filled_qty": str(self._hedge_filled_qty),
            "hedge_ok": self._hedge_ok,
            "post_hedge_positions": {
                k: str(v) for k, v in self._post_hedge_positions.items()
            },
            "residual_after_hedge": None
            if self._residual_after_hedge is None
            else str(self._residual_after_hedge),
            "hedge_abandoned_reason": self._hedge_abandoned_reason,
        }
        ref_px = Decimal(str(self.result.get("ref_px") or "1"))
        self.result["naked_usd"] = str(self.ledger.unhedged_usd(ref_px))
        cash = sum(self._cash.values(), _ZERO)
        fees = sum(self._fee.values(), _ZERO)
        self.result["cash_pnl_usd"] = str(cash)
        self.result["fees_usd"] = str(fees)
        self.result["round_pnl_usd"] = str(cash - fees)
        # 裸腿年龄分级（复用 nt_pairs.risk；权益未核实 → equity 传 1，权益闸门不生效）
        now = time.monotonic()
        action, reason = evaluate(
            pos_known=True,
            naked_usd=self.ledger.unhedged_usd(ref_px),
            naked_since=self._naked_since,
            equity_now=Decimal("1"),
            equity_peak=Decimal("1"),
            quota_left=10_000,
            now=now,
            limits=RiskLimits(),
        )
        self.result["risk"] = {
            "action": action.value,
            "reason": reason,
            "naked_age_s": None
            if self._naked_since is None
            else round(now - self._naked_since, 3),
            "note": "equity 参数固定为 1（账户权益未核实，权益闸门不生效）",
        }

    def _cancel_ours(self) -> None:
        """撤掉本脚本发出的全部未成交订单。"""
        seen: set[str] = set()
        close_cloids = [
            cloid
            for role in ("leader", "follower")
            for cloid in self._close_orders[role]
        ]
        for cloid in list(self._submitted) + close_cloids:
            if str(cloid) in seen:
                continue
            seen.add(str(cloid))
            order = self._order(cloid)
            if order is None or order.is_closed:
                continue
            try:
                self.cancel_order(cloid)
                self.log.warning(f"cleanup: cancel {cloid}")
            except Exception as exc:  # noqa: BLE001
                self.log.error(f"cleanup: cancel {cloid} 失败：{exc}")

    def _finish(self, *, exit_code: int, reason: str) -> None:
        """结束（成功 / 业务失败）：先撤单再收尾。"""
        self._cancel_ours()
        self._collect()
        self.result["exit_code"] = exit_code
        self.result["exit_reason"] = reason
        self.log.info(f"smoke finished exit_code={exit_code} reason={reason}")
        self.finished.set()

    def _reject(self, reason: str) -> None:
        """参数/上限被拒（退出码 2）。"""
        self.result["exit_code"] = EXIT_LIMIT
        self.result["exit_reason"] = reason
        self.result["phase"] = "rejected"
        self.log.error(f"rejected: {reason}")
        self.finished.set()

    def _fail(self, exc: BaseException, *, phase: str) -> None:
        """异常路径：撤单 + 打印两腿账户持仓 + 非 0 退出码。"""
        self.result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "phase": phase,
        }
        self.result["exit_code"] = EXIT_ORDER
        self.result["exit_reason"] = f"{phase} 阶段异常：{type(exc).__name__}: {exc}"
        self.result["phase"] = "failed"
        try:
            self._cancel_ours()
            self._collect()
        except Exception:  # noqa: BLE001 - 清理失败也不能吞掉原始异常
            pass
        self.log.error(f"smoke failed in {phase}: {type(exc).__name__}: {exc}")
        self.finished.set()

    def _fail_timeout(self, reason: str) -> None:
        """超时路径（与异常路径同样清理 + 非 0 退出码）。"""
        self.result["error"] = {
            "type": "TimeoutError",
            "message": reason,
            "traceback": traceback.format_exc(),
            "phase": self._phase,
        }
        self.result["exit_code"] = EXIT_ORDER
        self.result["exit_reason"] = reason
        self.result["phase"] = "timeout"
        try:
            self._cancel_ours()
            self._collect()
        except Exception:  # noqa: BLE001
            pass
        self.finished.set()


# ---------------------------------------------------------------------------
# 节点装配
# ---------------------------------------------------------------------------


def _build_plan(args: argparse.Namespace, *, dry_run: bool) -> LivePlan:
    """脚本自用的单标的计划（不走 load_live_plan——不读配置文件）。"""
    leader = LegPlan(
        role="leader",
        venue="HYPERLIQUID",
        instrument_id=args.leader,
        direction="BUY",
        max_qty=Decimal("1000"),
        maker_fee_bps=VENUE_FEES_BPS["HYPERLIQUID"]["maker"],
        taker_fee_bps=VENUE_FEES_BPS["HYPERLIQUID"]["taker"],
    )
    follower = LegPlan(
        role="follower",
        venue="LIGHTER",
        instrument_id=args.follower,
        direction="SELL",
        max_qty=Decimal("1000"),
        maker_fee_bps=VENUE_FEES_BPS["LIGHTER"]["maker"],
        taker_fee_bps=VENUE_FEES_BPS["LIGHTER"]["taker"],
    )
    return LivePlan(
        name="nt-pairs-smoke-order",
        leader=leader,
        follower=follower,
        ratio=Decimal("1"),
        sigma_1m_bps=Decimal("15"),
        max_leg_usd=_dec(args.notional_usd),
        dry_run=dry_run,
        state_dir=str(args.state_dir),
        book_max_age_ms=args.book_max_age_ms,
        # 订阅计划决定 build_client_specs 要装哪些客户端（不填 → 一个客户端都不装）
        subscriptions=(
            SubscriptionPlan(
                leader.instrument_id, "L2_MBP", "leader", "HYPERLIQUID"
            ),
            SubscriptionPlan(
                follower.instrument_id, "L2_MBP", "follower", "LIGHTER"
            ),
        ),
    )


def _sandbox_exec_config(venue: str, leg: LegPlan) -> Any:
    """沙盒执行端配置（币种按场所的标的计价货币）。

    与 ``nt_pairs.live_node._sandbox_exec_config`` 的唯一差别：计价货币按场所取
    （Lighter 标的以 USDC 计价），否则 RiskEngine 会因缺汇率映射 DENY 订单。
    """
    currency = Currency.from_str(SANDBOX_BASE_CURRENCY.get(venue, "USD"))
    maker_rate = Decimal(leg.maker_fee_bps) / Decimal(10_000)
    taker_rate = Decimal(leg.taker_fee_bps) / Decimal(10_000)
    return SandboxExecutionClientConfig(
        venue=Venue(venue),
        starting_balances=[Money(1_000_000, currency)],
        base_currency=currency,
        fee_model=MakerTakerFeeModel(maker_rate=maker_rate, taker_rate=taker_rate),
    )


def _build_node(
    plan: LivePlan,
    strategy: SmokeOrderStrategy,
    *,
    environment: str,
    connect_timeout_s: int,
) -> tuple[Any, list[Any]]:
    """按计划组装 LiveNode（数据端 + 执行端/沙盒端）。"""
    specs = build_client_specs(plan, dry_run=plan.dry_run, environment=environment)
    config = LiveNodeConfig(
        environment=None,
        load_state=False,
        save_state=False,
        shutdown_on_error=True,
    )
    builder = LiveNodeBuilder.from_config("nt-pairs-smoke-order", config)
    # nautilus 2.0.0rc6 的 with_timeout_connection 收「秒数 int」，不是 timedelta
    builder = builder.with_timeout_connection(connect_timeout_s)
    builder = builder.with_timeout_disconnection_secs(30)
    legs_by_venue = {plan.leader.venue: plan.leader, plan.follower.venue: plan.follower}
    for spec in specs:
        if spec.kind == "data":
            builder.add_data_client(spec.name, spec.factory, spec.config)
        elif spec.kind == "exec":
            builder.add_exec_client(spec.name, spec.factory, spec.config)
        else:
            leg = legs_by_venue[spec.venue]
            builder.add_simulated_exec_client(
                spec.name, spec.factory, _sandbox_exec_config(spec.venue, leg)
            )
    node = builder.build()
    node.add_strategy(strategy)
    return node, specs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="live_smoke_order",
        description=(
            "小额真单链路验证（HL maker 腿 + Lighter taker 腿）。"
            "默认沙盒；只有 --send 才发真实订单。"
        ),
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="发真实订单（默认不发，走 nautilus SandboxExecutionClient 沙盒）",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="显式沙盒模式（与默认行为相同；与 --send 互斥）",
    )
    parser.add_argument(
        "--notional-usd",
        default=str(DEFAULT_NOTIONAL_USD),
        help=f"单腿名义（美元），硬上限 {MAX_NOTIONAL_USD}（默认 {DEFAULT_NOTIONAL_USD}）",
    )
    parser.add_argument(
        "--ttl-s",
        type=float,
        default=DEFAULT_TTL_S,
        help=f"maker 挂单 TTL（秒），上限 {MAX_TTL_S}（默认 {DEFAULT_TTL_S}）",
    )
    parser.add_argument(
        "--hedge-collar-bps",
        default=str(MAX_COLLAR_BPS_HARD),
        help=f"对冲 collar（bps），上限 {MAX_COLLAR_BPS_HARD}（默认 {MAX_COLLAR_BPS_HARD}）",
    )
    parser.add_argument("--leader", default=DEFAULT_LEADER_ID, help="maker 腿 instrument id")
    parser.add_argument("--follower", default=DEFAULT_FOLLOWER_ID, help="taker 腿 instrument id")
    parser.add_argument(
        "--state-dir",
        default=DEFAULT_STATE_DIR,
        help=f"状态目录（轮次/亏损/cloid），默认 {DEFAULT_STATE_DIR}",
    )
    parser.add_argument(
        "--audit-out",
        default=None,
        help="审计 JSON 输出路径（默认 data/smoke_order_<utcstamp>.json）",
    )
    parser.add_argument(
        "--environment",
        default="mainnet",
        choices=("mainnet", "testnet"),
        help="适配器环境（默认 mainnet）",
    )
    parser.add_argument(
        "--force-fill",
        action="store_true",
        help="真单强制成交：leader 腿用穿价 taker（挂在 ask、不带 post_only），"
        "只为验证「成交→对冲→平仓」链路；仅限 --send",
    )
    parser.add_argument(
        "--book-max-age-ms",
        type=int,
        default=1000,
        help="盘口快照最大年龄（毫秒）；超过则不使用该快照定价",
    )
    parser.add_argument(
        "--deadline-s",
        type=float,
        default=DEFAULT_DEADLINE_S,
        help=f"单轮总时限（秒），默认 {DEFAULT_DEADLINE_S}",
    )
    parser.add_argument(
        "--dry-cross-maker",
        action="store_true",
        help=(
            "仅沙盒：把 maker 单挂到卖价上，强制沙盒撮合走完整链路"
            "（--send 下会被拒绝）"
        ),
    )
    return parser.parse_args(argv)


def _fail_json(payload: dict[str, Any], code: int) -> int:
    print(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default),
        file=sys.stderr,
    )
    return code


async def _run_node(node: Any, strategy: SmokeOrderStrategy) -> None:
    """跑节点直到策略跑完（或节点先挂掉）。"""
    runner = asyncio.create_task(node.run_async())
    node_error: Optional[BaseException] = None
    try:
        while not strategy.finished.is_set():
            if runner.done():
                break
            await asyncio.sleep(0.1)
    finally:
        try:
            node.handle().stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(runner, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            runner.cancel()
        except Exception as exc:  # noqa: BLE001 - 节点异常必须上抛
            node_error = exc
    if node_error is not None:
        raise node_error


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 主函数。"""
    args = _parse_args(argv)
    started = time.monotonic()

    # -- 1) 参数硬上限（先于任何网络/凭据访问） ---------------------------
    try:
        notional = _dec(args.notional_usd)
    except Exception:  # noqa: BLE001
        return _fail_json(
            {"error": "invalid_notional", "value": args.notional_usd}, EXIT_LIMIT
        )
    if notional <= 0:
        return _fail_json(
            {"error": "notional_must_be_positive", "value": str(notional)}, EXIT_LIMIT
        )
    if notional > MAX_NOTIONAL_USD:
        return _fail_json(
            {
                "error": "notional_over_limit",
                "value": str(notional),
                "max_notional_usd": str(MAX_NOTIONAL_USD),
                "hint": "单腿名义硬上限 50 USD；超过直接拒绝",
            },
            EXIT_LIMIT,
        )
    if args.ttl_s <= 0 or args.ttl_s > MAX_TTL_S:
        return _fail_json(
            {
                "error": "ttl_out_of_range",
                "value": args.ttl_s,
                "max_ttl_s": MAX_TTL_S,
            },
            EXIT_LIMIT,
        )
    try:
        collar = _dec(args.hedge_collar_bps)
    except Exception:  # noqa: BLE001
        return _fail_json(
            {"error": "invalid_collar", "value": args.hedge_collar_bps}, EXIT_LIMIT
        )
    if collar < 0 or collar > MAX_COLLAR_BPS_HARD:
        return _fail_json(
            {
                "error": "collar_out_of_range",
                "value": str(collar),
                "max_collar_bps": str(MAX_COLLAR_BPS_HARD),
            },
            EXIT_LIMIT,
        )
    if args.send and args.dry:
        return _fail_json({"error": "send_and_dry_mutually_exclusive"}, EXIT_LIMIT)
    if args.force_fill and not args.send:
        return _fail_json(
            {"error": "force_fill_requires_send", "hint": "--force-fill 只在 --send 下允许"},
            EXIT_LIMIT,
        )
    if args.send and args.dry_cross_maker:
        return _fail_json(
            {
                "error": "dry_cross_maker_requires_dry",
                "hint": "--dry-cross-maker 只在沙盒下允许",
            },
            EXIT_LIMIT,
        )

    send = bool(args.send)
    dry_run = not send

    # -- 2) 锁 + 每日状态闸门 ---------------------------------------------
    state_dir = Path(args.state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail_json(
            {"error": "state_dir_unwritable", "path": str(state_dir), "detail": str(exc)},
            EXIT_LIMIT,
        )
    lock = _RunLock(state_dir / "smoke_order.lock")
    lock_reason = lock.acquire()
    if lock_reason:
        return _fail_json(
            {"error": "concurrent_run_refused", "detail": lock_reason}, EXIT_LIMIT
        )

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = SmokeState.load(state_dir / "smoke_state.json", day)
    gate = state.gate_reason()
    if gate:
        lock.release()
        return _fail_json(
            {
                "error": "daily_gate_refused",
                "detail": gate,
                "day": state.day,
                "rounds": state.rounds,
                "max_rounds_per_day": MAX_ROUNDS_PER_DAY,
                "loss_usd": str(state.loss_usd),
                "daily_loss_limit_usd": str(DAILY_LOSS_LIMIT_USD),
            },
            EXIT_LIMIT,
        )

    # cloid 幂等：当日复用同一组 cloid（交易所侧去重，重复运行不会重复下单）
    seq = state.rounds + 1
    cloid_suffix = f"{day.replace('-', '')}-{seq:02d}"
    leader_cloid = state.cloids.get("leader") or f"SMOKE-HL-{cloid_suffix}"
    follower_cloid = state.cloids.get("follower") or f"SMOKE-LT-{cloid_suffix}"
    close_cloid_base = f"SMOKE-CL-{cloid_suffix}"
    state.cloids = {
        "leader": leader_cloid,
        "follower": follower_cloid,
        "close_base": close_cloid_base,
    }

    audit_path = (
        Path(args.audit_out)
        if args.audit_out
        else _REPO_ROOT / "data" / f"smoke_order_{_utc_stamp()}.json"
    )

    # -- 3) 装配（凭据缺失 → 退出码 3） -----------------------------------
    plan = _build_plan(args, dry_run=dry_run)
    strategy_cfg = SmokeOrderConfig(
        leader_id=args.leader,
        follower_id=args.follower,
        notional_usd=str(notional),
        ttl_s=args.ttl_s,
        collar_bps=str(collar),
        leader_cloid=leader_cloid,
        follower_cloid=follower_cloid,
        close_cloid_base=close_cloid_base,
        deadline_s=args.deadline_s,
        dry_run=dry_run,
        dry_cross_maker=bool(args.dry_cross_maker),
        force_fill=bool(args.force_fill),
        book_max_age_ms=args.book_max_age_ms,
    )
    strategy = SmokeOrderStrategy(strategy_cfg)

    try:
        node, specs = _build_node(
            plan, strategy, environment=args.environment, connect_timeout_s=60
        )
    except MissingCredentials as exc:
        lock.release()
        return _fail_json(exc.as_dict(), EXIT_CREDENTIALS)
    except Exception as exc:  # noqa: BLE001
        lock.release()
        return _fail_json(
            {
                "error": "node_build_failed",
                "exception": type(exc).__name__,
                "detail": str(exc),
            },
            EXIT_CREDENTIALS,
        )

    # 沙盒模式自检：执行端必须全是沙盒（不可能发真实订单）
    if dry_run:
        exec_specs = [s for s in specs if s.kind != "data"]
        if any(s.kind != "sim_exec" for s in exec_specs):
            lock.release()
            return _fail_json(
                {
                    "error": "dry_run_exec_client_not_sandbox",
                    "clients": [{"name": s.name, "kind": s.kind} for s in exec_specs],
                },
                EXIT_CREDENTIALS,
            )

    print(
        json.dumps(
            {
                "event": "smoke_order_start",
                "mode": "send" if send else "dry",
                "environment": args.environment,
                "instrument": {"leader": args.leader, "follower": args.follower},
                "notional_usd": str(notional),
                "cloids": {
                    "leader": leader_cloid,
                    "follower": follower_cloid,
                    "close_base": close_cloid_base,
                },
                "clients": [
                    {"name": s.name, "kind": s.kind, "venue": s.venue} for s in specs
                ],
                "state": {
                    "day": state.day,
                    "rounds": state.rounds,
                    "dry_runs": state.dry_runs,
                    "loss_usd": str(state.loss_usd),
                },
            },
            ensure_ascii=False,
            default=_json_default,
        ),
        flush=True,
    )

    # 真实轮次先占坑再下单——中途崩溃也不会给第二次机会
    if send:
        state.reserve_round()

    # -- 4) 跑 ------------------------------------------------------------
    try:
        asyncio.run(_run_node(node, strategy))
    except Exception as exc:  # noqa: BLE001 - 顶层异常必须显式失败
        strategy.result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "phase": "node",
        }
        strategy.result["exit_code"] = EXIT_ORDER
        strategy.result["exit_reason"] = f"节点异常：{type(exc).__name__}: {exc}"
    finally:
        if not strategy.finished.is_set():
            strategy.result.setdefault("exit_reason", "")
            strategy.result["exit_code"] = EXIT_ORDER
            strategy.result["exit_reason"] = (
                strategy.result.get("exit_reason")
                or f"策略未在 {args.deadline_s}s 内结束（节点先停）"
            )
            strategy.finished.set()

    exit_code = int(strategy.result.get("exit_code", EXIT_ORDER))
    duration = time.monotonic() - started

    # -- 5) 审计 JSON ------------------------------------------------------
    audit = {
        "schema": "nt-pairs/smoke-order/v1",
        "started_utc": datetime.fromtimestamp(
            time.time() - duration, tz=timezone.utc
        ).isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(duration, 3),
        "mode": "send" if send else "dry",
        "environment": args.environment,
        "instrument": {
            "leader": args.leader,
            "follower": args.follower,
            "leader_venue": "HYPERLIQUID",
            "follower_venue": "LIGHTER",
        },
        "notional_usd": str(notional),
        "qty": strategy.result.get("qty"),
        "cloids": {
            "leader": leader_cloid,
            "follower": follower_cloid,
            "hedge": f"{follower_cloid}-H",
            "close_base": close_cloid_base,
        },
        "limits": {
            "max_notional_usd": str(MAX_NOTIONAL_USD),
            "max_ttl_s": MAX_TTL_S,
            "max_collar_bps": str(MAX_COLLAR_BPS_HARD),
            "max_rounds_per_day": MAX_ROUNDS_PER_DAY,
            "daily_loss_limit_usd": str(DAILY_LOSS_LIMIT_USD),
            "venue_min_qty": {k: str(v) for k, v in VENUE_MIN_QTY.items()},
            "venue_min_notional_usd": {
                k: str(v) for k, v in VENUE_MIN_NOTIONAL_USD.items()
            },
        },
        "legs": strategy.result.get("legs", {}),
        "milestones": strategy.result.get("milestones", {}),
        "positions": strategy.result.get("positions", {}),
        "residual": strategy.result.get("residual"),
        "naked_leader_equiv": strategy.result.get("naked_leader_equiv"),
        "naked_usd": strategy.result.get("naked_usd"),
        "cash_pnl_usd": strategy.result.get("cash_pnl_usd"),
        "fees_usd": strategy.result.get("fees_usd"),
        "round_pnl_usd": strategy.result.get("round_pnl_usd"),
        "risk": strategy.result.get("risk", {}),
        "phase": strategy.result.get("phase"),
        "exit_code": exit_code,
        "exit_reason": strategy.result.get("exit_reason"),
        "error": strategy.result.get("error"),
        "state": {
            "path": str(state.path),
            "day": state.day,
            "rounds": state.rounds,
            "dry_runs": state.dry_runs,
            "loss_usd": str(state.loss_usd),
        },
        "notes": [
            "本脚本不读配置文件里的名义/闸门值：上限全部硬编码在脚本内。",
            "持仓来自 portfolio.net_position（账户侧状态），非脚本内存账本。",
            "交易所侧死亡开关未接：进程崩溃时挂单/持仓会留在交易所。",
        ],
    }
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(audit, indent=2, ensure_ascii=False, default=_json_default),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"警告：审计文件写入失败 {audit_path}: {exc}", file=sys.stderr)

    # -- 6) 状态落盘 -------------------------------------------------------
    round_pnl = _dec(strategy.result.get("round_pnl_usd") or "0")
    state.record_round(
        mode="send" if send else "dry",
        exit_code=exit_code,
        loss_usd=round_pnl if send else _ZERO,
        extra={
            "audit": str(audit_path),
            "notional_usd": str(notional),
            "qty": strategy.result.get("qty"),
        },
    )
    lock.release()

    print(
        json.dumps(
            {
                "event": "smoke_order_end",
                "exit_code": exit_code,
                "exit_reason": strategy.result.get("exit_reason"),
                "audit": str(audit_path),
                "positions": audit["positions"],
                "residual": audit["residual"],
                "round_pnl_usd": audit["round_pnl_usd"],
                "duration_s": audit["duration_s"],
            },
            ensure_ascii=False,
            default=_json_default,
        ),
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
