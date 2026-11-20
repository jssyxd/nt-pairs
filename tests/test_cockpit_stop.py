"""座舱停机判据的单测。

覆盖：
* 阈值表（``STOP_RULES``）的键、方向与数值 —— 阈值只允许在这张表里改；
* ``evaluate_stop_conditions`` 对每条规则给出正确的绿 / 黄 / 红；
* **缺数据一律判 UNKNOWN**（不是 GREEN、也不是 0）；
* 指标装配（Stage B 回放 → metrics）与 JSON 往返；
* CLI 的 ``--metrics-only`` / ``--strict`` 行为。
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
    STOP_RULES,
    STOP_RULES_BY_KEY,
    UNKNOWN,
    YELLOW,
    CockpitMetrics,
    LegMetric,
    PFillEstimate,
    build_metrics,
    evaluate_stop_conditions,
    load_chd_screen,
    metrics_from_dict,
    metrics_to_dict,
    overall_status,
    status_of,
)


def _metrics(**overrides) -> CockpitMetrics:
    """一份「全绿」的基准 metrics；各条规则再单独破坏某个字段。"""
    base = dict(
        generated_utc="2026-11-21T00:00:00Z",
        net_ev_by_day={"2026-11-18": 1.0, "2026-11-19": 2.0, "2026-11-20": 3.0},
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
# 阈值表
# ---------------------------------------------------------------------------


def test_threshold_table_is_the_single_source_of_truth():
    keys = [rule.key for rule in STOP_RULES]
    assert len(keys) == len(set(keys))
    assert set(keys) == {
        "rolling_net_ev",
        "depth",
        "cost_cover",
        "fill_rate",
        "naked_leg",
        "stale_quote",
    }
    assert STOP_RULES_BY_KEY["depth"].red == 10.0        # 闸门④：10 × 目标名义
    assert STOP_RULES_BY_KEY["rolling_net_ev"].red == 3.0  # 连续 3 个交易日
    assert STOP_RULES_BY_KEY["cost_cover"].red == 1.0      # p95 ≥ k·b*
    assert STOP_RULES_BY_KEY["fill_rate"].red == 0.6        # 实测 ≥ 60% 模型
    assert STOP_RULES_BY_KEY["naked_leg"].red == 180.0     # naked_flatten_s
    assert STOP_RULES_BY_KEY["stale_quote"].red == 0.10    # 重复收盘率 10%
    for rule in STOP_RULES:
        assert rule.direction in ("above", "below")
        assert rule.rationale


# ---------------------------------------------------------------------------
# 逐条规则
# ---------------------------------------------------------------------------


def test_all_green_baseline():
    conditions = evaluate_stop_conditions(_metrics())
    assert set(conditions) == {rule.key for rule in STOP_RULES}
    for key, value in conditions.items():
        assert status_of(value) == GREEN, f"{key}: {value}"
    assert overall_status(conditions) == GREEN


def test_net_ev_rule_counts_trailing_non_positive_days():
    two_days = _metrics(net_ev_by_day={"2026-11-19": -1.0, "2026-11-20": -2.0})
    assert status_of(evaluate_stop_conditions(two_days)["rolling_net_ev"]) == YELLOW

    three_days = _metrics(
        net_ev_by_day={"2026-11-18": -1.0, "2026-11-19": -2.0, "2026-11-20": -3.0}
    )
    assert status_of(evaluate_stop_conditions(three_days)["rolling_net_ev"]) == RED

    # 中间断一天正 EV → 连续计数归零
    reset = _metrics(
        net_ev_by_day={"2026-11-18": -1.0, "2026-11-19": 5.0, "2026-11-20": -2.0}
    )
    assert status_of(evaluate_stop_conditions(reset)["rolling_net_ev"]) == GREEN


def test_depth_rule_flags_thin_leg():
    thin = _metrics(
        legs=(
            LegMetric(label="A", depth_usd=900.0, depth_multiple=9.0, repeat_close_ratio=0.01),
            LegMetric(label="B", depth_usd=20_000.0, depth_multiple=200.0, repeat_close_ratio=0.01),
        )
    )
    assert status_of(evaluate_stop_conditions(thin)["depth"]) == RED


def test_cost_cover_rule_uses_k_times_b_star():
    broken = _metrics(basis_p95_bps=5.0, k=1.0, b_star_bps=11.0)
    assert status_of(evaluate_stop_conditions(broken)["cost_cover"]) == RED
    borderline = _metrics(basis_p95_bps=13.0, k=1.0, b_star_bps=11.0)
    assert status_of(evaluate_stop_conditions(borderline)["cost_cover"]) == YELLOW


def test_fill_rate_rule_compares_same_unit_only():
    collapsed = _metrics(
        p_fill_model=PFillEstimate(value=0.3, unit="per_minute", source="model"),
        p_fill_measured=PFillEstimate(value=0.1, unit="per_minute", source="measured"),
    )
    assert status_of(evaluate_stop_conditions(collapsed)["fill_rate"]) == RED

    mismatched = _metrics(
        p_fill_model=PFillEstimate(value=0.3, unit="per_event", source="model"),
        p_fill_measured=PFillEstimate(value=0.1, unit="per_minute", source="measured"),
    )
    assert status_of(evaluate_stop_conditions(mismatched)["fill_rate"]) == UNKNOWN


def test_naked_leg_rule_uses_flatten_threshold():
    warn = _metrics(naked_leg_age_s=90.0)
    assert status_of(evaluate_stop_conditions(warn)["naked_leg"]) == YELLOW
    breach = _metrics(naked_leg_age_s=200.0)
    assert status_of(evaluate_stop_conditions(breach)["naked_leg"]) == RED


def test_stale_quote_rule_uses_worst_leg():
    stale = _metrics(
        legs=(
            LegMetric(label="A", depth_usd=10_000.0, depth_multiple=100.0, repeat_close_ratio=0.12),
            LegMetric(label="B", depth_usd=20_000.0, depth_multiple=200.0, repeat_close_ratio=0.01),
        )
    )
    assert status_of(evaluate_stop_conditions(stale)["stale_quote"]) == RED


# ---------------------------------------------------------------------------
# 缺数据 = UNKNOWN（绝不显示 0）
# ---------------------------------------------------------------------------


def test_missing_data_is_unknown_not_green():
    empty = CockpitMetrics(generated_utc="2026-11-21T00:00:00Z")
    conditions = evaluate_stop_conditions(empty)
    assert set(status_of(v) for v in conditions.values()) == {UNKNOWN}
    assert overall_status(conditions) == UNKNOWN


def test_missing_naked_leg_is_unknown():
    conditions = evaluate_stop_conditions(_metrics(naked_leg_age_s=None))
    assert status_of(conditions["naked_leg"]) == UNKNOWN


def test_overall_status_takes_the_worst():
    conditions = evaluate_stop_conditions(
        _metrics(naked_leg_age_s=200.0, basis_p95_bps=5.0, k=1.0, b_star_bps=11.0)
    )
    assert overall_status(conditions) == RED
    assert overall_status({}) == UNKNOWN


# ---------------------------------------------------------------------------
# 指标装配与序列化
# ---------------------------------------------------------------------------


def test_metrics_from_chd_screen_repo_fixture():
    path = ROOT / "data" / "chd_screen.json"
    if not path.is_file():
        pytest.skip("仓库里没有 data/chd_screen.json")
    chd = load_chd_screen(str(path))
    assert chd is not None
    assert chd.target_notional_usd == 100.0
    assert chd.net_ev_by_day
    assert chd.b_star_bps and chd.b_star_bps > 0
    metrics = build_metrics(chd=chd)
    assert metrics.net_ev_by_day == chd.net_ev_by_day
    assert metrics.p_fill_model is not None
    conditions = evaluate_stop_conditions(metrics)
    assert status_of(conditions["cost_cover"]) == GREEN
    assert status_of(conditions["depth"]) == GREEN


def test_metrics_json_round_trip():
    metrics = _metrics()
    payload = metrics_to_dict(metrics)
    restored = metrics_from_dict(payload)
    assert restored.net_ev_by_day == metrics.net_ev_by_day
    assert [leg.label for leg in restored.legs] == [leg.label for leg in metrics.legs]
    assert restored.p_fill_model == metrics.p_fill_model
    assert evaluate_stop_conditions(restored) == evaluate_stop_conditions(metrics)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cockpit.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )


def test_cli_metrics_only_writes_json(tmp_path):
    out = tmp_path / "metrics.json"
    proc = _run_cli("--metrics-only", "--metrics-out", str(out),
                    "--chd", str(ROOT / "data" / "chd_screen.json"),
                    "--collector", str(tmp_path / "no-collector"))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload["stop_conditions"]) == {rule.key for rule in STOP_RULES}
    assert payload["overall_status"] in (GREEN, YELLOW, RED, UNKNOWN)


def test_cli_strict_exits_three_when_a_rule_is_red(tmp_path):
    chd = ROOT / "data" / "chd_screen.json"
    if not chd.is_file():
        pytest.skip("仓库里没有 data/chd_screen.json")
    # 篡改 Stage B 的成本门槛：b* 拉到 100bps → 成本覆盖必然失效
    payload = json.loads(chd.read_text(encoding="utf-8"))
    payload["stage_b"][0]["b_star_bps"] = 100.0
    broken = tmp_path / "chd_broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")

    out = tmp_path / "metrics.json"
    proc = _run_cli("--metrics-only", "--metrics-out", str(out), "--strict",
                    "--chd", str(broken), "--collector", str(tmp_path / "none"))
    assert proc.returncode == 3, proc.stdout + proc.stderr
    metrics_payload = json.loads(out.read_text(encoding="utf-8"))
    assert metrics_payload["overall_status"] == RED
    assert metrics_payload["stop_conditions"]["cost_cover"].startswith("RED")


def test_cli_without_any_input_still_renders_honest_panel(tmp_path):
    out = tmp_path / "metrics.json"
    proc = _run_cli("--metrics-only", "--metrics-out", str(out),
                    "--chd", str(tmp_path / "none.json"),
                    "--collector", str(tmp_path / "none"),
                    "--fill-calibration", str(tmp_path / "none.json"))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall_status"] == UNKNOWN
    assert payload["live_pnl_total"] is None
