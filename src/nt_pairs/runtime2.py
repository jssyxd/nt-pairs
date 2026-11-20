"""NautilusTrader 2.0（2.0.0rc9）引擎启动骨架。

2.0 删掉了 1.x 的 ``TradingNode`` / ``TradingNodeConfig``；替代品是
``nautilus_trader.live`` 里的 ``LiveNodeBuilder`` → ``LiveNode``。
本模块把仓库的 ``runtime.build_configs``（两腿 client config）接到 2.0 的启动路径上，
并提供一条**能真正跑起来**的自检链路：

* ``--check``      只装配（不联网、不启动），脱敏打印；
* ``--seconds N``  真正启动节点，跑 N 秒后停掉并打印自检报告；
* ``--live``       使用真实执行端（**必须**同时给出环境变量里的私钥），默认不启用。

安全约束（沿用 CONTRACT §6）：

* 默认 dry-run：执行端一律用 **sandbox**（``add_simulated_exec_client``），
  不可能发出真实订单，也**不读取任何私钥**；
* 私钥只从环境变量读，**不落盘**、不进日志（``_dump`` 会把
  ``private_key / api_key / secret`` 打成 ``***``）；
* ``--live`` 需要 ``HL_PRIVATE_KEY`` 与 ``LIGHTER_PRIVATE_KEY``，缺一即在联网前失败。

实测签名（nautilus_trader 2.0.0rc9，cp314）::

    LiveNodeBuilder.from_config(name, config=None, *, data_factories=None, exec_factories=None)
    LiveNodeBuilder.add_data_client(name, factory, config, routing=None)
    LiveNodeBuilder.add_exec_client(name, factory, config, routing=None)
    LiveNodeBuilder.add_simulated_exec_client(name, factory, config)
    LiveNodeBuilder.build() -> LiveNode
    LiveNode.add_strategy(strategy) / add_actor(actor)
    LiveNode.run() / run_async() / stop() / dispose()
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import is_dataclass
from datetime import timedelta
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
from nautilus_trader.adapters.sandbox import (
    SandboxExecutionClientConfig,
    SandboxExecutionClientFactory,
)
from nautilus_trader.common import DataActor
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.live import LiveNode, LiveNodeBuilder, LiveNodeConfig
from nautilus_trader.model import AccountId, Currency, Money, Venue

from .runtime import build_configs

REDACT = {"private_key", "api_key", "secret", "api_secret", "passphrase", "seed"}

VENUE_HYPERLIQUID = "HYPERLIQUID"
VENUE_LIGHTER = "LIGHTER"
#: sandbox（纸面）执行端的初始资金；dry-run 下不会有真实资金。
SANDBOX_STARTING_BALANCE = 100_000
USD = Currency.from_str("USD")

#: 真实执行端所需的凭据环境变量
REQUIRED_ENV = {
    VENUE_HYPERLIQUID: ("HL_PRIVATE_KEY", "HL_ACCOUNT_ADDRESS"),
    VENUE_LIGHTER: ("LIGHTER_PRIVATE_KEY",),
}


class MissingCredentials(RuntimeError):
    """缺少实盘凭据（在任何网络连接之前抛出）。"""

    def __init__(self, venue: str, missing: list[str]) -> None:
        self.venue = venue
        self.missing = list(missing)
        super().__init__(f"missing credentials for {venue}: {', '.join(self.missing)}")


def _dump(obj: Any) -> Any:
    """脱敏导出配置对象。"""
    if is_dataclass(obj):
        data = {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    elif type(obj).__module__.startswith("nautilus_trader"):
        # pyo3 / Cython 配置类没有 __dict__，只能按属性枚举。
        data = {}
        for name in dir(obj):
            if name.startswith("_"):
                continue
            try:
                value = getattr(obj, name)
            except Exception:  # noqa: BLE001
                continue
            if callable(value):
                continue
            data[name] = value
    elif hasattr(obj, "__dict__"):
        data = dict(obj.__dict__)
    elif hasattr(obj, "to_dict"):
        data = obj.to_dict()
    else:
        return repr(obj)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in REDACT:
            out[key] = "***" if value else None
        else:
            out[key] = value
    return out


def check_credentials(live: bool) -> None:
    """live 模式下的凭据自检（缺则抛 :class:`MissingCredentials`）。"""
    if not live:
        return
    for venue, names in REQUIRED_ENV.items():
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise MissingCredentials(venue, missing)


def build_node(
    *,
    live: bool = False,
    deployment: str = "LIGHTER",
    pair_path: str | None = None,
) -> tuple[LiveNode, dict[str, Any]]:
    """按 2.0 的 ``LiveNodeBuilder`` 装配节点（**不启动**）。

    dry-run（默认）：两个执行端都用 sandbox，不需要任何私钥。
    live：执行端换成 Hyperliquid / Lighter 的真实 factory，凭据只从环境变量读。
    """
    check_credentials(live)

    cfgs = build_configs(pair_path)
    hl_env = HyperliquidEnvironment.MAINNET
    lt_env = LighterEnvironment.MAINNET
    lt_deployment = (LighterDeployment.ROBINHOOD
                     if deployment.upper() == "ROBINHOOD"
                     else LighterDeployment.LIGHTER)

    builder = LiveNodeBuilder.from_config(
        name="nt-pairs",
        config=LiveNodeConfig(environment=None, load_state=False, save_state=False),
    )

    # --- 数据端（公开行情，不需要凭据） -----------------------------------
    builder.add_data_client(
        name=VENUE_HYPERLIQUID,
        factory=HyperliquidDataClientFactory(),
        config=HyperliquidDataClientConfig(environment=hl_env),
    )
    builder.add_data_client(
        name=VENUE_LIGHTER,
        factory=LighterDataClientFactory(),
        config=LighterDataClientConfig(environment=lt_env, deployment=lt_deployment),
    )

    # --- 执行端 -------------------------------------------------------------
    if live:
        builder.add_exec_client(
            name=VENUE_HYPERLIQUID,
            factory=HyperliquidExecutionClientFactory(),
            config=HyperliquidExecutionClientConfig(
                account_address=os.environ.get("HL_ACCOUNT_ADDRESS"),
                private_key=os.environ.get("HL_PRIVATE_KEY"),
                environment=hl_env,
            ),
        )
        builder.add_exec_client(
            name=VENUE_LIGHTER,
            factory=LighterExecutionClientFactory(),
            config=LighterExecutionClientConfig(
                account_id=AccountId(os.environ.get("LIGHTER_ACCOUNT_ID", "LIGHTER-001")),
                account_index=_int_or_none(os.environ.get("LIGHTER_ACCOUNT_INDEX")),
                api_key_index=_int_or_none(os.environ.get("LIGHTER_API_KEY_INDEX")) or 4,
                private_key=os.environ.get("LIGHTER_PRIVATE_KEY"),
                environment=lt_env,
                deployment=lt_deployment,
            ),
        )
    else:
        for name in (VENUE_HYPERLIQUID, VENUE_LIGHTER):
            builder.add_simulated_exec_client(
                name=name,
                factory=SandboxExecutionClientFactory(),
                config=SandboxExecutionClientConfig(
                    venue=Venue(name),
                    starting_balances=[Money(SANDBOX_STARTING_BALANCE, USD)],
                    base_currency=USD,
                    # rc9 要求显式给 fee_model（零费率也要显式写）。
                    fee_model=MakerTakerFeeModel(
                        maker_rate=Decimal("0"), taker_rate=Decimal("0")),
                ),
            )

    node = builder.build()
    summary = {
        "live": live,
        "pair": cfgs.get("pair"),
        "leader": cfgs.get("leader"),
        "follower": cfgs.get("follower"),
        "data_clients": {
            VENUE_HYPERLIQUID: _dump(cfgs["hyperliquid"]["data"]),
            VENUE_LIGHTER: _dump(cfgs["lighter"]["data"]),
        },
        "exec_clients": {
            VENUE_HYPERLIQUID: ("sandbox" if not live else _dump(cfgs["hyperliquid"]["exec"])),
            VENUE_LIGHTER: ("sandbox" if not live else _dump(cfgs["lighter"]["exec"])),
        },
        "node_type": type(node).__name__,
        "node_trader_id": str(node.trader_id),
    }
    return node, summary


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _StopActor(DataActor):
    """在节点的**事件循环内**到点停机。

    为什么不能用线程或外部 asyncio task：
      * ``PyLiveNode`` 是 pyo3 的 ``unsendable`` 类型 —— 丢到另一个线程会直接 panic
        （``PyLiveNode is unsendable, but sent to another thread``）；
      * ``LiveNode.run_async()`` 走的是它自己的事件循环，外部挂的 asyncio task 不会被调度。
    所以停表必须挂在节点自己的 clock 上。
    """

    # rc9 的 ``DataActor.__new__()`` 只接受 0~1 个位置参数，所以不能把 node/seconds
    # 放进构造函数；由调用方在实例化后赋值。
    _node: LiveNode
    _seconds: float
    _handle: Any = None

    def on_start(self) -> None:
        # handle 必须在**回调之外**先拿到（rc9 的报错原文：
        # "use the cache, portfolio, and handle captured beforehand"）。
        # rc9 的 `LiveNode.handle` 是**方法**（不是属性），必须调用它拿到
        # `LiveNodeHandle`；直接取 `node.handle` 拿到的是 bound method，没有 `.stop()`。
        self._handle = self._node.handle()
        self.clock.set_timer(
            name="stop-node",
            interval=timedelta(seconds=self._seconds),
            callback=self._stop,
        )

    def _stop(self, event: Any = None) -> None:
        # 定时器回调会带一个事件参数，签名必须能吃下它。
        # 必须用 **handle** 停机：在组件回调里直接调 ``node.stop()`` 会报
        # "LiveNode is busy servicing another call; do not re-enter the node from a
        # component callback, use the cache, portfolio, and handle captured beforehand"。
        self._handle.stop()


def run_round(node: LiveNode, seconds: float) -> dict[str, Any]:
    """启动节点 → 跑 ``seconds`` 秒 → 停掉（同步阻塞版）。"""
    started = time.time()
    actor = _StopActor()
    actor._node = node
    actor._seconds = seconds
    node.add_actor(actor)
    node.run()
    return {
        "started": True,
        "seconds": seconds,
        "elapsed_s": round(time.time() - started, 2),
        "is_running": node.is_running,
        "trader_id": str(node.trader_id),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只装配并脱敏打印（不启动）")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="启动后跑多少秒再停（>0 才会真正启动节点）")
    parser.add_argument("--live", action="store_true",
                        help="使用真实执行端（需要环境变量里的私钥；默认关闭）")
    parser.add_argument("--deployment", default="LIGHTER", choices=["LIGHTER", "ROBINHOOD"])
    parser.add_argument("--pair", default=None, help="pair 配置 JSON（可选）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        node, summary = build_node(live=args.live, deployment=args.deployment, pair_path=args.pair)
    except MissingCredentials as exc:
        print(json.dumps({"error": str(exc), "venue": exc.venue, "missing": exc.missing},
                         ensure_ascii=False, indent=2))
        return 2

    report: dict[str, Any] = {"assembly": summary}
    try:
        if args.seconds > 0:
            report["run"] = run_round(node, args.seconds)
        else:
            report["run"] = {"started": False,
                             "hint": "加 --seconds N 可真正启动节点跑一轮"}
    except KeyboardInterrupt:
        report["run"] = {"started": True, "interrupted": True}
    finally:
        try:
            node.dispose()
        except Exception:  # noqa: BLE001
            pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
