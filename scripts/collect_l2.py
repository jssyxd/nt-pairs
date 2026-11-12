#!/usr/bin/env python
"""L2 采集器：为「成交率 / markout 标定」与最终 EV 验证准备原始数据。

为什么需要它：
- HL `candleSnapshot` 单次最多约 500 根 → **拿不到 30 天的 1m/5m 序列**（无分页）；
- Lighter `/candles` 可分页，但两侧必须同粒度 → 需要自己按固定间隔采样；
- 没有逐笔 L2 历史，就无法标定 `p_fill`、队列长度与成交后的 markout，
  而这三项正是「97% 胜率」能否成立的最后一道验证。

采集内容（每 tick、每场所）：
- 盘口快照（top N 档：价格、数量、订单数）
- 逐笔成交（时间、价格、数量、主动方向）
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


def snap_hl(coin: str, depth: int = 0) -> dict:
    book = get_json(HL, {"type": "l2Book", "coin": coin})
    ctx = get_json(HL, {"type": "metaAndAssetCtxs", "dex": coin.split(":")[0]})
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
    det = get_json(f"{LT}/orderBookDetails?market_id={market_id}")
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


def snap(venue: str, ident: str, sym: str, market_id: int | None, depth: int = 0) -> dict:
    if venue == "HYPERLIQUID":
        return snap_hl(ident, depth)
    if venue == "LIGHTER":
        return snap_lt(int(market_id), sym, depth)
    return snap_okx(ident, depth)


def out_path(venue: str, root: str, day: str) -> str:
    p = os.path.join(root, venue)
    os.makedirs(p, exist_ok=True)
    return os.path.join(p, f"{day}.jsonl")


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
    ap.add_argument("--depth", type=int, default=0, help="每侧保留档位（0=端点返回的全部）")
    ap.add_argument("--compress", action="store_true", help="跨天关闭后把当日文件压成 .gz")
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
    paths: dict[str, str] = {}
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    deadline = time.time() + args.days * 86400
    n_ok = n_err = 0
    t0 = time.time()
    while True:
        tick = time.time()
        stamp = datetime.now(timezone.utc).isoformat()
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
        for t in targets:
            venue, ident, sym, mid = t["venue"], t["ident"], t["symbol"], t.get("market_id")
            if venue not in venues:
                continue
            try:
                s = snap(venue, ident, sym, mid, args.depth)
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