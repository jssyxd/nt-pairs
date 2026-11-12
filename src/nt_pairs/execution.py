"""Execution primitives (CONTRACT §4). Pure functions, no ``nautilus_trader``.

Hedging is fill-driven and **IOC-limit only** — a market order is never
produced anywhere in this module. All prices/quantities are ``Decimal``;
floats are rejected (:func:`~nt_pairs.pair_spec.to_decimal`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .pair_spec import to_decimal

__all__ = [
    "OrderSpec",
    "MAX_COLLAR_BPS",
    "FOLLOWER_STAGES",
    "h_star_s",
    "hedge_order",
    "chase_price",
]

#: CONTRACT §4: hedging collar is at most 50 bps beyond the book.
MAX_COLLAR_BPS = Decimal("50")
_BPS_DIVISOR = Decimal("10000")
_SECONDS_PER_MINUTE = Decimal("60")

#: CONTRACT §4: stage ∈ {"t1_passive","t2_chase","t3_ioc"}; T1 is leader-only.
FOLLOWER_STAGES = frozenset({"t2_chase", "t3_ioc"})

_SIDE_BUY = "BUY"
_SIDE_SELL = "SELL"


@dataclass(frozen=True)
class OrderSpec:
    """Venue-agnostic limit order specification (never a market order)."""

    side: str                      # "BUY" | "SELL"
    qty: Decimal
    price: Decimal
    tif: str = "IOC"
    reduce_only: bool = False
    stage: str = "t3_ioc"
    collar_bps: Decimal = Decimal("0")


def _ceil_to_tick(px: Decimal, tick: Decimal) -> Decimal:
    return (px / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _floor_to_tick(px: Decimal, tick: Decimal) -> Decimal:
    return (px / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _positive_decimal(field: str, value: object) -> Decimal:
    dec = to_decimal(field, value)
    if dec <= 0:
        raise ValueError(f"{field} must be > 0 (got {dec})")
    return dec


def h_star_s(
    k: Decimal, half_spread_bps: Decimal, sigma_1m_bps: Decimal
) -> Decimal:
    """Hedge TTL budget in seconds: ``(k * half_spread / sigma)^2 * 60``.

    ``sigma_1m_bps <= 0`` is a hard error (no volatility estimate → no
    defensible TTL). ``k`` must be > 0; ``half_spread_bps`` must be >= 0.
    """
    k = _positive_decimal("h_star_s.k", k)
    half_spread_bps = to_decimal("h_star_s.half_spread_bps", half_spread_bps)
    if half_spread_bps < 0:
        raise ValueError(
            f"h_star_s.half_spread_bps must be >= 0 (got {half_spread_bps})"
        )
    sigma_1m_bps = _positive_decimal("h_star_s.sigma_1m_bps", sigma_1m_bps)
    return ((k * half_spread_bps / sigma_1m_bps) ** 2) * _SECONDS_PER_MINUTE


def hedge_order(
    residual_qty: Decimal,
    *,
    book_bid: Decimal,
    book_ask: Decimal,
    tick: Decimal,
    size_increment: Decimal,
    min_qty: Decimal,
    collar_bps: Decimal,
    stage: str,
) -> OrderSpec | None:
    """Build the IOC hedge order for a signed follower residual.

    ``residual_qty > 0`` → BUY follower to complete the hedge; ``< 0`` → SELL.
    Price: BUY = ``book_ask * (1 + collar_bps/1e4)`` rounded **up** to tick
    (so BUY price >= ask always); SELL = ``book_bid * (1 - collar_bps/1e4)``
    rounded **down** to tick (SELL price <= bid always). Qty is floored to
    ``size_increment``; if the result is ``< min_qty`` (or residual is 0)
    returns ``None`` — dust waits for the next fill.

    ``stage="t1_passive"`` raises ``ValueError``: the follower leg is never
    passive. ``0 <= collar_bps <= 50`` is enforced (CONTRACT §4).
    """
    if stage not in FOLLOWER_STAGES:
        if stage == "t1_passive":
            raise ValueError(
                "hedge_order: stage='t1_passive' is leader-only; the follower "
                "hedge must use 't2_chase' or 't3_ioc' (never passive)"
            )
        raise ValueError(
            f"hedge_order: unknown stage {stage!r}; expected one of "
            f"{sorted(FOLLOWER_STAGES)}"
        )
    residual_qty = to_decimal("hedge_order.residual_qty", residual_qty)
    book_bid = _positive_decimal("hedge_order.book_bid", book_bid)
    book_ask = _positive_decimal("hedge_order.book_ask", book_ask)
    tick = _positive_decimal("hedge_order.tick", tick)
    size_increment = _positive_decimal(
        "hedge_order.size_increment", size_increment
    )
    min_qty = _positive_decimal("hedge_order.min_qty", min_qty)
    collar_bps = to_decimal("hedge_order.collar_bps", collar_bps)
    if collar_bps < 0 or collar_bps > MAX_COLLAR_BPS:
        raise ValueError(
            f"hedge_order.collar_bps must be within [0, {MAX_COLLAR_BPS}] "
            f"(got {collar_bps})"
        )

    units = (abs(residual_qty) / size_increment).to_integral_value(
        rounding=ROUND_FLOOR
    )
    qty = units * size_increment
    if qty < min_qty:
        return None  # dust (incl. zero residual): wait for the next fill

    if residual_qty > 0:
        side = _SIDE_BUY
        raw = book_ask * (
            Decimal(1) + collar_bps / _BPS_DIVISOR
        )
        price = _ceil_to_tick(raw, tick)
    else:
        # residual_qty < 0 guaranteed here: qty >= min_qty > 0 rules out 0.
        side = _SIDE_SELL
        raw = book_bid * (
            Decimal(1) - collar_bps / _BPS_DIVISOR
        )
        price = _floor_to_tick(raw, tick)

    return OrderSpec(
        side=side,
        qty=qty,
        price=price,
        tif="IOC",
        reduce_only=False,
        stage=stage,
        collar_bps=collar_bps,
    )


def chase_price(
    book_bid: Decimal,
    book_ask: Decimal,
    side: str,
    tick: Decimal,
    ticks: int,
) -> Decimal:
    """Chase-ladder price: BUY = ``ask + ticks*tick``, SELL = ``bid - ticks*tick``.

    Result is normalised onto the tick grid (BUY rounded up, SELL rounded
    down) and must stay > 0. ``ticks`` must be a non-negative ``int``.
    """
    if isinstance(ticks, bool) or not isinstance(ticks, int):
        raise ValueError(f"chase_price: ticks must be an int, got {ticks!r}")
    if ticks < 0:
        raise ValueError(f"chase_price: ticks must be >= 0 (got {ticks})")
    side_norm = side.upper() if isinstance(side, str) else side
    if side_norm not in (_SIDE_BUY, _SIDE_SELL):
        raise ValueError(
            f"chase_price: side must be BUY or SELL, got {side!r}"
        )
    book_bid = _positive_decimal("chase_price.book_bid", book_bid)
    book_ask = _positive_decimal("chase_price.book_ask", book_ask)
    tick = _positive_decimal("chase_price.tick", tick)

    step = Decimal(ticks) * tick
    if side_norm == _SIDE_BUY:
        price = _ceil_to_tick(book_ask + step, tick)
    else:
        price = _floor_to_tick(book_bid - step, tick)
    if price <= 0:
        raise ValueError(
            f"chase_price: {side_norm} ladder ran to non-positive price "
            f"{price} (ticks={ticks}, tick={tick})"
        )
    return price
