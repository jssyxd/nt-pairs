"""腿选择策略的行为测试（费率优先 → 点差 → 深度 → 成交概率）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nt_pairs.leg_policy import (
    LegQuote,
    expected_net_bps,
    leg_risk_bps,
    maker_cost_bps,
    pair_cost_bps,
    rank_legs,
    select_pair,
    slip_bps,
    taker_cost_bps,
)

D = Decimal


def q(venue: str, maker=D("1.5"), taker=D("4.5"), half=D("1.0"), depth=D("100000"),
      tick=D("0.01"), price=D("100"), pf=None, carry=D("0")) -> LegQuote:
    return LegQuote(venue=venue, instrument_id=f"{venue}-X", maker_bps=maker, taker_bps=taker,
                    half_spread_bps=half, depth25_usd=depth, tick=tick, price=price,
                    p_fill=pf, carry_bps_h=carry)


def test_slip_scales_with_size_and_is_capped():
    a = q("A", tick=D("0.01"), price=D("100"))
    b = q("B", tick=D("0.1"), price=D("100"))
    assert slip_bps(a, D("500")) == pytest.approx(2.0)
    assert slip_bps(b, D("500")) == pytest.approx(20.0)
    assert slip_bps(b, D("500"), ticks=2, cap_bps=D("5")) == D("5")


def test_maker_and_taker_costs():
    x = q("A", maker=D("0"), taker=D("7"), half=D("2"))
    assert maker_cost_bps(x) == D("2")
    # 7(taker) + 2(半价差) + 2 ticks(0.01/100 → 2 bps) = 11.0
    assert taker_cost_bps(x, D("100")) == D("11.0")


def test_rank_prefers_fee_then_depth_then_fill():
    cheap = q("CHEAP", maker=D("0"), taker=D("0"), half=D("0.5"))
    dear = q("DEAR", maker=D("2"), taker=D("5"), half=D("0.5"))
    deep = q("DEEP", maker=D("2"), taker=D("5"), half=D("0.5"), depth=D("900000"))
    order = [x.venue for x in rank_legs([deep, dear, cheap], "maker", D("500"))]
    assert order[0] == "CHEAP"
    assert set(order[1:]) == {"DEAR", "DEEP"}


def test_rank_taker_uses_slippage():
    tight = q("TIGHT", taker=D("5"), tick=D("0.01"), price=D("100"))
    loose = q("LOOSE", taker=D("5"), tick=D("0.5"), price=D("100"))
    order = [x.venue for x in rank_legs([loose, tight], "taker", D("1000"))]
    assert order[0] == "TIGHT"


def test_pair_cost_includes_carry_leg_risk_and_recon():
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0.5"))
    t = q("T", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0.2"))
    cost = pair_cost_bps(m, t, D("100"), hold_hours=D("1"))
    # 0(maker费+半价差) + 2.0(taker 滑点: 2 ticks) + 0.5 + 0.2 + 0.3 + 0.5
    assert cost == pytest.approx(D("3.5"))


def test_expected_net_sign_follows_capture_minus_cost():
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"))
    t = q("T", maker=D("0"), taker=D("0"), half=D("0"))
    assert expected_net_bps(m, t, D("20"), D("100")) > 0
    assert expected_net_bps(m, t, D("1"), D("100")) < 0


def test_expected_net_scales_with_p_fill():
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"), pf=D("0.25"))
    t = q("T", maker=D("0"), taker=D("0"), half=D("0"))
    # 0.25 × (20 − 2.8)；2.8 = 滑点 2.0 + 腿风险 0.3 + 对账 0.5
    assert expected_net_bps(m, t, D("20"), D("100")) == pytest.approx(D("4.3"))


def test_select_pair_prefers_zero_fee_taker_leg():
    lighter = q("LIGHTER", maker=D("0"), taker=D("0"), half=D("2"))
    okx = q("OKX", maker=D("2"), taker=D("5"), half=D("1"))
    maker = q("MAKER", maker=D("0"), taker=D("0"), half=D("1"))
    m, t, net = select_pair([maker, lighter, okx], D("20"), D("100"))
    assert t is not None and t.venue == "LIGHTER", "taker 必须选零费腿"
    assert net > 0


def test_select_pair_requires_two_distinct_legs():
    only = q("ONLY")
    m, t, net = select_pair([only], D("20"), D("100"))
    assert m is None and t is None and net == D("0")


def test_leg_risk_scales_with_sqrt_time():
    a = leg_risk_bps(D("10"), D("4"), D("0.3"))
    b = leg_risk_bps(D("10"), D("16"), D("0.3"))
    assert b == pytest.approx(2 * a, rel=1e-6)
    assert leg_risk_bps(D("0"), D("4"), D("0.3")) == D("0")