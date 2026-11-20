"""回测 JSON → tearsheet 的**输入契约**自测（静态检查，不需要 nautilus / 网络）。

背景：``scripts/run_pair_backtest.py``（worker 交付）产出的 JSON 必须能被
``scripts/make_tearsheet.py`` 直接消费；字段名不一致时只能**加适配层**，
不能改 tearsheet 的既有输入契约。

这里用 AST 静态比对两个脚本的字段名，避免为了测试而 import nautilus。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _payload_keys(script: str) -> set[str]:
    """取出脚本里 ``payload = {...}`` 字典字面量的字符串键。"""
    tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "payload" not in targets:
                continue
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
    return keys


def _required_stats_keys() -> set[str]:
    """取出 make_tearsheet.py 里 ``REQUIRED_STATS_KEYS`` 的内容。"""
    tree = ast.parse((ROOT / "scripts" / "make_tearsheet.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "REQUIRED_STATS_KEYS" in names and isinstance(node.value, ast.Tuple):
                return {
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    raise AssertionError("make_tearsheet.py 里找不到 REQUIRED_STATS_KEYS")


def test_run_pair_backtest_output_satisfies_tearsheet_contract():
    produced = _payload_keys("run_pair_backtest.py")
    required = _required_stats_keys()
    assert required, "tearsheet 的必需字段表不能为空"
    missing = required - produced
    assert not missing, (
        f"run_pair_backtest.py 的输出缺字段 {sorted(missing)}；"
        "要么改脚本产出，要么加一层适配（不要改 tearsheet 的输入契约）"
    )


def test_run_pair_backtest_emits_returns_series_for_tearsheet():
    produced = _payload_keys("run_pair_backtest.py")
    assert "returns" in produced
    assert "equity_curve" in produced  # 降级渲染也要用
    assert "run_info" in produced
