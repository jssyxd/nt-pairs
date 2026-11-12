#!/usr/bin/env python3
"""io:OAI (Hyperliquid Entropy io-dex) <-> Lighter OPENAI (market_id 192) basis probe.

Stdlib only (urllib). Public endpoints, no keys. Writes JSON to
data/basis_ioOAI_lighterOPENAI.json and prints a report.

Verified working endpoints (2026-11-11, see report):
  HL:
    POST https://api.hyperliquid.xyz/info
      {"type":"candleSnapshot","req":{"coin":"io:OAI","interval":"1h"|"1m",
                                      "startTime":<ms>,"endTime":<ms>}}
      {"type":"l2Book","coin":"io:OAI"}
      {"type":"fundingHistory","coin":"io:OAI","startTime":<ms>}
      {"type":"metaAndAssetCtxs","dex":"io"}     -> [meta, assetCtxs]
      {"type":"meta","dex":"io"}                  -> deployerFeeScale per asset
      {"type":"perpDexs"}                          -> io dex entry
  Lighter:
    GET https://mainnet.zklighter.elliot.ai/api/v1/candles
        ?market_id=192&resolution=1h|1m&start_timestamp=<SEC>&end_timestamp=<SEC>&count_back=<n>
      (resolution is a STRING like "1h"; timestamps SECONDS; max 500/call;
       count>500 or other names 400 "invalid param")
    GET .../api/v1/orderBookOrders?market_id=192&limit=100
      (limit only valid name; >~200 -> 400)
    GET .../api/v1/orderBooks?market_id=192        -> market meta incl OI
    GET .../api/v1/funding-rates?market_id=192     -> rates incl "lighter" entry
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HL_INFO = "https://api.hyperliquid.xyz/info"
LT_BASE = "https://mainnet.zklighter.elliot.ai/api/v1/"
LT_MARKET_ID = 192
HL_COIN = "io:OAI"

UA = {"User-Agent": "nt-pairs-probe/1.0 (stdlib)"}

# Assumption knobs (documented in report)
EXPECTED_HOLD_HOURS = 1.0   # carry horizon: one funding hour on HL
T_LEG_S = (2, 30)           # leg-risk horizons, seconds
P_1LEG = 0.30               # probability the follower leg fills late/partial
RECON_FRICTION_BPS = 0.5    # reconciliation/ops friction
MIN_NOTIONAL_USD = 10.0     # Lighter min_quote_amount; HL has no USD min


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def http_get_json(url: str, params: dict | None = None, timeout: float = 30.0):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body), round(time.time() - t0, 2)


def http_post_json(url: str, body: dict, timeout: float = 30.0):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={**UA, "Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    return json.loads(raw), round(time.time() - t0, 2)


# ----------------------------------------------------------------------------
# Data fetchers
# ----------------------------------------------------------------------------

def fetch_hl_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """interval in {'1h','1m'}. Returns [{t, T, o, c, h, l, v, n}] strings."""
    out = []
    cur = start_ms
    # candleSnapshot caps at 5000; chunk defensively at 2000
    while cur < end_ms:
        chunk_end = min(cur + 2000 * _interval_ms(interval), end_ms)
        d, _ = http_post_json(HL_INFO, {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval,
                    "startTime": cur, "endTime": chunk_end},
        })
        out.extend(d)
        cur = chunk_end
    # dedupe by t
    seen = {}
    for c in out:
        seen[int(c["t"])] = c
    return [seen[t] for t in sorted(seen)]


def _interval_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    return {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit] * n


def fetch_lt_candles(market_id: int, resolution: str, start_s: int,
                     end_s: int) -> list[dict]:
    """Pages /candles 500 at a time backwards. Zero-volume minutes are omitted
    by the API (docs), so buckets absent here are absent upstream."""
    step_ms = _interval_ms(resolution)
    collected: dict[int, dict] = {}
    end = end_s
    while True:
        # window must exceed 1 bucket so end > start after truncation
        span = max(2 * step_ms // 1000, 600)
        params = {"market_id": market_id, "resolution": resolution,
                  "start_timestamp": max(start_s, end - span),
                  "end_timestamp": end, "count_back": 500}
        d, _ = http_get_json(LT_BASE + "candles", params)
        rows = d.get("c", [])
        if not rows:
            break
        for c in rows:
            collected[int(c["t"])] = c
        oldest = min(int(c["t"]) for c in rows) // 1000
        if oldest <= start_s or len(rows) < 500:
            break
        end = oldest
        if end <= start_s:
            break
    return [collected[t] for t in sorted(collected)
            if start_s * 1000 <= t <= end_s * 1000]


def fetch_lt_book(market_id: int, limit: int = 100) -> dict:
    return http_get_json(LT_BASE + "orderBookOrders",
                         {"market_id": market_id, "limit": limit})[0]


def fetch_hl_book(coin: str) -> dict:
    return http_post_json(HL_INFO, {"type": "l2Book", "coin": coin})[0]


# ----------------------------------------------------------------------------
# Statistics (pure, unit-testable)
# ----------------------------------------------------------------------------

def percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile on a pre-sorted list, q in [0,100]."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q / 100.0
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def ar1_rho(x: list[float]) -> float:
    """OLS AR(1) coefficient of a series (no constant demeaning shift)."""
    if len(x) < 3:
        return float("nan")
    x0, x1 = x[:-1], x[1:]
    n = len(x0)
    mx = sum(x0) / n
    my = sum(x1) / n
    num = sum((x0[i] - mx) * (x1[i] - my) for i in range(n))
    den = sum((v - mx) ** 2 for v in x0)
    if den == 0:
        return float("nan")
    return num / den


def half_life_hours(rho: float) -> float:
    if math.isnan(rho) or rho <= 0 or rho >= 1:
        return float("inf") if rho >= 1 and not math.isnan(rho) else float("nan")
    return -math.log(2) / math.log(rho)


def effective_sample_factor(n: int, rho: float) -> float:
    """k = 1 + 1/sqrt(ESS), ESS = n(1-rho)/(1+rho)."""
    if n <= 0 or math.isnan(rho) or rho <= -1 or rho >= 1:
        return float("nan")
    ess = n * (1 - rho) / (1 + rho)
    if ess <= 0:
        return float("nan")
    return 1 + 1 / math.sqrt(ess)


def half_spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 1e4 / 2


def slip_2tick_bps(tick: float, mid: float) -> float:
    """IOC sweeping 2 ticks of distance from touch, in bps of mid."""
    return 2 * tick / mid * 1e4


def leg_risk_bps(sigma_1m_bps: float, t_leg_s: float) -> float:
    """sqrt(2/pi) * sigma_1m * sqrt(t_leg/60) * P_1leg * 0.5 (both legs amortized)."""
    return math.sqrt(2 / math.pi) * sigma_1m_bps * math.sqrt(t_leg_s / 60.0) * P_1LEG * 0.5


def book_depth_usd(levels: list[tuple[float, float]], mid: float,
                   side: str, bps: float) -> float:
    """levels: [(px, sz)] resting. side 'bid' sums bids within bps below mid."""
    if side == "bid":
        return sum(px * sz for px, sz in levels
                   if px >= mid * (1 - bps / 1e4))
    return sum(px * sz for px, sz in levels
               if px <= mid * (1 + bps / 1e4))


def analyze(basis_bps: list[float]) -> dict:
    """All per-series stats. Pure."""
    absb = sorted(abs(b) for b in basis_bps)
    n = len(absb)
    rho = ar1_rho(basis_bps)
    return {
        "n": n,
        "mean_bps": sum(basis_bps) / n,
        "std_bps": statistics.pstdev(basis_bps),
        "p50_abs_bps": percentile(absb, 50),
        "p90_abs_bps": percentile(absb, 90),
        "p95_abs_bps": percentile(absb, 95),
        "p99_abs_bps": percentile(absb, 99),
        "ar1_rho": rho,
        "half_life_hours": half_life_hours(rho),
        "frac_abs_ge_100_bps": sum(1 for b in absb if b >= 100) / n,
        "frac_abs_ge_200_bps": sum(1 for b in absb if b >= 200) / n,
        "frac_abs_ge_300_bps": sum(1 for b in absb if b >= 300) / n,
    }


def decide(stats: dict, b_star: float, k: float, depth_ok: bool,
           min_notional: float, depths: dict) -> dict:
    """tradable = p95 >= k*b* AND both-leg depth >= 10 x min_notional."""
    reasons = []
    p95 = stats["p95_abs_bps"]
    thresh = k * b_star
    if not (p95 >= thresh):
        reasons.append(f"p95 |basis| {p95:.1f} bps < k*b* {thresh:.1f} bps "
                       f"(k={k:.2f}, b*={b_star:.1f})")
    for leg in ("hl", "lt"):
        bid = depths[leg]["bid_25bps_usd"]
        ask = depths[leg]["ask_25bps_usd"]
        need = 10 * min_notional
        if min(bid, ask) < need:
            reasons.append(
                f"{leg} ±25bps depth bid/ask {bid:.0f}/{ask:.0f} USD < "
                f"10*min_notional {need:.0f} USD")
    tradable = not reasons and stats["n"] >= 30
    if stats["n"] < 30:
        reasons.append(f"only n={stats['n']} aligned buckets")
    return {"tradable": tradable, "threshold_k_b_star_bps": thresh,
            "reasons": reasons}


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--out", default="data/basis_ioOAI_lighterOPENAI.json")
    args = ap.parse_args()

    now_s = int(time.time())
    now_ms = now_s * 1000
    start_ms = now_ms - int(args.days * 86400 * 1000)
    start_s = start_ms // 1000
    fetched_at = utcnow_iso()
    sources: dict[str, dict] = {}
    warnings: list[str] = []

    # ---- 1h candles both venues ------------------------------------------
    hl_c = fetch_hl_candles(HL_COIN, "1h", start_ms, now_ms)
    sources["hl_candles"] = {
        "url": HL_INFO, "method": "POST",
        "body": {"type": "candleSnapshot", "req": {
            "coin": HL_COIN, "interval": "1h",
            "startTime": "<start_ms>", "endTime": "<end_ms>"}},
        "fetched_at_utc": fetched_at, "rows": len(hl_c)}
    lt_c = fetch_lt_candles(LT_MARKET_ID, "1h", start_s, now_s)
    sources["lt_candles"] = {
        "url": LT_BASE + "candles?market_id=192&resolution=1h"
              "&start_timestamp=<SEC>&end_timestamp=<SEC>&count_back=500",
        "fetched_at_utc": fetched_at, "rows": len(lt_c)}

    hl_map = {int(c["t"]): float(c["c"]) for c in hl_c}
    lt_map = {int(c["t"]): float(c["c"]) for c in lt_c}
    common = sorted(set(hl_map) & set(lt_map))
    dropped_hl = len(set(hl_map) - set(lt_map))
    dropped_lt = len(set(lt_map) - set(hl_map))
    basis = [(lt_map[t] / hl_map[t] - 1) * 1e4 for t in common]
    ratio_mean = sum(lt_map[t] / hl_map[t] for t in common) / len(common)

    stats = analyze(basis)

    # ---- 1m basis sigma for leg risk --------------------------------------
    lt_1m = fetch_lt_candles(LT_MARKET_ID, "1m", now_s - 3 * 86400, now_s)
    hl_1m = fetch_hl_candles(HL_COIN, "1m", (now_s - 3 * 86400) * 1000, now_ms)
    lt1 = {int(c["t"]): float(c["c"]) for c in lt_1m}
    hl1 = {int(c["t"]): float(c["c"]) for c in hl_1m}
    c1 = sorted(set(lt1) & set(hl1))
    b1 = [lt1[t] / hl1[t] - 1 for t in c1 if hl1[t] > 0]
    diffs = [b1[i + 1] - b1[i] for i in range(len(b1) - 1)]
    sigma_1m_bps = statistics.pstdev(diffs) * 1e4 if len(diffs) > 30 else float("nan")

    # ---- live books ---------------------------------------------------------
    hl_book = fetch_hl_book(HL_COIN)
    hb = [(float(l["px"]), float(l["sz"])) for l in hl_book["levels"][0]]
    ha = [(float(l["px"]), float(l["sz"])) for l in hl_book["levels"][1]]
    h_bid, h_ask = hb[0][0], ha[0][0]
    h_mid = (h_bid + h_ask) / 2

    lt_book = fetch_lt_book(LT_MARKET_ID)
    lt_asks = sorted(
        ((float(o["price"]), float(o["remaining_base_amount"]))
         for o in lt_book.get("asks", [])))
    lt_bids = sorted(
        ((float(o["price"]), float(o["remaining_base_amount"]))
         for o in lt_book.get("bids", [])), reverse=True)
    l_bid, l_ask = lt_bids[0][0], lt_asks[0][0]
    l_mid = (l_bid + l_ask) / 2

    sources["hl_l2book"] = {"url": HL_INFO, "method": "POST",
                            "body": {"type": "l2Book", "coin": HL_COIN},
                            "fetched_at_utc": utcnow_iso()}
    sources["lt_book"] = {
        "url": LT_BASE + "orderBookOrders?market_id=192&limit=100",
        "fetched_at_utc": utcnow_iso()}

    # ---- fees --------------------------------------------------------------
    # io dex deployerFeeScale (fee = base taker 4.5 / maker 1.5 bps * scale)
    meta_io, _ = http_post_json(HL_INFO, {"type": "meta", "dex": "io"})
    uni = {u["name"]: u for u in meta_io["universe"]}
    scale = float(uni[HL_COIN].get("deployerFeeScale", "nan"))
    hl_taker_bps = 4.5 * scale
    hl_maker_bps = 1.5 * scale

    # ---- funding -----------------------------------------------------------
    fh, _ = http_post_json(HL_INFO, {"type": "fundingHistory", "coin": HL_COIN,
                                     "startTime": now_ms - 7 * 86400 * 1000})
    recent = fh[-24:] if len(fh) >= 24 else fh
    hl_funding_hourly = abs(sum(float(f["fundingRate"]) for f in recent) / len(recent)) \
        if recent else float("nan")

    lt_fr, _ = http_get_json(LT_BASE + "funding-rates", {"market_id": LT_MARKET_ID})
    lt_rate = next((e["rate"] for e in lt_fr.get("funding_rates", [])
                    if e.get("exchange") == "lighter" and e.get("market_id") == LT_MARKET_ID),
                   None)

    # ---- market meta / OI / volume ------------------------------------------
    mm, _ = http_post_json(HL_INFO, {"type": "metaAndAssetCtxs", "dex": "io"})
    hl_meta, hl_ctxs = mm[0], mm[1]
    idx = next(i for i, u in enumerate(hl_meta["universe"])
               if u["name"] == HL_COIN)
    ctx = hl_ctxs[idx]

    lt_market, _ = http_get_json(LT_BASE + "orderBooks",
                                 {"market_id": LT_MARKET_ID})
    lt_m = lt_market["order_books"][0]

    # 24h volume Lighter: sum of V (quote USD) over last 24h of 1m candles
    lt_24h_usd = sum(float(c.get("V", 0)) for c in lt_1m
                     if int(c["t"]) >= (now_s - 86400) * 1000)

    # ---- cost b* (six items, bps) --------------------------------------------
    hs_hl = half_spread_bps(h_bid, h_ask)
    hs_lt = half_spread_bps(l_bid, l_ask)
    # ticks: HL 5-significant-digit rule at ~1360 -> 0.1; Lighter 0.01
    hl_tick = 0.1
    lt_tick = 0.01
    # verify hl tick from book ladder
    if len(ha) > 1:
        gaps = [round(ha[i + 1][0] - ha[i][0], 6) for i in range(min(5, len(ha) - 1))]
        cand = [g for g in gaps if g > 0]
        if cand:
            hl_tick = min(cand)
    slip_hl = slip_2tick_bps(hl_tick, h_mid)
    slip_lt = slip_2tick_bps(lt_tick, l_mid)
    carry_bps = hl_funding_hourly * 1e4 * EXPECTED_HOLD_HOURS
    leg2 = leg_risk_bps(sigma_1m_bps, T_LEG_S[0])
    leg30 = leg_risk_bps(sigma_1m_bps, T_LEG_S[1])

    b_star_taker = (hl_taker_bps + 0.0) + (hs_hl + hs_lt) / 2 * 2 / 2 + \
        slip_hl + slip_lt + carry_bps + leg2 + RECON_FRICTION_BPS
    # simpler, legible definition used for the verdict:
    # fee + half-spread (both legs, as cost of crossing once) + slip + carry + leg-risk + friction
    b_star_items = {
        "fees_bps": {"hl_taker": hl_taker_bps, "hl_maker": hl_maker_bps,
                     "lt_taker": 0.0, "lt_maker": 0.0,
                     "note": "io dex deployerFeeScale=%.2f applied to base 4.5/1.5" % scale},
        "half_spread_bps": {"hl": hs_hl, "lt": hs_lt, "used_sum_half": hs_hl + hs_lt},
        "slippage_2tick_bps": {"hl": slip_hl, "lt": slip_lt,
                               "hl_tick": hl_tick, "lt_tick": lt_tick},
        "carry_bps": {"hl_funding_hourly_abs": hl_funding_hourly,
                      "expected_hold_hours": EXPECTED_HOLD_HOURS,
                      "carry_bps": carry_bps},
        "leg_risk_bps": {"sigma_1m_bps": sigma_1m_bps,
                         "t2s": leg2, "t30s": leg30,
                         "formula": "sqrt(2/pi)*sigma_1m*sqrt(t/60)*0.5*P_1leg"},
        "recon_friction_bps": RECON_FRICTION_BPS,
    }
    b_star = (hl_taker_bps
              + (hs_hl + hs_lt)
              + slip_hl + slip_lt
              + carry_bps
              + leg2
              + RECON_FRICTION_BPS)

    # ---- k ------------------------------------------------------------------
    k = effective_sample_factor(stats["n"], stats["ar1_rho"])

    # ---- depth ---------------------------------------------------------------
    depths = {}
    for bps in (25, 50):
        depths.setdefault("hl", {})[f"bid_{bps}bps_usd"] = book_depth_usd(hb, h_mid, "bid", bps)
        depths["hl"][f"ask_{bps}bps_usd"] = book_depth_usd(ha, h_mid, "ask", bps)
        depths.setdefault("lt", {})[f"bid_{bps}bps_usd"] = book_depth_usd(lt_bids, l_mid, "bid", bps)
        depths["lt"][f"ask_{bps}bps_usd"] = book_depth_usd(lt_asks, l_mid, "ask", bps)

    # ---- BBO crossable margins ------------------------------------------------
    edge_hl_bid_lt_ask = (h_bid - l_ask) / ((h_bid + l_ask) / 2) * 1e4
    edge_lt_bid_hl_ask = (l_bid - h_ask) / ((l_bid + h_ask) / 2) * 1e4

    verdict = decide(stats, b_star, k, True, MIN_NOTIONAL_USD, depths)

    result = {
        "pair": "HL io:OAI <-> Lighter OPENAI(192)",
        "generated_at_utc": utcnow_iso(),
        "window": {"days": args.days, "start_utc": datetime.fromtimestamp(
            start_ms / 1000, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(now_s, tz=timezone.utc).isoformat()},
        "alignment": {"common_1h_buckets": len(common),
                      "dropped_hl_only": dropped_hl,
                      "dropped_lt_only": dropped_lt,
                      "note": "buckets missing on either side are dropped"},
        "unit_check": {
            "price_ratio_lt_over_hl_mean": ratio_mean,
            "verdict": ("same reference: ratio ~1 (OAI is 1 share/unit on both)"
                        if 0.5 < ratio_mean < 2 else
                        "DIFFERENT unit scale — check contract multiplier"),
            "hl_mid": h_mid, "lt_mid": l_mid},
        "basis_stats_bps": stats,
        "basis_sample_first12": [round(b, 2) for b in basis[:12]],
        "costs_b_star": {"items": b_star_items, "total_bps": b_star,
                         "total_with_30s_leg_bps": b_star - leg2 + leg30},
        "k": k, "k_b_star_bps": k * b_star,
        "depth": depths,
        "bbo_edges_bps": {
            "hl_bid_minus_lt_ask": edge_hl_bid_lt_ask,
            "lt_bid_minus_hl_ask": edge_lt_bid_hl_ask,
            "hl_bid": h_bid, "hl_ask": h_ask, "lt_bid": l_bid, "lt_ask": l_ask},
        "live_spreads_bps": {"hl": (h_ask - h_bid) / h_mid * 1e4,
                              "lt": (l_ask - l_bid) / l_mid * 1e4},
        "market_state": {
            "hl": {"funding_now": float(ctx["funding"]),
                    "open_interest_units": float(ctx["openInterest"]),
                    "day_notional_vlm_usd": float(ctx["dayNtlVlm"]),
                    "mark_px": float(ctx["markPx"]), "oracle_px": float(ctx["oraclePx"]),
                    "premium": float(ctx["premium"])},
            "lt": {"funding_rate_lighter": lt_rate,
                    "open_interest_units": float(lt_m["open_interest"]),
                    "open_interest_usd": float(lt_m["open_interest_usd"]),
                    "vol_24h_usd_from_1m_candles": lt_24h_usd,
                    "maker_fee": float(lt_m["maker_fee"]),
                    "taker_fee": float(lt_m["taker_fee"]),
                   },
        },
        "verdict": verdict,
        "sources": sources,
        "warnings": warnings,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
