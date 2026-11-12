"""腿选择与执行规格的闭环测试：选腿 → maker 挂价 → taker 对冲规格。

用真实测得的场所特征（费率/点差/深度）验证：
1. 同一标的在三所里，**费率最低的组合胜出**（深度不参与排序）；
2. 换成 OKX 做 taker 后净 EV 翻负（与 2026-11-12 实测一致）；
3. 选出的 maker 腿能直接喂给 `hedge_order` 生成 IOC 对冲单，且**永不市价**。
"""

from __future__ import annotations

from decimal import Decimal as D

from nt_pairs.execution import hedge_order
from nt_pairs.leg_policy import LegQuote, expected_net_bps, rank_legs, select_pair

# 实测特征（2026-11-12）：maker/taker 费、半价差、±25bps 深度
HL = LegQuote("HYPERLIQUID", "xyz:SOXL", D("1.5"), D("4.5"), D("1.2"), D("365000"), D("0.001"), D("60"))
LT = LegQuote("LIGHTER", "SOXL", D("0"), D("0"), D("1.5"), D("443000"), D("0.01"), D("60"))
OKX = LegQuote("OKX", "SOXL-USDT-SWAP", D("2"), D("5"), D("0.85"), D("895000"), D("0.01"), D("60"))
QTY = D("500")
CAPTURE = D("17")


def test_taker_ranking_is_fee_first_not_depth_first():
    order = [q.venue for q in rank_legs([HL, LT, OKX], "taker", QTY)]
    assert order == ["LIGHTER", "HYPERLIQUID", "OKX"]   # OKX 深度最大却排最后


def test_best_pair_is_hl_maker_plus_lighter_taker():
    maker, taker, net = select_pair([HL, LT, OKX], CAPTURE, QTY)
    assert maker.venue == "HYPERLIQUID" and taker.venue == "LIGHTER"
    assert net > D("0")


def test_okx_leg_is_negative_at_its_own_observed_capture():
    """实测（2026-11-12）：Lighter 腿捕获 +16.2~+28.2 bps，OKX 腿只有 +4.6~+13.3 bps，
    且 OKX taker 费 5 bps（Lighter 0）→ 用**各腿自己的实测捕获**时，OKX 腿净 EV 为负。"""
    capture_lt, capture_okx = D("17"), D("8")
    _, _, net_lt = select_pair([HL, LT], capture_lt, QTY)
    _, _, net_okx = select_pair([HL, OKX], capture_okx, QTY)
    assert net_lt > D("0")
    assert net_okx < D("0")


def test_same_capture_okx_combo_is_still_worse_than_lighter():
    """同捕获幅度下，OKX 组合的成本更高（taker 费 5 bps vs Lighter 0）。"""
    _, _, net_lt = select_pair([HL, LT], D("17"), QTY)
    _, _, net_okx = select_pair([HL, OKX], D("17"), QTY)
    assert net_lt > net_okx
    # 同捕获下两者相差不大（OKX 点差小，抵消了其 maker 费 2 bps）；
    # 实测上的巨大差距主要来自「OKX 腿的捕获幅度更小」（4.6~13.3 vs 16.2~28.2 bps）。
    assert net_lt - net_okx > D("0.5")


def pytest_approx(v):
    import pytest
    return pytest.approx(v)


def test_selected_legs_produce_ioc_hedge_never_market():
    maker, taker, _ = select_pair([HL, LT], CAPTURE, QTY)
    # follower 侧（taker）用其盘口生成对冲单
    spec = hedge_order(
        residual_qty=D("0.5"),
        book_bid=D("59.97"), book_ask=D("60.00"),
        tick=taker.tick, size_increment=D("0.0001"), min_qty=D("0.0035"),
        collar_bps=D("2"), stage="t2_chase",
    )
    assert spec is not None
    assert spec.tif == "IOC"
    assert spec.side in ("BUY", "SELL")
    assert spec.price > 0


def test_expected_net_uses_p_fill_when_known():
    maker_certain = LegQuote(**{**HL.__dict__, "p_fill": D("1")})
    maker_unsure = LegQuote(**{**HL.__dict__, "p_fill": D("0.2")})
    net_certain = expected_net_bps(maker_certain, LT, CAPTURE, QTY)
    net_unsure = expected_net_bps(maker_unsure, LT, CAPTURE, QTY)
    assert net_certain == pytest_approx(net_unsure * 5)