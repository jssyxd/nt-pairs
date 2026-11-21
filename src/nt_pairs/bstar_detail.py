"""`b*` 成本门槛的**分项拆解**（不改动 `costs.py` 的费率口径）。

`costs.round_cost_bps` 只返回一个总数；做「费率 vs 滑点 vs 腿风险各占多少」的对比时，
需要把同一笔账拆开。这里按 `round_cost_bps` 的**同一组参数**逐项重算，并断言
分项之和 == `b_star(...)`，保证拆解不会与仓库口径漂移。

分项：
* `fee_bps`      —— 4 次成交的手续费（leader 进 maker / leader 出 taker / follower 进+出 taker）
* `spread_bps`   —— 4 次成交的半价差
* `slippage_bps` —— 盘口吃单滑点（leader 出场 + follower 进出场）
* `carry_bps`    —— 两腿资金费（取绝对值，与 `round_cost_bps` 一致）
* `leg_risk_bps` —— 腿风险 `√(2/π)·σ₁m·√(t_leg/60)·P·α`
* `recon_bps`    —— 对账摩擦
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .costs import LegSpec, leg_risk_bps, round_cost_bps

__all__ = ["b_star_detail"]


def b_star_detail(maker: LegSpec, taker: LegSpec, *,
                   qty_usd: Decimal,
                   hold_hours: Decimal = Decimal("0.25"),
                   t_leg_s: Decimal = Decimal("10"),
                   sigma_1m_bps: Decimal = Decimal("12.9"),
                   p_one_leg: Decimal = Decimal("0.30"),
                   recon_bps: Decimal = Decimal("0.5"),
                   leader_slip_bps: Decimal = Decimal("0"),
                   follower_slip_bps: Optional[Decimal] = None) -> dict[str, Decimal]:
    """返回 `b*` 的分项拆解（含 `total_bps`，等于 `round_cost_bps` 的结果）。"""
    slip_f = follower_slip_bps if follower_slip_bps is not None else Decimal("0")
    fee = (maker.maker_bps + maker.taker_bps
           + taker.taker_bps + taker.taker_bps)
    spread = maker.half_spread_bps * 2 + taker.half_spread_bps * 2
    slippage = slip_f * 2 + leader_slip_bps
    carry = abs(maker.funding_bps_h * hold_hours * maker.carry_sign
                + taker.funding_bps_h * hold_hours * taker.carry_sign)
    risk = leg_risk_bps(sigma_1m_bps, t_leg_s, p_one_leg)
    total = round_cost_bps(maker, taker, qty_usd=qty_usd, hold_hours=hold_hours,
                           t_leg_s=t_leg_s, sigma_1m_bps=sigma_1m_bps,
                           p_one_leg=p_one_leg, recon_bps=recon_bps,
                           leader_slip_bps=leader_slip_bps,
                           follower_slip_bps=follower_slip_bps)
    parts = fee + spread + slippage + carry + risk + recon_bps
    if abs(parts - total) > Decimal("0.02"):
        raise AssertionError(f"b* 分项合计 {parts} 与总额 {total} 不一致")
    return {
        "fee_bps": fee,
        "spread_bps": spread,
        "slippage_bps": slippage,
        "carry_bps": carry,
        "leg_risk_bps": risk,
        "recon_bps": recon_bps,
        "total_bps": total,
    }
