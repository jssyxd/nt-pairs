#!/usr/bin/env python
"""高频采样：同一时刻的「盘口中点基差」与「最新成交价基差」，用来判定事件级回放里的
捕获到底是可成交的相对价格回归，还是成交打印（print）与盘口（book）不同步造成的假象。

每次采样同时取：
  - HL   `l2Book` 的 top-of-book mid（L2 可成交价）
  - Lighter `orderBookOrders` 的 top-of-book mid（L2 可成交价）
  - HL   `recentTrades` 最新一笔成交价（K 线 close 用的就是这个）
  - Lighter `recentTrades` 最新一笔成交价（同上）

输出：每行一个 JSON 到 `data/recheck/quote_samples_<SYM>.jsonl`

用法：python scripts/recheck_quote_sample.py --symbol SOXL --coin xyz:SOXL --market 197 --samples 200 --interval 3
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, payload: dict | None = None) -> object:
    if payload is None:
        req = urllib.request.Request(url, headers=UA)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as fh:
        return json.load(fh)


def sample(coin: str, market: int) -> dict | None:
    try:
        hb = _get(HL, {"type": "l2Book", "coin": coin})
        hbid = float(hb["levels"][0][0]["px"])
        hask = float(hb["levels"][1][0]["px"])
        hmid = (hbid + hask) / 2
        ht = sorted(_get(HL, {"type": "recentTrades", "coin": coin}),
                    key=lambda t: int(t["time"]))
        hpx = float(ht[-1]["px"])
        # HL recentTrades 的 "time" 是毫秒
        hage = (time.time() * 1000.0 - int(ht[-1]["time"])) / 1000.0

        lb = _get(f"{LT}/orderBookOrders?market_id={market}&limit=50")
        bids = sorted(((float(x["price"]), float(x["remaining_base_amount"]))
                       for x in lb.get("bids", [])), reverse=True)
        asks = sorted(((float(x["price"]), float(x["remaining_base_amount"]))
                       for x in lb.get("asks", [])))
        if not bids or not asks:
            return None
        lmid = (bids[0][0] + asks[0][0]) / 2
        lt = sorted(_get(f"{LT}/recentTrades?market_id={market}&limit=100").get("trades", []),
                    key=lambda t: int(t["timestamp"]))
        lpx = float(lt[-1]["price"])
        lage = time.time() - int(lt[-1]["timestamp"]) / 1000
        return {
            "ts": time.time(),
            "h_mid": hmid, "h_bid": hbid, "h_ask": hask,
            "h_spread_bps": (hask - hbid) / hmid * 1e4,
            "h_print": hpx, "h_print_age_s": hage,
            "l_mid": lmid, "l_bid": bids[0][0], "l_ask": asks[0][0],
            "l_spread_bps": (asks[0][0] - bids[0][0]) / lmid * 1e4,
            "l_print": lpx, "l_print_age_s": lage,
            "basis_book_bps": (lmid / hmid - 1) * 1e4,
            "basis_print_bps": (lpx / hpx - 1) * 1e4,
            "hl_mid_minus_print_bps": (hpx / hmid - 1) * 1e4,
            "lt_mid_minus_print_bps": (lpx / lmid - 1) * 1e4,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ts": time.time(), "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SOXL")
    ap.add_argument("--coin", default="xyz:SOXL")
    ap.add_argument("--market", type=int, default=197)
    ap.add_argument("--samples", type=int, default=200)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "recheck", f"quote_samples_{args.symbol}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for i in range(args.samples):
            rec = sample(args.coin, args.market)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if i % 10 == 0:
                print(f"  [{i+1}/{args.samples}]", flush=True)
            time.sleep(args.interval)
    print(f"[写出] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
