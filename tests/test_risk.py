"""Behaviour tests for nt_pairs.risk (CONTRACT §5)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from nt_pairs.risk import Action, RiskLimits, evaluate

D = Decimal

NOW = 10_000.0  # exact in binary float; ages below stay exact (x.0 / x.5)


def call(**over) -> tuple[Action, str]:
    kw = dict(
        pos_known=True,
        naked_usd=D("0"),
        naked_since=None,
        equity_now=D("10000"),
        equity_peak=D("10000"),
        quota_left=1000,
        now=NOW,
        limits=RiskLimits(),
    )
    kw.update(over)
    return evaluate(**kw)


def naked(age: float, **over):
    """evaluate() with a naked leg of the given age (seconds)."""
    return call(naked_since=NOW - age, naked_usd=D("1"), **over)


# ---------- naked-age boundaries: 60 / 180 / 600 ----------

@pytest.mark.parametrize(
    "age,expected",
    [
        (0.0, Action.NONE),
        (59.5, Action.NONE),
        (60.0, Action.WARN),        # boundary: >= warn
        (179.5, Action.WARN),
        (180.0, Action.FLATTEN),    # boundary: >= flatten
        (599.5, Action.FLATTEN),
        (600.0, Action.HALT),       # boundary: >= halt
        (700.0, Action.HALT),
    ],
)
def test_naked_age_boundaries(age, expected):
    action, reason = naked(age)
    assert action is expected
    assert isinstance(reason, str) and reason


# ---------- equity drawdown: 5% / 10% ----------

def test_equity_freeze_boundary():
    assert call(equity_now=D("9500"), equity_peak=D("10000"))[0] is Action.FREEZE_NEW
    assert call(equity_now=D("9500.01"), equity_peak=D("10000"))[0] is Action.NONE
    assert call(equity_now=D("10500"), equity_peak=D("10000"))[0] is Action.NONE  # new peak


def test_equity_kill_boundary():
    assert call(equity_now=D("9000"), equity_peak=D("10000"))[0] is Action.HALT
    assert call(equity_now=D("9000.01"), equity_peak=D("10000"))[0] is Action.FREEZE_NEW


def test_equity_kill_outranks_quota_and_age():
    action, _ = call(equity_now=D("8000"), equity_peak=D("10000"), quota_left=0)
    assert action is Action.HALT


def test_reason_mentions_drawdown():
    _, reason = call(equity_now=D("9000"), equity_peak=D("10000"))
    assert "drawdown" in reason and "10.00%" in reason


# ---------- quota gate: < 100 ----------

def test_quota_freeze_at_99_not_at_100():
    assert call(quota_left=99)[0] is Action.FREEZE_NEW
    assert call(quota_left=100)[0] is Action.NONE
    assert call(quota_left=0)[0] is Action.FREEZE_NEW
    _, reason = call(quota_left=99)
    assert "quota_left=99" in reason


def test_quota_gate_outranks_young_naked_warn():
    # quota FREEZE_NEW is returned before the (weaker) naked-age gate runs
    assert call(quota_left=50, naked_since=NOW - 70.0, naked_usd=D("1"))[0] is Action.FREEZE_NEW


# ---------- pos_known=False → WARN, never escalate ----------

def test_pos_unknown_only_warns_even_at_halt_age():
    action, reason = call(
        pos_known=False,
        naked_since=NOW - 700.0,   # age 700 ≥ halt 600
        naked_usd=D("5000"),
    )
    assert action is Action.WARN
    assert "pos_known" in reason


def test_pos_unknown_overrides_equity_halt():
    action, _ = call(
        pos_known=False, equity_now=D("1"), equity_peak=D("10000"), quota_left=0
    )
    assert action is Action.WARN  # never flatten on uncertain state


# ---------- naked_since=None → no age escalation ----------

def test_naked_since_none_never_escalates():
    assert call(naked_since=None, naked_usd=D("0"))[0] is Action.NONE
    # even a huge nominal naked_usd with no age information stays NONE
    assert call(naked_since=None, naked_usd=D("999999"))[0] is Action.NONE


# ---------- naked_usd > pct-of-equity (with age ≥ warn) → ≥ FREEZE_NEW ----------

def test_naked_pct_escalates_warn_to_freeze():
    # cap = 2% of 10000 = 200; naked 500 > 200, age 70 ∈ [warn, flatten)
    action, reason = call(naked_since=NOW - 70.0, naked_usd=D("500"))
    assert action is Action.FREEZE_NEW
    assert "cap" in reason


def test_naked_pct_below_cap_stays_warn():
    action, _ = call(naked_since=NOW - 70.0, naked_usd=D("100"))
    assert action is Action.WARN


def test_naked_pct_boundary_equal_is_not_over():
    action, _ = call(naked_since=NOW - 70.0, naked_usd=D("200"))  # == cap, not >
    assert action is Action.WARN


def test_naked_pct_does_not_downgrade_flatten_or_halt():
    assert call(naked_since=NOW - 200.0, naked_usd=D("500"))[0] is Action.FLATTEN
    assert call(naked_since=NOW - 700.0, naked_usd=D("500"))[0] is Action.HALT


def test_naked_pct_needs_min_age():
    # over cap but age < warn → NONE (age gate not reached)
    assert call(naked_since=NOW - 10.0, naked_usd=D("500"))[0] is Action.NONE


# ---------- degenerate inputs ----------

def test_negative_age_clock_skew_does_not_escalate():
    action, _ = call(naked_since=NOW + 30.0, naked_usd=D("500"))
    assert action is Action.NONE


def test_zero_equity_peak_skips_drawdown_gate_without_crash():
    action, _ = call(equity_now=D("-5"), equity_peak=D("0"))
    assert action is Action.NONE


def test_float_money_inputs_rejected():
    with pytest.raises(ValueError, match="float"):
        call(naked_usd=1.5)
    with pytest.raises(ValueError, match="float"):
        call(equity_now=9500.0)
