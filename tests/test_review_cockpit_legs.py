"""复核测试：座舱的「腿必须限定在所选 pair」（2026-11-21 独立复核）。

`build_metrics` 把采集器目录里的每条序列都当成「一条腿」参与闸门②④，
所以过滤必须既**不漏掉本 pair 的腿**，也**不把别人的腿放进来**。

已确认的边界（本文件用 xfail 标出，修复后 xfail 会变成 XPASS，不影响套件绿灯）：

* 过滤只按**标的符号**匹配，不看**场所** → 同一 base 的第三个场所（OKX:SOXL）
  会被当成本 pair 的腿混进来（原缺陷「深度为 0 的 OKX:KIOXIA」只是同类的另一个例子）；
* `canonical_symbol` 用的是精确大写场所名（"HYPERLIQUID"/"OKX"/"LIGHTER"），
  而 chd_screen 写进 stage_a 的场所名是 "hyperliquid_futures"/"okx_futures"/"lighter"
  → 归一时前缀/后缀不会被剥掉 → 含 OKX 的对会被误删腿。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nt_pairs.cockpit import (  # noqa: E402
    RED,
    build_metrics,
    evaluate_stop_conditions,
    load_chd_screen,
    load_collector,
)


def _write_chd(tmp_path: Path, a: str = "hyperliquid_futures:XYZ:SOXL",
               b: str = "lighter:SOXL") -> Path:
    payload = {
        "stage_b": [{
            "key": f"{a}~{b}",
            "stage_a": {"a": a, "b": b, "basis_p95_dev": 30.4, "k": 1.02,
                        "repeat_a_pct": 0.0, "repeat_b_pct": 0.0},
            "book": {"a": {"depth_bid_usd": 7_200_000.0, "depth_ask_usd": 7_200_000.0},
                     "b": {"depth_bid_usd": 5_760_000.0, "depth_ask_usd": 5_760_000.0}},
            "b_star_bps": 11.1,
            "depth_min_usd": 5_760_000.0,
            "replay": {"by_day": {"2026-11-20": {"net_bps_per_event": 3.0}},
                       "by_session": {}},
        }],
        "config": {"qty_usd": 100.0},
    }
    path = tmp_path / "chd.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(venue: str, symbol: str, t: float, mid: float, qty: float = 100.0) -> dict:
    return {"venue": venue, "symbol": symbol, "t_epoch": t,
            "bids": [[mid - 0.01, qty, 1]], "asks": [[mid + 0.01, qty, 1]],
            "mid": mid, "trades": [], "t": "2026-11-21T00:00:00+00:00"}


def _write_collector(tmp_path: Path, rows) -> Path:
    root = tmp_path / "collector"
    for venue, entries in rows.items():
        d = root / venue
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "20261121.jsonl", "w", encoding="utf-8") as fh:
            for row in entries:
                fh.write(json.dumps(row) + "\n")
    return root


def test_both_pair_legs_are_kept(tmp_path):
    """本 pair 的两条腿都必须出现（不能被过滤掉）。"""
    chd = _write_chd(tmp_path)
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    assert {leg.label for leg in metrics.legs} == {"HYPERLIQUID:SOXL", "LIGHTER:SOXL"}


def test_foreign_base_leg_is_excluded(tmp_path):
    """别的 base（OKX:KIOXIA，深度为 0）不得进入本 pair 的腿。"""
    chd = _write_chd(tmp_path)
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
        "OKX": [_row("OKX", "KIOXIA-USDT-SWAP", 1000.0, 9.0)],
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    labels = {leg.label for leg in metrics.legs}
    assert labels == {"HYPERLIQUID:SOXL", "LIGHTER:SOXL"}
    verdicts = evaluate_stop_conditions(metrics)
    assert not verdicts["depth"].startswith(RED), verdicts["depth"]


@pytest.mark.xfail(reason=(
    "已知缺陷：腿过滤只比符号、不比场所 → 同一 base 的第三个场所"
    "（如 OKX:SOXL，深度可能为 0）仍会被当成本 pair 的腿混进闸门②④"),
    strict=False)
def test_same_base_other_venue_is_excluded(tmp_path):
    """同一 base、不同场所的腿不属于本 pair，必须排除。"""
    chd = _write_chd(tmp_path)
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
        "OKX": [_row("OKX", "SOXL-USDT-SWAP", 1000.0, 9.0)],
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    labels = {leg.label for leg in metrics.legs}
    assert labels == {"HYPERLIQUID:SOXL", "LIGHTER:SOXL"}


@pytest.mark.xfail(reason=(
    "已知缺陷：canonical_symbol 只认精确大写场所名，而 stage_a 里的场所名是 "
    "'okx_futures'/'hyperliquid_futures' → OKX 后缀 '-USDT-SWAP' 没被剥掉 → "
    "含 OKX 的对会被误删腿"),
    strict=False)
def test_okx_leg_of_the_pair_is_kept(tmp_path):
    """pair 里含 OKX 腿时，那条腿必须保留。"""
    chd = _write_chd(tmp_path, b="okx_futures:SOXL-USDT-SWAP")
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "SOXL", 1000.0, 56.0)],
        "OKX": [_row("OKX", "SOXL-USDT-SWAP", 1000.0, 56.0)],
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    assert {leg.label for leg in metrics.legs} == {"HYPERLIQUID:SOXL", "OKX:SOXL"}


def test_no_chd_pair_keeps_all_collector_series(tmp_path):
    """没有 chd 上下文时不做过滤（保持旧行为，不凭空丢腿）。"""
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
    })
    metrics = build_metrics(chd=None, collector=load_collector(str(collector)))
    assert {leg.label for leg in metrics.legs} == {"HYPERLIQUID:SOXL", "LIGHTER:SOXL"}


def test_stale_quote_rule_reports_coverage_separately(tmp_path):
    """采集缺口（覆盖率低）必须单独报，且不得把断流说成报价陈旧。"""
    chd = _write_chd(tmp_path)
    collector = _write_collector(tmp_path, {
        "HYPERLIQUID": [_row("HYPERLIQUID", "SOXL", 1000.0, 56.0)],
        "LIGHTER": [_row("LIGHTER", "SOXL", 1000.0, 56.0)],
    })
    metrics = build_metrics(chd=load_chd_screen(str(chd)),
                            collector=load_collector(str(collector)))
    verdicts = evaluate_stop_conditions(metrics)
    assert not verdicts["stale_quote"].startswith(RED), verdicts["stale_quote"]
    for leg in metrics.legs:
        assert leg.coverage is not None and 0.0 < leg.coverage <= 1.0
