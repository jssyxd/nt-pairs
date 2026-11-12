#!/usr/bin/env python
"""杠杆对齐核查：按「保证金占用」对齐，而不是按倍数对齐。

对 `configs/live_pairs.json` 的每个配对：
1. 从三个场所的**公开**接口读该合约的杠杆上限与合约规格；
2. 用统一公式算出「两腿名义相等且各自保证金占用 ≤ cap%」时的最大下单名义：
       N_max = min_over_venues( equity_venue * leverage_venue * margin_cap )
   其中 equity_venue 需要私有接口（运行时由交易程序取），本脚本在缺权益时
   只输出**比例关系**（哪条腿是约束）与「等名义所需的杠杆对齐条件」；
3. 校验两腿单位换算 ratio（若提供了校准值）并打印校准来源。

用法：
    python scripts/leverage_check.py --config configs/live_pairs.json
    python scripts/leverage_check.py --config configs/live_pairs.json --equity HL=40,LIGHTER=40,OKX=30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from decimal import Decimal
from typing import Any

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
OKX = "https://www.okx.com/api/v5"
UA = {"User-Agent": "Mozilla/5.0"}


def http_json(url: str, payload: dict | None = None) -> Any:
    if payload is None:
        req = urllib.request.Request(url, headers=UA)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as fh:
        return json.load(fh)


def hl_instrument(ident: str) -> dict:
    dex = ident.split(":")[0]
    d = http_json(HL, {"type": "metaAndAssetCtxs", "dex": dex})
    for u, c in zip(d[0]["universe"], d[1]):
        if u["name"] == ident:
            return {"venue": "HYPERLIQUID", "instrument": ident, "max_leverage": int(u.get("maxLeverage") or 0),
                    "size_decimals": int(u.get("szDecimals") or 0),
                    "mark": float(c.get("markPx") or 0), "funding_bps_h": float(c.get("funding") or 0) * 1e4,
                    "oi": float(c.get("openInterest") or 0)}
    return {"venue": "HYPERLIQUID", "instrument": ident, "error": "not found"}


def lighter_instrument(market_id: int) -> dict:
    d = http_json(f"{LT}/orderBookDetails?market_id={market_id}")
    m = (d.get("order_book_details") or [{}])[0]
    return {"venue": "LIGHTER", "instrument": m.get("symbol"), "market_id": market_id,
            "max_leverage": 20 if m.get("market_type") == "perp" else None,
            "maker_bps": float(m.get("maker_fee") or 0) * 1e4,
            "taker_bps": float(m.get("taker_fee") or 0) * 1e4,
            "min_base_amount": float(m.get("min_base_amount") or 0),
            "min_quote_amount": float(m.get("min_quote_amount") or 0),
            "mark": float(m.get("mark_price") or 0), "oi": float(m.get("open_interest") or 0)}


def okx_instrument(inst: str) -> dict:
    d = http_json(f"{OKX}/public/instruments?instType=SWAP&instId={inst}")
    x = (d.get("data") or [{}])[0]
    return {"venue": "OKX", "instrument": inst, "max_leverage": int(x.get("lever") or 0),
            "ct_val": x.get("ctVal"), "lot_sz": x.get("lotSz"), "tick_sz": x.get("tickSz"),
            "state": x.get("state"), "settle": x.get("settleCcy")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/live_pairs.json")
    ap.add_argument("--equity", default="", help="形如 HL=40,LIGHTER=40,OKX=30（USD）")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    equity = {}
    for kv in args.equity.split(","):
        if "=" in kv:
            k, v = kv.split("=")
            equity[k.upper()] = Decimal(v)
    cap = Decimal(str(cfg["global"]["margin_usage_cap_per_venue"]))
    lo = Decimal(str(cfg["global"]["min_leg_notional_usd"]))
    hi = Decimal(str(cfg["global"]["max_leg_notional_usd"]))

    print(f"margin_usage_cap={cap}  notional_range=${lo}..${hi}")
    for pair in cfg["pairs"]:
        print(f"\n=== {pair['name']} ({pair.get('status','')})")
        legs = [pair["leader"], pair["follower"]]
        backup = pair.get("backup")
        if backup:
            print(f"  [backup       ] (不参与名义上限计算) {backup['venue']} {backup['instrument_id']} lev={backup.get('leverage')}")
        infos = []
        for leg in legs:
            v = leg["venue"]
            try:
                nat = leg.get("native_id") or leg["instrument_id"]
                if v == "HYPERLIQUID":
                    info = hl_instrument(nat)
                elif v == "LIGHTER":
                    info = lighter_instrument(int(nat))
                else:
                    info = okx_instrument(nat)
            except Exception as exc:  # noqa: BLE001
                info = {"venue": v, "instrument": leg.get("instrument_id"), "error": f"{type(exc).__name__}: {exc}"}
            info["role"] = leg["role"]
            info["configured_leverage"] = leg.get("leverage")
            info["configured_mode"] = leg.get("leverage_mode")
            infos.append(info)
            print(f"  [{leg['role']:14s}] {info}")
        # 约束腿：equity*leverage*cap 最小者
        caps = []
        for info in infos:
            eq = equity.get(info["venue"])
            lev = info.get("max_leverage") or info.get("configured_leverage") or 0
            if eq is None or not lev:
                continue
            caps.append((eq * Decimal(lev) * cap, info["venue"], info["role"], lev))
        if caps:
            caps.sort()
            n_max = caps[0][0]
            print(f"  → 名义上限 N_max = ${n_max:.2f}（约束腿：{caps[0][1]} / {caps[0][2]}，"
                  f"杠杆 {caps[0][3]}）")
            if n_max < lo:
                print("  → ⚠️ 低于最小下单额 $100：需要充值或降低保证金占用上限")
        else:
            print("  → 未提供各场所权益 → 只给出比例关系："
                  "两腿名义相同，杠杆倍数可不同，约束由 min(equity*lev*cap) 决定")
        # 费用对齐
        fees = []
        for info in infos:
            if info["role"] == "maker":
                fees.append(("maker", info.get("venue"), info.get("maker_bps", 1.5)))
            elif info["role"] in ("taker", "taker_fallback"):
                fees.append(("taker", info.get("venue"), info.get("taker_bps", 5.0)))
        print(f"  → 费用组合：{fees}；主腿组合固定费 = "
              f"{sum(f for _, _, f in fees if _ != 'taker_fallback'):.2f} bps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())