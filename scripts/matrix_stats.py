"""纯统计与口径函数（M2 矩阵用，零 IO，可单测）。

所有函数只依赖 math/statistics，与 scripts/scan_matrix.py 的 IO 层解耦。
分位数、AR(1)、单位证据、会话分层、窗口对齐、打分公式都在这里定义，
scan_matrix 只做枚举/读盘/组装。
"""

from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# 分位数：线性插值（同 numpy 默认 / scripts/probe_basis.py 的 percentile）
# ---------------------------------------------------------------------------


def percentile(sorted_vals: Sequence[float], q: float) -> float:
    """q in [0,100]; vals must be pre-sorted ascending. Linear interpolation."""
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    if n == 1:
        return float(sorted_vals[0])
    k = (n - 1) * q / 100.0
    f = math.floor(k)
    c = min(f + 1, n - 1)
    if f == c:
        return float(sorted_vals[f])
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def p95(vals: Sequence[float]) -> float:
    return percentile(sorted(vals), 95.0)


def series_stats(vals: Sequence[float]) -> dict:
    """n, mean, sd, p50, p90, p95, p99, min, max（空序列 → n=0 其余 nan）。"""
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "sd": float("nan"),
                "p50": float("nan"), "p90": float("nan"), "p95": float("nan"),
                "p99": float("nan"), "min": float("nan"), "max": float("nan")}
    s = sorted(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    return {"n": n, "mean": sum(vals) / n, "sd": sd,
            "p50": percentile(s, 50), "p90": percentile(s, 90),
            "p95": percentile(s, 95), "p99": percentile(s, 99),
            "min": s[0], "max": s[-1]}


# ---------------------------------------------------------------------------
# AR(1) / 半衰期
# ---------------------------------------------------------------------------


def ar1_rho(x: Sequence[float]) -> float:
    """OLS AR(1) 系数（不去均值平移，与 probe_basis 同口径）。n<2 → nan。"""
    n = len(x)
    if n < 2:
        return float("nan")
    mu = sum(x) / n
    num = sum((x[i] - mu) * (x[i + 1] - mu) for i in range(n - 1))
    den = sum((v - mu) ** 2 for v in x)
    if den == 0:
        return float("nan")
    return num / den


def half_life_hours(rho: float, interval: str = "1h") -> float:
    """AR(1) 半衰期（小时）。rho<=0 → nan；rho>=1 → inf。interval: '1m'|'1h'。"""
    if math.isnan(rho):
        return float("nan")
    if rho <= 0:
        return float("nan")
    if rho >= 1:
        return float("inf")
    h = -math.log(2) / math.log(rho)
    if interval == "1m":
        h = h / 60.0
    return h


def effective_sample_factor(n: int, rho: float) -> float:
    """k = 1 + 1/sqrt(ESS)，ESS = n(1-rho)/(1+rho)（probe_basis 同口径）。"""
    if n < 1 or math.isnan(rho) or rho >= 1 or rho <= -1:
        return float("nan")
    ess = n * (1 - rho) / (1 + rho)
    if ess <= 0:
        return float("nan")
    return 1 + 1 / math.sqrt(ess)


# ---------------------------------------------------------------------------
# 单位证据（ratio_evidence）与配对判定
# ---------------------------------------------------------------------------


def ratio_evidence(prices_a: Sequence[float], prices_b: Sequence[float]) -> dict:
    """同桶 P_B/P_A 的 mean/sd/cv/n。cv=sd/mean。空 → n=0, 其余 nan。"""
    rs = [b / a for a, b in zip(prices_a, prices_b) if a]
    n = len(rs)
    if n == 0:
        return {"mean": float("nan"), "sd": float("nan"), "cv": float("nan"), "n": 0}
    mean = sum(rs) / n
    sd = statistics.stdev(rs) if n > 1 else 0.0
    cv = abs(sd / mean) if mean else float("nan")
    return {"mean": mean, "sd": sd, "cv": cv, "n": n}


def unit_verdict(ev: dict) -> tuple[bool, str | None]:
    """(same_unit, reason)。cv<=0.005 且 |mean-1|<=0.05 才可配对。"""
    mean, cv = ev.get("mean", float("nan")), ev.get("cv", float("nan"))
    n = ev.get("n", 0)
    if n == 0:
        return False, "no_ratio_samples"
    if math.isnan(mean) or math.isnan(cv):
        return False, "ratio_nan"
    if cv > 0.005:
        return False, "unit_mismatch_cv"
    if abs(mean - 1.0) > 0.05:
        return False, "unit_mismatch_mean"
    return True, None


# ---------------------------------------------------------------------------
# 会话分层
# ---------------------------------------------------------------------------


def bucket_sessions(ts_ms_list: Sequence[int], classify_fn) -> dict[str, int]:
    """各会话采样数（classify_fn: ms -> SessionInfo）。"""
    out: dict[str, int] = {"pre": 0, "rth": 0, "post": 0, "closed": 0}
    for t in ts_ms_list:
        s = classify_fn(t).session
        out[s] = out.get(s, 0) + 1
    out["total"] = sum(v for k, v in out.items() if k != "total")
    return out


def session_split(mean_rth: float, mean_closed: float, sd: float) -> float:
    """|mean_rth - mean_closed| / sd；sd<=0 或 nan → nan。"""
    if any(math.isnan(v) for v in (mean_rth, mean_closed, sd)):
        return float("nan")
    if sd <= 0:
        return float("nan")
    return abs(mean_rth - mean_closed) / sd


# ---------------------------------------------------------------------------
# 窗口对齐（缺桶计数）
# ---------------------------------------------------------------------------


def align_buckets(map_a: dict, map_b: dict, drop_labels: Sequence[str] = ("ok",)) -> dict:
    """map_*: {t_ms: close}。返回 common/aligned/missing_a/missing_b + 对齐后序列。

    missing_a: B 有 A 无的桶数（记 'dropped' 的来源）；同 probe_basis 口径：
    任一侧缺桶 → 丢该桶并计数。
    """
    ka, kb = set(map_a), set(map_b)
    common = sorted(ka & kb)
    return {
        "common": common,
        "n_common": len(common),
        "missing_a": len(kb - ka),
        "missing_b": len(ka - kb),
    }


def window_slice(map_ts_vals: dict, start_ms: int, end_ms: int) -> dict:
    """截取 [start_ms, end_ms] 内的桶（含端点）。"""
    return {t: v for t, v in map_ts_vals.items() if start_ms <= t <= end_ms}


# ---------------------------------------------------------------------------
# b*（六项）与滑点/深度
# ---------------------------------------------------------------------------


def half_spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    if mid <= 0:
        return float("nan")
    return (ask - bid) / mid * 1e4 / 2


def book_depth_usd(levels: Sequence[tuple[float, float]], mid: float,
                   side: str, bps: float) -> float:
    """levels: [(px, sz)]（sz 为 base 单位或张数换算后的 base）。side='bid' 求
    mid*(1-bps) 之上的买深度, 'ask' 求 mid*(1+bps) 之下的卖深度。"""
    if side == "bid":
        lo = mid * (1 - bps / 1e4)
        return sum(px * sz for px, sz in levels if px >= lo)
    hi = mid * (1 + bps / 1e4)
    return sum(px * sz for px, sz in levels if px <= hi)


def vwap_fill(levels: Sequence[tuple[float, float]], qty: float) -> tuple[float, bool]:
    """按档位吃 qty（base）, 返回 (vwap, fillable)。levels 按价格有利方向排序。"""
    rem = qty
    cost = 0.0
    for px, sz in levels:
        take = min(rem, sz)
        cost += take * px
        rem -= take
        if rem <= 0:
            return cost / qty, True
    if rem <= 0:
        return cost / qty, True
    return (cost / (qty - rem)) if (qty - rem) > 0 else float("nan"), False


def slip_bps(levels: Sequence[tuple[float, float]], mid: float, qty_usd: float) -> dict:
    """qty_usd 名义额 → base 量 → 吃单 VWAP 相对 mid 偏离（bps）。"""
    if mid <= 0 or not levels:
        return {"slip_bps": float("nan"), "fillable": False}
    qty_base = qty_usd / mid
    vwap, ok = vwap_fill(levels, qty_base)
    if not ok or math.isnan(vwap):
        return {"slip_bps": float("nan"), "fillable": False}
    return {"slip_bps": (vwap / mid - 1) * 1e4, "fillable": True}


def leg_risk_bps(sigma_1m_bps: float, t_leg_s: float, p_leg: float = 0.30) -> float:
    """sqrt(2/pi)*sigma_1m*sqrt(t/60)*P*0.5（两腿摊销）。"""
    if math.isnan(sigma_1m_bps) or sigma_1m_bps <= 0:
        return float("nan")
    return math.sqrt(2 / math.pi) * sigma_1m_bps * math.sqrt(t_leg_s / 60.0) * p_leg * 0.5


def b_star_bps(items: dict) -> float:
    """六项求和：fees + half_spreads + slippage + carry + leg_risk + recon。"""
    total = 0.0
    for v in items.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if math.isnan(float(v)):
                return float("nan")
            total += float(v)
    return total


# ---------------------------------------------------------------------------
# 打分（spec §6）
# ---------------------------------------------------------------------------


def edge_ratio(p95: float, k_b_star: float) -> float:
    if any(math.isnan(v) for v in (p95, k_b_star)) or k_b_star <= 0:
        return float("nan")
    return (p95 - k_b_star) / k_b_star


def capacity_ok(cap_usd: float, min_tier_usd: float = 500.0) -> bool:
    if math.isnan(cap_usd):
        return False
    return cap_usd >= 2 * min_tier_usd


def rank_score(edge_ratio_v: float, cap_usd: float, session_split_v: float,
               min_tier_usd: float = 500.0) -> float:
    """rank = edge_ratio × min(1, cap/5000) / (1 + session_split)。nan 安全。"""
    if math.isnan(edge_ratio_v) or math.isnan(cap_usd):
        return float("nan")
    sp = session_split_v if not math.isnan(session_split_v) else 0.0
    return edge_ratio_v * min(1.0, cap_usd / 5000.0) / (1 + max(sp, 0.0))
