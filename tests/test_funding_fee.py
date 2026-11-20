"""`nt_pairs.funding_fee` 的行为测试（费率表 + `b*` 门槛口径）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nt_pairs import costs
from nt_pairs.funding_fee import VENUE_FEES, VenueFees, b_star, venue_fees


def _leg(venue: str, maker: str, taker: str, price: str = "100",
         spread_bps: str = "2", tick: str = "0.01", depth: str = "100000",
         funding: str = "0") -> costs.LegSpec:
    return costs.LegSpec(venue=venue, maker_bps=Decimal(maker), taker_bps=Decimal(taker),
                          half_spread_bps=Decimal(spread_bps), price=Decimal(price),
                          tick=Decimal(tick), depth_bid_usd=Decimal(depth),
                          depth_ask_usd=Decimal(depth),
                          funding_bps_h=Decimal(funding),
                          carry_sign=1 if float(funding) > 0 else -1)


# ------------------------------------------------------------------ 费率表

def test_lighter_is_zero_fee():
    f = venue_fees("lighter")
    assert f.maker_bps == 0 and f.taker_bps == 0


def test_okx_and_binance_defaults_match_repo():
    assert venue_fees("okx_futures").taker_bps == Decimal("5")
    assert venue_fees("okx_futures").maker_bps == Decimal("2")
    assert venue_fees("binance_futures").taker_bps == Decimal("5")


def test_hyperliquid_hip3_fee_formula():
    """scale=1.0 + growthMode → base × (scale*2) × 0.1 = maker 0.3 / taker 0.9 bps。"""
    f = venue_fees("hyperliquid_futures", deployer_fee_scale=1.0, growth_mode=True)
    assert f.maker_bps == Decimal("0.3000")
    assert f.taker_bps == Decimal("0.9000")


def test_hyperliquid_unknown_scale_is_conservative_base():
    f = venue_fees("hyperliquid_futures")
    assert f.maker_bps == VENUE_FEES["hyperliquid_futures"].maker_bps
    assert f.taker_bps == VENUE_FEES["hyperliquid_futures"].taker_bps


def test_unknown_venue_raises():
    with pytest.raises(KeyError):
        venue_fees("nope_venue")


# -------------------------------------------------------------------- b*

def test_b_star_matches_costs_module():
    maker = _leg("lighter", "0", "0")
    taker = _leg("hyperliquid_futures", "1.5", "4.5")
    got = b_star(maker, taker, qty_usd=Decimal("500"))
    want = costs.round_cost_bps(maker, taker, qty_usd=Decimal("500"))
    assert got == want


def test_b_star_increases_with_size_when_slippage_grows():
    """规模↑ → 吃盘口滑点↑ → b*↑（不得用固定 2-tick 代理）。"""
    maker = _leg("lighter", "0", "0")
    taker = _leg("hyperliquid_futures", "1.5", "4.5")
    small = b_star(maker, taker, qty_usd=Decimal("100"), follower_slip_bps=Decimal("0.5"))
    big = b_star(maker, taker, qty_usd=Decimal("5000"), follower_slip_bps=Decimal("20"))
    assert big > small


def test_b_star_increases_with_higher_fee_leg():
    cheaper = b_star(_leg("lighter", "0", "0"), _leg("hyperliquid_futures", "1.5", "4.5"),
                     qty_usd=Decimal("500"))
    pricier = b_star(_leg("lighter", "0", "0"), _leg("okx_futures", "2", "5"),
                     qty_usd=Decimal("500"))
    assert pricier > cheaper


def test_b_star_increases_with_slippage():
    maker = _leg("lighter", "0", "0")
    taker = _leg("hyperliquid_futures", "1.5", "4.5")
    base = b_star(maker, taker, qty_usd=Decimal("500"))
    with_slip = b_star(maker, taker, qty_usd=Decimal("500"),
                       follower_slip_bps=Decimal("10"))
    assert with_slip > base


def test_b_star_counts_four_fills():
    """一轮 = 4 次成交：maker 入场 + taker 入场 + taker 平仓 + maker 平仓。"""
    maker = _leg("lighter", "0", "0")
    taker = _leg("hyperliquid_futures", "1.5", "4.5")
    bst = b_star(maker, taker, qty_usd=Decimal("500"), recon_bps=Decimal("0"),
                 leader_slip_bps=Decimal("0"), follower_slip_bps=Decimal("0"))
    # 费率：leader 入场 maker 1.5 + follower 入场 taker 4.5 + follower 平仓 4.5
    #       + leader 平仓 taker 4.5 = 15.0 bps；半价差 4×2 = 8 bps；腿风险 ≈0.63
    assert bst > Decimal("17")
    assert bst < Decimal("30")


def test_b_star_adds_carry_and_recon():
    maker = _leg("lighter", "0", "0")
    taker = _leg("hyperliquid_futures", "1.5", "4.5")
    plain = b_star(maker, taker, qty_usd=Decimal("500"), recon_bps=Decimal("0"))
    funding_maker = _leg("lighter", "0", "0", funding="2")
    funding_taker = _leg("hyperliquid_futures", "1.5", "4.5", funding="2")
    with_carry = b_star(funding_maker, funding_taker, qty_usd=Decimal("500"),
                         recon_bps=Decimal("0"), hold_hours=Decimal("8"))
    with_recon = b_star(maker, taker, qty_usd=Decimal("500"), recon_bps=Decimal("1"))
    assert with_carry > plain
    assert with_recon > plain


def test_venue_fees_override():
    custom = VenueFees("lighter", Decimal("0.5"), Decimal("1.0"), "custom")
    f = venue_fees("lighter", override=custom)
    assert f.taker_bps == Decimal("1.0")
