"""Behaviour tests for nt_pairs.execution (CONTRACT §4)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from nt_pairs.execution import OrderSpec, chase_price, h_star_s, hedge_order

D = Decimal


def order(residual, *, bid=D("99.90"), ask=D("100.10"), tick=D("0.01"),
          inc=D("0.001"), min_qty=D("0.001"), collar=D("10"),
          stage="t3_ioc"):
    return hedge_order(
        residual, book_bid=bid, book_ask=ask, tick=tick,
        size_increment=inc, min_qty=min_qty, collar_bps=collar, stage=stage,
    )


# ---------- h_star_s ----------

def test_h_star_matches_reference_value():
    # hs = 1.97 bps, sigma = 13.05 bps, k = 1 → (1.97/13.05)^2 * 60 ≈ 1.367 s
    h = h_star_s(D("1"), D("1.97"), D("13.05"))
    assert float(h) == pytest.approx(1.3673, abs=1e-3)
    assert D("1.36") < h < D("1.38")


def test_h_star_scales_with_k_squared():
    # exact decimal values: (1*1/10)^2*60 = 0.6 ; k=2 → (0.2)^2*60 = 2.4
    h1 = h_star_s(D("1"), D("1"), D("10"))
    h2 = h_star_s(D("2"), D("1"), D("10"))
    assert h1 == D("0.6")
    assert h2 == D("2.4") == h1 * 4


def test_h_star_sigma_non_positive_raises():
    for sigma in (D("0"), D("-13.05")):
        with pytest.raises(ValueError, match="sigma_1m_bps"):
            h_star_s(D("1"), D("1.97"), sigma)


# ---------- collar scaling across tick regimes ----------

def test_two_tick_collar_price_offset_differs_10x():
    # 2-tick collar: tick 0.1 @ ask 100 → 20 bps; tick 0.01 → 2 bps
    ask = D("100")
    big = order(D("1"), ask=ask, bid=D("99.9"), tick=D("0.1"), collar=D("20"))
    small = order(D("1"), ask=ask, bid=D("99.99"), tick=D("0.01"), collar=D("2"))
    assert big.price == D("100.2")
    assert small.price == D("100.02")
    assert (big.price - ask) == 10 * (small.price - ask)


# ---------- quantity handling ----------

def test_qty_floors_to_size_increment():
    spec = order(D("1.2349"), inc=D("0.001"))
    assert spec.qty == D("1.234")
    spec = order(D("1.2349"), inc=D("0.1"))
    assert spec.qty == D("1.2")
    spec = order(D("-1.2349"), inc=D("0.001"))  # floors |residual| too
    assert spec.side == "SELL"
    assert spec.qty == D("1.234")


def test_below_min_qty_returns_none():
    # floored to 0.0034 < min 0.0035 (Lighter min_base_amount)
    assert order(D("0.0034"), inc=D("0.0001"), min_qty=D("0.0035")) is None
    # floor to increment drops below min: 0.0039 → 0.003 < 0.0035
    assert order(D("0.0039"), inc=D("0.001"), min_qty=D("0.0035")) is None
    # zero residual → nothing to hedge
    assert order(D("0")) is None
    # exactly at min → order produced
    spec = order(D("0.0035"), inc=D("0.0001"), min_qty=D("0.0035"))
    assert spec is not None and spec.qty == D("0.0035")


# ---------- stage rules ----------

def test_t1_passive_stage_rejected_for_follower():
    with pytest.raises(ValueError, match="t1_passive"):
        order(D("1"), stage="t1_passive")


def test_unknown_stage_rejected():
    with pytest.raises(ValueError, match="stage"):
        order(D("1"), stage="t9_yolo")


def test_t2_chase_stage_accepted():
    spec = order(D("1"), stage="t2_chase")
    assert spec.stage == "t2_chase"


# ---------- price direction invariants (never cross the wrong way) ----------

@pytest.mark.parametrize(
    "bid,ask,tick,collar",
    [
        (D("100.01"), D("100.03"), D("0.01"), D("10")),
        (D("1355.9"), D("1356.1"), D("0.1"), D("50")),
        (D("0.5003"), D("0.5007"), D("0.0001"), D("0")),
        (D("99.99"), D("100.01"), D("0.01"), D("1")),  # collar finer than tick
    ],
)
def test_buy_price_never_below_ask_and_sell_never_above_bid(bid, ask, tick, collar):
    buy = order(D("5"), bid=bid, ask=ask, tick=tick, collar=collar)
    sell = order(D("-5"), bid=bid, ask=ask, tick=tick, collar=collar)
    assert buy.side == "BUY"
    assert buy.price >= ask
    assert sell.side == "SELL"
    assert sell.price <= bid
    # prices sit on the tick grid
    assert buy.price % tick == 0
    assert sell.price % tick == 0


def test_never_produces_market_order():
    spec = order(D("5"), collar=D("0"))
    assert isinstance(spec, OrderSpec)
    assert spec.tif == "IOC"
    assert spec.reduce_only is False
    assert isinstance(spec.price, Decimal) and spec.price > 0  # limit, not market
    assert spec.collar_bps == D("0")


def test_collar_bounds_enforced():
    with pytest.raises(ValueError, match="collar_bps"):
        order(D("1"), collar=D("51"))     # CONTRACT §4 max 50 bps
    with pytest.raises(ValueError, match="collar_bps"):
        order(D("1"), collar=D("-1"))


def test_float_inputs_rejected():
    with pytest.raises(ValueError, match="float"):
        order(1.5)
    with pytest.raises(ValueError, match="float"):
        order(D("1"), ask=100.1)


# ---------- chase_price ----------

def test_chase_price_ladder():
    assert chase_price(D("100.01"), D("100.03"), "BUY", D("0.01"), 2) == D("100.05")
    assert chase_price(D("100.01"), D("100.03"), "SELL", D("0.01"), 2) == D("99.99")
    assert chase_price(D("100.01"), D("100.03"), "BUY", D("0.01"), 0) == D("100.03")
    assert chase_price(D("100.01"), D("100.03"), "SELL", D("0.01"), 0) == D("100.01")


def test_chase_price_larger_tick_ladder_moves_10x_further():
    fine = chase_price(D("100"), D("100.01"), "BUY", D("0.01"), 2)
    coarse = chase_price(D("100"), D("100.1"), "BUY", D("0.1"), 2)
    assert (fine - D("100.01")) == D("0.02")
    assert (coarse - D("100.1")) == D("0.2")


def test_chase_price_rejects_bad_input():
    with pytest.raises(ValueError, match="ticks"):
        chase_price(D("100"), D("100.1"), "BUY", D("0.1"), -1)
    with pytest.raises(ValueError, match="ticks"):
        chase_price(D("100"), D("100.1"), "BUY", D("0.1"), 1.5)
    with pytest.raises(ValueError, match="side"):
        chase_price(D("100"), D("100.1"), "HOLD", D("0.1"), 1)
    with pytest.raises(ValueError, match="non-positive"):
        chase_price(D("0.10"), D("0.11"), "SELL", D("0.01"), 20)  # bid-0.20 < 0
