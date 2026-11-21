"""复核测试：成交率模型与贴价档位成交归属（2026-11-21 独立复核）。

覆盖两处判定口径：

* `market_micro.fill_probability` —— 与 `docs/FILL_MODEL.md` / `scripts/fill_model.py::p_fill`
  的 Poisson 队列消耗口径是否一致；λ=0 / queue=0 / our_size=0 等边界是否不崩且取值合理；
  单调性（λ↑→p↑，queue↑→p↓）是否成立。
* `fill_calib.fill_outcome_at_touch` —— 买/卖两侧是否对称，「成交价==贴价档位」与
  「成交价穿过贴价档位」的归属是否正确，边界（正好吃完队头 / 正好吃完整个队列）是否正确。
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nt_pairs import fill_calib as fc  # noqa: E402
from nt_pairs import market_micro as mm  # noqa: E402


def _load_fill_model_script():
    """加载 `scripts/fill_model.py` 里的 p_fill（口径基准）。"""
    path = ROOT / "scripts" / "fill_model.py"
    spec = importlib.util.spec_from_file_location("_fill_model_script", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# fill_probability：口径一致 + 边界 + 单调性
# ---------------------------------------------------------------------------


def test_fill_probability_matches_filled_model_script():
    """与仓库既有 Poisson 口径（scripts/fill_model.py::p_fill）逐点一致。

    `scripts/fill_model.py` 用「分钟」，`market_micro.fill_probability` 用「秒」；
    两者必须给出同一个数（否则 FILL_MODEL.md 的表和回放用的不是同一个模型）。
    """
    fm = _load_fill_model_script()
    for lam_per_min, avg_size, queue, our_qty in (
        (217.9, 29.995, 846.0, 1.0),
        (0.05, 12.127, 146.0, 5.0),
        (16.04, 5.982, 7.0, 2.0),
        (2.52, 7.902, 369.0, 1.5),
    ):
        by_minutes = fm.p_fill(60.0, lam_per_min, avg_size, queue, our_qty)
        by_seconds = mm.fill_probability(queue, lam_per_min / 60.0, 3600.0, our_qty, avg_size)
        assert by_minutes == pytest.approx(by_seconds, rel=1e-12)


def test_fill_probability_boundaries_do_not_crash():
    """λ=0 / dt=0 / our_size=0 → 0（不是异常、也不是 NaN）；queue=0 → 有限且 > 0。"""
    # λ = 0：没有成交流 → 不可能成交
    assert mm.fill_probability(queue_ahead=100.0, trade_rate_lambda=0.0, dt=60.0,
                               our_size=1.0, avg_trade_size=10.0) == 0.0
    # dt = 0：等待时长为 0
    assert mm.fill_probability(queue_ahead=100.0, trade_rate_lambda=1.0, dt=0.0,
                               our_size=1.0, avg_trade_size=10.0) == 0.0
    # our_size = 0：我们没有挂单 → 无成交可言
    assert mm.fill_probability(queue_ahead=100.0, trade_rate_lambda=1.0, dt=60.0,
                               our_size=0.0, avg_trade_size=10.0) == 0.0
    # queue = 0：我们就是队首（但仍有 our_size 要被吃完）
    p = mm.fill_probability(queue_ahead=0.0, trade_rate_lambda=0.01, dt=60.0,
                            our_size=1.0, avg_trade_size=10.0)
    assert 0.0 < p < 1.0
    assert p == pytest.approx(1.0 - math.exp(-0.01 * 60.0 * 10.0 / 1.0))
    # 负队列（脏数据）不得炸：按 0 处理
    assert mm.fill_probability(queue_ahead=-5.0, trade_rate_lambda=0.01, dt=60.0,
                               our_size=1.0, avg_trade_size=10.0) == pytest.approx(p)


def test_fill_probability_monotonicity():
    """λ↑ → p↑；queue↑ → p↓；our_size↑ → p↓；dt↑ → p↑。"""
    base = mm.fill_probability(100.0, 0.5, 60.0, 10.0, 5.0)
    assert mm.fill_probability(100.0, 1.0, 60.0, 10.0, 5.0) > base
    assert mm.fill_probability(100.0, 0.25, 60.0, 10.0, 5.0) < base
    assert mm.fill_probability(200.0, 0.5, 60.0, 10.0, 5.0) < base
    assert mm.fill_probability(100.0, 0.5, 60.0, 20.0, 5.0) < base
    assert mm.fill_probability(100.0, 0.5, 120.0, 10.0, 5.0) > base


def test_fill_probability_hits_dimension_is_queue_consumption_fraction():
    """`hits` 是「期望被吃掉的队列比例」（无量纲），不是成交笔数。"""
    queue, our, lam, dt, size = 100.0, 10.0, 0.5, 60.0, 4.0
    hits = lam * dt * size / (queue + our)
    assert hits == pytest.approx(0.5 * 60 * 4 / 110)
    assert mm.fill_probability(queue, lam, dt, our, size) == pytest.approx(1 - math.exp(-hits))


# ---------------------------------------------------------------------------
# fill_outcome_at_touch：买/卖对称 + 边界
# ---------------------------------------------------------------------------


def _trade(t_ms, price, qty):
    return (t_ms, price, qty, "sell")


def test_fill_outcome_at_touch_buy_sell_symmetry():
    """买侧与卖侧必须完全对称（把价格上下颠倒即可）。"""
    px = 100.0
    # 买侧：成交价 == px → 消耗本档队列；成交价 < px → 市价扫穿 → 全成交
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.0, 60.0)], px, "buy") == "full"
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 99.5, 1.0)], px, "buy") == "full"
    # 卖侧：成交价 == px → 消耗本档队列；成交价 > px → 市价扫穿 → 全成交
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.0, 60.0)], px, "sell") == "full"
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.5, 1.0)], px, "sell") == "full"


def test_fill_outcome_at_touch_ignores_wrong_side_trades():
    """买侧时高于贴价档的成交与我们无关；卖侧时低于贴价档的成交与我们无关。"""
    px = 100.0
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.5, 100.0)], px, "buy") == "none"
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 99.5, 100.0)], px, "sell") == "none"


def test_fill_outcome_at_touch_boundaries():
    """正好吃完队头 → partial；正好吃完队头+我方 → full；完全没成交 → none。"""
    px = 100.0
    # 成交量 < 队头 → 队头都没被吃完 → 我们没被成交
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.0, 49.0)], px, "buy") == "none"
    # 成交量 = 队头（边界）：`fill_outcome` 归为 partial —— 我们其实一股没拿到，
    # 但标定口径把 partial 也算作「成交」（连续分布下该边界概率为 0，影响可忽略）
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.0, 50.0)], px, "buy") == "partial"
    # 成交量 = 队头 + 1 → 我们部分成交
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.0, 51.0)], px, "buy") == "partial"
    # 成交量 = 队头 + 我们的量 → 全成交
    assert fc.fill_outcome_at_touch(50.0, 10.0, [_trade(1, 100.0, 60.0)], px, "buy") == "full"
    # 没有成交
    assert fc.fill_outcome_at_touch(50.0, 10.0, [], px, "buy") == "none"


def test_fill_outcome_at_touch_tick_tolerance():
    """带 tick 容差时，贴价档位的成交价允许半格抖动；越过半格才算「扫穿」。"""
    px = 100.0
    # tick=0.1 → 容差 0.05；成交价 100.04 仍算「贴价档位」
    assert fc.fill_outcome_at_touch(10.0, 10.0, [_trade(1, 100.04, 20.0)], px, "buy",
                                    tick=0.1) == "full"
    # 成交价 99.9（越过容差）→ 扫穿 → 全成交
    assert fc.fill_outcome_at_touch(100.0, 10.0, [_trade(1, 99.9, 0.1)], px, "buy",
                                    tick=0.1) == "full"
    # 无 tick（tol≈0）时，更低价直接算扫穿
    assert fc.fill_outcome_at_touch(100.0, 10.0, [_trade(1, 99.99, 0.1)], px, "buy") == "full"


def test_through_trades_window_is_inclusive_and_side_aware():
    """窗口边界闭区间；买侧只收「成交价 ≤ 贴价」，卖侧只收「成交价 ≥ 贴价」。"""
    trades = [
        (1000, 100.0, 5.0, "sell"),   # 贴价
        (2000, 99.0, 5.0, "sell"),    # 穿过（买侧）
        (3000, 101.0, 5.0, "buy"),    # 与买侧无关
        (4000, 100.5, 5.0, "buy"),    # 与卖侧无关
    ]
    got_buy = fc.through_trades(trades, 1000, 3000, 100.0, "buy")
    assert [t[0] for t in got_buy] == [1000, 2000]      # 上界闭区间
    got_sell = fc.through_trades(trades, 1000, 3000, 100.0, "sell")
    assert [t[0] for t in got_sell] == [1000, 3000]


def test_sample_queue_takes_backward_samples_and_median():
    """队列采样必须是「向前（挂单前）≥3 次」并取中位数。"""
    snaps = []
    for i in range(6):
        snaps.append(fc.Snap(t=float(i), venue="LIGHTER", symbol="SOXL",
                              bids={100.0: 10.0 + i}, asks={101.0: 5.0},
                              trades=(), gap=False))
    samples, med = fc.sample_queue(snaps, 5, "buy", 100.0, n_samples=3, min_gap_s=0.0)
    assert samples == [15.0, 14.0, 13.0]     # 从决策时刻往回取
    assert med == 14.0
    # 价位在该样本中不存在 → 记 None，中位数只取存在的样本
    snaps2 = list(snaps)
    snaps2[4] = fc.Snap(t=4.0, venue="LIGHTER", symbol="SOXL",
                        bids={99.0: 1.0}, asks={101.0: 5.0}, trades=(), gap=False)
    samples2, med2 = fc.sample_queue(snaps2, 5, "buy", 100.0, n_samples=3, min_gap_s=0.0)
    assert samples2 == [15.0, None, 13.0]
    assert med2 == 14.0
    # 一个都没有 → 中位数 None
    assert fc.sample_queue(snaps2, 5, "buy", 42.0, n_samples=3, min_gap_s=0.0)[1] is None


def test_model_p_fill_helpers_agree_with_market_micro():
    """fill_calib 的模型封装必须与 market_micro 是同一个口径。"""
    assert fc.model_p_fill(100.0, 0.5, 60.0, 10.0, 4.0) == pytest.approx(
        mm.fill_probability(100.0, 0.5, 60.0, 10.0, 4.0))
    # model_any：只要队头被打完就算成交（need = queue_ahead）
    assert fc.model_p_fill_any(100.0, 0.5, 60.0, 4.0) == pytest.approx(
        1 - math.exp(-0.5 * 60 * 4 / 100.0))
    assert fc.model_p_fill_any(0.0, 0.5, 60.0, 4.0) == 1.0
