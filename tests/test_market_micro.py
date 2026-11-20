"""`nt_pairs.market_micro` 的行为测试（纯函数，无需网络）。"""

from __future__ import annotations

import math

import pytest

from nt_pairs import market_micro as mm


def _book(bids, asks):
    return {"bids": list(bids), "asks": list(asks)}


# ---------------------------------------------------------------- infer_tick

def test_infer_tick_dense_book():
    # 稠密盘口：相邻档位间隔 = 0.001
    prices = [63.992 + 0.001 * i for i in range(50)]
    tick = mm.infer_tick(prices)
    assert math.isclose(tick, 0.001, rel_tol=1e-6)


def test_infer_tick_cent_level():
    prices = [100.00 + 0.01 * i for i in range(30)]
    assert math.isclose(mm.infer_tick(prices), 0.01, rel_tol=1e-6)


def test_infer_tick_sparse_book_is_not_ten_times_too_large():
    """经验规则（$100 以下 0.1）会把 tick 高估 10 倍 → 反推必须回到 0.001 量级。"""
    prices = [63.992 + 0.005 * i for i in range(20)]
    tick = mm.infer_tick(prices)
    assert tick < 0.01


def test_infer_tick_ignores_duplicates_and_non_positive():
    prices = [10.0, 10.0, 10.25, 10.5, 0.0, -1.0]
    tick = mm.infer_tick(prices)
    assert math.isclose(tick, 0.25, rel_tol=1e-6)


def test_infer_tick_needs_two_prices():
    assert mm.infer_tick([10.0]) == 0.0
    assert mm.infer_tick([]) == 0.0


# ---------------------------------------------------------------- book_depth

def test_book_depth_counts_only_band():
    b = _book([(99.0, 1.0), (99.9, 2.0)], [(100.1, 3.0), (101.0, 4.0)])
    d = mm.book_depth(b, mid=100.0, band_bps=25.0)   # ±0.25
    assert d["bid_usd"] == pytest.approx(99.9 * 2.0)
    assert d["ask_usd"] == pytest.approx(100.1 * 3.0)
    assert d["bid_qty"] == pytest.approx(2.0)
    assert d["ask_qty"] == pytest.approx(3.0)


def test_book_depth_wider_band_grows():
    b = _book([(99.0, 1.0), (99.9, 2.0)], [(100.1, 3.0), (101.0, 4.0)])
    narrow = mm.book_depth(b, 100.0, 5.0)
    wide = mm.book_depth(b, 100.0, 200.0)
    assert wide["bid_usd"] > narrow["bid_usd"]
    assert wide["ask_usd"] > narrow["ask_usd"]


# ---------------------------------------------------------------- walk_book

def test_walk_book_buy_consumes_asks_and_reports_slippage():
    levels = [(100.0, 1.0), (100.1, 1.0), (100.2, 5.0)]
    avg, slip, filled = mm.walk_book("buy", levels, 2.0, mid=100.0)
    assert filled == pytest.approx(2.0)
    assert avg == pytest.approx((100.0 + 100.1) / 2)
    assert slip > 0


def test_walk_book_sell_consumes_bids_descending():
    levels = [(100.0, 1.0), (99.9, 1.0)]
    avg, slip, filled = mm.walk_book("sell", levels, 2.0, mid=100.0)
    assert filled == pytest.approx(2.0)
    assert avg == pytest.approx((100.0 + 99.9) / 2)


def test_walk_book_partial_fill_when_book_too_thin():
    """深度不够 → filled < qty（调用方据此判定「不填满」边界）。"""
    levels = [(100.0, 0.5)]
    avg, slip, filled = mm.walk_book("buy", levels, 2.0, mid=100.0)
    assert filled == pytest.approx(0.5)
    assert avg == pytest.approx(100.0)


def test_walk_book_empty_book():
    assert mm.walk_book("buy", [], 1.0) == (0.0, 0.0, 0.0)


def test_walk_book_slippage_grows_with_size():
    levels = [(100.0, 1.0), (100.1, 1.0), (100.2, 10.0)]
    _, s_small, _ = mm.walk_book("buy", levels, 0.5, mid=100.0)
    _, s_big, _ = mm.walk_book("buy", levels, 3.0, mid=100.0)
    assert s_big > s_small


# ------------------------------------------------------------ fill_probability

def test_fill_probability_monotone_in_lambda_and_dt():
    a = mm.fill_probability(queue_ahead=10.0, trade_rate_lambda=1.0, dt=60.0, our_size=1.0)
    b = mm.fill_probability(queue_ahead=10.0, trade_rate_lambda=2.0, dt=60.0, our_size=1.0)
    c = mm.fill_probability(queue_ahead=10.0, trade_rate_lambda=1.0, dt=120.0, our_size=1.0)
    assert 0.0 < a < b
    assert 0.0 < a < c


def test_fill_probability_decreasing_in_queue_ahead():
    a = mm.fill_probability(queue_ahead=1.0, trade_rate_lambda=1.0, dt=60.0, our_size=1.0)
    b = mm.fill_probability(queue_ahead=100.0, trade_rate_lambda=1.0, dt=60.0, our_size=1.0)
    assert b < a


def test_fill_probability_decreasing_in_our_size():
    a = mm.fill_probability(queue_ahead=5.0, trade_rate_lambda=1.0, dt=60.0, our_size=1.0)
    b = mm.fill_probability(queue_ahead=5.0, trade_rate_lambda=1.0, dt=60.0, our_size=50.0)
    assert b < a


def test_fill_probability_zero_when_no_flow():
    assert mm.fill_probability(queue_ahead=1.0, trade_rate_lambda=0.0, dt=60.0, our_size=1.0) == 0.0
    assert mm.fill_probability(queue_ahead=1.0, trade_rate_lambda=1.0, dt=0.0, our_size=1.0) == 0.0


def test_fill_probability_is_probability():
    p = mm.fill_probability(queue_ahead=3.0, trade_rate_lambda=5.0, dt=30.0, our_size=2.0)
    assert 0.0 <= p <= 1.0


# --------------------------------------------------------------- 滑点曲线

def test_estimate_slippage_curve_monotone_in_size():
    books = []
    for i in range(20):
        books.append(_book([(100.0 - 0.01 * k, 1.0) for k in range(1, 11)],
                           [(100.0 + 0.01 * k, 1.0) for k in range(1, 11)]))
    curve = mm.estimate_slippage_curve(books, [1.0, 5.0, 10.0], side="buy")
    vals = [curve[q]["slippage_bps_mean"] for q in (1.0, 5.0, 10.0)]
    assert vals[0] < vals[1] < vals[2]


def test_estimate_slippage_curve_reports_fill_ratio():
    books = [_book([(99.0, 1.0)], [(101.0, 1.0)])]
    curve = mm.estimate_slippage_curve(books, [1.0, 100.0], side="buy")
    assert curve[1.0]["fill_ratio"] == pytest.approx(1.0)
    assert curve[100.0]["fill_ratio"] == pytest.approx(0.0)


# --------------------------------------------------------- 陈旧打印检测

def test_repeat_close_pct_detects_stale_prints():
    stale = [10.0] * 20 + [11.0] * 5          # 19/24 相邻相同
    assert mm.repeat_close_pct(stale) > 70.0
    fresh = [float(i) for i in range(25)]
    assert mm.repeat_close_pct(fresh) == 0.0


def test_repeat_close_pct_needs_two_samples():
    assert mm.repeat_close_pct([1.0]) == 0.0
