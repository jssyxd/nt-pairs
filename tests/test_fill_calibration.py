"""成交率标定的离线单测（不联网、不读真实采集数据）。

覆盖：
* 标的归一（HL `xyz:SOXL` ↔ Lighter `SOXL` ↔ OKX `SOXL-USDT-SWAP`）；
* L2 簿聚合（HL 按价位聚合 vs Lighter 逐笔挂单 → 必须按价求和）；
* 贴价队列的 3 次采样与中位数；
* 成交判定（队头 / 全成交两条边界）；
* 模型口径（与 `market_micro.fill_probability` 完全一致）与复合 Poisson 变体；
* 端到端：合成采集夹具 → 标定脚本 → 实测 p_fill 与模型 p_fill。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "calibrate_fill_model.py"
sys.path.insert(0, str(ROOT / "src"))

from nt_pairs import fill_calib as fc  # noqa: E402
from nt_pairs import market_micro as mm  # noqa: E402


# ---------------------------------------------------------------- 归一与聚合

def test_canonical_symbol_normalizes_across_venues():
    assert fc.canonical_symbol("HYPERLIQUID", "xyz:SOXL") == "SOXL"
    assert fc.canonical_symbol("HYPERLIQUID", "io:OAI") == "OAI"
    assert fc.canonical_symbol("LIGHTER", "SOXL") == "SOXL"
    assert fc.canonical_symbol("OKX", "SOXL-USDT-SWAP") == "SOXL"


def test_load_venue_aggregates_per_order_levels(tmp_path):
    """Lighter 的簿是逐笔挂单 → 同一价位的数量必须求和（否则队列量低估一个数量级）。"""
    root = tmp_path / "collector"
    d = root / "LIGHTER"
    d.mkdir(parents=True)
    rec = {
        "venue": "LIGHTER", "symbol": "SOXL", "t_epoch": 1_780_000_000.0,
        "bids": [[33.44, 20.0, 1], [33.44, 24.8, 1], [33.43, 50.0, 1]],
        "asks": [[33.46, 75.2, 1]],
    }
    (d / "20260101.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    snaps = fc.load_venue(str(root), "LIGHTER", {"SOXL"})["SOXL"]
    assert len(snaps) == 1
    px, size = fc.best_level(snaps[0], "buy")
    assert px == 33.44
    assert size == pytest.approx(44.8)   # 20.0 + 24.8，不是第一笔的 20.0
    px2, size2 = fc.best_level(snaps[0], "sell")
    assert px2 == 33.46 and size2 == pytest.approx(75.2)


def test_load_venue_key_is_canonical(tmp_path):
    root = tmp_path / "collector"
    d = root / "HYPERLIQUID"
    d.mkdir(parents=True)
    rec = {"venue": "HYPERLIQUID", "symbol": "xyz:SOXL", "t_epoch": 1.0,
           "bids": [[33.4, 10.0, 2]], "asks": [[33.5, 12.0, 1]]}
    (d / "20260101.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    snaps = fc.load_venue(str(root), "HYPERLIQUID", {"SOXL"})
    assert "SOXL" in snaps


# ---------------------------------------------------------------- 队列采样

def _snap(t, bid_qty, ask_qty=5.0, px=33.4):
    return fc.Snap(t=t, venue="LIGHTER", symbol="SOXL",
                   bids={px: bid_qty}, asks={px + 0.02: ask_qty}, trades=(), gap=False)


def test_sample_queue_takes_median_of_three_backward_samples():
    snaps = [_snap(0.0, 10.0), _snap(1.0, 20.0), _snap(2.0, 30.0), _snap(3.0, 40.0)]
    samples, med = fc.sample_queue(snaps, 3, "buy", 33.4, n_samples=3, min_gap_s=0.2)
    assert samples == [40.0, 30.0, 20.0]      # 从决策时刻向前（挂单前）采样
    assert med == 30.0                         # 中位数


def test_sample_queue_skips_samples_closer_than_min_gap():
    snaps = [_snap(0.0, 10.0), _snap(0.1, 20.0), _snap(0.2, 30.0), _snap(5.0, 40.0)]
    samples, med = fc.sample_queue(snaps, 3, "buy", 33.4, n_samples=3, min_gap_s=0.2)
    assert samples == [40.0, 30.0, 10.0]       # 0.1s 的样本太近被跳过
    assert med == 30.0


def test_sample_queue_marks_missing_level_as_none():
    snaps = [fc.Snap(t=0.0, venue="L", symbol="SOXL", bids={33.3: 10.0}, asks={}, trades=(), gap=False),
             _snap(1.0, 20.0), _snap(2.0, 30.0)]
    samples, med = fc.sample_queue(snaps, 2, "buy", 33.4, n_samples=3, min_gap_s=0.2)
    assert samples == [30.0, 20.0, None]       # 最早那次采样时该价位不存在
    assert med == 25.0


def test_sample_queue_returns_none_when_level_never_existed():
    snaps = [_snap(0.0, 10.0), _snap(1.0, 10.0)]
    samples, med = fc.sample_queue(snaps, 1, "buy", 99.9)
    assert samples == [None, None] and med is None


# ---------------------------------------------------------------- 成交判定

def test_through_trades_filters_by_price_and_window():
    trades = [(1000, 33.5, 5.0, "S"), (2000, 33.4, 7.0, "S"), (3000, 33.3, 9.0, "S"),
              (4000, 33.6, 4.0, "B")]
    buy = fc.through_trades(trades, 1000, 3000, 33.4, "buy")
    assert [t[1] for t in buy] == [33.4, 33.3]        # 卖单打到我们买价及以下
    sell = fc.through_trades(trades, 1000, 3000, 33.5, "sell")
    assert [t[1] for t in sell] == [33.5]             # 买单打到我们卖价及以上
    assert fc.cum_volume(buy) == pytest.approx(16.0)


def test_fill_outcome_thresholds():
    assert fc.fill_outcome(10.0, 5.0, 0.0) == "none"
    assert fc.fill_outcome(10.0, 5.0, 9.9) == "none"
    assert fc.fill_outcome(10.0, 5.0, 10.0) == "partial"
    assert fc.fill_outcome(10.0, 5.0, 14.9) == "partial"
    assert fc.fill_outcome(10.0, 5.0, 15.0) == "full"
    assert fc.fill_outcome(10.0, 5.0, 40.0) == "full"


# ---------------------------------------------------------------- 模型口径

def test_model_p_fill_matches_repository_model():
    got = fc.model_p_fill(queue_ahead=30.0, lam=2.0, dt=60.0, our_size=3.0, avg_size=25.0)
    want = mm.fill_probability(30.0, 2.0, 60.0, 3.0, 25.0)
    assert got == pytest.approx(want)


def test_model_p_fill_is_monotone():
    base = fc.model_p_fill(3000.0, 0.5, 60.0, 10.0, 25.0)
    assert 0.0 < base < 1.0
    assert fc.model_p_fill(3000.0, 1.0, 60.0, 10.0, 25.0) > base   # λ↑ → p↑
    assert fc.model_p_fill(3000.0, 0.5, 120.0, 10.0, 25.0) > base  # dt↑ → p↑
    assert fc.model_p_fill(6000.0, 0.5, 60.0, 10.0, 25.0) < base   # 队列↑ → p↓
    assert fc.model_p_fill(3000.0, 0.5, 60.0, 100.0, 25.0) < base  # 我们更大 → p↓


def test_compound_p_fill_is_deterministic_and_monotone():
    sizes = [5.0] * 20
    a = fc.compound_p_fill(need=50.0, lam=2.0, dt=60.0, sizes=sizes, draws=500, seed=7)
    b = fc.compound_p_fill(need=50.0, lam=2.0, dt=60.0, sizes=sizes, draws=500, seed=7)
    assert a == b
    big = fc.compound_p_fill(need=5000.0, lam=2.0, dt=60.0, sizes=sizes, draws=500, seed=7)
    assert big < a
    assert 0.0 <= a <= 1.0


def test_queue_bucket_edges():
    assert fc.queue_bucket(0) == "0"
    assert fc.queue_bucket(3) == "<=5"
    assert fc.queue_bucket(20) == "6-20"
    assert fc.queue_bucket(50) == "21-50"
    assert fc.queue_bucket(200) == "51-200"
    assert fc.queue_bucket(1000) == ">200"


# ---------------------------------------------------------------- 端到端

def _write_fixture(root: pathlib.Path, minutes: int = 20) -> None:
    """构造一段采集数据：HL 买一档队列 10 股，每秒一笔 5 股的卖单穿过 → 必然成交。"""
    base = 1_780_000_000.0
    for venue, mid in (("HYPERLIQUID", 33.4), ("LIGHTER", 33.42)):
        d = root / venue
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "20260101.jsonl", "w", encoding="utf-8") as fh:
            for i in range(minutes * 60):
                t = base + i
                bid, ask = mid - 0.01, mid + 0.01
                trades = []
                if venue == "HYPERLIQUID" and i % 2 == 0:
                    trades.append([int(t * 1000), bid, 5.0, "S"])
                fh.write(json.dumps({
                    "venue": venue, "symbol": "SOXL" if venue == "LIGHTER" else "xyz:SOXL",
                    "t_epoch": t, "ts": "2026-01-01T00:00:00Z",
                    "bids": [[bid, 10.0, 2], [bid - 0.01, 30.0, 1]],
                    "asks": [[ask, 20.0, 1], [ask + 0.01, 40.0, 1]],
                    "trades": trades, "trades_gap": False,
                    "mark": mid, "mid": mid, "funding": 0.0, "oi": 1.0, "vlm24h": 1.0}) + "\n")


def test_calibration_script_end_to_end(tmp_path):
    root = tmp_path / "collector"
    _write_fixture(root, minutes=20)
    out = tmp_path / "cal.json"
    rep = tmp_path / "cal.md"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--symbols", "SOXL",
         "--ttls", "30,60", "--notionals", "100", "--probe-interval", "10",
         "--out", str(out), "--report", str(rep)],
        capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.loads(out.read_text(encoding="utf-8"))
    hl = data["calibration"]["HYPERLIQUID"]["buy:100"]["by_ttl"]["60"]
    assert hl["n"] > 0
    # 构造的成交（每秒 5 股、队列 10 股）足以吃满我们的 3 股 → 实测必然成交
    assert hl["p_emp_full"] == pytest.approx(1.0)
    assert hl["p_model_full"] == pytest.approx(1.0, abs=0.05)
    # 数据质量字段必须在
    q = data["data_quality"]["HYPERLIQUID:SOXL"]
    assert q["snapshots"] > 0 and q["trades"] > 0
    assert rep.exists() and "实测 vs 模型" in rep.read_text(encoding="utf-8")
