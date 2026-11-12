#!/usr/bin/env python3
"""
M1: Venue raw data dump — OKX / Lighter / Hyperliquid
Read-only public endpoints, no keys. Rate limit ≥150ms per venue.
Retries ≤2; writes error JSON on persistent failure.
"""
import json, os, sys, time, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

ROOT = Path("data/raw")
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / "okx_books").mkdir(exist_ok=True)
(ROOT / "lighter_books").mkdir(exist_ok=True)
(ROOT / "hl_books").mkdir(exist_ok=True)
(ROOT / "candles").mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0"}
MAX_RETRIES = 2
RATE = 0.160  # seconds between requests to same venue

# ── Target symbols ──────────────────────────────────────────────
# Canonical base symbols (upper); aliases resolved below
TARGET_BASES = [
    "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "AMD",
    "INTC", "COIN", "MSTR", "HOOD", "CRCL", "ASML", "IREN", "KIOXIA",
    "KORU", "SMCI", "ARM", "AVGO", "MU", "SNDK", "TSM", "SOXL", "SMH",
    "EWY", "BABA", "PLTR", "ORCL", "NFLX", "MCD", "JPM", "XOM",
    "OPENAI", "OAI", "ANTHROPIC", "ANTH", "ANT", "SPACEX",
    "H100", "DRAM", "SKHX", "SMSN",
]

# HL-specific aliases: the HL API uses different symbols for some
# We'll try the base symbols directly, plus these aliases
HL_ALIASES = {
    "OPENAI": ["OPENAI", "OAI"],
    "OAI": ["OAI", "OPENAI"],
    "ANTHROPIC": ["ANTHROPIC", "ANTH", "ANT"],
    "ANTH": ["ANTH", "ANTHROPIC", "ANT"],
    "ANT": ["ANT", "ANTH", "ANTHROPIC"],
}

# ── Helpers ──────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self):
        self.last = {}
    def wait(self, venue: str):
        now = time.monotonic()
        if venue in self.last:
            elapsed = now - self.last[venue]
            if elapsed < RATE:
                time.sleep(RATE - elapsed)
        self.last[venue] = time.monotonic()

rl = RateLimiter()

def fetch(url, venue="default", method="GET", json_body=None, timeout=30):
    """Fetch with retry. Returns (status, data_or_error_str)."""
    for attempt in range(MAX_RETRIES + 1):
        rl.wait(venue)
        try:
            if method == "GET":
                r = requests.get(url, headers=UA, timeout=timeout)
            else:
                r = requests.post(url, headers=UA, json=json_body, timeout=timeout)
            if r.status_code == 200:
                return (200, r.json())
            # Non-200
            err = f"HTTP {r.status_code}: {r.text[:500]}"
        except Exception as e:
            err = f"Exception: {str(e)[:500]}"
        if attempt < MAX_RETRIES:
            time.sleep(1.0 * (attempt + 1))
    return (-1, err)

def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def save_error(path: Path, reason: str):
    save_json(path, {"error": reason})

# ── Report accumulator ───────────────────────────────────────────
report = {
    "okx": {"success": 0, "fail": 0, "notes": []},
    "lighter": {"success": 0, "fail": 0, "notes": []},
    "hl": {"success": 0, "fail": 0, "notes": []},
    "candles": {"okx_1h": 0, "okx_1m": 0, "lighter_1h": 0, "lighter_1m": 0,
                "hl_1h": 0, "hl_1m": 0, "errors": []},
    "overlap": {},
    "surprises": [],
    "endpoint_pitfalls": [],
    "lighter_extra_perps": [],
}

def note(venue, msg):
    report[venue]["notes"].append(msg)

def pitfall(msg):
    report["endpoint_pitfalls"].append(msg)

def surprise(msg):
    report["surprises"].append(msg)

# ══════════════════════════════════════════════════════════════════
#  PHASE 1: OKX
# ══════════════════════════════════════════════════════════════════

print("=== PHASE 1: OKX ===")

# 1a. Instruments
print("  Fetching instruments...")
status, data = fetch("https://www.okx.com/api/v5/public/instruments?instType=SWAP", "okx")
if status == 200:
    raw = data.get("data", [])
    filtered = [inst for inst in raw if inst.get("instId","").endswith("-USDT-SWAP")]
    keep_keys = ["instId","ctVal","ctValCcy","lotSz","tickSz","minSz","lever",
                 "state","listTime","instCategory","uly"]
    okx_inst = [{k: inst.get(k) for k in keep_keys} for inst in filtered]
    save_json(ROOT / "okx_instruments.json", okx_inst)
    report["okx"]["success"] += 1
    okx_ids = {inst["instId"] for inst in okx_inst}
    print(f"    Saved {len(okx_inst)} USDT-SWAP instruments")
    note("okx", f"Instruments: {len(raw)} raw → {len(okx_inst)} USDT-SWAP filtered")
else:
    save_error(ROOT / "okx_instruments.json", data)
    report["okx"]["fail"] += 1
    okx_ids = set()
    pitfall(f"OKX instruments failed: {data}")
    print(f"    FAILED: {data[:200]}")

# 1b. Tickers
print("  Fetching tickers...")
status, data = fetch("https://www.okx.com/api/v5/market/tickers?instType=SWAP", "okx")
if status == 200:
    raw = data.get("data", [])
    filtered = [t for t in raw if t.get("instId","").endswith("-USDT-SWAP")]
    keep_keys = ["instId","last","bidPx","askPx","bidSz","askSz","vol24h","volCcy24h","ts"]
    okx_tickers = [{k: t.get(k) for k in keep_keys} for t in filtered]
    save_json(ROOT / "okx_tickers.json", okx_tickers)
    report["okx"]["success"] += 1
    print(f"    Saved {len(okx_tickers)} tickers")
else:
    save_error(ROOT / "okx_tickers.json", data)
    report["okx"]["fail"] += 1
    pitfall(f"OKX tickers failed: {data}")

# 1c. Order books for target symbols
print("  Fetching order books...")
okx_target_ids = []
for sym in TARGET_BASES:
    inst_id = f"{sym}-USDT-SWAP"
    if inst_id in okx_ids:
        okx_target_ids.append(inst_id)

print(f"    {len(okx_target_ids)} target instruments found on OKX: {okx_target_ids}")
note("okx", f"Target books: {len(okx_target_ids)} matching instruments")

for inst_id in okx_target_ids:
    rl.wait("okx")
    status, data = fetch(f"https://www.okx.com/api/v5/market/books?instId={inst_id}&sz=50", "okx")
    fname = ROOT / "okx_books" / f"{inst_id}.json"
    if status == 200:
        save_json(fname, data)
        report["okx"]["success"] += 1
    else:
        save_error(fname, data)
        report["okx"]["fail"] += 1
        pitfall(f"OKX book {inst_id}: {data}")

# 1d. Candles
print("  Fetching candles...")
NOW = datetime.now(timezone.utc)
now_ms = int(NOW.timestamp() * 1000)

def fetch_okx_candles(inst_id, bar, after_ms, before_ms):
    """Paginate OKX history-candles. Returns list of [ts,o,h,l,c,vol,volCcy]."""
    all_candles = []
    page_before = str(before_ms)
    while True:
        rl.wait("okx")
        url = (f"https://www.okx.com/api/v5/market/history-candles"
               f"?instId={inst_id}&bar={bar}&limit=300&before={page_before}")
        status, data = fetch(url, "okx")
        if status != 200:
            return None, f"HTTP/error: {data}"
        candles = data.get("data", [])
        if not candles:
            break
        all_candles.extend(candles)
        if len(candles) < 300:
            break
        # OKX returns candles newest-first; last entry is oldest
        page_before = candles[-1][0]  # timestamp of the oldest candle in this page
        if len(all_candles) > 5000:
            break  # safety
    return all_candles, None

for inst_id in okx_target_ids:
    # 1h candles: 30 days
    t_30d_ago = int((NOW - timedelta(days=30)).timestamp() * 1000)
    candles_1h, err = fetch_okx_candles(inst_id, "1H", t_30d_ago, str(now_ms))
    fname_1h = ROOT / "candles" / f"okx_{inst_id}_1h.json"
    if err:
        save_error(fname_1h, err)
        report["candles"]["errors"].append(f"okx_{inst_id}_1h: {err}")
        pitfall(f"OKX candles 1h {inst_id}: {err}")
    else:
        normalized = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                        "l": float(c[3]), "c": float(c[4]), "v": float(c[5]),
                        "qv": float(c[6])} for c in candles_1h]
        save_json(fname_1h, normalized)
        report["candles"]["okx_1h"] += 1

    # 1m candles: 72 hours (3 days)
    t_3d_ago = int((NOW - timedelta(hours=72)).timestamp() * 1000)
    candles_1m, err = fetch_okx_candles(inst_id, "1m", t_3d_ago, str(now_ms))
    fname_1m = ROOT / "candles" / f"okx_{inst_id}_1m.json"
    if err:
        save_error(fname_1m, err)
        report["candles"]["errors"].append(f"okx_{inst_id}_1m: {err}")
        pitfall(f"OKX candles 1m {inst_id}: {err}")
    else:
        normalized = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                        "l": float(c[3]), "c": float(c[4]), "v": float(c[5]),
                        "qv": float(c[6])} for c in candles_1m]
        save_json(fname_1m, normalized)
        report["candles"]["okx_1m"] += 1

print(f"    OKX candles done: 1h={report['candles']['okx_1h']}, 1m={report['candles']['okx_1m']}")

# ══════════════════════════════════════════════════════════════════
#  PHASE 2: Lighter
# ══════════════════════════════════════════════════════════════════

print("=== PHASE 2: Lighter ===")

# 2a. All markets
print("  Fetching markets...")
status, data = fetch("https://mainnet.zklighter.elliot.ai/api/v1/orderBooks", "lighter")
if status == 200:
    save_json(ROOT / "lighter_markets.json", data)
    report["lighter"]["success"] += 1
    print(f"    Saved {len(data)} markets")
else:
    save_error(ROOT / "lighter_markets.json", data)
    report["lighter"]["fail"] += 1
    pitfall(f"Lighter markets failed: {data}")
    data = []

# Build lookup: symbol → market_id, and market_id → market info
lighter_symbol_to_id = {}
lighter_market_info = {}
for m in data:
    sym = (m.get("symbol") or "").upper()
    mid = m.get("market_id")
    if sym and mid is not None:
        lighter_symbol_to_id[sym] = mid
        lighter_market_info[mid] = m

# 2b. Find target symbols + stock/ETF keyword markets on Lighter
STOCK_ETF_KEYWORDS = [
    "STOCK", "ETF", "EQUITY", "PREIPO", "PRE-IPO", "IPO",
    "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "AMD",
    "INTC", "COIN", "MSTR", "HOOD", "CRCL", "ASML", "IREN",
    "ARM", "AVGO", "MU", "SNDK", "TSM", "SOXL", "SMH", "EWY",
    "BABA", "PLTR", "ORCL", "NFLX", "MCD", "JPM", "XOM",
    "OPENAI", "OAI", "ANTHROPIC", "ANTH", "ANT", "SPACEX",
    "H100", "DRAM", "SKHX", "SMSN", "KIOXIA", "KORU", "SMCI",
]

lighter_target_ids = set()

# Match by exact symbol
for sym in TARGET_BASES:
    if sym in lighter_symbol_to_id:
        lighter_target_ids.add(lighter_symbol_to_id[sym])
    # Try aliases
    for alias in HL_ALIASES.get(sym, [sym]):
        if alias in lighter_symbol_to_id:
            lighter_target_ids.add(lighter_symbol_to_id[alias])

# Match by stock/ETF keywords in any field
for m in data:
    mid = m.get("market_id")
    sym = (m.get("symbol") or "").upper()
    mtype = (m.get("market_type") or "").lower()
    desc = (m.get("description") or "").upper()
    name = (m.get("name") or "").upper()
    combined = f"{sym} {desc} {name}"
    if any(kw.upper() in combined for kw in STOCK_ETF_KEYWORDS):
        lighter_target_ids.add(mid)

print(f"    Target market_ids: {sorted(lighter_target_ids)}")
note("lighter", f"Target books: {len(lighter_target_ids)} market_ids")

# 2c. Collect extra perp markets not in target list
for m in data:
    sym = (m.get("symbol") or "").upper()
    mid = m.get("market_id")
    mtype = (m.get("market_type") or "").lower()
    if mtype == "perp" and sym not in TARGET_BASES:
        report["lighter_extra_perps"].append({"symbol": sym, "market_id": mid})

# 2d. Order books
print("  Fetching order books...")
for mid in sorted(lighter_target_ids):
    rl.wait("lighter")
    status, bdata = fetch(f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id={mid}&limit=200", "lighter")
    fname = ROOT / "lighter_books" / f"{mid}.json"
    if status == 200:
        save_json(fname, bdata)
        report["lighter"]["success"] += 1
    else:
        save_error(fname, bdata)
        report["lighter"]["fail"] += 1
        pitfall(f"Lighter book market_id={mid}: {bdata}")

# 2e. Candles
print("  Fetching candles...")
def fetch_lighter_candles(market_id, resolution, start_s, end_s):
    """Fetch Lighter candles. resolution: '60' for 1m, '3600' for 1h."""
    params = {
        "marketId": market_id,
        "resolution": resolution,
        "startTime": start_s,
        "endTime": end_s,
        "countBack": 10000,
    }
    rl.wait("lighter")
    # Build URL with query params
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://mainnet.zklighter.elliot.ai/api/v1/candles?{qs}"
    status, data = fetch(url, "lighter")
    if status != 200:
        return None, f"HTTP/error: {data}"
    return data, None

now_s = int(NOW.timestamp())
t_30d_s = int((NOW - timedelta(days=30)).timestamp())
t_3d_s = int((NOW - timedelta(hours=72)).timestamp())

for mid in sorted(lighter_target_ids):
    # 1h candles
    candles, err = fetch_lighter_candles(mid, "3600", t_30d_s, now_s)
    fname = ROOT / "candles" / f"lighter_{mid}_1h.json"
    if err:
        save_error(fname, err)
        report["candles"]["errors"].append(f"lighter_{mid}_1h: {err}")
        pitfall(f"Lighter candles 1h market_id={mid}: {err}")
    elif candles is None:
        save_error(fname, "Empty/null response")
        report["candles"]["errors"].append(f"lighter_{mid}_1h: empty")
    else:
        # Normalize: Lighter returns {candles: [{time, open, high, low, close, volume, quoteVolume}]}
        raw_list = candles if isinstance(candles, list) else candles.get("candles", [])
        normalized = []
        for c in raw_list:
            t = c.get("time") or c.get("t") or 0
            if isinstance(t, str):
                t = int(t)
            normalized.append({
                "t": t * 1000 if t < 1e12 else t,  # ensure ms
                "o": float(c.get("open", c.get("o", 0))),
                "h": float(c.get("high", c.get("h", 0))),
                "l": float(c.get("low", c.get("l", 0))),
                "c": float(c.get("close", c.get("c", 0))),
                "v": float(c.get("volume", c.get("v", 0))),
                "qv": float(c.get("quoteVolume", c.get("V", c.get("qv", 0)))),
            })
        save_json(fname, normalized)
        report["candles"]["lighter_1h"] += 1

    # 1m candles
    candles, err = fetch_lighter_candles(mid, "60", t_3d_s, now_s)
    fname = ROOT / "candles" / f"lighter_{mid}_1m.json"
    if err:
        save_error(fname, err)
        report["candles"]["errors"].append(f"lighter_{mid}_1m: {err}")
        pitfall(f"Lighter candles 1m market_id={mid}: {err}")
    elif candles is None:
        save_error(fname, "Empty/null response")
        report["candles"]["errors"].append(f"lighter_{mid}_1m: empty")
    else:
        raw_list = candles if isinstance(candles, list) else candles.get("candles", [])
        normalized = []
        for c in raw_list:
            t = c.get("time") or c.get("t") or 0
            if isinstance(t, str):
                t = int(t)
            normalized.append({
                "t": t * 1000 if t < 1e12 else t,
                "o": float(c.get("open", c.get("o", 0))),
                "h": float(c.get("high", c.get("h", 0))),
                "l": float(c.get("low", c.get("l", 0))),
                "c": float(c.get("close", c.get("c", 0))),
                "v": float(c.get("volume", c.get("v", 0))),
                "qv": float(c.get("quoteVolume", c.get("V", c.get("qv", 0)))),
            })
        save_json(fname, normalized)
        report["candles"]["lighter_1m"] += 1

print(f"    Lighter candles done: 1h={report['candles']['lighter_1h']}, 1m={report['candles']['lighter_1m']}")

# ══════════════════════════════════════════════════════════════════
#  PHASE 3: Hyperliquid
# ══════════════════════════════════════════════════════════════════

print("=== PHASE 3: Hyperliquid ===")

HL_URL = "https://api.hyperliquid.xyz/info"

# 3a. perpDexs
print("  Fetching perpDexs...")
status, data = fetch(HL_URL, "hl", "POST", {"type": "perpDexs"})
if status == 200:
    # Filter null elements
    filtered = [d for d in data if d is not None]
    save_json(ROOT / "hl_dexes.json", filtered)
    report["hl"]["success"] += 1
    print(f"    Saved {len(filtered)} dexes (filtered {len(data) - len(filtered)} nulls)")
    note("hl", f"perpDexs: {len(data)} raw → {len(filtered)} non-null")
else:
    save_error(ROOT / "hl_dexes.json", data)
    report["hl"]["fail"] += 1
    pitfall(f"HL perpDexs failed: {data}")
    filtered = []
    print(f"    FAILED")

# 3b. metaAndAssetCtxs per dex
DEX_LIST = ["xyz", "io", "vntl", "flx", "hyna", "km", "mkts", "para", "cash", "abcd"]
# Also discover dex names from perpDexs
dex_names_from_api = set()
for d in filtered:
    name = d.get("name") if isinstance(d, dict) else None
    if name:
        dex_names_from_api.add(name)

all_dexes = sorted(set(DEX_LIST) | dex_names_from_api)
print(f"  Dexes to query: {all_dexes}")

hl_asset_map = {}  # dex → set of symbols (upper)

for dex in all_dexes:
    rl.wait("hl")
    status, mdata = fetch(HL_URL, "hl", "POST", {"type": "metaAndAssetCtxs", "dex": dex})
    fname = ROOT / f"hl_meta_{dex}.json"
    if status == 200:
        save_json(fname, mdata)
        report["hl"]["success"] += 1
        # Build asset map
        universe = mdata[0] if isinstance(mdata, list) and len(mdata) > 0 else {}
        assets = universe.get("universe", [])
        if isinstance(assets, list):
            for a in assets:
                sym = (a.get("name") if isinstance(a, dict) else str(a)).upper()
                hl_asset_map.setdefault(dex, set()).add(sym)
        print(f"    {dex}: {len(hl_asset_map.get(dex, set()))} assets")
    else:
        save_error(fname, mdata)
        report["hl"]["fail"] += 1
        pitfall(f"HL meta {dex}: {mdata}")
        print(f"    {dex}: FAILED")

# 3c. l2Book for target symbols
print("  Fetching l2Books...")
# For each target symbol, try each dex where it might exist
tried_hl_books = set()
hl_book_success = 0
hl_book_fail = 0

for base_sym in TARGET_BASES:
    aliases = HL_ALIASES.get(base_sym, [base_sym])
    for alias in aliases:
        for dex in all_dexes:
            coin = f"{dex}:{alias}"
            if coin in tried_hl_books:
                continue
            # Check if this dex has this asset
            dex_assets = hl_asset_map.get(dex, set())
            if alias not in dex_assets and base_sym not in dex_assets:
                continue  # skip if not in universe
            tried_hl_books.add(coin)
            rl.wait("hl")
            status, bdata = fetch(HL_URL, "hl", "POST", {"type": "l2Book", "coin": coin})
            fname = ROOT / "hl_books" / f"{coin.replace(':', '_')}.json"
            if status == 200:
                save_json(fname, bdata)
                hl_book_success += 1
                report["hl"]["success"] += 1
            else:
                save_error(fname, bdata)
                hl_book_fail += 1
                report["hl"]["fail"] += 1
                # Only log if it seemed like it should exist
                if alias in dex_assets:
                    pitfall(f"HL l2Book {coin} (in universe but failed): {bdata}")

# Also try explicit known pairs from the task examples
KNOWN_HL_PAIRS = [
    "xyz:NVDA", "io:OAI", "vntl:OPENAI",
]
for coin in KNOWN_HL_PAIRS:
    if coin in tried_hl_books:
        continue
    tried_hl_books.add(coin)
    rl.wait("hl")
    status, bdata = fetch(HL_URL, "hl", "POST", {"type": "l2Book", "coin": coin})
    fname = ROOT / "hl_books" / f"{coin.replace(':', '_')}.json"
    if status == 200:
        save_json(fname, bdata)
        hl_book_success += 1
        report["hl"]["success"] += 1
    else:
        save_error(fname, bdata)
        hl_book_fail += 1
        report["hl"]["fail"] += 1
        pitfall(f"HL l2Book {coin} (known pair): {bdata}")

print(f"    HL books: {hl_book_success} success, {hl_book_fail} fail")
note("hl", f"l2Books: {hl_book_success} success, {hl_book_fail} fail")

# 3d. Candles
print("  Fetching HL candles...")

def fetch_hl_candles(coin, interval, start_ms, end_ms):
    """Fetch Hyperliquid candleSnapshot."""
    rl.wait("hl")
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        }
    }
    status, data = fetch(HL_URL, "hl", "POST", body)
    if status != 200:
        return None, f"HTTP/error: {data}"
    return data, None

# For HL candles, we need to know which coins exist. Use successful l2Book queries.
hl_candle_coins = set()
for coin in tried_hl_books:
    fname = ROOT / "hl_books" / f"{coin.replace(':', '_')}.json"
    if fname.exists():
        try:
            book = json.loads(fname.read_text())
            if "error" not in book and book.get("levels"):
                hl_candle_coins.add(coin)
        except:
            pass

# Also add the known pairs
for coin in KNOWN_HL_PAIRS:
    hl_candle_coins.add(coin)

print(f"    HL candle coins to query: {sorted(hl_candle_coins)}")

for coin in sorted(hl_candle_coins):
    safe_name = coin.replace(":", "_")

    # 1h candles
    candles, err = fetch_hl_candles(coin, "1h", t_30d_ms := int((NOW - timedelta(days=30)).timestamp() * 1000), now_ms)
    fname = ROOT / "candles" / f"hl_{safe_name}_1h.json"
    if err:
        save_error(fname, err)
        report["candles"]["errors"].append(f"hl_{safe_name}_1h: {err}")
        pitfall(f"HL candles 1h {coin}: {err}")
    elif candles is None or (isinstance(candles, list) and len(candles) == 0):
        save_error(fname, "Empty response or no candles")
        report["candles"]["errors"].append(f"hl_{safe_name}_1h: empty")
    else:
        # HL returns list of {t, o, h, l, c, v, ...}
        raw_list = candles if isinstance(candles, list) else []
        normalized = [{
            "t": int(c.get("t", 0)),
            "o": float(c.get("o", 0)),
            "h": float(c.get("h", 0)),
            "l": float(c.get("l", 0)),
            "c": float(c.get("c", 0)),
            "v": float(c.get("v", 0)),
        } for c in raw_list]
        save_json(fname, normalized)
        report["candles"]["hl_1h"] += 1

    # 1m candles
    candles, err = fetch_hl_candles(coin, "1m", t_3d_ms := int((NOW - timedelta(hours=72)).timestamp() * 1000), now_ms)
    fname = ROOT / "candles" / f"hl_{safe_name}_1m.json"
    if err:
        save_error(fname, err)
        report["candles"]["errors"].append(f"hl_{safe_name}_1m: {err}")
        pitfall(f"HL candles 1m {coin}: {err}")
    elif candles is None or (isinstance(candles, list) and len(candles) == 0):
        save_error(fname, "Empty response or no candles")
        report["candles"]["errors"].append(f"hl_{safe_name}_1m: empty")
    else:
        raw_list = candles if isinstance(candles, list) else []
        normalized = [{
            "t": int(c.get("t", 0)),
            "o": float(c.get("o", 0)),
            "h": float(c.get("h", 0)),
            "l": float(c.get("l", 0)),
            "c": float(c.get("c", 0)),
            "v": float(c.get("v", 0)),
        } for c in raw_list]
        save_json(fname, normalized)
        report["candles"]["hl_1m"] += 1

print(f"    HL candles done: 1h={report['candles']['hl_1h']}, 1m={report['candles']['hl_1m']}")

# ══════════════════════════════════════════════════════════════════
#  Cross-venue overlap analysis
# ══════════════════════════════════════════════════════════════════

print("=== Cross-venue analysis ===")

# OKX symbols (strip -USDT-SWAP)
okx_bases = set()
for iid in okx_ids:
    if iid.endswith("-USDT-SWAP"):
        okx_bases.add(iid[:-len("-USDT-SWAP")])

# Lighter symbols
lighter_bases = set(lighter_symbol_to_id.keys())

# HL symbols (from all dexes)
hl_bases = set()
for dex, syms in hl_asset_map.items():
    hl_bases |= syms

target_set = set(TARGET_BASES)
target_set_no_aliases = {s for s in TARGET_BASES if s not in {"OAI", "ANTH", "ANT"}}

# Find overlap
okx_target = okx_bases & target_set
lighter_target = lighter_bases & target_set
hl_target = hl_bases & target_set

all_three = okx_target & lighter_target & hl_target
okx_lighter = (okx_target & lighter_target) - hl_target
okx_hl = (okx_target & hl_target) - lighter_target
lighter_hl = (lighter_target & hl_target) - okx_target
okx_only = okx_target - lighter_target - hl_target
lighter_only = lighter_target - okx_target - hl_target
hl_only = hl_target - okx_target - lighter_target

report["overlap"] = {
    "all_three": sorted(all_three),
    "okx_lighter": sorted(okx_lighter),
    "okx_hl": sorted(okx_hl),
    "lighter_hl": sorted(lighter_hl),
    "okx_only": sorted(okx_only),
    "lighter_only": sorted(lighter_only),
    "hl_only": sorted(hl_only),
    "total_targets": len(target_set),
    "found_on_any": len(okx_target | lighter_target | hl_target),
}

print(f"    All three: {sorted(all_three)}")
print(f"    OKX+Lighter: {sorted(okx_lighter)}")
print(f"    OKX+HL: {sorted(okx_hl)}")
print(f"    Lighter+HL: {sorted(lighter_hl)}")

# ══════════════════════════════════════════════════════════════════
#  Generate report
# ══════════════════════════════════════════════════════════════════

print("=== Generating report ===")

REPORT_DIR = Path(".herd-dispatch/jobs/venue-dump")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

lines = []
lines.append("# M1 Venue Raw Data Dump — Report")
lines.append(f"Generated: {NOW.isoformat()}\n")

lines.append("## 1. File counts by venue\n")
lines.append("| Venue | Success | Fail |")
lines.append("|-------|---------|------|")
for v in ["okx", "lighter", "hl"]:
    lines.append(f"| {v} | {report[v]['success']} | {report[v]['fail']} |")

lines.append(f"\n### Candles\n")
lines.append(f"- OKX 1h: {report['candles']['okx_1h']}, 1m: {report['candles']['okx_1m']}")
lines.append(f"- Lighter 1h: {report['candles']['lighter_1h']}, 1m: {report['candles']['lighter_1m']}")
lines.append(f"- HL 1h: {report['candles']['hl_1h']}, 1m: {report['candles']['hl_1m']}")
if report["candles"]["errors"]:
    lines.append(f"- Candle errors: {len(report['candles']['errors'])}")
    for e in report["candles"]["errors"]:
        lines.append(f"  - {e}")

lines.append("\n## 2. Cross-venue overlap\n")
ov = report["overlap"]
lines.append(f"Target symbols: {ov['total_targets']} | Found on ≥1 venue: {ov['found_on_any']}\n")
lines.append(f"### All three venues: {len(ov['all_three'])}")
lines.append(", ".join(ov["all_three"]) if ov["all_three"] else "(none)")
lines.append(f"\n### Two-venue overlap")
lines.append(f"- OKX + Lighter: {', '.join(ov['okx_lighter']) if ov['okx_lighter'] else '(none)'}")
lines.append(f"- OKX + HL: {', '.join(ov['okx_hl']) if ov['okx_hl'] else '(none)'}")
lines.append(f"- Lighter + HL: {', '.join(ov['lighter_hl']) if ov['lighter_hl'] else '(none)'}")
lines.append(f"\n### Single venue only")
lines.append(f"- OKX only: {', '.join(ov['okx_only']) if ov['okx_only'] else '(none)'}")
lines.append(f"- Lighter only: {', '.join(ov['lighter_only']) if ov['lighter_only'] else '(none)'}")
lines.append(f"- HL only: {', '.join(ov['hl_only']) if ov['hl_only'] else '(none)'}")

lines.append("\n## 3. Endpoint pitfalls\n")
if report["endpoint_pitfalls"]:
    for p in report["endpoint_pitfalls"]:
        lines.append(f"- {p}")
else:
    lines.append("(none)")

lines.append("\n## 4. Surprises\n")
if report["surprises"]:
    for s in report["surprises"]:
        lines.append(f"- {s}")
else:
    lines.append("(none)")

lines.append("\n## 5. Lighter extra perp markets (not in target list)\n")
if report["lighter_extra_perps"]:
    lines.append("| Symbol | market_id |")
    lines.append("|--------|-----------|")
    for m in sorted(report["lighter_extra_perps"], key=lambda x: x["symbol"]):
        lines.append(f"| {m['symbol']} | {m['market_id']} |")
else:
    lines.append("(none)")

lines.append("\n## 6. Notes\n")
for v in ["okx", "lighter", "hl"]:
    if report[v]["notes"]:
        lines.append(f"### {v}")
        for n in report[v]["notes"]:
            lines.append(f"- {n}")

report_path = REPORT_DIR / "report.md"
report_path.write_text("\n".join(lines), encoding="utf-8")
print(f"Report saved to {report_path}")

# Save raw report JSON for debugging
(REPORT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))

print("\n=== DONE ===")
print(f"Report: {report_path}")