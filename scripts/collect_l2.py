#!/usr/bin/env python
"""L2 采集器：为「成交率 / markout 标定」与最终 EV 验证准备原始数据。

为什么需要它：
- HL `candleSnapshot` 单次最多约 500 根 → **拿不到 30 天的 1m/5m 序列**（无分页）；
- Lighter `/candles` 可分页，但两侧必须同粒度 → 需要自己按固定间隔采样；
- 没有逐笔 L2 历史，就无法标定 `p_fill`、队列长度与成交后的 markout，
  而这三项正是「97% 胜率」能否成立的最后一道验证。

采集内容（每 tick、每场所）：
- 盘口快照（top N 档：价格、数量、订单数）
- 逐笔成交（时间、价格、数量、主动方向）—— 只记录**上一次轮询之后新增**的成交
- 周期性：mark / mid / funding / OI / 24h 量

输出：`data/collector/<venue>/<YYYYMMDD>.jsonl`（每行一条快照，原始字段全保留）。
只用公开端点、无密钥；断线重试与退避；磁盘占用可控（默认 1 Hz、top 20 档）。

用法：
    python scripts/collect_l2.py --once                 # 冒烟：各场所抓一次并打印
    python scripts/collect_l2.py --interval 1 --days 30 # 正式采集
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
OKX = "https://www.okx.com/api/v5"

# 采集标的：优先四对候选 + 第一对
TARGETS = [
    ("HYPERLIQUID", "xyz:SOXL", "SOXL", None),
    ("HYPERLIQUID", "xyz:RKLB", "RKLB", None),
    ("HYPERLIQUID", "xyz:KIOXIA", "KIOXIA", None),
    ("HYPERLIQUID", "xyz:MRVL", "MRVL", None),
    ("HYPERLIQUID", "xyz:EWY", "EWY", None),
    ("HYPERLIQUID", "io:OAI", "OPENAI", None),
    ("LIGHTER", "OPENAI", "OPENAI", 192),
    ("LIGHTER", "SOXL", "SOXL", 197),
    ("LIGHTER", "RKLB", "RKLB", 186),
    ("LIGHTER", "KIOXIA", "KIOXIA", 225),
    ("LIGHTER", "MRVL", "MRVL", 174),
    ("LIGHTER", "EWY", "EWY", 166),
    ("OKX", "SOXL-USDT-SWAP", "SOXL", None),
    ("OKX", "KIOXIA-USDT-SWAP", "KIOXIA", None),
    ("OKX", "RKLB-USDT-SWAP", "RKLB", None),
    ("OKX", "NVDA-USDT-SWAP", "NVDA", None),
    ("OKX", "INTC-USDT-SWAP", "INTC", None),
]

UA = {"User-Agent": "Mozilla/5.0"}


def get_json(url: str, payload: dict | None = None, retries: int = 3) -> Any:
    last = None
    for i in range(retries):
        try:
            if payload is None:
                req = urllib.request.Request(url, headers=UA)
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (i + 1))
    raise last  # type: ignore[misc]


_SLOW_CACHE: dict[str, tuple[float, Any]] = {}
_SLOW_TTL_S = 30.0
_SLOW_POOL = ThreadPoolExecutor(max_workers=2)
_SLOW_LOCK = threading.Lock()


def slow_meta(key: str, fetch) -> Any:
    """取慢变元数据（mark/funding/OI/费率），30 秒刷一次、后台刷新。

    这些字段每个 tick 都取会把 tick 拉长 1~2 秒 → 采样率掉到 0.3Hz，
    而 HL 的逐笔成交端点只返回最近 ~10 笔 → 采样间隔越长丢的成交越多。
    """
    now = time.time()
    with _SLOW_LOCK:
        hit = _SLOW_CACHE.get(key)
        fresh = hit is not None and (now - hit[0]) < _SLOW_TTL_S
    if fresh:
        return hit[1]
    if hit is not None:
        _SLOW_POOL.submit(lambda: _refresh_slow(key, fetch))
        return hit[1]
    val = fetch()
    with _SLOW_LOCK:
        _SLOW_CACHE[key] = (now, val)
    return val


def _refresh_slow(key: str, fetch) -> None:
    try:
        val = fetch()
    except Exception:  # noqa: BLE001
        return
    with _SLOW_LOCK:
        _SLOW_CACHE[key] = (time.time(), val)


def snap_hl(coin: str, depth: int = 0) -> dict:
    book = get_json(HL, {"type": "l2Book", "coin": coin})
    ctx = slow_meta(f"hl_ctx:{coin.split(':')[0]}",
                    lambda: get_json(HL, {"type": "metaAndAssetCtxs", "dex": coin.split(":")[0]}))
    ctx_by = {u["name"]: c for u, c in zip(ctx[0]["universe"], ctx[1])}
    c = ctx_by.get(coin, {})
    bids = book["levels"][0]
    asks = book["levels"][1]
    if depth > 0:
        bids, asks = bids[:depth], asks[:depth]
    return {
        "venue": "HYPERLIQUID", "symbol": coin,
        "bids": [[float(x["px"]), float(x["sz"]), int(x.get("n", 1))] for x in bids],
        "asks": [[float(x["px"]), float(x["sz"]), int(x.get("n", 1))] for x in asks],
        "mark": float(c.get("markPx") or 0), "mid": float(c.get("midPx") or 0),
        "funding": float(c.get("funding") or 0), "oi": float(c.get("openInterest") or 0),
        "vlm24h": float(c.get("dayNtlVlm") or 0),
    }


def snap_lt(market_id: int, symbol: str, depth: int = 0) -> dict:
    lim = 50 if depth <= 0 else depth
    book = get_json(f"{LT}/orderBookOrders?market_id={market_id}&limit={lim}")
    det = slow_meta(f"lt_det:{market_id}",
                    lambda: get_json(f"{LT}/orderBookDetails?market_id={market_id}"))
    d = (det.get("order_book_details") or [{}])[0]
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if depth > 0:
        bids, asks = bids[:depth], asks[:depth]
    return {
        "venue": "LIGHTER", "symbol": symbol, "market_id": market_id,
        "bids": [[float(x["price"]), float(x["remaining_base_amount"]), 1] for x in bids],
        "asks": [[float(x["price"]), float(x["remaining_base_amount"]), 1] for x in asks],
        "mark": float(d.get("mark_price") or 0), "mid": float(d.get("last_trade_price") or 0),
        "funding": float(d.get("funding_premium_multiplier") or 0),
        "oi": float(d.get("open_interest") or 0),
        "vlm24h": float(d.get("daily_quote_token_volume") or 0),
    }


def snap_okx(inst: str, depth: int = 0) -> dict:
    sz = 50 if depth <= 0 else depth
    book = get_json(f"{OKX}/market/books?instId={inst}&sz={sz}")["data"][0]
    # 注意：OKX 的复数端点 /market/tickers?instId= 对所有 instId 返回 HTTP 400；
    # 单数 /market/ticker?instId= 正常。用单数。
    tick = get_json(f"{OKX}/market/ticker?instId={inst}")["data"][0]
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if depth > 0:
        bids, asks = bids[:depth], asks[:depth]
    return {
        "venue": "OKX", "symbol": inst,
        "bids": [[float(p), float(s), 1] for p, s, *_ in bids],
        "asks": [[float(p), float(s), 1] for p, s, *_ in asks],
        "mark": float(tick.get("last") or 0), "mid": float(tick.get("last") or 0),
        "funding": 0.0, "oi": 0.0, "vlm24h": float(tick.get("volCcy24h") or 0),
    }


def hl_trades(coin: str) -> list[dict]:
    """HL 最近的逐笔成交。端点只返回最近 ~10 笔 → 必须增量去重，否则会漏成交。
    URL 上带 cache-buster：实测不加时端点会返回**几分钟前的同一批成交**
    （连续 4 次轮询窗口完全不变），加了之后每轮都推进。
    """
    raw = get_json(f"{HL}?_={int(time.time() * 1000)}", {"type": "recentTrades", "coin": coin})
    out = []
    for r in raw or []:
        out.append({"id": str(r.get("tid")), "t": int(r.get("time") or 0),
                    "px": float(r["px"]), "sz": float(r["sz"]),
                    "side": str(r.get("side") or "")})
    return out


def lt_trades(market_id: int) -> list[dict]:
    """Lighter 最近的逐笔成交。`is_maker_ask=True` → maker 在 ask 侧 → taker 是买方。"""
    raw = get_json(f"{LT}/recentTrades?market_id={market_id}&limit=100&_={int(time.time() * 1000)}")
    out = []
    for r in (raw or {}).get("trades") or []:
        out.append({"id": str(r.get("trade_id_str") or r.get("trade_id")),
                    "t": int(r.get("timestamp") or 0), "px": float(r["price"]),
                    "sz": float(r["size"]),
                    "side": "B" if r.get("is_maker_ask") else "S"})
    return out


class TradeDeduper:
    """按成交 id 去重，只放行「上一次轮询之后新增」的成交。

    端点返回的是**滚动窗口**（HL 最近 ~10 笔、Lighter 最近 100 笔），
    每轮全量写盘会把同一批成交重复 N 次；不去重则 λ 会被放大 N 倍。
    """

    def __init__(self, keep: int = 4000) -> None:
        self.seen: set[str] = set()
        self.order: list[str] = []
        self.keep = keep
        self.last_max_t = 0

    def take(self, trades: list[dict]) -> tuple[list[dict], bool]:
        """返回 (新增成交, 是否有覆盖缺口)。

        缺口 = 本轮返回的最老成交时间 > 上一轮返回的最晚成交时间 → 中间可能有成交没抓到。
        """
        gap = False
        if trades:
            ts = [t["t"] for t in trades]
            if self.last_max_t and min(ts) > self.last_max_t + 50:
                gap = True
            self.last_max_t = max(self.last_max_t, max(ts))
        fresh = []
        for t in trades:
            if t["id"] in self.seen:
                continue
            self.seen.add(t["id"])
            self.order.append(t["id"])
            fresh.append(t)
        if len(self.order) > self.keep:
            drop = self.order[: len(self.order) - self.keep]
            self.seen.difference_update(drop)
            self.order = self.order[-self.keep:]
        return fresh, gap


def snap(venue: str, ident: str, sym: str, market_id: int | None, depth: int = 0) -> dict:
    if venue == "HYPERLIQUID":
        return snap_hl(ident, depth)
    if venue == "LIGHTER":
        return snap_lt(int(market_id), sym, depth)
    return snap_okx(ident, depth)


def trades_for(venue: str, ident: str, market_id: int | None) -> list[dict]:
    if venue == "HYPERLIQUID":
        return hl_trades(ident)
    if venue == "LIGHTER":
        return lt_trades(int(market_id))
    return []


def fetch_fast(venue: str, ident: str, sym: str, market_id: int | None, depth: int) -> dict:
    """每 tick 都要取的两类数据：盘口 + 逐笔成交（并发）。"""
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_book = ex.submit(snap, venue, ident, sym, market_id, depth)
        f_tr = ex.submit(trades_for, venue, ident, market_id)
        s = f_book.result()
        raw_tr = f_tr.result()
    return s, raw_tr


def fetch_target(target: dict, depth: int, want_trades: bool,
                 dedup: dict[str, TradeDeduper]) -> tuple[dict, list[dict], bool]:
    """取一个标的的盘口 + 新增逐笔成交（盘口与成交并发取）。"""
    venue, ident, sym, mid = target["venue"], target["ident"], target["symbol"], target.get("market_id")
    s, raw_tr = fetch_fast(venue, ident, sym, mid, depth)
    gap = False
    if want_trades:
        key = f"{venue}:{ident}"
        d = dedup.get(key)
        if d is None:
            d = dedup[key] = TradeDeduper()
        fresh, gap = d.take(raw_tr)
        s["trades"] = [[t["t"], t["px"], t["sz"], t["side"]] for t in fresh]
        s["trades_gap"] = gap
        # 端点返回窗口的覆盖范围（HL 只给最近 ~10 笔 → 用来量化「漏了多少成交」）
        if raw_tr:
            ts = [t["t"] for t in raw_tr]
            s["trades_win"] = [min(ts), max(ts), len(raw_tr)]
    return s, raw_tr, gap


def out_path(venue: str, root: str, stamp: str) -> str:
    p = os.path.join(root, venue)
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, f"{stamp}.jsonl")


def free_mb(path: str) -> float:
    """磁盘剩余空间（MB）。用于保护同机其它服务不被采集器写满磁盘。"""
    try:
        return shutil.disk_usage(os.path.dirname(os.path.abspath(path)) or "/").free / 1e6
    except OSError:
        return 1e9


def compress_day(path: str) -> None:
    """把已关闭的当日文件压成 .gz。写盘中的文件不可读（gzip 流未收尾），
    所以只有跨天关闭后的文件才压缩。"""
    try:
        with open(path, "rb") as src, gzip.open(path + ".gz", "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        os.remove(path)
    except Exception as exc:  # noqa: BLE001
        print(f"compress failed {path}: {type(exc).__name__} {exc}", file=sys.stderr)


def prune_old(root: str, keep_days: int) -> None:
    """删除超过保留期的每日文件（VPS 磁盘通常只有几 GB）。"""
    if keep_days <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y%m%d")
    if not os.path.isdir(root):
        return
    for venue in os.listdir(root):
        vd = os.path.join(root, venue)
        if not os.path.isdir(vd):
            continue
        for fn in os.listdir(vd):
            base = fn[:-6] if fn.endswith(".jsonl") else (fn[:-9] if fn.endswith(".jsonl.gz") else None)
            if base and base < cutoff:
                os.remove(os.path.join(vd, fn))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只抓一轮并打印摘要")
    ap.add_argument("--interval", type=float, default=1.0, help="采样间隔（秒）")
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--venues", default="HYPERLIQUID,LIGHTER,OKX")
    ap.add_argument("--targets", default=None, help="目标清单 JSON（见 configs/collector_targets.json）")
    ap.add_argument("--symbols", default="", help="只采这些标的（逗号分隔，按 sym 匹配）；空=全部")
    ap.add_argument("--root", default="data/collector", help="输出根目录")
    ap.add_argument("--keep-days", type=int, default=7, help="每日文件保留天数（0=不删）")
    ap.add_argument("--workers", type=int, default=10, help="并发抓取线程数")
    ap.add_argument("--depth", type=int, default=20, help="每侧保留档位（0=端点返回的全部）")
    ap.add_argument("--no-trades", action="store_true", help="不采逐笔成交（只采盘口）")
    ap.add_argument("--compress", action="store_true", help="跨天关闭后把当日文件压成 .gz")
    ap.add_argument("--min-free-mb", type=float, default=512.0,
                    help="磁盘剩余低于此值（MB）则停止采集并退出；0=不检查")
    args = ap.parse_args()
    venues = set(args.venues.split(","))
    if args.targets:
        with open(args.targets, encoding="utf-8") as fh:
            targets = json.load(fh)
    else:
        targets = TARGETS
    targets = [t if isinstance(t, dict)
               else {"venue": t[0], "ident": t[1], "symbol": t[2], "market_id": t[3]}
               for t in targets]
    sym_filter = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    targets = [t for t in targets if not sym_filter or t["symbol"].upper() in sym_filter]
    prune_old(args.root, args.keep_days)

    handles: dict[str, Any] = {}
    dedup: dict[str, TradeDeduper] = {}
    paths: dict[str, str] = {}
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    deadline = time.time() + args.days * 86400
    n_ok = n_err = 0
    t0 = time.time()
    while True:
        tick = time.time()
        stamp = datetime.now(timezone.utc).isoformat()
        if args.min_free_mb > 0:
            free = free_mb(args.root)
            if free < args.min_free_mb:
                # 先把已关闭的历史文件压掉，再判断；仍不足就退出，不能把同机
                # 的其它服务（如 ant-arb.service）写挂。
                for venue in list(handles):
                    handles[venue].close()
                    if args.compress:
                        compress_day(paths[venue])
                handles.clear()
                paths.clear()
                prune_old(args.root, 1)
                free = free_mb(args.root)
                if free < args.min_free_mb:
                    print(f"stopping: free disk {free:.0f}MB < {args.min_free_mb:.0f}MB",
                          file=sys.stderr)
                    return 2
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if today != day:
            # 跨天：关闭并压缩昨日文件
            for venue, fh in handles.items():
                fh.close()
                if args.compress:
                    compress_day(paths[venue])
            handles.clear()
            paths.clear()
            day = today
        active = [t for t in targets if t["venue"] in venues]
        # 标的之间并发：单标的时 tick 由最慢的那个请求决定
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {ex.submit(fetch_target, t, args.depth, not args.no_trades, dedup): t
                    for t in active}
            for fut, t in futs.items():
                venue, sym = t["venue"], t["symbol"]
                try:
                    s, _raw, _gap = fut.result()
                    s["ts"] = stamp
                    s["t_epoch"] = tick
                    fh = handles.get(venue)
                    if fh is None:
                        path = out_path(venue, args.root, day)
                        paths[venue] = path
                        fh = handles[venue] = open(path, "a", encoding="utf-8")
                    fh.write(json.dumps(s, ensure_ascii=False) + "\n")
                    n_ok += 1
                    if args.once:
                        b = s["bids"][0][0] if s["bids"] else 0
                        a = s["asks"][0][0] if s["asks"] else 0
                        print(f"{venue:12s} {sym:12s} bid={b} ask={a} depth_bid={s['bids'] and round(sum(p*q for p,q,_ in s['bids']),1)}")
                except Exception as exc:  # noqa: BLE001
                    n_err += 1
                    if args.once:
                        print(f"{venue:12s} {sym:12s} FAILED: {type(exc).__name__} {exc}")
        for fh in handles.values():
            fh.flush()
        if args.once:
            print(f"ok={n_ok} err={n_err}  {time.time()-t0:.1f}s")
            return 0
        if time.time() >= deadline:
            break
        time.sleep(max(0.05, args.interval - (time.time() - tick)))
    for fh in handles.values():
        fh.close()
    if args.compress:
        for venue, path in paths.items():
            compress_day(path)
    print(f"collection finished: ok={n_ok} err={n_err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())