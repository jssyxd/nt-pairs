"""成本与期望净边际的统一口径（2026-11-13 独立复核后重写）。

修正的四个错误（见 `.herd-dispatch/jobs/recheck/report.md`）：
- S5：HIP-3 的实际费率不是 1.5/4.5 bps。官方公式：
      scaleIfHip3   = scale < 1 ? scale + 1 : scale * 2
      growthScale    = growthMode ? 0.1 : 1
      fee_bps        = base_bps * scaleIfHip3 * growthScale
  对本仓库用到的 HIP-3 资产（deployerFeeScale=1.0、growthMode=enabled）→ maker 0.30 / taker 0.90 bps。
- S6：一轮 = **4 次成交**（leader 入场 maker、follower 入场 taker、follower 平仓 taker、leader 平仓 taker）。
  旧口径只算了 1 maker + 1 taker，漏约 7.6 bps/轮。
- S7：腿风险必须用公式 `√(2/π)·σ_1m·√(t_leg/60)·P_1leg·α`，不能写死 0.3。
- S17：滑点按**目标规模吃盘口**（VWAP 偏离），不是 2×tick 代理。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# HL 主网 Tier0 基础费率（bps）
HL_BASE_MAKER_BPS = Decimal("1.5")
HL_BASE_TAKER_BPS = Decimal("4.5")


def hl_fee_bps(deployer_fee_scale: float | Decimal | None,
               growth_mode: bool | None,
               base_bps: Decimal) -> Decimal:
    """HIP-3 实际费率（bps）。

    官方 `feeRates` 公式：scale<1 → scale+1；scale≥1 → scale*2；growthMode → 再 ×0.1。
    `deployer_fee_scale=None` 或 `growth_mode=None` 表示**未知**，返回 base（保守上界）并由调用方标注。
    """
    if deployer_fee_scale is None or growth_mode is None:
        return base_bps
    s = Decimal(str(deployer_fee_scale))
    scale_if_hip3 = (s + 1) if s < 1 else (s * 2)
    growth = Decimal("0.1") if growth_mode else Decimal("1")
    return (base_bps * scale_if_hip3 * growth).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class LegSpec:
    """一条腿在一次往返中的静态特征。"""

    venue: str
    maker_bps: Decimal
    taker_bps: Decimal
    half_spread_bps: Decimal
    price: Decimal
    tick: Decimal
    depth_bid_usd: Decimal
    depth_ask_usd: Decimal
    funding_bps_h: Decimal = Decimal("0")
    carry_sign: int = 0          # +1 多头付资金费 / -1 空头收资金费


def vwap_slip_bps(levels: list[tuple[Decimal, Decimal]], side: str, qty: Decimal,
                  price: Decimal) -> Decimal:
    """按规模吃盘口的滑点（bps，相对 mid）。levels 必须按有利方向排序。"""
    if price <= 0 or qty <= 0 or not levels:
        return Decimal("0")
    rem, cost = qty, Decimal("0")
    for px, sz in levels:
        take = min(rem, sz)
        cost += take * px
        rem -= take
        if rem <= 0:
            break
    filled = qty - rem
    if filled <= 0:
        return Decimal("0")
    vwap = cost / filled
    return (abs(vwap - price) / price * 10000).quantize(Decimal("0.001"))


def leg_risk_bps(sigma_1m_bps: Decimal, t_leg_s: Decimal, p_one_leg: Decimal,
                 alpha: Decimal = Decimal("0.5")) -> Decimal:
    if sigma_1m_bps <= 0:
        return Decimal("0")
    v = (math.sqrt(2 / math.pi) * float(sigma_1m_bps) * math.sqrt(float(t_leg_s) / 60.0)
         * float(p_one_leg) * float(alpha))
    return Decimal(str(v)).quantize(Decimal("0.0001"))


def round_cost_bps(maker: LegSpec, taker: LegSpec, *,
                   qty_usd: Decimal,
                   hold_hours: Decimal = Decimal("0.25"),
                   t_leg_s: Decimal = Decimal("10"),
                   sigma_1m_bps: Decimal = Decimal("12.9"),
                   p_one_leg: Decimal = Decimal("0.30"),
                   recon_bps: Decimal = Decimal("0.5"),
                   leader_slip_bps: Decimal = Decimal("0"),
                   follower_slip_bps: Optional[Decimal] = None) -> Decimal:
    """一轮往返的成本（bps，占单腿名义）。

    4 次成交：
      leader 入场 (maker) = maker 费 + 半价差
      follower 入场 (taker) = taker 费 + 半价差 + 滑点
      follower 平仓 (taker) = taker 费 + 半价差 + 滑点
      leader 平仓 (taker) = taker 费 + 半价差 (+ 滑点，若指定)
    再加：两腿 carry（按方向带符号）、腿风险、对账。
    """
    slip_f = follower_slip_bps if follower_slip_bps is not None else Decimal("0")
    cost = (maker.maker_bps + maker.half_spread_bps
            + taker.taker_bps + taker.half_spread_bps + slip_f
            + taker.taker_bps + taker.half_spread_bps + slip_f
            + maker.taker_bps + maker.half_spread_bps + leader_slip_bps)
    carry = (maker.funding_bps_h * hold_hours * maker.carry_sign
             + taker.funding_bps_h * hold_hours * taker.carry_sign)
    cost += abs(carry)
    cost += leg_risk_bps(sigma_1m_bps, t_leg_s, p_one_leg)
    cost += recon_bps
    return cost.quantize(Decimal("0.01"))


def expected_net_bps(capture_bps: Decimal, cost_bps: Decimal,
                     p_fill: Decimal | None) -> tuple[Decimal, str]:
    """期望净边际（bps/轮）。

    `p_fill=None` → 返回乐观上界并在 note 里标注「p_fill 未知」。
    """
    p = p_fill if p_fill is not None else Decimal("1")
    net = (p * (capture_bps - cost_bps)).quantize(Decimal("0.01"))
    note = "上界(p_fill=1，未标定)" if p_fill is None else f"p_fill={p_fill}"
    return net, note
