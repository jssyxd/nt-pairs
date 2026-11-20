"""费率与 `b*` 成本门槛（**复用** `nt_pairs.costs` 的口径，不另立一套）。

腿选择规则（DECISION.md，实测）：**费率 → 点差 → 深度 → 成交概率**（深度排最后）。
把腿从 Lighter（0 费）换成 OKX（maker2+taker5），胜率不变但净 EV 从 +0.5~+14
变成 −6.7~−13 bps/轮 —— 差额几乎全部来自 taker 费。

`b_star` 直接调用 `costs.round_cost_bps`（4 次成交 + carry + 腿风险 + 对账），
保证与仓库既有 `probe_basis.py` / `leg_policy.py` 的口径完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .costs import LegSpec, hl_fee_bps, round_cost_bps

__all__ = [
    "VenueFees",
    "VENUE_FEES",
    "venue_fees",
    "b_star",
    "carry_bps_h",
]


@dataclass(frozen=True)
class VenueFees:
    """一个场所的 maker/taker 费率（bps，正数=付费）。"""

    venue: str
    maker_bps: Decimal
    taker_bps: Decimal
    note: str = ""


# 默认费率表（可被 --fees 覆盖）。HIP-3 的实际费率以成交回执为准，
# 未知 deployerFeeScale 时用 HL 基础档（保守上界）。
VENUE_FEES: dict[str, VenueFees] = {
    "lighter": VenueFees("lighter", Decimal("0"), Decimal("0"),
                         "Lighter maker/taker 0（费率隐性支柱）"),
    "hyperliquid_futures": VenueFees("hyperliquid_futures", Decimal("1.5"), Decimal("4.5"),
                                     "HL 基础 perp 档 1.5/4.5；HIP-3 需乘 deployerFeeScale"),
    "okx_futures": VenueFees("okx_futures", Decimal("2"), Decimal("5"),
                             "OKX 永续 VIP0 2/5；股票永续另查"),
    "binance_futures": VenueFees("binance_futures", Decimal("2"), Decimal("5"),
                                 "Binance USDⓈ-M VIP0 2/5；股票永续另查"),
    "bybit": VenueFees("bybit", Decimal("1"), Decimal("5"), "Bybit VIP0"),
    "kraken_derivatives": VenueFees("kraken_derivatives", Decimal("2"), Decimal("5"),
                                    "Kraken futures 2/5"),
    "bitget_futures": VenueFees("bitget_futures", Decimal("1"), Decimal("5"), "Bitget 1/5"),
    "aster_futures": VenueFees("aster_futures", Decimal("0"), Decimal("5"), "Aster 0/5"),
}


def venue_fees(venue: str, *, deployer_fee_scale: float | None = None,
               growth_mode: bool | None = None,
               override: Optional[VenueFees] = None) -> VenueFees:
    """取一个场所的费率。

    Hyperliquid HIP-3：按官方公式
    `fee_bps = base_bps * (scale<1 ? scale+1 : scale*2) * (growthMode ? 0.1 : 1)`
    换算（`costs.hl_fee_bps`）。`deployer_fee_scale`/`growth_mode` 未知时
    返回基础档（保守上界）并保留 note。
    """
    if override is not None:
        return override
    base = VENUE_FEES.get(venue)
    if base is None:
        raise KeyError(f"unknown venue {venue!r}; known: {sorted(VENUE_FEES)}")
    if venue == "hyperliquid_futures":
        maker = hl_fee_bps(deployer_fee_scale, growth_mode, base.maker_bps)
        taker = hl_fee_bps(deployer_fee_scale, growth_mode, base.taker_bps)
        note = base.note
        if deployer_fee_scale is None or growth_mode is None:
            note += "（deployerFeeScale 未知 → 用基础档保守上界）"
        return VenueFees(venue, maker, taker, note)
    return base


def carry_bps_h(venue: str, funding_bps_h: float | None) -> Decimal:
    """资金费（bps/小时，带方向）。未知 → 0，并由调用方标注「carry 未知」。"""
    if funding_bps_h is None:
        return Decimal("0")
    return Decimal(str(funding_bps_h))


def b_star(maker: LegSpec, taker: LegSpec, *,
           qty_usd: Decimal,
           hold_hours: Decimal = Decimal("0.25"),
           t_leg_s: Decimal = Decimal("10"),
           sigma_1m_bps: Decimal = Decimal("12.9"),
           p_one_leg: Decimal = Decimal("0.30"),
           recon_bps: Decimal = Decimal("0.5"),
           leader_slip_bps: Decimal = Decimal("0"),
           follower_slip_bps: Optional[Decimal] = None) -> Decimal:
    """一轮往返的成本门槛 `b*`（bps，占单腿名义）。

    口径与 `nt_pairs.costs.round_cost_bps` 完全一致（**不另立一套**）：
    4 次成交（leader 入场 maker、follower 入场 taker、follower 平仓 taker、
    leader 平仓 taker）+ 两腿 carry + 腿风险 `√(2/π)·σ₁m·√(t_leg/60)·P·α` + 对账摩擦。

    `leader_slip_bps` / `follower_slip_bps` 必须来自**盘口吃单**（`market_micro.walk_book`），
    不得用「2×tick」的经验代理（那会把 2-tick 滑点高估 10 倍）。
    """
    return round_cost_bps(maker, taker, qty_usd=qty_usd, hold_hours=hold_hours,
                           t_leg_s=t_leg_s, sigma_1m_bps=sigma_1m_bps,
                           p_one_leg=p_one_leg, recon_bps=recon_bps,
                           leader_slip_bps=leader_slip_bps,
                           follower_slip_bps=follower_slip_bps)
