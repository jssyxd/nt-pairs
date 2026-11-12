"""腿选择策略（纯函数，零 IO）。

2026-11-12 的实测结论改了选腿顺序：**费率 → 点差 → 深度 → 成交概率**。
把对腿从 Lighter（0 费）换成 OKX（maker2+taker5）后，胜率仍是 95~100%，但净 EV 从
+0.5~+14.0 bps/轮 翻成 −6.7~−13.0 bps/轮 —— 差额 100% 来自费率。

本模块把这条规则写成可测的纯函数，供 `strategy.py` / 选票脚本调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional


@dataclass(frozen=True)
class LegQuote:
    """一个场所/标的在某一时刻的可交易报价（只含决策需要的字段）。"""

    venue: str
    instrument_id: str
    maker_bps: Decimal
    taker_bps: Decimal
    half_spread_bps: Decimal
    depth25_usd: Decimal
    tick: Decimal
    price: Decimal
    p_fill: Optional[Decimal] = None      # maker 侧在目标 TTL 内的成交概率（可未知）
    carry_bps_h: Decimal = Decimal("0")

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.instrument_id}"


def slip_bps(q: LegQuote, qty_usd: Decimal, ticks: int = 2, cap_bps: Decimal = Decimal("50")) -> Decimal:
    """按目标规模吃盘口的滑点估计：至少 `ticks` 个 tick，受 `cap_bps` 限制。"""
    if q.price <= 0 or qty_usd <= 0:
        return Decimal("0")
    raw = Decimal(ticks) * q.tick / q.price * Decimal("10000")
    return min(raw, cap_bps)


def maker_cost_bps(q: LegQuote) -> Decimal:
    """做 maker 一侧的成本：maker 费 + 半价差（机会成本）。"""
    return q.maker_bps + q.half_spread_bps


def taker_cost_bps(q: LegQuote, qty_usd: Decimal) -> Decimal:
    """做 taker 一侧的成本：taker 费 + 半价差 + 按规模滑点。"""
    return q.taker_bps + q.half_spread_bps + slip_bps(q, qty_usd)


def carry_bps(q: LegQuote, hold_hours: Decimal) -> Decimal:
    return abs(q.carry_bps_h) * hold_hours


def pair_cost_bps(maker_leg: LegQuote, taker_leg: LegQuote, qty_usd: Decimal, *,
                  hold_hours: Decimal = Decimal("1"),
                  leg_risk_bps: Decimal = Decimal("0.3"),
                  recon_bps: Decimal = Decimal("0.5")) -> Decimal:
    """一轮（开仓 + 平仓各一次）的摩擦成本，按腿角色计入。"""
    return (maker_cost_bps(maker_leg) + taker_cost_bps(taker_leg, qty_usd)
            + carry_bps(maker_leg, hold_hours) + carry_bps(taker_leg, hold_hours)
            + leg_risk_bps + recon_bps)


def expected_net_bps(maker_leg: LegQuote, taker_leg: LegQuote, capture_bps: Decimal,
                      qty_usd: Decimal, **kw) -> Decimal:
    """一轮的期望净边际（bps/轮）：`p_fill × (捕获 − 成本)`；`p_fill` 未知时按 1 计（乐观上界）。"""
    cost = pair_cost_bps(maker_leg, taker_leg, qty_usd, **kw)
    p = maker_leg.p_fill if maker_leg.p_fill is not None else Decimal("1")
    return p * (capture_bps - cost)


def rank_legs(quotes: Iterable[LegQuote], role: str, qty_usd: Decimal) -> list[LegQuote]:
    """按「费率 → 点差 → 深度 → 成交概率」排序。

    role="maker"：成本 = maker 费 + 半价差；
    role="taker"：成本 = taker 费 + 半价差 + 滑点。
    同成本时深度大的优先；深度再同则成交概率高的优先。
    """
    def key(q: LegQuote):
        cost = maker_cost_bps(q) if role == "maker" else taker_cost_bps(q, qty_usd)
        pf = q.p_fill if q.p_fill is not None else Decimal("-1")
        return (cost, -q.depth25_usd, -pf)

    return sorted(quotes, key=key)


def select_pair(quotes: Iterable[LegQuote], capture_bps: Decimal, qty_usd: Decimal, **kw
                ) -> tuple[Optional[LegQuote], Optional[LegQuote], Decimal]:
    """在候选腿集合里挑「maker 腿 + taker 腿」的最优组合。

    返回 (maker_leg, taker_leg, expected_net_bps)；候选不足 2 条时返回 (None, None, 0)。
    约束：maker 与 taker 必须是不同标的（避免自成交）；同标的不允许。
    """
    qs = list(quotes)
    best = (None, None, Decimal("-1e9"))
    for m in qs:
        for t in qs:
            if m.key == t.key:
                continue
            net = expected_net_bps(m, t, capture_bps, qty_usd, **kw)
            if net > best[2]:
                best = (m, t, net)
    if best[0] is None:
        return None, None, Decimal("0")
    return best


def leg_risk_bps(sigma_basis_bps: Decimal, t_leg_s: Decimal, p_one_leg: Decimal,
                 alpha: Decimal = Decimal("0.5")) -> Decimal:
    """腿风险溢价：`√(2/π)·σ·√(t/60)·P·α`。"""
    import math
    if sigma_basis_bps <= 0:
        return Decimal("0")
    v = math.sqrt(2 / math.pi) * float(sigma_basis_bps) * math.sqrt(float(t_leg_s) / 60.0) \
        * float(p_one_leg) * float(alpha)
    return Decimal(str(v))