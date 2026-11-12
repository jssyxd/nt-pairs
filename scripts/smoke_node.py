"""节点冒烟：只连数据（不连执行、不用密钥），确认两腿 WS 真的收到盘口增量。

用法：
    python scripts/smoke_node.py --leader xyz:RKLB-USD-PERP.HYPERLIQUID \
                                 --follower RKLB-PERP.LIGHTER --seconds 60
退出码 0 = 至少一条腿收到数据。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from nautilus_trader.adapters.hyperliquid import (  # noqa: E402
    HyperliquidDataClientConfig,
    HyperliquidDataClientFactory,
    HyperliquidEnvironment,
)
from nautilus_trader.adapters.lighter import (  # noqa: E402
    LighterDataClientConfig,
    LighterDataClientFactory,
    LighterDeployment,
    LighterEnvironment,
)
from nautilus_trader.config import StrategyConfig  # noqa: E402
from nautilus_trader.live import LiveNodeBuilder  # noqa: E402
from nautilus_trader.model import BookType, InstrumentId  # noqa: E402
from nautilus_trader.trading import Strategy  # noqa: E402


class BookCounterConfig(StrategyConfig):
    # 必须 keyword-only：Rust 侧的 StrategyConfig 会把位置参数当成 strategy_id
    def __init__(self, *, leader: str, follower: str) -> None:
        super().__init__()
        self.leader = leader
        self.follower = follower


class BookCounter(Strategy):
    """只数盘口增量，不下单。"""

    def __init__(self, config: BookCounterConfig) -> None:
        super().__init__(config)
        self.leader = InstrumentId.from_str(config.leader)
        self.follower = InstrumentId.from_str(config.follower)
        self.counts: dict[str, int] = {config.leader: 0, config.follower: 0}
        self.last_mid: dict[str, float] = {}

    def on_start(self) -> None:
        self.subscribe_book_deltas(self.leader, BookType.L2_MBP)
        self.subscribe_book_deltas(self.follower, BookType.L2_MBP)

    def on_book_deltas(self, deltas: Any) -> None:
        iid = deltas.instrument_id.value
        self.counts[iid] = self.counts.get(iid, 0) + 1
        # 2.0 的 OrderBookDeltas 只有 .deltas（逐档）；单档信息在 delta.order 上
        best_bid = best_ask = None
        for d in deltas.deltas:
            if d.is_delete:
                continue
            order = d.order
            if order is None:
                continue
            px = float(order.price)
            if str(order.side) == "BID":
                if best_bid is None or px > best_bid:
                    best_bid = px
            elif str(order.side) == "ASK":
                if best_ask is None or px < best_ask:
                    best_ask = px
        if best_bid is not None and best_ask is not None:
            self.last_mid[iid] = (best_bid + best_ask) / 2


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader", default="xyz:RKLB-USD-PERP.HYPERLIQUID")
    ap.add_argument("--follower", default="RKLB-PERP.LIGHTER")
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    builder = LiveNodeBuilder.from_config(name="nt-pairs-smoke")
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
        ),
    )
    node = builder.build()

    counter = BookCounter(BookCounterConfig(leader=args.leader, follower=args.follower))
    node.add_strategy(counter)

    print(f"smoke: running {args.seconds}s for {args.leader} / {args.follower}", flush=True)
    # 2.0 的节点必须 run 起来才会启动数据客户端并触发 on_start 订阅。
    # run_async() 会一直等到节点停止，所以放成后台任务，再自己控制时长。
    runner = asyncio.create_task(node.run_async())
    await asyncio.sleep(args.seconds)
    # 2.0: run_async 期间必须用 handle 停止节点
    handle = node.handle()
    handle.stop()
    try:
        await asyncio.wait_for(runner, timeout=20)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        runner.cancel()
    result = {
        "counts": counter.counts,
        "last_mid": counter.last_mid,
        "leader": args.leader,
        "follower": args.follower,
    }
    print("RESULT " + repr(result), flush=True)
    return 0 if any(counter.counts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
