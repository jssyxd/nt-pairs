"""Pair specification and unit conversion (CONTRACT §1-§2).

Pure data + validation. No IO beyond :func:`load_pair`; NEVER imports
``nautilus_trader``. All quantities are :class:`~decimal.Decimal`; floats are
rejected at every boundary (:func:`to_decimal`) so unit conversion stays exact
and is written down exactly once (here), per CONTRACT §1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

__all__ = ["Leg", "PairSpec", "load_pair", "to_decimal"]

_LEG_DECIMAL_FIELDS = (
    "price_tick",
    "size_increment",
    "min_qty",
    "min_notional_usd",
    "maker_fee_bps",
    "taker_fee_bps",
)
_PAIR_DECIMAL_FIELDS = ("ratio", "sigma_1m_bps", "max_leg_usd")
_PAIR_REQUIRED_KEYS = (
    "name",
    "leader",
    "follower",
    "ratio",
    "unit_evidence",
    "sigma_1m_bps",
    "max_leg_usd",
)


def to_decimal(field: str, value: object) -> Decimal:
    """Coerce ``value`` to ``Decimal`` without ever passing through float.

    Accepts ``Decimal`` / ``int`` / numeric ``str``; rejects ``float`` and
    ``bool`` so no binary floating point can leak into price or quantity math.
    Raises ``ValueError`` naming ``field`` on anything unusable.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (bool, float)):
        raise ValueError(
            f"{field}: float is prohibited for Decimal fields (got {value!r}); "
            "pass a str or Decimal instead"
        )
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            raise ValueError(
                f"{field}: cannot parse {value!r} as Decimal"
            ) from None
    raise ValueError(
        f"{field}: expected Decimal/int/str, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class Leg:
    """One venue leg of the pair (CONTRACT §2)."""

    venue: str                     # "HYPERLIQUID" | "LIGHTER"
    instrument_id: str             # e.g. "io:OAI-USD-PERP.HYPERLIQUID"
    price_tick: Decimal
    size_increment: Decimal
    min_qty: Decimal
    min_notional_usd: Decimal
    maker_fee_bps: Decimal         # negative = rebate
    taker_fee_bps: Decimal

    def __post_init__(self) -> None:
        for name in _LEG_DECIMAL_FIELDS:
            object.__setattr__(
                self, name, to_decimal(f"Leg.{name}", getattr(self, name))
            )
        if self.price_tick <= 0:
            raise ValueError(
                f"Leg.price_tick must be > 0 (got {self.price_tick}) "
                f"[venue={self.venue!r}]"
            )
        if self.size_increment <= 0:
            raise ValueError(
                f"Leg.size_increment must be > 0 (got {self.size_increment}) "
                f"[venue={self.venue!r}]"
            )


@dataclass(frozen=True)
class PairSpec:
    """Leader/follower pair with the single authoritative unit ratio.

    ``ratio = follower_qty / leader_qty`` (CONTRACT §1); ``unit_evidence``
    (quote-unit evidence + calibration time) is mandatory.
    """

    name: str
    leader: Leg                    # passive leg (posts maker orders)
    follower: Leg                  # active leg (IOC hedge)
    ratio: Decimal
    unit_evidence: str
    sigma_1m_bps: Decimal          # 1-minute basis volatility
    max_leg_usd: Decimal           # per-leg notional cap

    def __post_init__(self) -> None:
        if not isinstance(self.leader, Leg):
            raise ValueError(
                "PairSpec.leader must be a Leg instance "
                f"(got {type(self.leader).__name__})"
            )
        if not isinstance(self.follower, Leg):
            raise ValueError(
                "PairSpec.follower must be a Leg instance "
                f"(got {type(self.follower).__name__})"
            )
        for name in _PAIR_DECIMAL_FIELDS:
            object.__setattr__(
                self, name, to_decimal(f"PairSpec.{name}", getattr(self, name))
            )
        if self.ratio <= 0:
            raise ValueError(f"PairSpec.ratio must be > 0 (got {self.ratio})")
        if not isinstance(self.unit_evidence, str) or not self.unit_evidence.strip():
            raise ValueError(
                "PairSpec.unit_evidence must be a non-empty string "
                "(quote-unit evidence + calibration time)"
            )
        if self.sigma_1m_bps <= 0:
            raise ValueError(
                f"PairSpec.sigma_1m_bps must be > 0 (got {self.sigma_1m_bps})"
            )


def load_pair(path: str | Path) -> PairSpec:
    """Load a :class:`PairSpec` from a JSON document.

    JSON *is* the supported YAML subset: PyYAML is deliberately not used, so
    ``.json``/``.yaml``/``.yml`` suffixes are accepted but the content must be
    valid JSON. Numeric literals are parsed straight to ``Decimal`` via
    ``parse_float``/``parse_int`` — no float ever materialises. Validation
    errors are ``ValueError`` naming the offending field.
    """
    p = Path(path)
    if p.suffix.lower() not in (".json", ".yaml", ".yml"):
        raise ValueError(
            f"load_pair: unsupported suffix {p.suffix!r} for {p}; "
            "expected .json/.yaml/.yml (content must be JSON)"
        )
    data = json.loads(
        p.read_text(encoding="utf-8"), parse_float=Decimal, parse_int=Decimal
    )
    if not isinstance(data, dict):
        raise ValueError(f"load_pair: top level of {p} must be a JSON object")
    missing = [k for k in _PAIR_REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(
            f"load_pair: {p.name} missing required field(s): {', '.join(missing)}"
        )
    try:
        leader = Leg(**data["leader"])
        follower = Leg(**data["follower"])
    except TypeError as exc:
        raise ValueError(f"load_pair: bad leg object in {p.name}: {exc}") from None
    return PairSpec(
        name=data["name"],
        leader=leader,
        follower=follower,
        ratio=data["ratio"],
        unit_evidence=data["unit_evidence"],
        sigma_1m_bps=data["sigma_1m_bps"],
        max_leg_usd=data["max_leg_usd"],
    )
