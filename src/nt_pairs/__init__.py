"""nt-pairs pure-logic kernel (CONTRACT §0).

The modules re-exported here (pair_spec / ledger / execution / risk) NEVER
import ``nautilus_trader``; only ``strategy.py`` / ``runtime.py`` (separate
tasks) may do so. This keeps the kernel testable on bare CPython.
"""
from __future__ import annotations

from .execution import OrderSpec, chase_price, h_star_s, hedge_order
from .leg_policy import (
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
from .ledger import LegLedger
from .pair_spec import Leg, PairSpec, load_pair, to_decimal
from .risk import Action, RiskLimits, evaluate

__version__ = "0.1.0"

__all__ = [
    "Leg",
    "PairSpec",
    "load_pair",
    "to_decimal",
    "LegLedger",
    "OrderSpec",
    "h_star_s",
    "hedge_order",
    "chase_price",
    "Action",
    "RiskLimits",
    "evaluate",
    "LegQuote",
    "rank_legs",
    "select_pair",
    "expected_net_bps",
    "pair_cost_bps",
    "maker_cost_bps",
    "taker_cost_bps",
    "slip_bps",
    "leg_risk_bps",
]
