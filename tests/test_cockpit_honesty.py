"""座舱「诚实性」回归测试：防止面板把两件事混为一谈。

覆盖三个此前会误导人的地方：

1. **腿必须限定在所选 pair** —— 采集器目录里别的标的（如深度为 0 的 `OKX:KIOXIA`）
   不能混进来把闸门④报红。
2. **成交率必须用同口径比较** —— 跨来源归一化（模型=回放机会数×概率 vs 实测=标定成交/分钟）
   两边「机会」定义不同，比出来的比值没有意义 → 只能给参考值，不能给绿/黄/红。
   同口径（同一批探针内的 实测 p_fill vs 模型 p_fill）才可以判定。
3. **报价陈旧 ≠ 数据缺口** —— 采集器断流 5 小时在「缺小时沿用上收盘」口径下会被算成
   80% 重复收盘率，但那是运维问题不是报价陈旧 → 必须分开报（覆盖率）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nt_pairs.cockpit import (  # noqa: E402
    GREEN,
    RED,
    UNKNOWN,
    CockpitMetrics,
    LegMetric,
    PFillEstimate,
    build_metrics,
    evaluate_stop_conditions,
    load_chd_screen,
    load_collector,
)


def _metrics(**overrides) -> CockpitMetrics:
    base = dict(
        generated_utc="2026-11-21T00:00:00Z",
        net_ev_by_day={"2026-11-20": 3.0},
        net_ev_source="replay",
        legs=(
            LegMetric(label="A", depth_usd=10_000.0, depth_multiple=100.0, repeat_close_ratio=0.01),
            LegMetric(label="B", depth_usd=20_000.0, depth_multiple=200.0, repeat_close_ratio=0.01),
        ),
        target_notional_usd=100.0,
        basis_p95_bps=27.0,
        k=1.02,
        b_star_bps=11.0,
        p_fill_model=PFillEstimate(value=0.3, unit="per_minute", source="model"),
        p_fill_measured=PFillEstimate(value=0.25, unit="per_minute", source="measured"),
        naked_leg_age_s=0.0,
        naked_leg_exposure_usd=0.0,
    )
    base.update(overrides)
    return CockpitMetrics(**base)


# ---------------------------------------------------------------------------
# 1. 腿必须限定在所选 pair
# ---------------------------------------------------------------------------


def _write_chd(tmp_path: Path, pair: str = "hyperliquid_futures:XYZ:SOXL~lighter:SOXL") -> Path:
    payload = {
        "stage_b": [{
            "key": pair,
            "stage_a": {
                "a": "hyperliquid_futures:XYZ:SOXL",
                "b": "lighter:SOXL",
                "basis_p95_dev": 30.4,
                "k": 1.02,
                "repeat_a_pct": 0.0,
                "repeat_b_pct": 0.0,
            },
            "book": {
                "a": {"depth_bid_usd": 7_200_000.0, "depth_ask_usd": 7_200_000.0},
                "b": {"depth_bid_usd": 5_760_000.0, "depth_ask_usd": 5_760_000.0},
            },
            "b_star_bps": 14.4,
            "depth_min_usd": 5_760_000.0,
            "replay": {"by_day": {"2026-11-20": {"net_bps_per_event": 3.0}},
                       "by_session": {}},
        }],
        "config": {"qty_usd": 100.0},
    }
    path = tmp_path / "chd.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_collector(tmp_path: Path, rows) -> Path:
    root = tmp_path / "collector"
    for venue, entries in rows.items():
        d = root / venue
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "20261121.jsonl", "w", encoding="utf-8") as fh:
            for row in entries:
                fh.write(json.dumps(row) + "\n")
    return root


def _row(venue: str, symbol: str, t: float, mid: float, qty: float = 100.0) -> dict:
    return {
        "venue": venue, "symbol": symbol, "t_epoch": t,
        "bids": [[mid - 0.01, qty, 1]], "asks": [[mid + 0.01, qty, 1]],
        "mid": mid, "trades": [], "t": "2026-11-21T00:00:00+00:00",
    }


def test_collector_legs_are_scoped_to_the_selected_pair(tmp_path):
    """采集器里有别的标的时，不得混进本 pair 的腿。"""
    chd = _write_chd(tmp_path)
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "xyz:SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
        "OKX": [_row("OKX", "KIOXIA-USDT-SWAP", 1000.0, 9.0)],  # 深度为 0 的无关标的
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    labels = {leg.label for leg in metrics.legs}
    assert "OKX:KIOXIA" not in labels, "无关标的不得进入本 pair 的腿"
    assert not any(leg.label.endswith("KIOXIA") for leg in metrics.legs)

    verdicts = evaluate_stop_conditions(metrics)
    assert not verdicts["depth"].startswith(RED), (
        f"无关标的深度为 0 不应把本 pair 报红：{verdicts['depth']}")


def test_legs_still_include_both_pair_legs(tmp_path):
    chd = _write_chd(tmp_path)
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "xyz:SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    labels = {leg.label for leg in metrics.legs}
    assert labels == {"HYPERLIQUID:SOXL", "LIGHTER:SOXL"}


# ---------------------------------------------------------------------------
# 2. 成交率必须同口径比较
# ---------------------------------------------------------------------------


def test_probe_ratio_is_used_when_available():
    """同口径探针比值优先 → 给出绿/黄/红。"""
    metrics = _metrics(p_fill_probe_ratio=1.57)
    verdict = evaluate_stop_conditions(metrics)["fill_rate"]
    assert verdict.startswith(GREEN), verdict


def test_probe_ratio_below_threshold_is_red():
    metrics = _metrics(p_fill_probe_ratio=0.4)
    verdict = evaluate_stop_conditions(metrics)["fill_rate"]
    assert verdict.startswith(RED), verdict


def test_cross_source_ratio_is_reference_only_not_a_verdict():
    """跨来源归一化的比值不能用来判绿/红（两边机会定义不同）。"""
    metrics = _metrics(
        p_fill_probe_ratio=None,
        p_fill_model_per_min=0.1479,
        p_fill_measured_per_min=8.1504,
    )
    verdict = evaluate_stop_conditions(metrics)["fill_rate"]
    assert verdict.startswith(UNKNOWN), (
        f"跨来源比值（55×）不得作为判定依据：{verdict}")


def test_same_unit_comparison_without_probe_ratio_still_works():
    metrics = _metrics(p_fill_probe_ratio=None)
    verdict = evaluate_stop_conditions(metrics)["fill_rate"]
    assert verdict.startswith(GREEN), verdict


# ---------------------------------------------------------------------------
# 3. 报价陈旧 ≠ 数据缺口
# ---------------------------------------------------------------------------


def test_feed_gap_is_reported_separately_from_stale_quotes():
    """覆盖率低要单独说明，不能把断流说成报价陈旧。"""
    metrics = _metrics(legs=(
        LegMetric(label="LIGHTER:SOXL", depth_usd=5_760_000.0, depth_multiple=57.6,
                  repeat_close_ratio=0.0, coverage=0.20),
        LegMetric(label="HYPERLIQUID:SOXL", depth_usd=7_200_000.0, depth_multiple=72.0,
                  repeat_close_ratio=0.0, coverage=0.27),
    ))
    verdict = evaluate_stop_conditions(metrics)["stale_quote"]
    assert verdict.startswith(GREEN), verdict
    assert "覆盖率" in verdict, f"必须点明采集缺口：{verdict}"
    assert "LIGHTER:SOXL 覆盖率 20%" in verdict


def test_true_stale_quotes_still_flag_red():
    metrics = _metrics(legs=(
        LegMetric(label="LIGHTER:SOXL", depth_usd=5_760_000.0, depth_multiple=57.6,
                  repeat_close_ratio=0.88, coverage=1.0),
    ))
    verdict = evaluate_stop_conditions(metrics)["stale_quote"]
    assert verdict.startswith(RED), verdict


def test_cli_strict_still_works_after_changes(tmp_path):
    chd = _write_chd(tmp_path)
    out = tmp_path / "metrics.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cockpit.py"), "--chd", str(chd),
         "--metrics-only", "--metrics-out", str(out)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["legs"], "应当有腿指标"
