"""单测：OKX 合约元数据（ctVal）与 `b*` 分项拆解。

Run: python -m pytest tests/test_okx_meta.py -q
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nt_pairs.bstar_detail import b_star_detail  # noqa: E402
from nt_pairs.costs import LegSpec, round_cost_bps  # noqa: E402
from nt_pairs.okx_meta import (  # noqa: E402
    OkxInstrument,
    contract_multiplier,
    load_instruments,
    parse_instruments,
    save_instruments,
)

PAYLOAD = {
    "code": "0",
    "data": [
        {"instId": "SOXL-USDT-SWAP", "instType": "SWAP", "ctVal": "1", "ctValCcy": "SOXL",
         "tickSz": "0.01", "lotSz": "0.01", "state": "live"},
        {"instId": "BTC-USD-SWAP", "instType": "SWAP", "ctVal": "0.01", "ctValCcy": "BTC",
         "tickSz": "0.1", "lotSz": "0.1", "state": "live"},
        {"instId": "NOCTVAL-USDT-SWAP", "instType": "SWAP", "ctVal": "", "ctValCcy": "X",
         "state": "live"},
        {"instId": "SPOT-BTC-USDT", "instType": "SPOT", "ctVal": "1", "state": "live"},
    ],
}


def test_parse_instruments_keeps_swap_with_ctval():
    insts = parse_instruments(PAYLOAD)
    assert set(insts) == {"SOXL-USDT-SWAP", "BTC-USD-SWAP"}
    assert insts["SOXL-USDT-SWAP"].ct_val == Decimal("1")
    assert insts["BTC-USD-SWAP"].ct_val == Decimal("0.01")


def test_contract_multiplier_scales_okx_only():
    insts = parse_instruments(PAYLOAD)
    assert contract_multiplier("okx_futures", "SOXL-USDT-SWAP", insts) == Decimal("1")
    assert contract_multiplier("okx_futures", "BTC-USD-SWAP", insts) == Decimal("0.01")
    assert contract_multiplier("lighter", "SOXL", insts) == Decimal("1")
    assert contract_multiplier("hyperliquid_futures", "XYZ:SOXL", insts) == Decimal("1")


def test_contract_multiplier_without_metadata_is_one():
    assert contract_multiplier("okx_futures", "SOXL-USDT-SWAP", None) == Decimal("1")


def test_save_and_reload_cache(tmp_path):
    insts = parse_instruments(PAYLOAD)
    path = tmp_path / "okx_instruments.json"
    save_instruments(insts, path)
    payload = json.loads(path.read_text())
    assert payload["instruments"]["SOXL-USDT-SWAP"]["ctVal"] == "1"

    got, note = load_instruments(cache_path=path, offline=True)
    assert got["SOXL-USDT-SWAP"].ct_val == Decimal("1")
    assert "缓存命中" in note


def test_load_instruments_offline_without_cache(tmp_path):
    got, note = load_instruments(cache_path=tmp_path / "missing.json", offline=True)
    assert got == {}
    assert "offline" in note


def test_load_instruments_offline_uses_stale_cache(tmp_path):
    path = tmp_path / "okx_instruments.json"
    save_instruments(parse_instruments(PAYLOAD), path)
    payload = json.loads(path.read_text())
    payload["fetched_at"] = 0.0          # 极旧 → 过期
    path.write_text(json.dumps(payload), encoding="utf-8")
    got, note = load_instruments(cache_path=path, ttl_s=1.0, offline=True)
    assert got["SOXL-USDT-SWAP"].ct_val == Decimal("1")
    assert "过期" in note


def test_b_star_breakdown_matches_round_cost():
    maker = LegSpec(venue="okx_futures", maker_bps=Decimal("2"), taker_bps=Decimal("5"),
                    half_spread_bps=Decimal("0.77"), price=Decimal("60"), tick=Decimal("0.01"),
                    depth_bid_usd=Decimal("1000000"), depth_ask_usd=Decimal("1000000"))
    taker = LegSpec(venue="lighter", maker_bps=Decimal("0"), taker_bps=Decimal("0"),
                    half_spread_bps=Decimal("2.28"), price=Decimal("60"), tick=Decimal("0.01"),
                    depth_bid_usd=Decimal("300000"), depth_ask_usd=Decimal("300000"))
    kwargs = dict(qty_usd=Decimal("500"), hold_hours=Decimal("0.25"),
                  leader_slip_bps=Decimal("0.79"), follower_slip_bps=Decimal("2.27"))
    total = round_cost_bps(maker, taker, **kwargs)
    parts = b_star_detail(maker, taker, **kwargs)
    assert parts["total_bps"] == total
    assert parts["fee_bps"] == Decimal("7")          # 2+5 (maker 进/出) + 0+0 (taker 进/出)
    assert parts["slippage_bps"] == Decimal("5.33")   # 2×2.27 + 0.79
    summed = sum(parts[k] for k in ("fee_bps", "spread_bps", "slippage_bps", "carry_bps",
                                    "leg_risk_bps", "recon_bps"))
    assert abs(summed - total) <= Decimal("0.02")      # total 有 0.01 量化


def test_instrument_live_flag():
    assert OkxInstrument("X", Decimal("1"), state="live").live
    assert not OkxInstrument("X", Decimal("1"), state="suspend").live
