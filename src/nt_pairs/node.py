"""NautilusTrader 2.0 节点启动器（LiveNodeBuilder）。

背景（见 STATUS.md 的缺口「运行时节点启动 ❌ 未接线」）：
Nautilus 2.0 删除了 1.x 的 ``TradingNode`` / ``TradingNodeConfig``，改由
``nautilus_trader.live.LiveNodeBuilder`` 装配。本模块把 ``runtime.build_configs``
产出的两套适配器配置接到 builder 上，并把 ``PairHedgeStrategy`` 挂进节点。

默认 **dry-run**：``--live`` 不给时策略的 ``dry_run=True``，只写日志不下单。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from typing import Any

from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidEnvironment,
    HyperliquidExecutionClientConfig,
    HyperliquidExecutionClientFactory,
)
from nautilus_trader.adapters.lighter import (
    LighterDataClientConfig,
    LighterDataClientFactory,
    LighterDeployment,
    LighterEnvironment,
    LighterExecutionClientConfig,
    LighterExecutionClientFactory,
)
from nautilus_trader.live import LiveNodeBuilder
from nautilus_trader.model import AccountId

from .pair_spec import load_pair
from .strategy import PairHedgeConfig, PairHedgeStrategy


def build_node(pair_path: str, *, dry_run: bool = True, state_dir: str = "state",
               env: dict[str, str] | None = None, b_star_bps: str = "21.0",
               follower_price: float | None = None) -> Any:
    """装配 LiveNode（未 run）。

    ``follower_price`` 用于把美元名义上限换算成张数；不给则退化为最小下单量。
    """
    env = env if env is not None else os.environ
    pair = load_pair(pair_path)

    hl_key = env.get("HL_PRIVATE_KEY")
    hl_account = env.get("HL_ACCOUNT_ADDRESS")
    lt_key = env.get("LIGHTER_PRIVATE_KEY")
    lt_account_index = env.get("LIGHTER_ACCOUNT_INDEX")
    lt_api_index = int(env.get("LIGHTER_API_KEY_INDEX", "4"))
    lt_account_id = env.get("LIGHTER_ACCOUNT_ID", "LIGHTER-001")

    builder = LiveNodeBuilder.from_config(name="nt-pairs")

    builder.add_data_client(
        name="HYPERLIQUID",
        factory=HyperliquidDataClientFactory(),
        config=HyperliquidDataClientConfig(environment=HyperliquidEnvironment.MAINNET),
    )
    builder.add_data_client(
        name="LIGHTER",
        factory=LighterDataClientFactory(),
        config=LighterDataClientConfig(
            environment=LighterEnvironment.MAINNET,
            deployment=LighterDeployment.LIGHTER,
            account_index=int(lt_account_index) if lt_account_index else None,
            api_key_index=lt_api_index,
            private_key=lt_key,
        ),
    )

    if not dry_run:
        if not hl_key or not hl_account:
            raise SystemExit("live 模式需要 HL_PRIVATE_KEY 与 HL_ACCOUNT_ADDRESS")
        if not lt_key:
            raise SystemExit("live 模式需要 LIGHTER_PRIVATE_KEY")
        builder.add_exec_client(
            name="HYPERLIQUID",
            factory=HyperliquidExecutionClientFactory(),
            config=HyperliquidExecutionClientConfig(
                account_address=hl_account,
                private_key=hl_key,
                environment=HyperliquidEnvironment.MAINNET,
            ),
        )
        builder.add_exec_client(
            name="LIGHTER",
            factory=LighterExecutionClientFactory(),
            config=LighterExecutionClientConfig(
                account_id=AccountId(lt_account_id),
                account_index=int(lt_account_index) if lt_account_index else None,
                api_key_index=lt_api_index,
                private_key=lt_key,
                environment=LighterEnvironment.MAINNET,
                deployment=LighterDeployment.LIGHTER,
            ),
        )

    # max_leg_qty 是**张数**，而 PairSpec 只给了美元名义上限。
    # 数量必须按盘口价格反推；没有价格时用 min_qty 兜底（保守：只允许最小单）。
    if follower_price and follower_price > 0:
        max_leg_qty = pair.max_leg_usd / Decimal(str(follower_price))
    else:
        max_leg_qty = pair.follower.min_qty

    strategy = PairHedgeStrategy(
        PairHedgeConfig(
            leader_id=pair.leader.instrument_id,
            follower_id=pair.follower.instrument_id,
            ratio=str(pair.ratio),
            b_star_bps=b_star_bps,
            sigma_1m_bps=str(pair.sigma_1m_bps),
            max_leg_qty=str(max_leg_qty),
            dry_run=dry_run,
            state_dir=state_dir,
        ),
    )
    node = builder.build()
    node.add_strategy(strategy)
    return node


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", required=True)
    parser.add_argument("--live", action="store_true", help="真实下单（默认 dry-run）")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--check", action="store_true", help="只装配不启动")
    parser.add_argument("--b-star-bps", default="21.0")
    parser.add_argument("--follower-price", type=float, default=None,
                        help="follower 现价；用于把美元名义上限换算成张数")
    args = parser.parse_args()

    node = build_node(args.pair, dry_run=not args.live, state_dir=args.state_dir,
                      b_star_bps=args.b_star_bps, follower_price=args.follower_price)
    if args.check:
        print(json.dumps({
            "pair": args.pair,
            "dry_run": not args.live,
            "node": type(node).__name__,
            "running": node.is_running,
        }, ensure_ascii=False, indent=2))
        node.dispose()
        return 0
    node.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
