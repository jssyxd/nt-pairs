"""单测：`scripts/okx_leg_review.py` 的报告渲染（不依赖网络与真实数据）。

Run: python -m pytest tests/test_okx_leg_review.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import okx_leg_review as rev  # noqa: E402

PAYLOAD = {
    "generated_at": "2026-11-12T20:00:00Z",
    "window": {"start": "2026-11-11 19:00", "end": "2026-11-12 18:00", "replay_hours": 24},
    "config": {"qty_usd": 500.0},
    "okx_instruments": {"note": "缓存命中",
                        "contract_multipliers": {"okx_futures:SOXL-USDT-SWAP": "1"}},
    "stage_b": [
        {
            "key": "lighter:SOXL~okx_futures:SOXL-USDT-SWAP",
            "stage_a": {"gate1_unit": True, "gate2_fresh": True, "basis_p95_dev": 23.1, "k": 1.03},
            "book": {"a": {"spread_bps": 4.5, "slip_bps": 2.2},
                     "b": {"spread_bps": 1.5, "slip_bps": 0.8}},
            "b_star_bps": 21.09,
            "b_star_breakdown": {"fee_bps": 10.0, "spread_bps": 6.1, "slippage_bps": 3.9,
                                 "carry_bps": 0.0, "leg_risk_bps": 0.63, "recon_bps": 0.5,
                                 "total_bps": 21.09},
            "b_star_roles": {"maker": "lighter:SOXL", "taker": "okx_futures:SOXL-USDT-SWAP"},
            "coverage_ratio": 1.07, "depth_min_usd": 285000.0,
            "gate3_cost_cover": True, "gate4_capacity": True, "gate5_net_ev": False,
            "replay": {
                "n_events": 375,
                "by_session": {"rth": {"events": 297, "win_rate": 0.0,
                                        "net_bps_per_event": -4.43,
                                        "gross_capture_bps_per_event": 6.38,
                                        "mean_p_fill": 0.31, "opportunities_per_day": 386.1}},
                "by_day": {"2026-11-12": {"events": 375, "win_rate": 0.0,
                                          "net_bps_per_event": -4.43,
                                          "gross_capture_bps_per_event": 6.38,
                                          "mean_p_fill": 0.31}},
                "pfill_scenarios": {"conservative": {"total_net_bps": -800.0},
                                    "neutral": {"total_net_bps": -1600.0},
                                    "optimistic": {"total_net_bps": -2400.0}},
            },
        },
        {
            "key": "hyperliquid_futures:XYZ:SOXL~lighter:SOXL",
            "stage_a": {"gate1_unit": True, "gate2_fresh": True, "basis_p95_dev": 22.4, "k": 1.03},
            "book": {"a": {"spread_bps": 1.6, "slip_bps": 1.0},
                     "b": {"spread_bps": 4.5, "slip_bps": 2.2}},
            "b_star_bps": 14.08,
            "b_star_breakdown": {"fee_bps": 1.2, "spread_bps": 6.2, "slippage_bps": 5.5,
                                 "carry_bps": 0.0, "leg_risk_bps": 0.63, "recon_bps": 0.5,
                                 "total_bps": 14.08},
            "b_star_roles": {"maker": "lighter:SOXL", "taker": "hyperliquid_futures:XYZ:SOXL"},
            "coverage_ratio": 1.55, "depth_min_usd": 285000.0,
            "gate3_cost_cover": True, "gate4_capacity": True, "gate5_net_ev": True,
            "replay": {
                "n_events": 389,
                "by_session": {"rth": {"events": 302, "win_rate": 0.49,
                                        "net_bps_per_event": 4.30,
                                        "gross_capture_bps_per_event": 20.16,
                                        "mean_p_fill": 0.37, "opportunities_per_day": 392.6}},
                "by_day": {"2026-11-12": {"events": 389, "win_rate": 0.47,
                                          "net_bps_per_event": 3.63,
                                          "gross_capture_bps_per_event": 19.17,
                                          "mean_p_fill": 0.365}},
                "pfill_scenarios": {"conservative": {"total_net_bps": 700.0},
                                    "neutral": {"total_net_bps": 1400.0},
                                    "optimistic": {"total_net_bps": 2100.0}},
            },
        },
    ],
}


def test_render_contains_all_pairs_and_metrics():
    md = rev.render(PAYLOAD)
    assert "lighter:SOXL~okx_futures:SOXL-USDT-SWAP" in md
    assert "hyperliquid_futures:XYZ:SOXL~lighter:SOXL" in md
    assert "-4.43" in md and "4.30" in md
    assert "21.09" in md and "14.08" in md
    assert "Lighter:SOXL / OKX:SOXL-USDT-SWAP" in md


def test_rows_sorted_by_rth_net_ev_desc():
    rows = rev._sorted_rows(PAYLOAD)
    assert rows[0]["key"] == "hyperliquid_futures:XYZ:SOXL~lighter:SOXL"
    assert rows[1]["key"] == "lighter:SOXL~okx_futures:SOXL-USDT-SWAP"


def test_rows_with_error_are_dropped():
    payload = dict(PAYLOAD)
    payload["stage_b"] = list(PAYLOAD["stage_b"]) + [{"key": "broken", "error": "盘口数据缺失"}]
    assert len(rev._rows(payload)) == 2


def test_main_writes_report(tmp_path):
    import json
    src = tmp_path / "screen.json"
    src.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    out = tmp_path / "review.md"
    rc = rev.main(["--screen", str(src), "--out", str(out)])
    assert rc == 0
    assert "OKX 腿复核" in out.read_text(encoding="utf-8")
