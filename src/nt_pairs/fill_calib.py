"""成交率标定：把排队模型（`market_micro.fill_probability`）与 L2 回放实测并排比较。

为什么要有这个模块：`docs/2026-11-14-CHD_SCREENING.md` §9 报的净 EV（+2.80 bps/事件）
里的 `p_fill` 来自排队模型本身，**没有用真实成交校验过**；而 `docs/FILL_MODEL.md` §3
已经证明同一模型两次运行能对同一标的给出 3% 与 97% 两个结果（差异全在贴价队列的单次快照）。

这里的做法是：把采集器采到的 L2 + 逐笔成交回放成「合成挂单」，用
**队列消耗**（累计穿过我们价位的成交量 vs 队头 + 我们的量）判定成交，
再和模型在同一窗口、同一 λ、同一队列、同一名义下给出的概率对比。

模块内全是纯函数（零 IO、零网络），可直接离线单测。
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from nt_pairs import market_micro as mm

__all__ = [
    "Snap",
    "canonical_symbol",
    "load_venue",
    "best_level",
    "sample_queue",
    "through_trades",
    "cum_volume",
    "fill_outcome",
    "model_p_fill",
    "model_p_fill_any",
    "compound_p_fill",
    "queue_bucket",
    "mid_of",
]


@dataclass(frozen=True)
class Snap:
    """一个时刻的盘口快照 + 该时刻新出现的逐笔成交。

    `bids` / `asks` 是 **价格 → 该价 resting 总量** 的映射：
    HL 的 `l2Book` 本来就是按价位聚合的；Lighter 的 `orderBookOrders` 返回**逐笔挂单**，
    必须按价格求和，否则「贴价队列」会被算成单笔挂单量（低估一个数量级）。
    """

    t: float
    venue: str
    symbol: str
    bids: dict[float, float]
    asks: dict[float, float]
    trades: tuple[tuple[int, float, float, str], ...]
    gap: bool
    win: tuple[int, int, int] | None = None   # 端点返回的成交窗口 (最老, 最新, 笔数)


def canonical_symbol(venue: str, sym: str) -> str:
    """把各场所的 instrument id 归一到同一个基础标的。

    HL 的 HIP-3 符号带 deployer 前缀（`xyz:SOXL` / `io:OAI`），OKX 带 `-USDT-SWAP` 后缀，
    Lighter 是裸名 —— **不能按字符串求交集**（历史 bug：配对恒为空 → 输出 0 个标的）。
    """
    s = str(sym or "").strip()
    if venue.upper() == "HYPERLIQUID":
        if ":" in s:
            s = s.split(":", 1)[1]
    elif venue.upper() == "OKX":
        for suf in ("-USDT-SWAP", "-USDC-SWAP", "-USD-SWAP"):
            if s.endswith(suf):
                s = s[: -len(suf)]
                break
    return s.upper()


def _agg(levels: Iterable[Sequence[float]]) -> dict[float, float]:
    """把价位列表聚合成 {价格: 数量}（数量求和）。"""
    out: dict[float, float] = {}
    for lv in levels or ():
        try:
            px = float(lv[0])
            sz = float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if px <= 0 or sz <= 0:
            continue
        out[px] = out.get(px, 0.0) + sz
    return out


def _iter_records(root: str, venue: str) -> Iterable[dict]:
    """读 `data/collector/<venue>/*.jsonl(.gz)` 的全部行。"""
    import gzip

    d = os.path.join(root, venue)
    if not os.path.isdir(d):
        return
    for fn in sorted(os.listdir(d)):
        if not fn.endswith((".jsonl", ".jsonl.gz")):
            continue
        path = os.path.join(d, fn)
        try:
            if fn.endswith(".gz"):
                fh = gzip.open(path, "rt", encoding="utf-8", errors="replace")
            else:
                fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def load_venue(root: str, venue: str, symbols: set[str] | None = None,
               max_rows: int | None = None, band_bps: float = 25.0) -> dict[str, list[Snap]]:
    """载入一个场所的采集结果 → {symbol: [Snap...]}（按时间升序）。

    `max_rows` 限制**每个标的**保留的快照数（内存保护：VPS 只有 960MB，
    历史上把 5 天 JSONL 全读进内存被 OOM kill 过）。
    `band_bps` 把离 mid 太远的档位丢掉（Lighter 的整本簿有 ~350 档，
    留着只是吃内存；贴价档与 ±25bps 带内的深度才是分析要用的）。
    """
    out: dict[str, list[Snap]] = {}
    for r in _iter_records(root, venue):
        sym = canonical_symbol(venue, str(r.get("symbol") or ""))
        if not sym:
            continue
        if symbols and sym.upper() not in symbols:
            continue
        trades = tuple((int(t[0]), float(t[1]), float(t[2]), str(t[3]))
                       for t in (r.get("trades") or []))
        bids, asks = _agg(r.get("bids") or []), _agg(r.get("asks") or [])
        if band_bps > 0 and bids and asks:
            mid = (max(bids) + min(asks)) / 2.0
            lo, hi = mid * (1 - band_bps / 1e4), mid * (1 + band_bps / 1e4)
            bids = {p: q for p, q in bids.items() if p >= lo}
            asks = {p: q for p, q in asks.items() if p <= hi}
        s = Snap(t=float(r.get("t_epoch") or 0.0), venue=str(r.get("venue") or venue),
                 symbol=sym, bids=bids, asks=asks,
                 trades=trades, gap=bool(r.get("trades_gap")),
                 win=tuple(r["trades_win"]) if r.get("trades_win") else None)
        lst = out.setdefault(sym, [])
        lst.append(s)
        if max_rows and len(lst) > max_rows:
            del lst[: len(lst) - max_rows]
    for sym in out:
        out[sym].sort(key=lambda x: x.t)
    return out


def mid_of(snap: Snap) -> float | None:
    if not snap.bids or not snap.asks:
        return None
    return (max(snap.bids) + min(snap.asks)) / 2.0


def best_level(snap: Snap, side: str) -> tuple[float, float] | None:
    """贴价档（买一 / 卖一）→ (价格, 该价 resting 总量)。"""
    levels = snap.bids if side == "buy" else snap.asks
    if not levels:
        return None
    px = max(levels) if side == "buy" else min(levels)
    return px, levels[px]


def sample_queue(snaps: Sequence[Snap], i: int, side: str, px: float,
                 n_samples: int = 3, min_gap_s: float = 0.2) -> tuple[list[float | None], float | None]:
    """在决策时刻**向前**（挂单前）采样贴价档位的 resting 量。

    `docs/FILL_MODEL.md` §3 的硬要求：贴价队列必须**逐笔采样 ≥3 次取中位数**，
    单次快照的队列量毫无意义（同一模型两次运行给出 3% / 97% 两个 p_fill）。

    返回 `(每次采样值, 中位数)`；某次采样时该价位不存在 → 该次记 `None`
    （价位被撤单/价格移动），中位数只取存在的样本；一个都没有 → 中位数 `None`。
    """
    samples: list[float | None] = []
    last_t = None
    for j in range(i, max(-1, i - 3 * n_samples), -1):
        if j < 0 or j >= len(snaps):
            continue
        s = snaps[j]
        if last_t is not None and (last_t - s.t) < min_gap_s - 1e-9:
            continue
        last_t = s.t
        levels = s.bids if side == "buy" else s.asks
        samples.append(levels.get(px))
        if len(samples) >= n_samples:
            break
    vals = [v for v in samples if v is not None]
    med = statistics.median(vals) if vals else None
    return samples, med


def through_trades(trades: Sequence[tuple[int, float, float, str]], t0_ms: int, t1_ms: int,
                    px: float, side: str) -> list[tuple[int, float, float, str]]:
    """窗口内**穿过我们价位**的成交。

    买侧挂单（挂在买一）→ 只有打到 `px` 或更低的成交才会消耗我们的队列；
    卖侧挂单（挂在卖一）→ 只有打到 `px` 或更高的成交才算。
    """
    out = []
    for t in trades:
        if t[0] < t0_ms or t[0] > t1_ms:
            continue
        if (side == "buy" and t[1] <= px) or (side == "sell" and t[1] >= px):
            out.append(t)
    return out


def cum_volume(trades: Sequence[tuple[int, float, float, str]]) -> float:
    """成交累计量（base unit）。"""
    return sum(t[2] for t in trades)


def fill_outcome(queue_ahead: float, our_size: float, cum: float) -> str:
    """成交状态：`none` / `partial`（被碰到但没吃满）/ `full`。

    队头被吃完 → 我们开始成交；`queue + our_size` 都被吃满 → 我们全成交。
    """
    if cum <= 0:
        return "none"
    if cum < queue_ahead:
        return "none"
    if cum < queue_ahead + our_size:
        return "partial"
    return "full"


def model_p_fill(queue_ahead: float, lam: float, dt: float, our_size: float,
                 avg_size: float) -> float:
    """仓库现用模型（Poisson 队列消耗），口径与 `scripts/chd_screen.py` 完全一致。"""
    return mm.fill_probability(queue_ahead, lam, dt, our_size, avg_size)


def model_p_fill_any(queue_ahead: float, lam: float, dt: float, avg_size: float) -> float:
    """模型变体：只要队头被打完就算成交（`need = queue_ahead`），对应实测的 `any` 口径。"""
    if dt <= 0 or lam <= 0 or avg_size <= 0:
        return 0.0
    need = max(float(queue_ahead), 0.0)
    if need <= 0:
        return 1.0
    hits = lam * dt * avg_size / need
    return 1.0 - math.exp(-hits)


def compound_p_fill(need: float, lam: float, dt: float,
                    sizes: Sequence[float], draws: int = 4000, seed: int = 7) -> float:
    """**复合 Poisson** 版本：成交笔数 N ~ Poisson(λ·dt)，每笔成交量抽自实测分布。

    与 `model_p_fill` 的差别只在模型形式：`model_p_fill` 把「需要的成交笔数」当 Poisson，
    等价于假设每笔成交量都等于均值；本函数保留了实测的单笔量分布（厚尾）。
    两者之差 = 「模型形式」造成的偏差（与 λ 口径、队列口径无关）。

    固定随机种子 → 结果可复现、可单测。
    """
    if need <= 0:
        return 1.0
    if lam <= 0 or dt <= 0 or not sizes:
        return 0.0
    rng = random.Random(seed)
    mean_n = lam * dt
    # Poisson 采样（Knuth，小 λ 足够）
    n_hit = 0
    for _ in range(draws):
        l = math.exp(-mean_n)
        k = 0
        p = 1.0
        while True:
            p *= rng.random()
            if p <= l:
                break
            k += 1
        if k == 0:
            continue
        total = 0.0
        for _ in range(k):
            total += sizes[rng.randrange(len(sizes))]
        if total >= need:
            n_hit += 1
    return n_hit / draws


def queue_bucket(q: float) -> str:
    """贴价队列分档（用于看偏差随队列规模怎么变）。"""
    if q <= 0:
        return "0"
    if q <= 5:
        return "<=5"
    if q <= 20:
        return "6-20"
    if q <= 50:
        return "21-50"
    if q <= 200:
        return "51-200"
    return ">200"
