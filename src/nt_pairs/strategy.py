"""NautilusTrader 2.0 策略：leader 挂单 + follower 按成交逐笔 IOC 对冲。

设计约束（见 CONTRACT.md §6 与 §6-1）：
- 只有 leader 挂被动单（post_only）；follower 只做 IOC 限价对冲，**永不市价**。
- 对冲量来自成交流水（`LegLedger`），不是计划量。
- 开平仓用带状规则（b_hat ± z·σ_b），且带漂移过滤。
- 默认 dry_run=True：所有下单只记日志。
"""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from nautilus_trader.common import OrderFactory
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import (
    BookType,
    InstrumentId,
    OrderSide,
    Price,
    Quantity,
    TimeInForce,
)
from nautilus_trader.trading import Strategy

from .execution import hedge_order, h_star_s
from .ledger import LegLedger
from .risk import Action, RiskLimits, evaluate


class PairHedgeConfig(StrategyConfig):
    """2.0 的 StrategyConfig 是可变对象（属性在实例化后赋值），不要用 frozen=True。"""

    def __init__(
        self,
        *,
        leader_id: str,
        follower_id: str,
        ratio: str = "1",
        b_star_bps: str = "21.0",
        entry_z: float = 1.5,
        exit_z: float = 0.3,
        drift_max_bps: float = 25.0,
        sigma_1m_bps: str = "12.86",
        max_leg_qty: str = "0.05",
        book_max_age_ms: int = 1000,
        dry_run: bool = True,
        state_dir: str = "state",
    ) -> None:
        super().__init__()
        self.leader_id = leader_id
        self.follower_id = follower_id
        self.ratio = Decimal(ratio)
        self.b_star_bps = Decimal(b_star_bps)
        self.entry_z = float(entry_z)
        self.exit_z = float(exit_z)
        self.drift_max_bps = float(drift_max_bps)
        self.sigma_1m_bps = Decimal(sigma_1m_bps)
        self.max_leg_qty = Decimal(max_leg_qty)
        self.book_max_age_ms = int(book_max_age_ms)
        self.dry_run = bool(dry_run)
        self.state_dir = state_dir


class PairHedgeStrategy(Strategy):
    """单对双腿策略。leader 被动、follower 影子跟单。"""

    def __init__(self, config: PairHedgeConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self.leader_iid = InstrumentId.from_str(config.leader_id)
        self.follower_iid = InstrumentId.from_str(config.follower_id)

        self.ledger = LegLedger(
            self.leader_iid.venue.value, self.follower_iid.venue.value, config.ratio
        )
        self.book_ts: dict[str, int] = {}
        self.basis_history: list[float] = []
        self.entry_side: int = 0          # 0=flat, +1=long basis, -1=short basis
        self.entry_basis: float = 0.0
        self.naked_since: Optional[float] = None
        self._seen_trade_ids: set[str] = set()
        self._state_path = os.path.join(config.state_dir, "band_state.json")
        self._band_state: dict[str, float] = {}
        self._load_state()

    # ------------------------------------------------------------------ state
    def _load_state(self) -> None:
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                self._band_state = json.load(fh)
        except Exception:
            self._band_state = {}

    def _save_state(self) -> None:
        try:
            os.makedirs(self.cfg.state_dir, exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._band_state, fh)
            os.replace(tmp, self._state_path)
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"band state save failed: {exc}")

    # ------------------------------------------------------------- lifecycle
    def on_start(self) -> None:
        self.subscribe_book_deltas(self.leader_iid, BookType.L2_MBP)
        self.subscribe_book_deltas(self.follower_iid, BookType.L2_MBP)
        self.clock.set_timer(
            name="risk",
            interval=timedelta(milliseconds=200),
            callback=self._on_risk_timer,
        )
        self.log.info(
            f"pair strategy started leader={self.cfg.leader_id} follower={self.cfg.follower_id} "
            f"dry_run={self.cfg.dry_run}"
        )

    # ------------------------------------------------------------------ books
    def on_book_deltas(self, deltas) -> None:
        iid = str(deltas.instrument_id)
        self.book_ts[iid] = int(deltas.ts_event)

        leader_book = self._book(self.leader_iid)
        follower_book = self._book(self.follower_iid)
        if leader_book is None or follower_book is None:
            return

        now_ns = int(self.clock.timestamp_ns())
        age_ms = max(
            now_ns - self.book_ts.get(str(self.leader_iid), 0),
            now_ns - self.book_ts.get(str(self.follower_iid), 0),
        ) / 1e6
        if age_ms > self.cfg.book_max_age_ms:
            return

        lbid, lask = leader_book.best_bid_price(), leader_book.best_ask_price()
        fbid, fask = follower_book.best_bid_price(), follower_book.best_ask_price()
        if not all((lbid, lask, fbid, fask)):
            return

        lmid = float(lbid + lask) / 2.0
        fmid = float(fbid + fask) / 2.0
        if lmid <= 0:
            return
        basis = (fmid / lmid - 1.0) * 1e4
        self.basis_history.append(basis)
        if len(self.basis_history) > 720:
            self.basis_history.pop(0)

        if not self._update_band(basis):
            return
        self._maybe_enter_or_exit(basis, lbid, lask, fbid, fask)

    def _update_band(self, basis: float) -> bool:
        if len(self.basis_history) < 72:
            return False
        window = self.basis_history[-168:]
        ordered = sorted(window)
        med = ordered[len(ordered) // 2]
        devs = sorted(abs(x - med) for x in window)
        mad = devs[len(devs) // 2]
        sigma = max(1.4826 * mad, 1e-9)

        prev = self._band_state.get("median", med)
        drift = med - prev
        self._band_state = {"median": med, "sigma": sigma}
        self._save_state()

        if self.entry_side != 0 and abs(drift) > 2 * sigma:
            self.log.error(f"band re-pricing detected drift={drift:.1f} bps -> HALT")
            self._halt("band redrift")
            return False

        self._band_med = med
        self._band_sigma = sigma
        self._band_drift = drift
        return True

    def _maybe_enter_or_exit(self, basis: float, lbid, lask, fbid, fask) -> None:
        med, sigma = self._band_med, self._band_sigma
        z = (basis - med) / sigma

        if self.entry_side == 0:
            if abs(z) < self.cfg.entry_z:
                return
            side = 1 if z > 0 else -1
            drift = getattr(self, "_band_drift", 0.0)
            if side * drift < -self.cfg.drift_max_bps:
                return
            if (self.cfg.entry_z - self.cfg.exit_z) * sigma < float(self.cfg.b_star_bps):
                return
            self.entry_side, self.entry_basis = side, basis
            self.log.info(f"ENTRY basis={basis:.1f} z={z:.2f} side={side}")
            return

        if abs(z) <= self.cfg.exit_z:
            self.log.info(f"EXIT basis={basis:.1f} z={z:.2f}")
            self.entry_side = 0
            self.entry_basis = 0.0

    # ------------------------------------------------------------------ fills
    def on_order_filled(self, event) -> None:
        trade_id = str(getattr(event, "trade_id", ""))
        if trade_id and trade_id in self._seen_trade_ids:
            return
        if trade_id:
            self._seen_trade_ids.add(trade_id)

        is_leader = event.instrument_id == self.leader_iid
        venue = str(event.instrument_id.venue.value)  # 账本按场所名记账
        side = "buy" if event.is_buy else "sell"
        fee = getattr(event, "commission", None)
        fee_dec = Decimal(str(fee)) if fee is not None else None

        self.ledger.on_fill(
            trade_id=trade_id or f"{event.ts_event}",
            venue=venue,
            side=side,
            qty=Decimal(str(event.last_qty)),
            px=Decimal(str(event.last_px)),
            fee=fee_dec,
        )

        if not is_leader:
            return
        self._hedge()

    def _hedge(self) -> None:
        residual = self.ledger.residual()
        if residual == 0:
            self.naked_since = None
            return
        if self.naked_since is None:
            self.naked_since = self._now_s()

        book = self._book(self.follower_iid)
        if book is None:
            return
        bid, ask = book.best_bid_price(), book.best_ask_price()
        if not bid or not ask:
            return
        inst = self._instrument(self.follower_iid)
        if inst is None:
            return

        spec = hedge_order(
            residual_qty=residual,
            book_bid=Decimal(str(bid)),
            book_ask=Decimal(str(ask)),
            tick=Decimal(str(inst.price_increment)),
            size_increment=Decimal(str(inst.size_increment)),
            min_qty=Decimal(str(inst.min_quantity)),
            collar_bps=Decimal("5"),
            stage="t2_chase",
        )
        if spec is None:
            return

        self._submit(
            instrument_id=self.follower_iid,
            side=spec.side,
            qty=spec.qty,
            price=spec.price,
            tif=TimeInForce.IOC,
            reduce_only=False,
            tag=f"hedge:{spec.stage}",
        )

    # ------------------------------------------------------------------- risk
    def _on_risk_timer(self, event=None) -> None:
        now = self._now_s()
        follower_book = self._book(self.follower_iid)
        ref_px = Decimal("1")
        if follower_book is not None and follower_book.best_bid_price() and follower_book.best_ask_price():
            ref_px = (
                Decimal(str(follower_book.best_bid_price()))
                + Decimal(str(follower_book.best_ask_price()))
            ) / Decimal("2")
        naked_usd = self.ledger.unhedged_usd(ref_px)
        action, reason = evaluate(
            pos_known=True,
            naked_usd=naked_usd,
            naked_since=self.naked_since,
            equity_now=Decimal("1"),
            equity_peak=Decimal("1"),
            quota_left=10_000,
            now=now,
            limits=RiskLimits(),
        )
        if action in (Action.FLATTEN, Action.HALT):
            self.log.error(f"risk {action.value}: {reason}")
            if action is Action.HALT:
                self._halt(reason)
        elif action is not Action.NONE:
            self.log.warning(f"risk {action.value}: {reason}")

    def _halt(self, reason: str) -> None:
        for iid in (self.leader_iid, self.follower_iid):
            if self.cfg.dry_run:
                self.log.warning(f"[dry_run] cancel_all {iid} ({reason})")
            else:
                self.cancel_all_orders(iid)

    # ------------------------------------------------------------------ orders
    def _submit(
        self,
        *,
        instrument_id: InstrumentId,
        side: str,
        qty: Decimal,
        price: Decimal,
        tif: TimeInForce,
        reduce_only: bool,
        tag: str,
    ) -> None:
        # execution.hedge_order 返回 "BUY"/"SELL"；这里大小写都要正确映射（曾被小写判断反选方向）
        order_side = OrderSide.BUY if str(side).upper() == "BUY" else OrderSide.SELL
        post_only = tif is TimeInForce.GTC and not reduce_only
        if self.cfg.dry_run:
            self.log.info(
                f"[dry_run] {tag} {instrument_id} {side} qty={qty} px={price} tif={tif}"
            )
            return
        inst = self._instrument(instrument_id)
        if inst is None:
            self.log.error(f"instrument missing {instrument_id}")
            return
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=Quantity(qty, inst.size_precision),
            price=Price(price, inst.price_precision),
            time_in_force=tif,
            post_only=post_only,
            reduce_only=reduce_only,
        )
        self.submit_order(order)

    # ------------------------------------------------------------------- utils
    def _book(self, instrument_id):
        try:
            return self.cache.order_book(instrument_id)
        except RuntimeError:
            return None

    def _instrument(self, instrument_id):
        try:
            return self.cache.instrument(instrument_id)
        except RuntimeError:
            return None

    def _now_s(self) -> float:
        """时钟优先；未注册 trader 时退回墙钟（单测可跑）。"""
        try:
            return self.clock.timestamp_ns() / 1e9
        except RuntimeError:
            return time.time()

    def on_stop(self) -> None:
        self._save_state()
        self.log.info("pair strategy stopped")