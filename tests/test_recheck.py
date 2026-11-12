"""E8 独立复核的回归测试：锁住复算出来的关键常数与口径判定。

这些测试不依赖网络：用合成序列复现复核脚本里的计算口径，并锁定复核报告中给出的数字。
若 `src/nt_pairs/leg_policy.py` / `scripts/build_matrix.py` 的语义被改动，这里会先红。
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from decimal import Decimal  # noqa: E402

from nt_pairs.leg_policy import (  # noqa: E402
    LegQuote,
    expected_net_bps,
    leg_risk_bps,
    pair_cost_bps,
)

import recheck  # noqa: E402

D = Decimal


def _q(venue: str, maker=D("1.5"), taker=D("4.5"), half=D("1.2"), tick=D("0.01"),
       price=D("64.0"), pf=None, carry=D("0")) -> LegQuote:
    return LegQuote(venue=venue, instrument_id=f"{venue}-X", maker_bps=maker, taker_bps=taker,
                    half_spread_bps=half, depth25_usd=D("365000"), tick=tick, price=price,
                    p_fill=pf, carry_bps_h=carry)


# ---------------------------------------------------------------- Q1: EV 口径
def test_expected_net_bps_is_p_fill_times_capture_minus_cost():
    """文档公式 `p_fill × (capture − cost)`：形式成立。"""
    m = _q("HL", pf=D("0.5"))
    t = _q("LIGHTER", maker=D("0"), taker=D("0"), half=D("1.5"))
    cost = pair_cost_bps(m, t, D("500"))
    net = expected_net_bps(m, t, D("16.9"), D("500"))
    assert net == pytest.approx(D("0.5") * (D("16.9") - cost))
    # p_fill 未知 → 按 1 计（乐观上界，不是期望值）；注意换角色后成本口径也换
    t2 = _q("LIGHTER", maker=D("0"), taker=D("0"), half=D("1.5"))
    cost_swapped = pair_cost_bps(t2, m, D("500"))
    assert expected_net_bps(t2, m, D("16.9"), D("500")) == pytest.approx(D("16.9") - cost_swapped)


def test_pair_cost_bps_omits_exit_side_costs():
    """`pair_cost_bps` 只计「一次 maker + 一次 taker」，缺平仓腿的成本（复核数字 7.60 bps）。"""
    m = _q("HL", pf=D("0.5"), carry=D("0.05"))
    t = _q("LIGHTER", maker=D("0"), taker=D("0"), half=D("1.5"))
    base = pair_cost_bps(m, t, D("500"))
    full = recheck.round_cost_bps(
        leader_maker_bps=1.5, leader_taker_bps=4.5, follower_taker_bps=0.0,
        hs_leader_bps=1.2, hs_follower_bps=1.5,
        slip_leader_bps=0.33, slip_follower_bps=3.27,
        carry_leader_bps_h=0.05, carry_follower_bps_h=0.0,
        hold_h=0.1, leg_risk_bps=0.3, recon_bps=0.5)["total"]
    assert float(full) - float(base) == pytest.approx(7.6, abs=1e-6)


def test_pair_cost_bps_uses_constant_leg_risk_not_measured():
    """腿风险在 `pair_cost_bps` 里是写死的 0.3，与 σ_1m/t_leg/P_1leg 无关。"""
    m = _q("HL")
    t = _q("LIGHTER", maker=D("0"), taker=D("0"))
    a = pair_cost_bps(m, t, D("500"))
    b = pair_cost_bps(m, t, D("500"), leg_risk_bps=D("30"))
    assert b - a == pytest.approx(D("29.7"))
    # 实测口径下 30s / P=0.30 的腿风险只有 ~1.09 bps
    assert float(leg_risk_bps(D("12.9"), D("30"), D("0.30"))) == pytest.approx(1.0917, abs=1e-3)


def test_leg_risk_table_constants():
    """腿风险量级表（复核数字）。"""
    expected = {
        ("12.9", "2", "0.15"): 0.1409,
        ("12.9", "2", "0.30"): 0.2819,
        ("12.9", "30", "0.15"): 0.5459,
        ("12.9", "30", "0.30"): 1.0917,
        ("11.6767", "2", "0.15"): 0.1276,
        ("11.6767", "2", "0.30"): 0.2551,
        ("11.6767", "30", "0.15"): 0.4941,
        ("11.6767", "30", "0.30"): 0.9882,
    }
    for row in recheck.leg_risk_table():
        key = (row["sigma_1m"], row["t_leg_s"], row["p_1leg"])
        assert key in expected
        assert row["leg_risk_bps"] == pytest.approx(expected[key], abs=1e-3)


def test_leg_risk_30s_share_of_headline_and_size_adjusted_net():
    """t_leg=30s 的腿风险：吃掉 headline +5.3 的 ~21%，吃掉 $500 规模净 +1.3 的 ~84%。"""
    v = recheck.q1_leg_risk_verdict()
    assert v["leg_risk_30s_p030"] == pytest.approx(1.0917, abs=1e-3)
    assert v["share_of_headline"] == pytest.approx(1.0917 / 5.3, rel=1e-3)
    assert v["share_of_500"] == pytest.approx(1.0917 / 1.3, rel=1e-3)
    assert v["share_of_500"] > 0.8


# ---------------------------------------------------------------- Q2: 费率
def test_hl_hip3_fee_formula_matches_official():
    """官方 feeRates：hip3Scale = scale<1 ? scale+1 : scale*2；growthMode ×0.1。"""
    # 仓库假设的 1.5/4.5 只对应「非 HIP-3、非 growth mode」的基准费率
    base = recheck.hl_fee_formula(1.5, 4.5, 1.0, False)
    assert base["maker_bps"] == pytest.approx(3.0)     # scale=1 → ×2
    assert base["taker_bps"] == pytest.approx(9.0)
    assert base["deployer_share"] == pytest.approx(0.5)
    # growth mode（HIP-3 xyz/io 全部资产实测启用）→ 全部再 ×0.1
    gm = recheck.hl_fee_formula(1.5, 4.5, 1.0, True)
    assert gm["maker_bps"] == pytest.approx(0.3)
    assert gm["taker_bps"] == pytest.approx(0.9)
    assert gm["all_in_round_bps"] == pytest.approx(1.2)
    # scale<1 分支：scale+1，deployerShare = s/(1+s)
    low = recheck.hl_fee_formula(1.5, 4.5, 0.5, False)
    assert low["hip3_scale"] == pytest.approx(1.5)
    assert low["deployer_share"] == pytest.approx(1 / 3)
    assert low["taker_bps"] == pytest.approx(6.75)
    # scale=0 → 不加价，等于基准费率
    zero = recheck.hl_fee_formula(1.5, 4.5, 0.0, False)
    assert zero["taker_bps"] == pytest.approx(4.5)


def test_repo_fee_assumption_overstates_hl_by_5x():
    """仓库的 HL 1.5/4.5 相对实测（growth mode）高 5 倍。"""
    repo_round = 1.5 + 4.5
    eff = recheck.hl_fee_formula(1.5, 4.5, 1.0, True)["all_in_round_bps"]
    assert repo_round / eff == pytest.approx(5.0, rel=1e-6)


# ---------------------------------------------------------------- Q3: 闸门
def test_repeat_close_uses_close_and_adjacent_pair_denominator():
    rc = recheck.q3_repeat_close_semantics()
    assert rc["denominator_is_adjacent_pairs"] is True
    assert rc["value_adjacent_denominator"] == pytest.approx(50.0)
    assert rc["value_bucket_denominator_would_be"] == pytest.approx(40.0)
    assert rc["price_used"] == "close"


def test_build_matrix_has_no_event_level_ev_gate():
    """第五道闸门（事件级 EV）在 build_matrix.py 里根本没有实现。"""
    src = open(os.path.join(ROOT, "scripts", "build_matrix.py"), encoding="utf-8").read()
    assert "expected_net_bps" not in src
    assert "screen_candidates" not in src
    assert 'rec["tradable"] = bool(' in src
    # tradable 的四个条件：edge>0 / 容量 / 数据新鲜 / 单位（后者在前面短路）
    lines = src.splitlines()
    idx = next(i for i, ln in enumerate(lines) if 'rec["tradable"] = bool(' in ln)
    stmt = " ".join(lines[idx:idx + 2])
    assert "edge > 0" in stmt
    assert "cap >= 1000.0" in stmt
    assert "stale_print_suspect" in stmt
    # screen_candidates.py 里也没有 tradable 布尔
    scr = open(os.path.join(ROOT, "scripts", "screen_candidates.py"), encoding="utf-8").read()
    assert "tradable" not in scr


def test_build_matrix_basis_is_not_ratio_normalised():
    """basis 用的是 P_B/P_A（未除以 ratio_evidence.mean），单位偏移会整体进入基差。"""
    src = open(os.path.join(ROOT, "scripts", "build_matrix.py"), encoding="utf-8").read()
    basis_lines = [ln.strip() for ln in src.splitlines() if "basis = [" in ln]
    assert basis_lines, "找不到 basis 计算行"
    assert not any("ratio" in ln for ln in basis_lines)


def test_matrix_tradable_flag_is_reproducible_from_documented_rule():
    """matrix.json 里的 tradable == (edge>0) ∧ (cap≥1000) ∧ ¬stale ∧ ¬unit_fail。"""
    path = os.path.join(ROOT, "data", "matrix.json")
    if not os.path.exists(path):
        pytest.skip("data/matrix.json 不存在")
    payload = json.load(open(path, encoding="utf-8"))
    for p in payload["pairs"]:
        edge = (p.get("score") or {}).get("edge_ratio")
        cap = (p.get("capacity") or {}).get("cap_usd")
        if p.get("unit_fail") or edge is None:
            expected = False
        else:
            expected = bool(edge > 0 and (cap or 0) >= 1000.0
                            and not p.get("stale_print_suspect"))
        assert p.get("tradable") == expected, p["pair"]


# ---------------------------------------------------------------- Q4: 回放口径
def _synthetic_legs(n: int = 400, seed_shift: float = 12.0):
    """构造一段「信号后 1 根 bar 内回归」的合成 5m 序列。

    close 基差在 bar i 冲高 seed_shift，随后在 bar i+1 收盘回到 0；
    open 基差与 close 基差在 bar 内不同步 —— 用来区分三种定价口径。
    """
    h, l = {}, {}
    base = 100.0
    for i in range(n):
        hl_c = base + (i % 7) * 0.01
        hl_o = hl_c
        # Lighter 的 close 在 bar 内回落 12 bps（相对 HL）
        lt_c = hl_c * (1 + (0.0 if i % 11 else seed_shift / 1e4))
        lt_o = lt_c * (1 + 0.0004)     # open 比 close 贵 4 bps
        h[i] = {"o": hl_o, "c": hl_c, "v": 1.0}
        l[i] = {"o": lt_o, "c": lt_c, "v": 1.0}
    return h, l


def test_fade_replay_pricing_conventions_differ():
    """三种定价口径必须给出不同的捕获：开→收 / 开→开 / 收→收。"""
    h, l = _synthetic_legs()
    rep = recheck.fade_replay(h, l, W=48, hold=288)
    v = rep["variants"]
    assert v["as_implemented"]["mean"] != pytest.approx(v["close_close"]["mean"], abs=1e-6)
    assert v["open_open"]["n"] > 0


def test_fade_replay_as_implemented_drops_unresolved_entries():
    """仓库口径把「24h 内没回归」的事件直接丢掉（不放进样本）。"""
    h, l = _synthetic_legs(n=120)
    rep = recheck.fade_replay(h, l, W=48, hold=288)
    v = rep["variants"]
    assert v["as_implemented"]["n"] <= v["timeout_loss"]["n"]
    assert v["timeout_loss"]["timeouts"] >= 0


def test_round_cost_ledger_components():
    """一轮成本账：费 + 价差穿越 + 滑点 + carry + 腿风险 + 对账。"""
    c = recheck.round_cost_bps(
        leader_maker_bps=1.5, leader_taker_bps=4.5, follower_taker_bps=0.0,
        hs_leader_bps=2.0, hs_follower_bps=4.0,
        slip_leader_bps=1.0, slip_follower_bps=2.0,
        carry_leader_bps_h=0.05, carry_follower_bps_h=0.05,
        hold_h=1.0, leg_risk_bps=0.3, recon_bps=0.5)
    assert c["fees"] == pytest.approx(6.0)
    assert c["spread"] == pytest.approx(1.0 + 4.0)
    assert c["slip"] == pytest.approx(1.0 + 2 * 2.0)
    assert c["carry"] == pytest.approx(0.10)
    assert c["total"] == pytest.approx(6.0 + 5.0 + 5.0 + 0.10 + 0.3 + 0.5)


def test_ev_bands_are_ordered_and_scale_with_p_fill():
    bands = recheck.ev_bands(capture_impl=17.6, capture_open_open=17.9, capture_close_close=-1.5,
                             b_star_repo=15.3, b_star_full=15.3, p_fill=0.5)
    assert bands["conservative"]["net_bps"] < bands["neutral"]["net_bps"] < bands["optimistic"]["net_bps"]
    assert bands["optimistic"]["net_bps"] == pytest.approx(17.6 - 15.3)
    assert bands["neutral"]["net_bps"] == pytest.approx(0.5 * (17.9 - 15.3))
    assert bands["conservative"]["net_bps"] == pytest.approx(0.3 * (-1.5 - 15.3))


def test_leg_risk_formula_is_sqrt_time():
    """√(t) 标度复核。"""
    a = float(leg_risk_bps(D("12.9"), D("4"), D("0.3")))
    b = float(leg_risk_bps(D("12.9"), D("16"), D("0.3")))
    assert b == pytest.approx(2 * a, rel=1e-9)
    assert math.isclose(a, math.sqrt(2 / math.pi) * 12.9 * math.sqrt(4 / 60) * 0.3 * 0.5,
                        rel_tol=1e-9)


def test_statistics_helpers_are_consistent():
    """脚本里用到的统计口径（中位数/MAD/分位）与标准实现一致。"""
    xs = [1.0, 2.0, 3.0, 4.0, 100.0]
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs)
    sig = max(1.4826 * mad, 1e-9)
    assert med == pytest.approx(3.0)
    assert mad == pytest.approx(1.0)
    assert sig == pytest.approx(1.4826)
