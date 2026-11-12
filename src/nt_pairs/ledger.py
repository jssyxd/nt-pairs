"""Target-based fill ledger (CONTRACT §3). Pure logic, no ``nautilus_trader``.

Event source in production: Nautilus ``OrderFilled`` (``last_qty``,
``order_side``, ``instrument_id``, ``trade_id``) — but this module only needs
the primitives below.

Hard rules implemented here:
- **Target-based**: positions are signed sums of applied fills; the hedge
  target is recomputed from the leader position every time, so no ack order
  can inflate the position.
- ``fee is None`` (HL inferred fills carry no commission) is counted in
  :attr:`LegLedger.missing_fee_count` and contributes 0 — the fee is NEVER
  guessed from notional.
- Duplicate ``trade_id`` delivery is ignored and counted in
  :attr:`LegLedger.duplicates`.
"""
from __future__ import annotations

from decimal import Decimal

from .pair_spec import PairSpec, to_decimal

__all__ = ["LegLedger"]

_ZERO = Decimal(0)


class LegLedger:
    """Signed per-venue quantity book with idempotent fill application."""

    def __init__(
        self, leader_venue: str, follower_venue: str, ratio: Decimal
    ) -> None:
        if leader_venue == follower_venue:
            raise ValueError(
                "LegLedger: leader and follower venue must differ "
                f"(both {leader_venue!r})"
            )
        ratio = to_decimal("LegLedger.ratio", ratio)
        if ratio <= 0:
            raise ValueError(f"LegLedger.ratio must be > 0 (got {ratio})")
        self.leader_venue = leader_venue
        self.follower_venue = follower_venue
        self.ratio = ratio
        #: duplicate trade_id deliveries ignored so far
        self.duplicates = 0
        #: fills applied with fee=None so far
        self.missing_fee_count = 0
        self._q: dict[str, Decimal] = {
            leader_venue: _ZERO,
            follower_venue: _ZERO,
        }
        self._net_fees = _ZERO
        self._seen_trade_ids: set[str] = set()

    @classmethod
    def from_spec(cls, spec: PairSpec) -> "LegLedger":
        """Build a ledger from a :class:`~nt_pairs.pair_spec.PairSpec`."""
        return cls(spec.leader.venue, spec.follower.venue, spec.ratio)

    def on_fill(
        self,
        trade_id: str,
        venue: str,
        side: str,
        qty: Decimal,
        px: Decimal,
        fee: Decimal | None,
    ) -> None:
        """Apply one fill; idempotent on ``trade_id``.

        ``qty`` is unsigned (> 0); the sign comes from ``side`` (BUY = +,
        SELL = −). Invalid fills raise ``ValueError`` and consume nothing —
        the trade_id is only marked seen after the fill is fully applied.
        """
        if trade_id in self._seen_trade_ids:
            self.duplicates += 1
            return
        if not trade_id:
            raise ValueError("on_fill: trade_id must be a non-empty string")
        if venue not in self._q:
            raise ValueError(
                f"on_fill: unknown venue {venue!r}; expected "
                f"{self.leader_venue!r} or {self.follower_venue!r}"
            )
        side_norm = side.upper() if isinstance(side, str) else side
        if side_norm not in ("BUY", "SELL"):
            raise ValueError(
                f"on_fill: side must be BUY or SELL, got {side!r}"
            )
        qty = to_decimal("on_fill.qty", qty)
        if qty <= 0:
            raise ValueError(f"on_fill.qty must be > 0 (got {qty})")
        px = to_decimal("on_fill.px", px)
        if px <= 0:
            raise ValueError(f"on_fill.px must be > 0 (got {px})")

        signed = qty if side_norm == "BUY" else -qty
        self._q[venue] += signed
        if fee is None:
            # Never guess the fee from notional (CONTRACT §3).
            self.missing_fee_count += 1
        else:
            self._net_fees += to_decimal("on_fill.fee", fee)
        self._seen_trade_ids.add(trade_id)

    def q(self, venue: str) -> Decimal:
        """Signed net quantity at ``venue`` (BUY positive, SELL negative)."""
        try:
            return self._q[venue]
        except KeyError:
            raise ValueError(
                f"q: unknown venue {venue!r}; expected "
                f"{self.leader_venue!r} or {self.follower_venue!r}"
            ) from None

    def target_follower(self) -> Decimal:
        """Follower position that fully hedges the leader: ``-q_leader * ratio``."""
        return -self.q(self.leader_venue) * self.ratio

    def residual(self) -> Decimal:
        """``target_follower() - q(follower)``, signed.

        > 0 → still need to BUY that much follower; < 0 → need to SELL.
        """
        return self.target_follower() - self.q(self.follower_venue)

    def naked_leader_equiv(self) -> Decimal:
        """Unhedged position in leader units: ``q_leader + q_follower / ratio``."""
        return self.q(self.leader_venue) + self.q(self.follower_venue) / self.ratio

    def unhedged_usd(self, ref_px: Decimal) -> Decimal:
        """Absolute naked leader-equivalent exposure valued at ``ref_px``."""
        ref_px = to_decimal("unhedged_usd.ref_px", ref_px)
        if ref_px <= 0:
            raise ValueError(f"unhedged_usd.ref_px must be > 0 (got {ref_px})")
        return abs(self.naked_leader_equiv()) * ref_px

    def net_fees(self) -> Decimal:
        """Sum of all applied fees (negative fees = rebates); None-fees add 0."""
        return self._net_fees

    def snapshot(self) -> dict:
        """Point-in-time dict view for logging/risk (keys are stable)."""
        return {
            "q_leader": self.q(self.leader_venue),
            "q_follower": self.q(self.follower_venue),
            "target": self.target_follower(),
            "residual": self.residual(),
            "naked": self.naked_leader_equiv(),
            "net_fees": self.net_fees(),
            "duplicates": self.duplicates,
            "missing_fee_count": self.missing_fee_count,
        }
