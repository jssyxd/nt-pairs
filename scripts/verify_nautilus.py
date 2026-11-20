#!/usr/bin/env python
"""验证本机 NautilusTrader 安装（**不要**只看 ``import nautilus_trader``）。

为什么不能只 import：未安装时 Python 可能解析到一个**空命名空间包**
（``nautilus_trader.__file__ is None``），import 会「假成功」。
所以这里用：
  * ``importlib.metadata.version("nautilus_trader")`` —— 判断装没装 + 版本
  * ``pkgutil.iter_modules(nautilus_trader.adapters.__path__)`` —— 列出**实际可用**的适配器

退出码：0 = 全部齐备；非 0 = 有缺失项（缺失项逐条打印）。
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import pkgutil
import sys
from typing import Any

#: 本仓库需要的适配器模块 → 说明
REQUIRED_ADAPTERS = {
    "hyperliquid": "Hyperliquid 数据/执行（leader 腿）",
    "lighter": "Lighter 数据/执行（follower 腿）",
    "okx": "OKX 数据（第三场所，矩阵用）",
    "binance": "Binance 数据（矩阵用）",
    "tardis": "Tardis 数据（历史回放）",
}

#: 2.0 把「Lighter on Robinhood」做成了同一个适配器的 deployment 枚举，
#: 没有单独的 ``lighter_robinhood`` 模块。
REQUIRED_DEPLOYMENTS = {
    ("lighter", "LighterDeployment", "ROBINHOOD"): "Lighter on Robinhood（ROBINHOOD deployment）",
}

EXPECTED_VERSION = "2.0.0rc9"


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--require-version", default=EXPECTED_VERSION,
                        help="期望的 nautilus_trader 版本（默认 2.0.0rc9）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    problems: list[str] = []
    report: dict[str, Any] = {"problems": problems}

    # --- 1. 发行版 / 依赖版本 ---------------------------------------------
    versions = {
        "nautilus_trader": _version("nautilus_trader"),
        "pandas": _version("pandas"),
        "numpy": _version("numpy"),
        "plotly": _version("plotly"),
        "kaleido": _version("kaleido"),
    }
    report["versions"] = versions

    nt_version = versions["nautilus_trader"]
    if nt_version is None:
        problems.append("nautilus_trader 未安装（importlib.metadata 查不到发行版）")
    elif args.require_version and nt_version != args.require_version:
        problems.append(f"nautilus_trader 版本是 {nt_version}，期望 {args.require_version}")

    # --- 2. 适配器（实际可用） --------------------------------------------
    adapters: list[str] = []
    adapters_importable: dict[str, bool] = {}
    if nt_version is not None:
        try:
            adapters_pkg = importlib.import_module("nautilus_trader.adapters")
        except ImportError as exc:  # pragma: no cover
            adapters_pkg = None
            problems.append(f"nautilus_trader.adapters 不可导入: {exc}")
        if adapters_pkg is not None and getattr(adapters_pkg, "__path__", None):
            for module in pkgutil.iter_modules(adapters_pkg.__path__):
                adapters.append(module.name)
        for name in REQUIRED_ADAPTERS:
            if name not in adapters:
                problems.append(f"缺少适配器模块: {name}（{REQUIRED_ADAPTERS[name]}）")
            else:
                try:
                    importlib.import_module(f"nautilus_trader.adapters.{name}")
                    adapters_importable[name] = True
                except Exception as exc:  # noqa: BLE001
                    adapters_importable[name] = False
                    problems.append(f"适配器 {name} 存在但导入失败: {exc}")
    report["adapters"] = sorted(adapters)
    report["required_adapters"] = {
        name: adapters_importable.get(name, False) for name in REQUIRED_ADAPTERS
    }

    # --- 3. deployment 枚举（Lighter on Robinhood 等） ----------------------
    deployments: dict[str, list[str]] = {}
    for module_name, enum_name, member in REQUIRED_DEPLOYMENTS:
        try:
            mod = importlib.import_module(f"nautilus_trader.adapters.{module_name}")
            enum = getattr(mod, enum_name)
            names = [n for n in dir(enum) if n.isupper()]
            deployments[f"{module_name}.{enum_name}"] = names
            if member not in names:
                problems.append(
                    f"{module_name}.{enum_name} 缺少 {member}（{REQUIRED_DEPLOYMENTS[(module_name, enum_name, member)]}）")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{module_name}.{enum_name} 不可用: {exc}")
    report["deployments"] = deployments

    # --- 4. visualization extra --------------------------------------------
    viz: dict[str, Any] = {}
    try:
        import plotly  # noqa: F401
        viz["plotly"] = True
    except ImportError as exc:
        viz["plotly"] = False
        problems.append(f"plotly 不可导入（缺 [visualization] extra）: {exc}")
    try:
        from nautilus_trader.analysis import create_tearsheet, create_tearsheet_from_stats
        viz["create_tearsheet"] = True
        viz["create_tearsheet_from_stats"] = True
    except Exception as exc:  # noqa: BLE001
        viz["create_tearsheet"] = False
        problems.append(f"nautilus_trader.analysis 不可导入: {exc}")
    try:
        import kaleido  # noqa: F401
        viz["kaleido"] = True
    except ImportError as exc:
        viz["kaleido"] = False
        problems.append(f"kaleido 不可导入（静态图导出不可用）: {exc}")
    report["visualization"] = viz

    # --- 5. 2.0 运行时 API 关键类 -----------------------------------------
    api: dict[str, bool] = {}
    for label, module, symbol in (
        ("LiveNodeBuilder", "nautilus_trader.live", "LiveNodeBuilder"),
        ("LiveNode", "nautilus_trader.live", "LiveNode"),
        ("BacktestEngine", "nautilus_trader.backtest", "BacktestEngine"),
        ("Strategy", "nautilus_trader.trading", "Strategy"),
        ("TradingNode(1.x, 应为空)", "nautilus_trader.live", "TradingNode"),
    ):
        try:
            mod = importlib.import_module(module)
            api[label] = hasattr(mod, symbol)
        except Exception:  # noqa: BLE001
            api[label] = False
    if not api.get("LiveNodeBuilder"):
        problems.append("nautilus_trader.live.LiveNodeBuilder 不存在（2.0 启动路径不可用）")
    if api.get("TradingNode(1.x, 应为空)"):
        problems.append("nautilus_trader.live.TradingNode 存在 —— 这不是预期的 2.0 布局")
    report["api"] = api

    # --- 输出 ---------------------------------------------------------------
    if args.json:
        import json
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("=" * 68)
        print("NautilusTrader 安装自检")
        print("=" * 68)
        print("版本:")
        for name, value in versions.items():
            print(f"  {name:<18} {value or 'MISSING'}")
        print(f"\n可用适配器 ({len(adapters)}): {', '.join(sorted(adapters))}")
        print("\n本项目需要的适配器:")
        for name, desc in REQUIRED_ADAPTERS.items():
            state = {True: "OK", False: "MISSING", None: "?"}[adapters_importable.get(name)]
            print(f"  {name:<16} {state:<8} {desc}")
        print("\ndeployment 枚举:")
        for key, names in deployments.items():
            print(f"  {key}: {', '.join(names)}")
        print("\nvisualization:")
        for key, value in viz.items():
            print(f"  {key:<28} {'OK' if value else 'MISSING'}")
        print("\n2.0 运行时 API:")
        for key, value in api.items():
            print(f"  {key:<28} {'OK' if value else 'MISSING'}")
        print("=" * 68)
        if problems:
            print(f"FAIL — {len(problems)} 个问题:")
            for item in problems:
                print(f"  - {item}")
            return 1
        print("PASS — 安装、适配器与 visualization extra 全部可用")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
