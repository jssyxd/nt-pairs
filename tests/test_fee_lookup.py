"""Regression tests for the HIP-3 fee lookup and the fill-probability caliber.

Background (see `.field-guide/nautilus_lighter_data.md` 2026-11-15):
  * D1 — HL's HIP-3 dex names are uppercase in the symbol universe (`XYZ:SOXL`)
    but the HL API only accepts lowercase (`xyz`). Not normalizing made the fee
    lookup miss and fall back to the base tier (1.5/4.5 bps) instead of the real
    HIP-3 growth-mode tier (0.30/0.90 bps) — a 5x error that flipped the
    go/no-go verdict for the main candidate pair.
  * D2 — the fill probability must use the Poisson queue-consumption caliber
    (`docs/FILL_MODEL.md`), not the "whole queue must trade through" exponential
    model, which pins p_fill at ~0 and makes every pair's net EV zero.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_chd_screen():
    """Load `scripts/chd_screen.py` as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location(
        "chd_screen_under_test", ROOT / "scripts" / "chd_screen.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cs():
    return _load_chd_screen()


# ------------------------------------------------------------------ D1 fee lookup


def test_hl_dex_name_is_lowercased_before_lookup(cs, monkeypatch):
    """`XYZ:SOXL` must query the dex `xyz`, not `XYZ` (the latter returns null)."""
    seen: list[str] = []

    def fake_meta(dex: str):
        seen.append(dex)
        return {"universe": [{"name": f"{dex}:SOXL", "deployerFeeScale": "1.0",
                               "growthMode": "enabled"}]}

    monkeypatch.setattr(cs, "hl_meta", fake_meta)
    cache: dict[str, dict] = {}
    leg = cs.Leg("hyperliquid_futures", "XYZ:SOXL", "SOXL")
    fee = cs.fees_for(leg, cache)

    assert seen == ["xyz"], f"必须用小写 dex 查询，实际 {seen}"
    assert "xyz" in cache
    # HIP-3 growth mode: base 1.5/4.5 × (scale=1 → ×2) × (growth → ×0.1)
    assert fee.maker_bps == Decimal("0.30")
    assert fee.taker_bps == Decimal("0.90")


def test_fee_lookup_falls_back_to_base_tier_when_unknown(cs, monkeypatch):
    """Unknown asset → conservative base tier, and the note says so."""
    monkeypatch.setattr(cs, "hl_meta", lambda dex: {"universe": []})
    leg = cs.Leg("hyperliquid_futures", "XYZ:UNKNOWN", "UNKNOWN")
    fee = cs.fees_for(leg, {})
    assert fee.maker_bps == Decimal("1.5")
    assert fee.taker_bps == Decimal("4.5")
    assert "deployerFeeScale" in fee.note


def test_non_hl_venue_uses_static_fee_table(cs):
    fee = cs.fees_for(cs.Leg("lighter", "SOXL", "SOXL"), {})
    assert fee.maker_bps == Decimal("0")
    assert fee.taker_bps == Decimal("0")


# ------------------------------------------------------------------ D2 fill model


def test_fill_probability_is_positive_with_realistic_flow():
    """Poisson queue consumption must not collapse to zero when flow exists."""
    from nt_pairs import market_micro as mm

    # 1 trade/sec for 60s, avg size 5 units, 20 units queued ahead, we add 2 units.
    p = mm.fill_probability(queue_ahead=20.0, trade_rate_lambda=1.0, dt=60.0,
                            our_size=2.0, avg_trade_size=5.0)
    assert p > 0.5, f"p_fill 不应塌到 0，实际 {p}"


def test_fill_probability_monotone_in_lambda_dt_and_size():
    from nt_pairs import market_micro as mm

    base = mm.fill_probability(10.0, 1.0, 60.0, 1.0, 2.0)
    assert base < mm.fill_probability(10.0, 2.0, 60.0, 1.0, 2.0)
    assert base < mm.fill_probability(10.0, 1.0, 120.0, 1.0, 2.0)
    assert base < mm.fill_probability(10.0, 1.0, 60.0, 1.0, 4.0)


def test_fill_probability_decreasing_in_queue_and_size():
    from nt_pairs import market_micro as mm

    assert mm.fill_probability(1.0, 1.0, 60.0, 1.0, 2.0) > \
        mm.fill_probability(100.0, 1.0, 60.0, 1.0, 2.0)
    assert mm.fill_probability(5.0, 1.0, 60.0, 1.0, 2.0) > \
        mm.fill_probability(5.0, 1.0, 60.0, 50.0, 2.0)


def test_fill_probability_zero_without_flow():
    from nt_pairs import market_micro as mm

    assert mm.fill_probability(1.0, 0.0, 60.0, 1.0, 2.0) == 0.0
    assert mm.fill_probability(1.0, 1.0, 0.0, 1.0, 2.0) == 0.0


# ------------------------------------------------------------------ D3 replay window


def test_volume_through_returns_volume_and_count(cs):
    trades = [(1000, 99.0, 2.0), (1100, 100.5, 3.0), (1200, 101.0, 1.0)]
    # passive buy at 100.0 → trades priced <= 100.0 count
    vol, n = cs.volume_through(trades, 0, 2000, 100.0, "buy")
    assert (vol, n) == (2.0, 1)
    # passive sell at 100.0 → trades priced >= 100.0 count
    vol, n = cs.volume_through(trades, 0, 2000, 100.0, "sell")
    assert (vol, n) == (4.0, 2)
    # 窗口是闭区间 [t0, t1]；t1=999 则第一笔（ts=1000）落在窗口外
    vol, n = cs.volume_through(trades, 0, 999, 100.0, "buy")
    assert (vol, n) == (0.0, 0)
    vol, n = cs.volume_through(trades, 0, 1000, 100.0, "buy")
    assert (vol, n) == (2.0, 1)
