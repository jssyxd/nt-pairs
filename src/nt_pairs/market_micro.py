"""盘口微结构：tick 反推、深度、吃单滑点、排队成交概率（纯函数，可离线单测）。

本模块对应 DECISION.md 的两条硬规则：

* tick 一律 `infer_tick(book)` 从盘口价位反推；经验规则（「$100 以下 0.1」）会把 2-tick
  滑点高估 **10 倍**（实测 32.7 bps vs 真实 0.33 bps）。
* 滑点必须来自**盘口档位**（`walk_book`），不得从价格变动反推。

零第三方依赖、零 IO：所有函数都是纯函数，测试不需要网络。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "infer_tick",
    "mid_from_book",
    "spread_bps",
    "book_depth",
    "walk_book",
    "fill_probability",
    "estimate_slippage_curve",
    "repeat_close_pct",
    "normalize_levels",
]

def _decimals(x: float) -> int:
    """float 的十进制位数（上限 8 位，避免浮点噪声）。"""
    s = repr(float(x))
    if "e" in s or "E" in s:
        return 8
    if "." not in s:
        return 0
    frac = s.split(".", 1)[1]
    return min(len(frac.rstrip("0")), 8) if frac.rstrip("0") else 0


def infer_tick(prices: Sequence[float]) -> float:
    """从盘口价位反推 tick（价格最小变动单位）。

    算法：
    1. 取相邻唯一价位的正差分，取最小值 `d_min`；
    2. 由价位的小数位数得到**网格** `grid = 10^-decimals`（价位必然是 tick 的整数倍，
       因此 `grid <= tick`）；
    3. 在 `grid` 的 1/2/5 倍数里，挑**能整除全部差分**的最大者作为 tick。

    稠密盘口（相邻档位连续）→ 返回真实 tick；稀疏盘口（价位很多但最小间隔远大于
    价格网格，例如隔档挂单）→ 返回**价格网格** `grid`。

    ⚠️ 经验规则（「$100 以下 0.1」）会高估 tick 10 倍，禁止使用。
    ⚠️ 价位极少（<10 个）时不做稀疏修正 —— 无法区分「粗 tick」与「稀疏簿」。
    """
    vals = sorted({round(float(p), 10) for p in prices if p is not None and float(p) > 0})
    if len(vals) < 2:
        return 0.0
    gaps = [b - a for a, b in zip(vals, vals[1:])]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return 0.0
    d_min = min(gaps)
    dec = max(_decimals(v) for v in vals)
    if dec <= 0:
        return float(d_min)
    grid = 10.0 ** (-dec)
    # 稀疏簿修正：价位很多、最小间隔 ≥5 倍价格网格 → 簿里跳档，真实 tick 更细
    if len(vals) >= 10 and d_min >= 5 * grid and \
            all(abs(g / grid - round(g / grid)) < 1e-6 for g in gaps):
        return grid
    return float(d_min)


def normalize_levels(levels: Iterable[Any]) -> list[tuple[float, float]]:
    """把多种价位表示统一成 `[(price, qty), ...]`。

    接受 `(px, qty)`、字典 `{"price":…, "quantity":…}`、`{"p":…, "q":…}`。
    非法/非正价位与数量直接丢弃（调用方据此判断深度是否可信）。
    """
    out: list[tuple[float, float]] = []
    for lv in levels or ():
        if isinstance(lv, Mapping):
            px = lv.get("price", lv.get("p", lv.get("px")))
            qty = lv.get("quantity", lv.get("qty", lv.get("q", lv.get("sz", lv.get("size")))))
        else:
            try:
                px, qty = lv[0], lv[1]
            except (TypeError, IndexError, KeyError):
                continue
        try:
            px = float(px)
            qty = float(qty)
        except (TypeError, ValueError):
            continue
        if px > 0 and qty > 0:
            out.append((px, qty))
    return out


def _sides(book: Any) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """从 book 取出 (bids, asks)。book 可以是 dict（`bids`/`asks`）或带属性的对象。"""
    if isinstance(book, Mapping):
        bids = book.get("bids") or book.get("bid") or ()
        asks = book.get("asks") or book.get("ask") or ()
    else:
        bids = getattr(book, "bids", ()) or ()
        asks = getattr(book, "asks", ()) or ()
    return normalize_levels(bids), normalize_levels(asks)


def mid_from_book(book: Any) -> float:
    """中间价 = (best_bid + best_ask) / 2；单边缺失时退化为另一边的最优价。"""
    bids, asks = _sides(book)
    if not bids and not asks:
        return 0.0
    bb = max(p for p, _ in bids) if bids else 0.0
    ba = min(p for p, _ in asks) if asks else 0.0
    if bb and ba:
        return (bb + ba) / 2.0
    return bb or ba


def spread_bps(book: Any) -> float:
    """买卖价差（bps，相对 mid）。"""
    bids, asks = _sides(book)
    if not bids or not asks:
        return 0.0
    bb = max(p for p, _ in bids)
    ba = min(p for p, _ in asks)
    mid = (bb + ba) / 2.0
    if mid <= 0:
        return 0.0
    return (ba - bb) / mid * 10000.0


def book_depth(book: Any, mid: float, band_bps: float) -> dict[str, float]:
    """`band_bps` 带内（±band_bps，围绕 mid）的双边累计名义与数量。

    返回 `{"bid_usd", "ask_usd", "bid_qty", "ask_qty", "bid_levels", "ask_levels"}`。
    名义 = 价格 × 数量（数量单位为标的数量/base unit，不是张数）。
    """
    bids, asks = _sides(book)
    lo = mid * (1.0 - band_bps / 10000.0)
    hi = mid * (1.0 + band_bps / 10000.0)
    bid_usd = bid_qty = ask_usd = ask_qty = 0.0
    n_bid = n_ask = 0
    for px, qty in bids:
        if px < lo:
            continue
        bid_usd += px * qty
        bid_qty += qty
        n_bid += 1
    for px, qty in asks:
        if px > hi:
            continue
        ask_usd += px * qty
        ask_qty += qty
        n_ask += 1
    return {
        "bid_usd": bid_usd,
        "ask_usd": ask_usd,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "bid_levels": float(n_bid),
        "ask_levels": float(n_ask),
    }


def walk_book(side: str, levels: Sequence[Any], qty: float,
              mid: float | None = None) -> tuple[float, float, float]:
    """按档位吃单：返回 `(avg_price, slippage_bps, filled_qty)`。

    * `side="buy"` 吃卖档（asks），`side="sell"` 吃买档（bids）；
    * `levels` 为 `[(price, qty), ...]`，内部会按有利方向排序；
    * `qty` 为**标的数量**（base unit），不是名义额；
    * `slippage_bps` 相对 `mid`（给了 mid）或相对该侧最优价（没给 mid）；
    * 深度不够时 `filled_qty < qty`，调用方必须检查（不填满 → 该档不可成交）。

    滑点只能来自盘口档位，禁止从价格变动反推。
    """
    if qty <= 0:
        return 0.0, 0.0, 0.0
    lv = normalize_levels(levels)
    if not lv:
        return 0.0, 0.0, 0.0
    if side == "buy":
        lv.sort(key=lambda x: x[0])
        ref = mid if mid else lv[0][0]
    elif side == "sell":
        lv.sort(key=lambda x: -x[0])
        ref = mid if mid else lv[0][0]
    else:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    rem = float(qty)
    cost = 0.0
    for px, sz in lv:
        take = min(rem, sz)
        cost += take * px
        rem -= take
        if rem <= 1e-12:
            break
    filled = qty - rem
    if filled <= 0 or ref <= 0:
        return 0.0, 0.0, 0.0
    vwap = cost / filled
    slip = abs(vwap - ref) / ref * 10000.0
    return vwap, slip, filled


def fill_probability(queue_ahead: float, trade_rate_lambda: float, dt: float,
                      our_size: float, avg_trade_size: float | None = None) -> float:
    """贴价挂单的成交概率（Poisson 队列消耗模型，与 `docs/FILL_MODEL.md` 同口径）。

    * `queue_ahead`：**我们排在队尾**时，头上的挂单量（base unit）；
    * `trade_rate_lambda`：该价位上的成交流率（**笔/秒**，由 trades 估）；
    * `dt`：等待时长（秒）；
    * `our_size`：我们的挂单量（base unit）；
    * `avg_trade_size`：平均单笔成交量（base unit）；**未知时取 1.0**。
      调用方**必须**传实测值（实测均值通常远小于我们的单量 → 更保守）。

    模型：每笔到达的成交消耗自身大小的队列；要把 `queue_ahead + our_size` 的队列吃完，
    需要的成交笔数期望为 `(queue_ahead + our_size) / E[size]`。
    成交笔数服从 Poisson(λ·dt)，故

        hits = λ · dt · E[size] / (queue_ahead + our_size)
        P(fill) = 1 − exp(−hits)

    单调性：λ↑ / dt↑ / E[size]↑ → p↑；queue_ahead↑ / our_size↑ → p↓。

    注：**不要**用「窗口内总成交量必须 ≥ 整个队列」的指数模型
    （P = exp(−need/E[V])）——那要求整条队列在 TTL 内被吃光，实测会把 p_fill 压到 ~0，
    使所有标的的净 EV 恒为 0。本口径与仓库 `scripts/fill_model.py::p_fill` 一致。
    """
    if dt <= 0 or our_size <= 0 or trade_rate_lambda <= 0:
        return 0.0
    size = avg_trade_size if avg_trade_size and avg_trade_size > 0 else 1.0
    need = max(float(queue_ahead), 0.0) + float(our_size)
    if need <= 0:
        return 0.0
    hits = trade_rate_lambda * dt * size / need
    if hits <= 0:
        return 0.0
    p = 1.0 - math.exp(-hits)
    return 0.0 if p < 0 else (1.0 if p > 1 else p)


def estimate_slippage_curve(book_series: Sequence[Any], qty_grid: Sequence[float],
                             side: str = "buy") -> dict[float, dict[str, float]]:
    """多档名义下的滑点曲线。

    `book_series`：盘口快照序列（每个元素含 `bids`/`asks`）；
    `qty_grid`：要评估的**标的数量**网格。

    返回 `{qty: {"slippage_bps_mean", "slippage_bps_p50", "slippage_bps_p95",
    "fill_ratio", "books"}}`，其中 `fill_ratio` = 吃满该数量的盘口占比
    （深度不足的盘口按部分成交折算）。
    """
    out: dict[float, dict[str, float]] = {}
    for qty in qty_grid:
        qty = float(qty)
        slips: list[float] = []
        filled_ok = 0
        used = 0
        for book in book_series:
            bids, asks = _sides(book)
            levels = asks if side == "buy" else bids
            if not levels:
                continue
            mid = mid_from_book(book)
            _, slip, filled = walk_book(side, levels, qty, mid=mid or None)
            if filled <= 0:
                continue
            used += 1
            if filled >= qty * (1 - 1e-9):
                filled_ok += 1
                slips.append(slip)
            else:
                # 只填了一部分：按可成交比例折算滑点
                slips.append(slip * filled / qty)
        slips.sort()
        n = len(slips)
        if n == 0:
            out[qty] = {"slippage_bps_mean": 0.0, "slippage_bps_p50": 0.0,
                        "slippage_bps_p95": 0.0, "fill_ratio": 0.0, "books": 0.0}
            continue
        mean = sum(slips) / n
        p50 = slips[int(0.5 * (n - 1))]
        p95 = slips[min(n - 1, int(0.95 * (n - 1)))]
        out[qty] = {"slippage_bps_mean": mean, "slippage_bps_p50": p50,
                    "slippage_bps_p95": p95, "fill_ratio": filled_ok / n,
                    "books": float(n)}
    return out


def repeat_close_pct(prices: Sequence[float]) -> float:
    """相邻采样点数值完全相同的比例（%）：**陈旧打印**检测。

    >10% 的腿其「机械回归」是沿用旧打印造成的假象（RKLB 73%、KORU 88%、
    KIOXIA 93%）→ 不得用于 EV 结论（DECISION.md 闸门②）。
    """
    vals = [float(p) for p in prices if p is not None]
    if len(vals) < 2:
        return 0.0
    same = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
    return 100.0 * same / (len(vals) - 1)
