"""Unit tests for the pure statistics in scripts/probe_basis.py.

Run: python -m pytest tests/test_probe_basis.py -q
(sys.path hack because scripts/ is not a package.)
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from probe_basis import (  # noqa: E402
    P_1LEG,
    analyze,
    ar1_rho,
    book_depth_usd,
    decide,
    effective_sample_factor,
    half_life_hours,
    half_spread_bps,
    leg_risk_bps,
    percentile,
    slip_2tick_bps,
)


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([1.0], 99) == 1.0
    assert percentile([10.0, 20.0], 0) == 10.0
    assert percentile([10.0, 20.0], 100) == 20.0


def test_ar1_rho_known_series():
    # x_{t+1} = 0.5 x_t + noise-free -> rho = 1 on exact recursion? OLS on
    # exact recursion with no constant is degenerate; use noise-free AR with
    # known intercept-free relation via alternating decay.
    xs = [100.0]
    for _ in range(200):
        xs.append(0.6 * xs[-1] + 5.0)
    # with intercept the OLS slope still recovers ~0.6 closely
    assert abs(ar1_rho(xs) - 0.6) < 0.05


def test_ar1_rho_flat_is_nan():
    assert math.isnan(ar1_rho([5.0, 5.0, 5.0, 5.0]))


def test_half_life():
    assert abs(half_life_hours(0.5) - 1.0) < 1e-9
    assert math.isnan(half_life_hours(-0.2))
    assert math.isinf(half_life_hours(1.0))


def test_effective_sample_factor():
    # iid: rho=0, ESS=n, k=1+1/sqrt(n)
    k = effective_sample_factor(100, 0.0)
    assert abs(k - (1 + 1 / 10)) < 1e-12
    # rho=0.5, n=300: ESS=100, k=1.1
    k = effective_sample_factor(300, 0.5)
    assert abs(k - 1.1) < 1e-12
    assert math.isnan(effective_sample_factor(10, 1.0))


def test_half_spread_and_slip():
    assert abs(half_spread_bps(99.0, 101.0) - 100.0) < 1e-9
    assert abs(slip_2tick_bps(0.1, 1000.0) - 2.0) < 1e-9


def test_leg_risk_formula():
    # sqrt(2/pi)*12*sqrt(2/60)*0.3*0.5
    want = math.sqrt(2 / math.pi) * 12 * math.sqrt(2 / 60) * P_1LEG * 0.5
    assert abs(leg_risk_bps(12, 2) - want) < 1e-12
    assert leg_risk_bps(12, 30) > leg_risk_bps(12, 2)


def test_book_depth():
    levels = [(100.0, 1.0), (99.0, 2.0), (98.0, 3.0)]
    # mid=100: 25bps floor 99.75 and 50bps floor 99.5 both keep only 100.0;
    # 300bps floor 97.0 keeps all three levels
    assert book_depth_usd(levels, 100.0, "bid", 25) == 100.0
    assert book_depth_usd(levels, 100.0, "bid", 50) == 100.0
    assert book_depth_usd(levels, 100.0, "bid", 300) == 100.0 + 198.0 + 294.0
    asks = [(101.0, 1.0), (102.0, 1.0)]
    assert book_depth_usd(asks, 100.0, "ask", 100) == 101.0
    assert book_depth_usd(asks, 100.0, "ask", 250) == 101.0 + 102.0

def test_analyze_counts_and_quantiles():
    xs = [0.0, 100.0, 200.0, 300.0, -100.0]
    s = analyze(xs)
    assert s["n"] == 5
    assert s["frac_abs_ge_100_bps"] == 0.8
    assert s["frac_abs_ge_300_bps"] == 0.2
    assert s["p50_abs_bps"] == 100.0


def test_decide_logic():
    stats = {"n": 100, "p95_abs_bps": 300.0}
    depths = {"hl": {"bid_25bps_usd": 500.0, "ask_25bps_usd": 500.0},
              "lt": {"bid_25bps_usd": 500.0, "ask_25bps_usd": 500.0}}
    v = decide(stats, 20.0, 1.1, True, 10.0, depths)
    assert v["tradable"] and not v["reasons"]
    v2 = decide(stats, 20.0, 20.0, True, 10.0, depths)  # k*b*=400 > 300
    assert not v2["tradable"]
    assert any("p95" in r for r in v2["reasons"])
    depths3 = {"hl": {"bid_25bps_usd": 5.0, "ask_25bps_usd": 500.0},
               "lt": {"bid_25bps_usd": 500.0, "ask_25bps_usd": 500.0}}
    v3 = decide(stats, 20.0, 1.1, True, 10.0, depths3)
    assert not v3["tradable"]
    assert any("hl" in r for r in v3["reasons"])
