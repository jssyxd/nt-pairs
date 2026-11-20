"""可视化座舱的适配器自测（不需要网络、不需要真实回测数据）。

覆盖：
* ``adapt_repo_json`` —— 仓库 JSON → tearsheet 四件套的映射；
* 降级渲染 —— 数据不足时必须产出 HTML 且标注降级原因；
* ``verify_nautilus.py`` —— 在当前解释器下必须自检通过（缺依赖时退出码非 0）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# make_tearsheet.py 直接 import pandas / nautilus_trader（原生 tearsheet 必需）。
# CI 的快速单测 job 不装这两个包 → 整份跳过，避免**收集阶段**报错中断整个 suite。
pytest.importorskip("nautilus_trader")

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


make_tearsheet = _load("make_tearsheet", ROOT / "scripts" / "make_tearsheet.py")


def _payload() -> dict:
    return {
        "stats_pnls": {"USD": {"PnL (total)": 93.64, "Win Rate": 0.505}},
        "stats_returns": {"Sharpe Ratio (252 days)": 1.5},
        "stats_general": {"Long Ratio": 0.5},
        "returns": [[1_700_000_000_000_000_000, 0.001], [1_700_000_300_000_000_000, -0.0005]],
        "run_info": {"Strategy": "test"},
        "equity_curve": [[1_700_000_000_000_000_000, 100.0],
                         [1_700_000_300_000_000_000, 100.05]],
    }


def test_adapt_repo_json_maps_all_four_pieces():
    adapted = make_tearsheet.adapt_repo_json(_payload())
    assert set(adapted["stats_pnls"]) == {"USD"}
    assert adapted["stats_returns"]["Sharpe Ratio (252 days)"] == 1.5
    assert adapted["stats_general"]["Long Ratio"] == 0.5
    assert len(adapted["returns"]) == 2
    assert adapted["returns"].iloc[0] == 0.001


def test_adapt_repo_json_rejects_missing_keys():
    payload = _payload()
    del payload["stats_returns"]
    try:
        make_tearsheet.adapt_repo_json(payload)
    except KeyError as exc:
        assert "stats_returns" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("缺少 stats_returns 时应当抛 KeyError")


def test_degraded_renderer_writes_html(tmp_path):
    out = tmp_path / "degraded.html"
    path = make_tearsheet.render_degraded(_payload(), str(out), "t", "降级原因：测试")
    assert Path(path).exists()
    html = Path(path).read_text(encoding="utf-8")
    assert "DEGRADED VIEW" in html
    assert "降级原因" in html


def test_verify_nautilus_selfcheck_passes():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_nautilus.py"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(proc.stdout)
    assert proc.returncode == 0, report
    assert report["versions"]["nautilus_trader"].startswith("2.0")
    assert report["required_adapters"]["hyperliquid"] is True
    assert report["required_adapters"]["lighter"] is True
    assert report["visualization"]["create_tearsheet"] is True
