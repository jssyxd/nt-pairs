"""Risk gates (CONTRACT §5). Pure function, no ``nautilus_trader``.

:func:`evaluate` maps the current world state to one :class:`Action` plus a
human-readable reason. Priority (CONTRACT §5):

1. ``pos_known=False`` → WARN only — never flatten on uncertain state.
2. Equity drawdown: ``>= equity_kill_pct`` → HALT, ``>= equity_freeze_pct``
   → FREEZE_NEW.
3. ``quota_left < 100`` → FREEZE_NEW.
4. Naked-leg age: ``>= naked_halt_s`` → HALT, ``>= naked_flatten_s`` →
   FLATTEN, ``>= naked_warn_s`` → WARN. ``naked_since is None`` → no
   age-based escalation at all.
5. ``naked_usd > max_naked_usd_pct * equity_now`` **and** age >= warn →
   escalate to at least FREEZE_NEW (never downgrade FLATTEN/HALT).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .pair_spec import to_decimal

__all__ = ["Action", "RiskLimits", "evaluate"]

_QUOTA_FREEZE_THRESHOLD = 100


class Action(Enum):
    NONE = "NONE"
    WARN = "WARN"
    FREEZE_NEW = "FREEZE_NEW"
    FLATTEN = "FLATTEN"
    HALT = "HALT"


#: severity order used for "at least FREEZE_NEW" escalation
_SEVERITY = {
    Action.NONE: 0,
    Action.WARN: 1,
    Action.FREEZE_NEW: 2,
    Action.FLATTEN: 3,
    Action.HALT: 4,
}


@dataclass(frozen=True)
class RiskLimits:
    naked_warn_s: float = 60.0
    naked_flatten_s: float = 180.0
    naked_halt_s: float = 600.0
    max_naked_usd_pct: float = 0.02     # fraction of equity
    equity_freeze_pct: float = 0.05
    equity_kill_pct: float = 0.10


def evaluate(
    *,
    pos_known: bool,
    naked_usd: Decimal,
    naked_since: float | None,
    equity_now: Decimal,
    equity_peak: Decimal,
    quota_left: int,
    now: float,
    limits: RiskLimits,
) -> tuple[Action, str]:
    """Pure risk decision; see module docstring for the priority order."""
    if not pos_known:
        return (
            Action.WARN,
            "pos_known=False: position state uncertain; warn only, "
            "never auto-flatten the other leg",
        )

    naked_usd = to_decimal("evaluate.naked_usd", naked_usd)
    equity_now = to_decimal("evaluate.equity_now", equity_now)
    equity_peak = to_decimal("evaluate.equity_peak", equity_peak)

    kill_pct = Decimal(str(limits.equity_kill_pct))
    freeze_pct = Decimal(str(limits.equity_freeze_pct))
    if equity_peak > 0:
        drawdown = (equity_peak - equity_now) / equity_peak
        if drawdown >= kill_pct:
            return (
                Action.HALT,
                f"equity drawdown {drawdown:.2%} >= kill {kill_pct:.2%} "
                f"(now={equity_now}, peak={equity_peak})",
            )
        if drawdown >= freeze_pct:
            return (
                Action.FREEZE_NEW,
                f"equity drawdown {drawdown:.2%} >= freeze {freeze_pct:.2%} "
                f"(now={equity_now}, peak={equity_peak})",
            )
    # equity_peak <= 0: drawdown undefined, skip the equity gate (degenerate
    # account; age/quota gates still apply).

    if quota_left < _QUOTA_FREEZE_THRESHOLD:
        return (
            Action.FREEZE_NEW,
            f"quota_left={quota_left} < {_QUOTA_FREEZE_THRESHOLD}",
        )

    action = Action.NONE
    reason = "no limit breached"
    if naked_since is not None:
        age = max(0.0, now - naked_since)  # negative age (clock skew) → 0
        if age >= limits.naked_halt_s:
            action = Action.HALT
            reason = f"naked age {age:.0f}s >= halt {limits.naked_halt_s:.0f}s"
        elif age >= limits.naked_flatten_s:
            action = Action.FLATTEN
            reason = (
                f"naked age {age:.0f}s >= flatten {limits.naked_flatten_s:.0f}s"
            )
        elif age >= limits.naked_warn_s:
            action = Action.WARN
            reason = f"naked age {age:.0f}s >= warn {limits.naked_warn_s:.0f}s"

        cap = Decimal(str(limits.max_naked_usd_pct)) * equity_now
        if age >= limits.naked_warn_s and naked_usd > cap:
            note = (
                f"naked_usd={naked_usd} > cap={cap} "
                f"({limits.max_naked_usd_pct:.0%} of equity={equity_now}) "
                f"at age {age:.0f}s"
            )
            if _SEVERITY[action] < _SEVERITY[Action.FREEZE_NEW]:
                action = Action.FREEZE_NEW
                reason = note
            else:
                reason = f"{reason}; {note}"

    return action, reason
