"""腿选择策略的行为测试（费率优先 → 点差 → 深度 → 成交概率）。

2026-11-13：成本口径改为 `nt_pairs.costs.round_cost_bps`（一轮 = 4 次成交），
断言用「与统一成本模型一致」而非硬编码常数，避免再次出现「数值凑巧对、成分全错」。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nt_pairs.costs import LegSpec, hl_fee_bps, round_cost_bps, vwap_slip_bps
from nt_pairs.leg_policy import (
    LegQuote,
    RepeatedCloseDetector,
    expected_net_bps,
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


def test_hl_hip3_fee_formula_matches_official():
    """官方公式：scale<1 → scale+1；scale≥1 → scale*2；growthMode → ×0.1。"""
    # growth mode + scale 1.0 → 1.5*2*0.1 = 0.30 bps maker
    assert hl_fee_bps(1.0, True, D("1.5")) == D("0.3000")
    assert hl_fee_bps(1.0, True, D("4.5")) == D("0.9000")
    # 无 growth mode → 1.5*2 = 3.0
    assert hl_fee_bps(1.0, False, D("1.5")) == D("3.0000")
    # scale<1 → scale+1
    assert hl_fee_bps(Decimal("0.5"), False, D("1.5")) == D("2.2500")
    # 未知 → 返回 base（保守），由调用方标注
    assert hl_fee_bps(None, True, D("1.5")) == D("1.5")


def test_vwap_slip_scales_with_size():
    levels = [(D("100.0"), D("1.0")), (D("101.0"), D("1.0"))]
    small = vwap_slip_bps(levels, "buy", D("1.0"), D("100.0"))
    big = vwap_slip_bps(levels, "buy", D("2.0"), D("100.0"))
    assert small == 0
    assert big == pytest.approx(50.0, abs=D("0.01"))   # vwap 100.5 → 50 bps


def test_slip_bps_uses_tick_proxy():
    a = q("A", tick=D("0.01"), price=D("100"))
    b = q("B", tick=D("0.1"), price=D("100"))
    assert slip_bps(a, D("500")) == pytest.approx(2.0)
    assert slip_bps(b, D("500")) == pytest.approx(20.0)
    assert slip_bps(b, D("500"), ticks=2, cap_bps=D("5")) == D("5")


def test_maker_and_taker_costs():
    x = q("A", maker=D("0"), taker=D("7"), half=D("2"))
    assert maker_cost_bps(x) == D("2")
    assert taker_cost_bps(x, D("100")) == D("11.0")   # 7 + 2 + 2 ticks(0.01/100)=2 bps


def test_pair_cost_matches_unified_model():
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0"))
    t_ = q("T", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0"))
    got = pair_cost_bps(m, t_, D("100"), hold_hours=D("0.25"))
    ms = LegSpec("M", D("0"), D("0"), D("0"), D("100"), D("0.01"), D("1"), D("1"))
    ts = LegSpec("T", D("0"), D("0"), D("0"), D("100"), D("0.01"), D("1"), D("1"))
    assert got == round_cost_bps(ms, ts, qty_usd=D("100"), hold_hours=D("0.25"))


def test_pair_cost_counts_taker_fee_twice():
    """follower 的 taker 费在入场与平仓各算一次 → 至少 2×taker 费。"""
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0"))
    t_ = q("T", maker=D("0"), taker=D("5"), half=D("0"), carry=D("0"))
    base = pair_cost_bps(m, q("T", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0")), D("100"))
    with_fee = pair_cost_bps(m, t_, D("100"))
    assert with_fee - base >= D("9.9")


def test_pair_cost_grows_with_leg_risk_inputs():
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0"))
    t_ = q("T", maker=D("0"), taker=D("0"), half=D("0"), carry=D("0"))
    short = pair_cost_bps(m, t_, D("100"), t_leg_s=D("2"), sigma_1m_bps=D("12.9"), p_one_leg=D("0.30"))
    long = pair_cost_bps(m, t_, D("100"), t_leg_s=D("60"), sigma_1m_bps=D("12.9"), p_one_leg=D("0.30"))
    assert long > short


def test_expected_net_sign_follows_capture_minus_cost():
    m = q("M", maker=D("0"), taker=D("0"), half=D("0"))
    t_ = q("T", maker=D("0"), taker=D("0"), half=D("0"))
    assert expected_net_bps(m, t_, D("40"), D("100")) > 0
    assert expected_net_bps(m, t_, D("1"), D("100")) < 0


def test_expected_net_scales_with_p_fill():
    m1 = q("M", maker=D("0"), taker=D("0"), half=D("0"), pf=D("1"))
    m25 = q("M", maker=D("0"), taker=D("0"), half=D("0"), pf=D("0.25"))
    t_ = q("T", maker=D("0"), taker=D("0"), half=D("0"))
    assert expected_net_bps(m25, t_, D("20"), D("100")) == pytest.approx(
        expected_net_bps(m1, t_, D("20"), D("100")) * D("0.25"))


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
    assert [x.venue for x in rank_legs([loose, tight], "taker", D("1000"))][0] == "TIGHT"


def test_select_pair_prefers_zero_fee_taker_leg():
    lighter = q("LIGHTER", maker=D("0"), taker=D("0"), half=D("2"))
    okx = q("OKX", maker=D("2"), taker=D("5"), half=D("1"))
    maker = q("MAKER", maker=D("0"), taker=D("0"), half=D("1"))
    m, t, net = select_pair([maker, lighter, okx], D("20"), D("100"))
    assert t is not None and t.venue == "LIGHTER"
    assert net > 0


def test_select_pair_requires_two_distinct_legs():
    only = q("ONLY")
    m, t, net = select_pair([only], D("20"), D("100"))
    assert m is None and t is None and net == D("0")


def test_repeated_close_detector_flags_stale_leg():
    det = RepeatedCloseDetector(threshold_pct=10.0, min_samples=10)
    for _ in range(20):
        det.push(D("100.0"))
    assert det.repeat_pct() == D("100.00")
    assert det.is_stale() is True


def test_repeated_close_detector_passes_clean_leg():
    det = RepeatedCloseDetector(threshold_pct=10.0, min_samples=10)
    for i in range(40):
        det.push(D("100") + D(i))
    assert det.repeat_pct() == D("0.00")
    assert det.is_stale() is False


def test_repeated_close_detector_needs_min_samples():
    det = RepeatedCloseDetector(threshold_pct=10.0, min_samples=30)
    for _ in range(5):
        det.push(D("100"))
    assert det.is_stale() is False