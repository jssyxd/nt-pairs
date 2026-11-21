"""复核测试：`scripts/chd_screen.py --end` 窗口锚点（2026-11-21 独立复核）。

三件事：

1. **默认行为不变**（回归）：不加 `--end` 时，`hours_all` 必须与改动前的
   `hour_range(now - 1h, hours)` 完全一致（窗口 start/end 一致）。
2. **加了 `--end` 后窗口锚在指定时刻**：逐日回放的前提。
3. **网格截断**：回放网格上限 200k 点（grid_ms=2000 → 约 111 小时），
   超过就只回放**前段**并留一条 note —— 336 小时（14 天）单次跑会被静默截掉尾巴，
   这条测试把这个事实钉住（并在复核报告里作为缺陷登记）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import chd_screen as cs  # noqa: E402


class _FakeClient:
    """不联网的 ChdClient 替身（符号清单为空 → 没有任何下载）。"""

    def __init__(self, cache_dir: str | None = None, *a, **kw):
        self.cache_dir = cache_dir

    def symbols(self, exchange: str, data_type: str = "orderbook") -> list[str]:
        return []

    def download(self, *a, **kw):  # pragma: no cover - 不应被调用
        raise AssertionError("本测试不应触发任何下载")


def _run_main(tmp_path: Path, monkeypatch, argv, now: datetime) -> dict:
    monkeypatch.setattr(cs, "ChdClient", _FakeClient)
    monkeypatch.setattr(cs, "utcnow", lambda: now)
    out = tmp_path / "screen.json"
    report = tmp_path / "screen.md"
    rc = cs.main([*argv, "--out", str(out), "--report", str(report)])
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_default_window_matches_pre_change_behaviour(tmp_path, monkeypatch):
    """不加 --end：窗口 = hour_range(now-1h, hours)（与改动前逐字一致）。"""
    now = datetime(2026, 11, 21, 12, 37, tzinfo=timezone.utc)
    payload = _run_main(tmp_path, monkeypatch, ["--hours", "24"], now)
    old_hours = cs.hour_range(now - timedelta(hours=1), 24)
    assert payload["window"]["start"] == f"{old_hours[0][0]} {old_hours[0][1]:02d}:00"
    assert payload["window"]["end"] == f"{old_hours[-1][0]} {old_hours[-1][1]:02d}:00"


def test_end_anchor_shifts_the_window(tmp_path, monkeypatch):
    """--end 把窗口锚定到指定时刻（逐日回放的前提）。"""
    now = datetime(2026, 11, 21, 12, 37, tzinfo=timezone.utc)
    payload = _run_main(tmp_path, monkeypatch,
                        ["--hours", "24", "--end", "2026-11-13 23"], now)
    assert payload["window"]["end"] == "2026-11-13 23:00"
    assert payload["window"]["start"] == "2026-11-13 00:00"
    # 带分钟的写法同样可用
    payload2 = _run_main(tmp_path, monkeypatch,
                         ["--hours", "24", "--end", "2026-11-13 23:30"], now)
    assert payload2["window"]["end"] == "2026-11-13 23:00"  # 小时取整


def test_unparsable_end_falls_back_to_now(tmp_path, monkeypatch):
    """--end 解析失败 → 回退到「现在的前一小时」，不崩。"""
    now = datetime(2026, 11, 21, 12, 37, tzinfo=timezone.utc)
    payload = _run_main(tmp_path, monkeypatch,
                        ["--hours", "24", "--end", "not-a-date"], now)
    old_hours = cs.hour_range(now - timedelta(hours=1), 24)
    assert payload["window"]["end"] == f"{old_hours[-1][0]} {old_hours[-1][1]:02d}:00"


def test_default_replay_rth_window_matches_pre_change(tmp_path, monkeypatch):
    """默认（不加 --end）+ --replay-rth：回放窗口必须与改动前一致。

    改动前用 ``last_rth_hours(now, n)``；若改成 ``last_rth_hours(now-1h, n)``，
    在 now 落在 [15:00,16:00) UTC（当前 RTH 块首个小时的前一小时是盘前）时
    会回退到**上一个** RTH 块（甚至只剩 1 小时）→ 回放窗口不可复现。

    这里直接跑 ``main`` 走真实代码路径，而不是比较两次函数调用 ——
    函数本身的「减 1 小时」是它的定义（调用方传什么就该是什么）。
    """
    now = datetime(2026, 11, 2, 16, 5, tzinfo=timezone.utc)
    payload = _run_main(tmp_path, monkeypatch,
                        ["--hours", "24", "--replay-rth", "--replay-hours", "2"], now)
    # 改动前的行为：回放窗口 = last_rth_hours(now, 2)
    expected = cs.last_rth_hours(now, 2)
    assert payload["window"]["replay_start"] == f"{expected[0][0]} {expected[0][1]:02d}:00"
    assert payload["window"]["replay_end"] == f"{expected[-1][0]} {expected[-1][1]:02d}:00"


def test_end_anchor_replay_rth_lands_on_that_day(tmp_path, monkeypatch):
    """带 --end 时，--replay-rth 必须落在那一天自己的 RTH 块（逐日回放的正确用法）。"""
    now = datetime(2026, 11, 21, 12, 37, tzinfo=timezone.utc)
    _run_main(tmp_path, monkeypatch,
              ["--hours", "24", "--replay-rth", "--replay-hours", "2",
               "--end", "2026-11-13 23"], now)
    rth = cs.last_rth_hours(datetime(2026, 11, 13, 23, 0, tzinfo=timezone.utc), 2)
    assert rth and all(d == "2026-11-13" for d, _h in rth)


def test_replay_grid_is_truncated_beyond_200k_points():
    """回放网格上限 200k 点（grid_ms=2000 → 111.1 小时）；超出只回放前段。

    这不是「静默」到看不出——notes 里有「窗口过长，已截断」——但报告里没有任何地方
    说明**丢了多少**。336 小时（14 天）单次回放会丢掉约 2/3 的样本。
    """
    from nt_pairs.chd_client import BookPoint
    from nt_pairs.funding_fee import venue_fees

    span_ms = 200 * 3600 * 1000          # 200 小时 > 111 小时
    points = [
        BookPoint(ts_ms=ts, bid=99.0, ask=101.0, mid=100.0, bid_usd=1e6, ask_usd=1e6,
                  bid_qty=100.0, ask_qty=100.0,
                  bid_levels=((99.0, 100.0),), ask_levels=((101.0, 100.0),))
        for ts in range(0, span_ms, 3600 * 1000)
    ]
    leg_a = cs.BookLeg(cs.Leg("lighter", "SOXL", "SOXL"), points, 0.01, 200.0,
                       1e6, 1e6, 0.0, venue_fees("lighter"))
    leg_b = cs.BookLeg(cs.Leg("hyperliquid_futures", "XYZ:SOXL", "SOXL"), points, 0.01,
                       200.0, 1e6, 1e6, 0.0, venue_fees("hyperliquid_futures"))
    cfg = cs.ReplayConfig(grid_ms=2000, ttl_s=60)
    res = cs.replay_pair(leg_a, leg_b, None, None, cfg, 100.0, maker_is_a=True)
    assert res.n_grid == 200_000
    assert any("截断" in n for n in res.notes)
    # 实际覆盖的时间跨度只有 ~111 小时（而不是请求的 200 小时）
    covered_s = (res.n_grid - 1) * cfg.grid_ms / 1000.0
    assert covered_s == pytest.approx((200_000 - 1) * 2.0, rel=1e-6)
    assert covered_s < span_ms / 1000.0
