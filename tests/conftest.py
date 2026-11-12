"""pytest 全局钩子：让「依赖 nautilus_trader 的测试模块」在没装 nautilus 的环境里自动跳过。

背景（仓库约定，CONTRACT §0）：
- 纯逻辑内核（``leg_policy`` / ``execution`` / ``ledger`` / ``risk`` / ``pair_spec`` / ``sessions``）
  **不依赖** ``nautilus_trader``，因此 CI 的快速单测 job 只装 pytest + pyyaml，保持 PR 反馈快；
- 只有装配层 / 策略层（``nt_pairs.live_node`` / ``live_runner`` / ``strategy`` / ``runtime``）
  需要 ``nautilus_trader>=2.0.0rc6``（含 Hyperliquid / Lighter 适配器）。

没有 nautilus（或版本太老、没有对应适配器）时，若直接收集这些模块会在 import 阶段抛
``ModuleNotFoundError``，**整个 suite 的收集被中断**（连纯逻辑测试也一起红）。
这里在**收集阶段**用静态 import 分析找出这些模块并跳过，并在终端摘要里列出被跳过的模块，
保证行为可见。

实现要点：只做静态分析 + ``importlib.util.find_spec``，**不真正 import** 目标模块
（避免在收集阶段执行重模块的副作用）。
"""

from __future__ import annotations

import ast
import importlib.util
import os
from typing import Any, List, Set, Tuple

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")

_skipped_modules: List[str] = []


def _module_file(dotted: str) -> str | None:
    """`nt_pairs.live_node` -> `<repo>/src/nt_pairs/live_node.py`。"""
    if dotted != "nt_pairs" and not dotted.startswith("nt_pairs."):
        return None
    rel = dotted.split(".")[1:]
    pkg_dir = os.path.join(SRC_DIR, "nt_pairs")
    if not rel:
        return os.path.join(pkg_dir, "__init__.py")
    path = os.path.join(pkg_dir, *rel) + ".py"
    return path if os.path.isfile(path) else None


def _imports_of(path: str) -> Set[str]:
    """模块/文件里 import 的顶层模块名（只保留绝对 import）。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return set()
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import，忽略
                continue
            if node.module:
                names.add(node.module)
    return names


def _spec_missing(dotted: str) -> bool:
    """模块不可导入（不存在，或父包缺失）。"""
    try:
        return importlib.util.find_spec(dotted) is None
    except (ImportError, AttributeError, ValueError):
        return True


def _nautilus_deps(dotted: str, seen: Set[str]) -> Set[str]:
    """返回 `dotted` 依赖的 nautilus_trader 子模块（跟着 nt_packages 内部依赖递归一层）。"""
    if dotted in seen:
        return set()
    seen.add(dotted)
    path = _module_file(dotted)
    if path is None:
        return set()
    deps: Set[str] = set()
    for name in _imports_of(path):
        if name.split(".")[0] == "nautilus_trader":
            deps.add(name)
        elif name.split(".")[0] == "nt_pairs":
            deps |= _nautilus_deps(name, seen)
    return deps


def _missing_nautilus_for(test_file: str) -> Tuple[str, str]:
    """返回 (缺失的 nautilus 模块, 依赖它的仓内模块)；没有缺失则返回 ("", "")。"""
    for name in sorted(_imports_of(test_file)):
        head = name.split(".")[0]
        if head == "nautilus_trader":
            if _spec_missing(name):
                return name, ""
            continue
        if head != "nt_pairs":
            continue
        for dep in sorted(_nautilus_deps(name, set())):
            if _spec_missing(dep):
                return dep, name
    return "", ""


def pytest_ignore_collect(collection_path: Any, config: Any) -> Any:
    """缺少 nautilus 时跳过依赖 nautilus 的测试模块（而不是收集阶段报错中断整个 suite）。"""
    path = str(collection_path)
    if not path.endswith(".py") or not os.path.basename(path).startswith("test_"):
        return None
    dep, owner = _missing_nautilus_for(path)
    if dep:
        _skipped_modules.append(
            f"{os.path.relpath(path, str(config.rootpath))}"
            f"（缺 {dep}{'，来自 ' + owner if owner else ''}）")
        return True
    return None


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if not _skipped_modules:
        return
    terminalreporter.write_sep("-", "依赖 nautilus 的测试模块已跳过")
    for name in sorted(set(_skipped_modules)):
        terminalreporter.write_line(name)
