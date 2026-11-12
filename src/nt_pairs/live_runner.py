"""实盘节点入口。

用法::

    python -m nt_pairs.live_runner --config configs/pair.json [--dry-run]

行为约定：
- ``--dry-run``：**不读取任何私钥**，执行端用沙盒（``add_simulated_exec_client``），
  策略自身的 ``dry_run`` 也会置位，因此不可能发出真实订单；
- 实盘（非 dry-run）：从环境变量读凭据，缺什么就打印结构化 JSON 错误并以非 0 退出，
  在任何网络连接之前失败；
- 所有错误都以 JSON（``error`` / ``hint`` 字段）打到 stderr，便于 VPS 上被监控采集。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from nautilus_trader.live import LiveNode

from .live_node import (
    MissingCredentials,
    NodeBundle,
    build_node,
    load_live_plan,
    scan_secret_fields,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_BUILD = 3
EXIT_RUNTIME = 4


def _fail(payload: dict[str, Any], code: int) -> int:
    """打印结构化错误并返回退出码。"""
    print(json.dumps(payload, indent=2, default=str), file=sys.stderr)
    return code


def _summary(bundle: NodeBundle) -> dict[str, Any]:
    """启动摘要（不含任何凭据）。"""
    plan = bundle.plan
    return {
        "pair": plan.name,
        "dry_run": plan.dry_run,
        "leader": {
            "instrument_id": plan.leader.instrument_id,
            "venue": plan.leader.venue,
            "direction": plan.leader.direction,
            "max_qty": str(plan.leader.max_qty),
        },
        "follower": {
            "instrument_id": plan.follower.instrument_id,
            "venue": plan.follower.venue,
            "direction": plan.follower.direction,
            "max_qty": str(plan.follower.max_qty),
        },
        "subscriptions": [
            {
                "instrument_id": sub.instrument_id,
                "book_type": sub.book_type,
                "role": sub.role,
                "venue": sub.venue,
            }
            for sub in plan.subscriptions
        ],
        "clients": [
            {
                "name": spec.name,
                "kind": spec.kind,
                "venue": spec.venue,
                "config": repr(spec.config),
            }
            for spec in bundle.client_specs
        ],
    }


def _run(node: LiveNode, max_secs: float) -> int:
    """跑节点；``max_secs > 0`` 时到点优雅停机。

    2.0 的 ``LiveNode.run()`` 是阻塞式同步入口，``handle().stop()`` 触发优雅关闭
    （实测关闭流程约 10s：residual events 10s + client disconnects）。
    """
    import threading
    import time

    handle = node.handle()
    timer: Optional[threading.Timer] = None
    if max_secs > 0:
        timer = threading.Timer(max_secs, handle.stop)
        timer.daemon = True
        timer.start()
    try:
        node.run()
    except Exception as exc:  # noqa: BLE001 - 顶层错误统一结构化
        return _fail(
            {
                "error": "node_run_failed",
                "exception": type(exc).__name__,
                "detail": str(exc),
                "hint": "check network reachability of the venue endpoints and the pair config",
            },
            EXIT_RUNTIME,
        )
    finally:
        if timer is not None:
            timer.cancel()
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 主函数。"""
    parser = argparse.ArgumentParser(prog="nt_pairs.live_runner")
    parser.add_argument("--config", required=True, help="pair 配置 JSON 路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读行情 + 沙盒执行端；不读私钥、不发真实订单",
    )
    parser.add_argument(
        "--environment",
        default="mainnet",
        choices=("mainnet", "testnet"),
        help="适配器环境（默认 mainnet）",
    )
    parser.add_argument(
        "--max-secs",
        type=float,
        default=60.0,
        help="运行时长上限（秒）；0 表示一直跑到 Ctrl-C。默认 60",
    )
    args = parser.parse_args(argv)

    # -- 配置 ---------------------------------------------------------------
    try:
        plan = load_live_plan(args.config)
    except FileNotFoundError:
        return _fail(
            {
                "error": "config_not_found",
                "path": args.config,
                "hint": "pass an existing pair config, e.g. configs/openai_entropy_lighter.json",
            },
            EXIT_CONFIG,
        )
    except ValueError as exc:
        return _fail(
            {"error": "invalid_config", "path": args.config, "detail": str(exc)},
            EXIT_CONFIG,
        )

    dry_run = bool(args.dry_run) or plan.dry_run

    # -- 装配 ---------------------------------------------------------------
    try:
        bundle = build_node(
            plan,
            dry_run=dry_run,
            environment=args.environment,
        )
    except MissingCredentials as exc:
        return _fail(exc.as_dict(), EXIT_BUILD)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            {
                "error": "node_build_failed",
                "exception": type(exc).__name__,
                "detail": str(exc),
            },
            EXIT_BUILD,
        )

    secrets = scan_secret_fields(bundle)
    if dry_run and secrets["populated"]:
        return _fail(
            {
                "error": "secret_leak_in_dry_run",
                "populated": secrets["populated"],
                "hint": "dry-run must not carry any credential",
            },
            EXIT_BUILD,
        )

    print(json.dumps(_summary(bundle), indent=2, default=str))
    print(
        json.dumps(
            {
                "event": "node_starting",
                "dry_run": dry_run,
                "instruments": list(plan.instrument_ids),
                "credentials_loaded": bool(secrets["populated"]),
            }
        ),
        flush=True,
    )

    return _run(bundle.node, args.max_secs)


if __name__ == "__main__":
    raise SystemExit(main())
