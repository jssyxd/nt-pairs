"""装配层测试（不联网、不下单）。

覆盖：
- ``--dry-run`` 在**没有任何密钥**的环境变量下也能成功构建节点；
- 装配结果里**没有**任何凭据字段被赋值；
- 两腿 instrument id 都进了订阅列表；
- 实盘路径缺凭据时给出结构化错误（在任何网络连接之前失败）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nt_pairs.live_node import (
    MissingCredentials,
    build_client_specs,
    build_node,
    load_live_plan,
    scan_secret_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PAIR_CONFIG = REPO_ROOT / "configs" / "pair.json"

# 凭据字段名（大小写不敏感扫描）
SECRET_FIELDS = ("private_key", "api_key", "api_secret", "secret", "passphrase")

# 实盘路径读取的环境变量
CRED_ENV_VARS = (
    "HL_PRIVATE_KEY",
    "HL_ACCOUNT_ADDRESS",
    "LIGHTER_PRIVATE_KEY",
    "LIGHTER_ACCOUNT_INDEX",
    "LIGHTER_API_KEY_INDEX",
    "LIGHTER_ACCOUNT_ID",
)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """保证测试环境里没有任何凭据。"""
    for name in CRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def plan():
    return load_live_plan(PAIR_CONFIG)


def _secret_attrs(obj: object) -> dict[str, object]:
    """取出对象上所有凭据类字段的值。"""
    found: dict[str, object] = {}
    for name in dir(obj):
        low = name.lower()
        if any(field in low for field in SECRET_FIELDS):
            try:
                value = getattr(obj, name)
            except Exception:  # noqa: BLE001 - 属性可能抛错，忽略
                continue
            found[name] = value
    return found


def test_dry_run_builds_node_without_credentials(plan) -> None:
    """dry-run 必须能在零凭据下构建出节点。"""
    bundle = build_node(plan, dry_run=True)

    assert bundle.node is not None
    assert bundle.strategy is not None
    assert bundle.heartbeat is not None
    # 执行端必须是沙盒（不可能发真实订单）
    kinds = {spec.kind for spec in bundle.client_specs}
    assert "exec" not in kinds
    assert "sim_exec" in kinds
    assert {spec.kind for spec in bundle.client_specs} == {"data", "sim_exec"}


def test_dry_run_node_has_no_credentials(plan) -> None:
    """装配结果里不能出现任何被赋值的凭据字段。"""
    bundle = build_node(plan, dry_run=True)

    secrets = scan_secret_fields(bundle)
    assert secrets["populated"] == []

    # 逐个配置对象检查：凭据字段要么不存在，要么为空
    for spec in bundle.client_specs:
        for name, value in _secret_attrs(spec.config).items():
            assert value in (None, "", []), f"{spec.name}.{name} 竟然有值: {value!r}"

    # repr 兜底：节点与配置对象的字符串表示里不得出现凭据字段名
    blob = (repr(bundle.node) + "".join(repr(spec.config) for spec in bundle.client_specs)).lower()
    for field in SECRET_FIELDS:
        assert field not in blob, f"repr 里出现了凭据字段名 {field}"


def test_both_legs_are_subscribed(plan) -> None:
    """两腿 instrument id 都必须进订阅列表。"""
    assert plan.leader.instrument_id in plan.instrument_ids
    assert plan.follower.instrument_id in plan.instrument_ids
    assert len(plan.subscriptions) == 2
    for sub in plan.subscriptions:
        assert sub.book_type == "L2_MBP"
    assert set(plan.instrument_ids) == {
        plan.leader.instrument_id,
        plan.follower.instrument_id,
    }
    assert {s.instrument_id for s in plan.subscriptions} == set(plan.instrument_ids)


def test_strategy_registers_both_legs(plan) -> None:
    """策略实例必须持有两条腿的 instrument id。"""
    bundle = build_node(plan, dry_run=True)
    assert str(bundle.strategy.leader_iid) == plan.leader.instrument_id
    assert str(bundle.strategy.follower_iid) == plan.follower.instrument_id


def test_live_mode_without_credentials_raises_structured_error(plan) -> None:
    """实盘缺凭据 → 结构化错误，且**不构建**任何节点。"""
    with pytest.raises(MissingCredentials) as excinfo:
        build_client_specs(plan, dry_run=False)

    payload = excinfo.value.as_dict()
    assert payload["error"] == "missing_credentials"
    assert payload["missing"]
    assert set(payload["missing"]) & set(CRED_ENV_VARS)


def test_dry_run_never_touches_credential_env(monkeypatch: pytest.MonkeyPatch, plan) -> None:
    """dry-run 装配过程中不得读取任何凭据环境变量。"""
    seen: list[str] = []

    real_getenv = os.environ.get

    def spy(name, default=None):  # noqa: ANN001, ANN202
        seen.append(name)
        return real_getenv(name, default)

    monkeypatch.setattr(os.environ, "get", spy)
    build_client_specs(plan, dry_run=True)

    assert not [name for name in seen if name in CRED_ENV_VARS]


def test_runner_reports_missing_config(tmp_path: Path) -> None:
    """配置不存在 → 结构化错误 + 非 0 退出码。"""
    from nt_pairs.live_runner import EXIT_CONFIG, main

    code = main(["--config", str(tmp_path / "nope.json"), "--dry-run"])
    assert code == EXIT_CONFIG


def test_runner_dry_run_builds_without_credentials(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    plan,
) -> None:
    """runner 的 dry-run 路径在零凭据下能走到节点构建（不启动网络循环）。"""
    from nt_pairs import live_runner
    from nt_pairs.live_runner import EXIT_OK, main

    # 只验证到"装配成功并打印摘要"为止：不跑 node.run()，避免任何网络/长循环。
    monkeypatch.setattr(live_runner, "_run", lambda node, max_secs: EXIT_OK)

    code = main(["--config", str(PAIR_CONFIG), "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_OK

    summary = json.loads(captured.out[: captured.out.index("\n}") + 2])
    assert summary["dry_run"] is True
    assert len(summary["subscriptions"]) == 2
    assert {s["instrument_id"] for s in summary["subscriptions"]} == set(plan.instrument_ids)
    assert all(spec["kind"] != "exec" for spec in summary["clients"])
