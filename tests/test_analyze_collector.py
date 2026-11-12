"""采集分析器的行为测试（用构造的 JSONL 夹具，不依赖网络）。"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_collector.py"


def _write_fixture(tmpdir: pathlib.Path) -> None:
    """构造一段 300 分钟的快照：基差先冲高再回落，中途出现一次穿价成交。"""
    base = 1_780_000_000.0   # 2026 年内的真实 epoch 秒（会话标定需要）
    for venue, mid0 in (("HYPERLIQUID", 60.0), ("LIGHTER", 60.9)):
        d = tmpdir / "collector" / venue
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "20260101.jsonl", "w", encoding="utf-8") as fh:
            for i in range(300):
                t = base + i * 60
                # 基差：100..119 分钟出现 +300 bps 的尖峰（滚动中位数跟不上 → z 很大），
                # 之后线性回落；110 分钟在 follower 侧穿价 → 成交
                if 100 <= i <= 119:
                    bps = 300.0
                elif i > 119:
                    bps = max(0.0, 300.0 - (i - 119) * 20.0)
                else:
                    bps = 0.0
                mid_hl = 60.0
                mid_lt = mid_hl * (1 + bps / 1e4)
                mid = mid_hl if venue == "HYPERLIQUID" else mid_lt
                bid, ask = mid * 0.9998, mid * 1.0002
                # 第 130 分钟在 **follower（Lighter）** 侧制造一次“买单打穿卖一”的穿价
                # （fade 在 basis>0 时是「卖贵的一侧」，maker 单挂在 Lighter 的卖一）
                if venue == "LIGHTER" and i == 110:
                    bid = ask + 0.001
                fh.write(json.dumps({
                    "venue": venue, "symbol": "SOXL", "t_epoch": t, "ts": "2026-01-01T00:00:00Z",
                    "bids": [[bid, 50.0, 1], [bid * 0.999, 80.0, 1]],
                    "asks": [[ask, 60.0, 1], [ask * 1.001, 90.0, 1]],
                    "mark": mid, "mid": mid, "funding": 0.0, "oi": 1000.0, "vlm24h": 1e6}) + "\n")


def test_analyzer_detects_fill_and_markout(tmp_path):
    _write_fixture(tmp_path)
    out = tmp_path / "cal.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path / "collector"),
         "--qty", "100", "--window", "60", "--max-hold", "60", "--capture-bps", "17", "--fee-bps", "7",
         "--out", str(tmp_path / "fill_calibration.json")],
        capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.load(open(tmp_path / "fill_calibration.json", encoding="utf-8"))
    assert data, "应至少产出一个标的的标定结果"
    rec = data[0]
    assert rec["symbol"] == "SOXL"
    assert rec["minutes"] >= 200
    # 至少一次成交（构造了穿价），且 markout 字段存在
    assert rec["fills"] >= 1
    assert rec["markout_5_mean"] is not None
    assert rec["p_fill_per_min"] >= 0.0
    # 净期望 = 捕获 − 固定费 + markout
    assert rec["net_bps_per_fill"] == pytest.approx(17 - 7 + rec["markout_5_mean"], abs=0.01)