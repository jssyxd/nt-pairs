"""策略骨架冒烟测试（不连网、不下单）。

只验证「能在 2.0rc6 里实例化 + dry_run 绝不调用 submit_order + 带状状态可持久化」，
不验证真实撮合（那需要 L2 回放，见 docs/2026-11-11-economics.md 的前置条件）。
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader.adapters.lighter", reason="需要 nautilus_trader>=2.0.0rc6（含 Lighter 适配器）")

from nt_pairs.strategy import PairHedgeConfig, PairHedgeStrategy  # noqa: E402

LEADER = "io:OAI-USD-PERP.HYPERLIQUID"
FOLLOWER = "OPENAI-PERP.LIGHTER"


def _strategy(tmp_path) -> PairHedgeStrategy:
    cfg = PairHedgeConfig(
        leader_id=LEADER,
        follower_id=FOLLOWER,
        dry_run=True,
        state_dir=str(tmp_path),
    )
    return PairHedgeStrategy(cfg)


def test_config_coerces_to_decimal():
    cfg = PairHedgeConfig(leader_id=LEADER, follower_id=FOLLOWER, ratio="0.5", b_star_bps="21.5")
    assert cfg.ratio == Decimal("0.5")
    assert cfg.b_star_bps == Decimal("21.5")
    assert isinstance(cfg.ratio, Decimal)


def test_ledger_is_target_based_on_init(tmp_path):
    s = _strategy(tmp_path)
    snap = s.ledger.snapshot()
    assert snap["q_leader"] == 0
    assert snap["residual"] == 0
    assert s.ledger.ratio == Decimal("1")


def test_dry_run_never_submits(tmp_path, monkeypatch):
    s = _strategy(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("submit_order must not be called in dry_run")

    monkeypatch.setattr(s, "submit_order", boom)
    s._submit(
        instrument_id=s.follower_iid,
        side="buy",
        qty=Decimal("0.01"),
        price=Decimal("1400.00"),
        tif=__import__("nautilus_trader.model", fromlist=["TimeInForce"]).TimeInForce.IOC,
        reduce_only=False,
        tag="pytest",
    )


def test_band_state_roundtrip(tmp_path):
    s = _strategy(tmp_path)
    s._band_state = {"median": 305.0, "sigma": 53.5}
    s._save_state()

    s2 = _strategy(tmp_path)
    assert s2._band_state["median"] == pytest.approx(305.0)
    assert s2._band_state["sigma"] == pytest.approx(53.5)


def test_band_requires_warmup(tmp_path):
    s = _strategy(tmp_path)
    for i in range(10):
        s.basis_history.append(300.0 + i)
    assert s._update_band(305.0) is False  # <72 个点不建带


def test_naked_timer_sets_since(tmp_path):
    s = _strategy(tmp_path)
    s.ledger.on_fill(trade_id="t1", venue="HYPERLIQUID", side="buy", qty=Decimal("0.01"),
                     px=Decimal("1360"), fee=None)
    assert s.ledger.residual() != 0
    s.naked_since = None
    # 没有 book 时 _hedge 不应崩，且应记录裸腿起点
    s._hedge()
    assert s.naked_since is not None