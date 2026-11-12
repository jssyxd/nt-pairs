"""matrix_stats 纯函数的行为测试（captain 重写：原文件在 worker 配额耗尽时被截断）。"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matrix_stats as ms  # noqa: E402
from nt_pairs.sessions import classify  # noqa: E402


def ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------ percentile
def test_percentile_linear_interpolation():
    vals = list(range(1, 101))
    assert ms.percentile(vals, 0) == 1
    assert ms.percentile(vals, 100) == 100
    assert abs(ms.percentile(vals, 95) - 95.05) < 1e-9
    assert ms.percentile(vals, 50) == 50.5


def test_percentile_edge_cases():
    assert math.isnan(ms.percentile([], 50))
    assert ms.percentile([7.0], 95) == 7.0


def test_p95_is_percentile_of_the_values_given():
    # p95 本身不做 abs；调用方应传 |basis|（口径见 MATRIX_SPEC §3）
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 90.0, 100.0]
    assert abs(ms.p95(vals) - 97.0) < 1e-9
    assert abs(ms.p95([abs(x) for x in (-100.0, -5.0, 1.0, 2.0, 3.0, 4.0, 5.0, 90.0)]) - 96.5) < 1e-9


def test_series_stats_fields():
    st = ms.series_stats([1.0, 2.0, 3.0, 4.0])
    assert st["n"] == 4 and st["mean"] == 2.5
    assert "sd" in st and "p50" in st and "p95" in st


# --------------------------------------------------------------------- ar(1)
def test_ar1_rho_and_half_life():
    assert math.isnan(ms.ar1_rho([1.0]))  # n<2 → nan
    rho = ms.ar1_rho([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    assert 0.3 < rho < 0.95
    assert abs(ms.half_life_hours(0.5, "1h") - 1.0) < 1e-9
    assert math.isinf(ms.half_life_hours(1.0))


def test_effective_sample_factor_monotone():
    assert ms.effective_sample_factor(50, 0.0) > ms.effective_sample_factor(5000, 0.0)
    assert abs(ms.effective_sample_factor(50, 0.0) - (1 + 1 / math.sqrt(50))) < 1e-12


# ---------------------------------------------------------------- unit check
def test_ratio_evidence_passes_for_same_asset():
    a = [100.0 + i * 0.01 for i in range(60)]
    b = [x * 1.0002 for x in a]
    ev = ms.ratio_evidence(a, b)
    assert abs(ev["mean"] - 1.0002) < 1e-3
    assert ev["cv"] < 0.005
    ok, reason = ms.unit_verdict(ev)
    assert ok and reason is None


def test_ratio_evidence_rejects_cv():
    a = [100.0] * 60
    b = [100.0 + (0.5 if i % 2 else -0.5) for i in range(60)]
    ev = ms.ratio_evidence(a, b)
    ok, reason = ms.unit_verdict(ev)
    assert not ok and reason == "unit_mismatch_cv"


def test_ratio_evidence_rejects_mean_shift():
    a = [100.0] * 30
    b = [130.0] * 30
    ev = ms.ratio_evidence(a, b)
    ok, reason = ms.unit_verdict(ev)
    assert not ok and reason == "unit_mismatch_mean"


# ----------------------------------------------------------------- sessions
def test_bucket_sessions_counts():
    stamps = [ts(2026, 11, 12, 15, 0), ts(2026, 11, 14, 15, 0), ts(2026, 11, 12, 13, 0)]
    out = ms.bucket_sessions(stamps, classify)
    assert out["rth"] == 1 and out["closed"] == 1 and out["pre"] == 1


def test_session_split_zero_when_same():
    assert ms.session_split(10.0, 10.0, 5.0) == 0.0
    assert ms.session_split(12.0, 8.0, 5.0) == pytest.approx(0.8)


# ------------------------------------------------------------------ windows
def test_align_buckets_counts_missing():
    a = {1: 10.0, 2: 11.0, 3: 12.0}
    b = {1: 10.1, 3: 12.1}
    out = ms.align_buckets(a, b)
    assert out["n_common"] == 2 and out["common"] == [1, 3]
    assert out["missing_a"] == 0 and out["missing_b"] == 1


def test_window_slice_filters_range():
    m = {1: 1.0, 2: 2.0, 3: 3.0}
    out = ms.window_slice(m, 2, 3)
    assert set(out) == {2, 3}


# ------------------------------------------------------------------- books
def test_half_spread_and_depth():
    # 半价差 = (ask-bid)/mid/2
    assert abs(ms.half_spread_bps(99.0, 101.0) - 100.0) < 1e-9  # (ask-bid)/mid/2 * 1e4
    bid_depth = ms.book_depth_usd([(99.9, 2.0), (98.0, 3.0)], 100.0, "bid", 25.0)
    ask_depth = ms.book_depth_usd([(100.1, 1.0)], 100.0, "ask", 25.0)
    assert bid_depth == pytest.approx(99.9 * 2)
    assert ask_depth == pytest.approx(100.1)


def test_vwap_fill_crosses_levels():
    vwap, complete = ms.vwap_fill([(100.0, 1.0), (101.0, 1.0)], 2.0)
    assert complete and vwap == pytest.approx(100.5)


def test_slip_bps_signs_and_incomplete():
    buy = ms.slip_bps([(100.0, 5.0)], 99.0, 100.0)
    sell = ms.slip_bps([(99.0, 5.0)], 100.0, 100.0)
    # 买：VWAP 高于 mid → 正；卖：VWAP 低于 mid → 负（两者都是不利方向的成本绝对值）
    assert buy["slip_bps"] > 0 and sell["slip_bps"] < 0
    too_big = ms.slip_bps([(100.0, 0.01)], 100.0, 1000.0)
    assert too_big["fillable"] is False


# -------------------------------------------------------------------- costs
def test_b_star_sums_all_items():
    items = {"fees": 6.0, "half_spreads": 3.0, "slippage": 1.0, "carry": 0.5,
             "leg_risk": 0.3, "recon": 0.5}
    assert ms.b_star_bps(items) == pytest.approx(11.3)


def test_leg_risk_scales_with_sqrt_time():
    a = ms.leg_risk_bps(10.0, 4.0)
    b = ms.leg_risk_bps(10.0, 16.0)
    assert b == pytest.approx(2 * a, rel=1e-6)


def test_edge_ratio_and_capacity_and_rank():
    assert ms.edge_ratio(25.0, 16.0) == pytest.approx((25 - 16) / 16)
    assert ms.capacity_ok(1000.0, 500.0) is True
    assert ms.capacity_ok(100.0, 500.0) is False
    r = ms.rank_score(1.5, 5000.0, 0.5)
    assert r > 0
    r2 = ms.rank_score(1.5, 100.0, 0.0)
    assert r2 < r