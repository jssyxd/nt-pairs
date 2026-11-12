"""运行时装配：把 env 变量变成两个适配器的 client config。

NautilusTrader 2.0 移除了 1.x 的 `TradingNode` / `TradingNodeConfig`；本模块只做**配置装配**
（可用 `--check` 脱敏打印），节点/引擎的启动方式属于 2.0 的新 runtime API，尚未在本仓库接线
（见 STATUS.md 的待办）。

用法：
    python -m nt_pairs.runtime --check
    python -m nt_pairs.runtime --check --pair configs/openai_entropy_lighter.json
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidEnvironment,
    HyperliquidExecutionClientConfig,
)
from nautilus_trader.model import AccountId

from nautilus_trader.adapters.lighter import (
    LighterDataClientConfig,
    LighterDeployment,
    LighterEnvironment,
    LighterExecutionClientConfig,
)

REDACT = {"private_key", "api_key", "secret"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None and name in REDACT:
        return "***"
    return value


def build_configs(pair_path: str | None = None) -> dict[str, Any]:
    """只读取环境变量，不发起网络请求。"""
    pair: dict[str, Any] = {}
    if pair_path:
        with open(pair_path, encoding="utf-8") as fh:
            pair = json.load(fh)

    hl_key = os.environ.get("HL_PRIVATE_KEY")
    hl_account = os.environ.get("HL_ACCOUNT_ADDRESS")
    lt_account = os.environ.get("LIGHTER_ACCOUNT_INDEX")
    lt_key = os.environ.get("LIGHTER_PRIVATE_KEY")
    lt_api_index = int(os.environ.get("LIGHTER_API_KEY_INDEX", "4"))

    cfg: dict[str, Any] = {
        "pair": pair.get("name", "unknown"),
        "leader": pair.get("leader", {}).get("instrument_id"),
        "follower": pair.get("follower", {}).get("instrument_id"),
        "hyperliquid": {
            "data": HyperliquidDataClientConfig(environment=HyperliquidEnvironment.MAINNET),
            "exec": HyperliquidExecutionClientConfig(
                account_address=hl_account,
                private_key=hl_key,
                environment=HyperliquidEnvironment.MAINNET,
            ),
            "has_key": bool(hl_key),
        },
        "lighter": {
            "data": LighterDataClientConfig(
                environment=LighterEnvironment.MAINNET,
                account_index=int(lt_account) if lt_account else None,
                api_key_index=lt_api_index,
                private_key=lt_key,
            ),
            "exec": LighterExecutionClientConfig(
                account_id=AccountId(os.environ.get("LIGHTER_ACCOUNT_ID", "LIGHTER-001")),
                account_index=int(lt_account) if lt_account else None,
                api_key_index=lt_api_index,
                private_key=lt_key,
                environment=LighterEnvironment.MAINNET,
                deployment=LighterDeployment.LIGHTER,
            ),
            "has_key": bool(lt_key),
        },
    }
    return cfg


def _dump(obj: Any) -> Any:
    if is_dataclass(obj):
        data = asdict(obj)
    elif hasattr(obj, "__dict__"):
        data = dict(obj.__dict__)
    elif hasattr(obj, "to_dict"):
        data = obj.to_dict()
    else:
        return repr(obj)
    return {k: ("***" if k in REDACT else v) for k, v in data.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只打印脱敏配置，不连网")
    parser.add_argument("--pair", default=None)
    args = parser.parse_args()

    cfg = build_configs(args.pair)
    out = {k: v for k, v in cfg.items() if k not in ("hyperliquid", "lighter")}
    for venue in ("hyperliquid", "lighter"):
        out[venue] = {
            "data": _dump(cfg[venue]["data"]),
            "exec": _dump(cfg[venue]["exec"]),
            "has_key": cfg[venue]["has_key"],
        }
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())