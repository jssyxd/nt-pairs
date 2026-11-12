"""Behaviour tests for nt_pairs.ledger (CONTRACT §3)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from nt_pairs.ledger import LegLedger

D = Decimal

LEADER = "HYPERLIQUID"
FOLLOWER = "LIGHTER"


def make_ledger(ratio: str = "1") -> LegLedger:
    return LegLedger(LEADER, FOLLOWER, D(ratio))


def test_fully_hedged_pair_has_zero_residual_and_naked():
    led = make_ledger()
    led.on_fill("t1", LEADER, "BUY", D("10"), D("1356.1"), D("0.30"))
    led.on_fill("t2", FOLLOWER, "SELL", D("10"), D("1356.2"), D("0.25"))
    assert led.target_follower() == D("-10")
    assert led.residual() == D("0")
    assert led.naked_leader_equiv() == D("0")
    assert led.unhedged_usd(D("1356")) == D("0")


def test_single_leader_buy_residual_is_negative_needs_follower_sell():
    led = make_ledger()
    led.on_fill("t1", LEADER, "BUY", D("10"), D("100"), D("0"))
    # target follower = -10, actual 0 → residual -10 → must SELL follower
    assert led.residual() == D("-10")
    assert led.naked_leader_equiv() == D("10")
    assert led.unhedged_usd(D("100")) == D("1000")


def test_single_leader_sell_residual_is_positive_needs_follower_buy():
    led = make_ledger()
    led.on_fill("t1", LEADER, "SELL", D("4"), D("100"), D("0"))
    assert led.residual() == D("4")
    assert led.naked_leader_equiv() == D("-4")


def test_three_fills_accumulate_with_signs():
    led = make_ledger()
    led.on_fill("t1", LEADER, "BUY", D("3"), D("100"), D("0"))
    led.on_fill("t2", LEADER, "BUY", D("4"), D("101"), D("0"))
    led.on_fill("t3", LEADER, "SELL", D("2"), D("102"), D("0"))
    assert led.q(LEADER) == D("5")
    assert led.residual() == D("-5")


def test_duplicate_trade_id_is_ignored_and_counted():
    led = make_ledger()
    led.on_fill("t1", LEADER, "BUY", D("10"), D("100"), D("0.5"))
    led.on_fill("t1", LEADER, "BUY", D("10"), D("100"), D("0.5"))  # replay
    led.on_fill("t1", FOLLOWER, "SELL", D("99"), D("1"), D("9"))   # even different content
    assert led.duplicates == 2
    assert led.q(LEADER) == D("10")
    assert led.q(FOLLOWER) == D("0")
    assert led.net_fees() == D("0.5")


def test_fee_none_counts_missing_and_does_not_change_net_fees():
    led = make_ledger()
    led.on_fill("t1", LEADER, "BUY", D("1"), D("100"), None)
    assert led.missing_fee_count == 1
    assert led.net_fees() == D("0")
    led.on_fill("t2", LEADER, "BUY", D("1"), D("100"), D("0.7"))
    led.on_fill("t3", FOLLOWER, "SELL", D("1"), D("100"), None)
    assert led.missing_fee_count == 2
    assert led.net_fees() == D("0.7")  # fee never guessed from notional


def test_negative_fee_rebate_reduces_net_fees():
    led = make_ledger()
    led.on_fill("t1", LEADER, "BUY", D("1"), D("100"), D("0.5"))
    led.on_fill("t2", LEADER, "BUY", D("1"), D("100"), D("-0.2"))
    assert led.net_fees() == D("0.3")


def test_ratio_not_one_naked_and_residual_formula():
    led = make_ledger(ratio="1000")  # 1 leader unit ↔ 1000 follower units
    led.on_fill("t1", LEADER, "BUY", D("2"), D("1350"), D("0"))
    led.on_fill("t2", FOLLOWER, "SELL", D("1500"), D("1350.5"), D("0"))
    assert led.target_follower() == D("-2000")
    assert led.residual() == D("-500")            # still need to sell 500
    assert led.naked_leader_equiv() == D("0.5")   # 2 + (-1500)/1000
    assert led.unhedged_usd(D("1350")) == D("675")


def test_snapshot_has_all_fields_with_hand_computed_values():
    led = make_ledger(ratio="2")
    led.on_fill("t1", LEADER, "BUY", D("3"), D("100"), D("0.10"))
    led.on_fill("t1", LEADER, "BUY", D("3"), D("100"), D("0.10"))  # duplicate
    led.on_fill("t2", FOLLOWER, "SELL", D("4"), D("100"), None)    # missing fee
    snap = led.snapshot()
    assert set(snap) == {
        "q_leader", "q_follower", "target", "residual",
        "naked", "net_fees", "duplicates", "missing_fee_count",
    }
    assert snap["q_leader"] == D("3")
    assert snap["q_follower"] == D("-4")
    assert snap["target"] == D("-6")          # -3 * 2
    assert snap["residual"] == D("-2")        # -6 - (-4)
    assert snap["naked"] == D("1")            # 3 + (-4)/2
    assert snap["net_fees"] == D("0.10")
    assert snap["duplicates"] == 1
    assert snap["missing_fee_count"] == 1


def test_out_of_order_acks_do_not_inflate_position():
    # follower fill arrives before the leader fill it hedges
    led = make_ledger()
    led.on_fill("t2", FOLLOWER, "SELL", D("10"), D("100"), D("0"))
    # leader empty → target 0, actual -10 → residual +10: must BUY follower back
    assert led.residual() == D("10")
    assert led.naked_leader_equiv() == D("-10")
    led.on_fill("t1", LEADER, "BUY", D("10"), D("100"), D("0"))
    assert led.residual() == D("0")
    assert led.naked_leader_equiv() == D("0")


def test_invalid_inputs_rejected_with_field_named():
    led = make_ledger()
    with pytest.raises(ValueError, match="venue"):
        led.on_fill("t1", "BINANCE", "BUY", D("1"), D("1"), D("0"))
    with pytest.raises(ValueError, match="side"):
        led.on_fill("t1", LEADER, "HOLD", D("1"), D("1"), D("0"))
    with pytest.raises(ValueError, match="qty"):
        led.on_fill("t1", LEADER, "BUY", D("0"), D("1"), D("0"))
    with pytest.raises(ValueError, match="float"):
        led.on_fill("t1", LEADER, "BUY", 10.0, D("1"), D("0"))
    with pytest.raises(ValueError, match="unknown venue"):
        led.q("BINANCE")
    # rejected fills applied nothing and burned no trade_id
    assert led.q(LEADER) == D("0")
    assert led.duplicates == 0
    led.on_fill("t1", LEADER, "BUY", D("1"), D("100"), D("0"))
    assert led.q(LEADER) == D("1")


def test_constructor_rejects_same_venue_and_bad_ratio():
    with pytest.raises(ValueError, match="must differ"):
        LegLedger(LEADER, LEADER, D("1"))
    with pytest.raises(ValueError, match="ratio"):
        LegLedger(LEADER, FOLLOWER, D("0"))
    with pytest.raises(ValueError, match="ratio"):
        LegLedger(LEADER, FOLLOWER, D("-1"))
