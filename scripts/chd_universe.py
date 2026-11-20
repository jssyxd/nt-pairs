#!/usr/bin/env python
"""Enumerate the CryptoHFTData "equity perp" universe and find cross-venue name overlaps.

For every venue it pulls the symbol list of `orderbook` and `trades`, normalizes the
venue-native symbol to a base ticker, classifies it (known stock / pre-IPO / crypto /
commodity) and reports

  * per venue: the equity-like symbols,
  * the tickers that exist on >= 3 of the venues (candidate two-leg pairs),
  * price-ratio evidence per candidate (same underlying? not assumed).

Outputs: data/chd_universe.json and docs/CHD_UNIVERSE.md.

Usage:
    python scripts/chd_universe.py                 # full run
    python scripts/chd_universe.py --no-price-evidence
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nt_pairs.chd import CHDClient, CHDNotFound  # noqa: E402

VENUES = ["hyperliquid_futures", "lighter", "okx_futures", "binance_futures"]
DATA_TYPES = ["orderbook", "trades"]

# Hyperliquid HIP-3 deployer prefixes -> (full name, category).
# Source: POST https://api.hyperliquid.xyz/info {"type":"perpDexs"} (verified 2026-11-14).
HL_DEPLOYERS = {
    "XYZ": ("XYZ (Trade.xyz)", "equity_perp"),
    "IO": ("EntropyIO", "equity_perp"),
    "VNTL": ("Ventuals", "preipo_theme_perp"),
    "KM": ("Markets by Kinetiq", "equity_perp"),
    "MKTS": ("Markets By Kinetiq", "index_perp"),
    "CASH": ("dreamcash", "tokenized_equity_perp"),
    "PARA": ("Paragon", "equity_perp"),
    "FLX": ("Felix Exchange", "mixed_equity_commodity_perp"),
    "HYNA": ("HyENA", "crypto_perp"),
}

PRE_IPO_TICKERS = {
    "OPENAI",
    "OAI",
    "ANTHROPIC",
    "ANTH",
    "SPACEX",
    "SPCX",
    "XAI",
    "GROK",
    "STRIPE",
    "ROBOT",
    "BIOTECH",
    "UNITREE",
    "MINIMAX",
    "ZHIPU",
    "SKHY",
    "GIGADEV",
    "YMTC",
    "CAMBRICON",
    "PIEVERSE",
}

# Tickers that look like equities but are crypto / commodity / FX / index contracts.
NON_EQUITY_TICKERS = {
    # crypto majors & memes
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "DOT", "LINK", "TRX",
    "TON", "SHIB", "PEPE", "FARTCOIN", "WIF", "BONK", "FLOKI", "SATS", "RATS", "BOME",
    "APT", "SUI", "SEI", "TIA", "INJ", "RNDR", "RENDER", "TAO", "JUP", "PYTH", "WLD",
    "ONDO", "ENA", "ETHFI", "JTO", "ZEC", "ETC", "XMR", "LTC", "BCH", "ICP", "FIL",
    "ATOM", "ALGO", "AAVE", "UNI", "LDO", "MKR", "CRV", "SNX", "COMP", "GMX", "ARB",
    "OP", "IMX", "STRK", "JTO", "DYM", "PORTAL", "BANANA", "MOG", "BOB", "TURBO",
    "MEME", "PEPE", "FLOKI", "BLAST", "ZRO", "W", "TNSR", "SAGA", "MANTA", "ALT",
    "AI16Z", "ZEREBRO", "BRETT", "DEGEN", "WOO", "SPELL", "BADGER", "LQTY", "GLMR",
    "ACA", "ASTR", "API3", "BAL", "BAND", "BEL", "BLUR", "BOND", "CFX", "CHESS",
    "CHR", "COW", "CVC", "DAR", "DEXE", "DF", "DODO", "DREP", "DUSK", "DYDX", "EDU",
    "ELF", "ENS", "EPS", "ERN", "FARM", "FET", "FIDA", "FLM", "FLOKI", "FOR", "FORTH",
    "FRONT", "FXS", "GAL", "GAS", "GLM", "GMX", "GNO", "GNS", "GPS", "GRT", "GTC",
    "HARD", "HFT", "HIFI", "HIVE", "HOOK", "HOT", "ICX", "ILV", "IMX", "IOST",
    "IQ", "IRIS", "JOE", "JST", "KAVA", "KDA", "KEEP", "KLAY", "KMD", "KSM", "LAZIO",
    "LINA", "LOOM", "LPT", "LQTY", "LRC", "LSK", "LUNC", "MAGIC", "MASK", "MATIC",
    "MBL", "MBOX", "MC", "MCO", "MDT", "MDX", "MEME", "METIS", "MINA", "MLN", "MOVR",
    "MTL", "MULTI", "NEBL", "NEXO", "NFP", "NMR", "NULS", "OAX", "OCEAN", "OG",
    "OHLC", "OM", "OMG", "OMNI", "ORCA", "ORDI", "ORN", "OSMO", "OXT", "PENDLE",
    "PEOPLE", "PERL", "PHA", "PHB", "PIVX", "PIXEL", "PLA", "POLS", "POLYX", "POWR",
    "PROM", "PROS", "PSG", "PUNDIX", "PYR", "QI", "QKC", "QNT", "QUICK", "RAD",
    "RARE", "RAY", "RDNT", "REI", "REQ", "REZ", "RIF", "RLC", "RNDR", "ROSE", "RPL",
    "RSR", "RVN", "SANTOS", "SCR", "SCRT", "SFP", "SHRIMPY", "SLF", "SLP", "SNM",
    "SPELL", "SRM", "SSV", "STEEM", "STG", "STMX", "STORJ", "STORM", "STRAT", "STX",
    "SUN", "SUPER", "SUSD", "SUSHI", "SXP", "SYS", "T", "TAO", "TEA", "TEL", "TFUEL",
    "THE", "THETA", "TIA", "TKO", "TLM", "TNB", "TNT", "TOKEN", "TOMO", "TORN",
    "TOWNS", "TRB", "TRIBE", "TRIG", "TRU", "TRUMP", "TRX", "TUSD", "TUSDB", "UMA",
    "UNFI", "UNI", "USTC", "VIC", "VIDT", "VITE", "VOXEL", "VTHO", "WAVES", "WAXP",
    "WBTC", "WETH", "WING", "WLD", "WOO", "WPR", "WRX", "WTC", "XEC", "XNO",
    "XRP", "XTZ", "XVG", "XVS", "YFI", "YGG", "ZEE", "ZIL", "ZRX", "SPX6900", "SPX",
    "PNUT", "SNAI", "FARTCOIN", "TRUMP", "MELT", "MOBY", "SIREN", "SPY", "QQQ",
    "GLD", "SLV", "USOIL", "BRENTOIL", "NATGAS", "WTI", "GOLD", "SILVER", "COPPER",
    "PLATINUM", "PALLADIUM", "ALUMINIUM", "WHEAT", "CORN", "SOY", "URANIUM",
    "URNM", "USA100", "USA500", "US500", "SP500", "USTECH", "SMALL2000", "BVIV",
    "USBOND", "USDE", "GOLDJM", "SILVERJM", "MAG7", "SEMIS", "INFOTECH", "DEFENSE",
    "ENERGY", "NUCLEAR", "OTHERS", "PAPER", "TOTAL2", "TREAD", "USBOND", "10Y", "2Y",
    "30Y", "EUR", "GBP", "JPY", "KRW", "VIX", "DXY", "JP225", "KR200", "GLDMINE",
    "STRC", "USAR", "SY", "BE", "BB", "HO", "MU", "MUU", "OURA", "SPCX", "JY",
}

FALLBACK_STOCK_TICKERS = {
    "AAPL", "ABBV", "ADBE", "AMD", "AMZN", "ANTH", "ARM", "ASML", "AVGO", "AXP",
    "BABA", "BAC", "BA", "BRK", "C", "CAT", "CRM", "CRWD", "CRCL", "CSCO", "CVX",
    "DIS", "DRAM", "DRV", "EEM", "F", "GE", "GM", "GOOGL", "GS", "HOOD", "INTC",
    "IONQ", "IREN", "JPM", "KO", "KWEB", "LLY", "META", "MRVL", "MSFT", "MU", "NFLX",
    "NVDA", "O", "ORCL", "PLTR", "PYPL", "QCOM", "QNT", "RDDT", "RKLB", "SMCI",
    "SNAP", "SNDK", "SOXL", "SPY", "T", "TSLA", "TSM", "UBER", "V", "WMT", "XOM",
    "XLE", "XBI", "SMH", "EWY", "EWJ", "EWT", "EWZ", "KIOXIA", "KORU", "SKHX", "SKHY",
    "GPRO", "NBIS", "XIAOMI", "TCNT", "PRL", "DRV", "MSTR", "HOOD", "SNDK", "INTC",
    "CASH", "USDE",
}

STOCK_LIST_URL = "https://www.alphavantage.co/query?function=LISTING_STATUS&apikey=demo"
SP500_LIST_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "master/data/constituents.csv"
)
CRYPTO_LIST_URL = "https://api.coinpaprika.com/v1/tickers"

STOCK_LIST_TTL_DAYS = 30


def load_stock_tickers(cache_dir: Path, refresh: bool = False) -> tuple[set[str], str]:
    """Return (known equity/ETF tickers, provenance).

    Primary source is the Alpha Vantage LISTING_STATUS dump (NASDAQ/NYSE/AMEX,
    stocks + ETFs). It is cached on disk; if the network is unavailable the
    embedded fallback list is used.
    """
    cache_file = cache_dir / "stock_tickers.json"
    if cache_file.is_file() and not refresh:
        payload = json.loads(cache_file.read_text())
        fetched = datetime.fromisoformat(payload["fetched_at"])
        if (datetime.now(timezone.utc) - fetched).days < STOCK_LIST_TTL_DAYS:
            return set(payload["tickers"]), payload["source"]

    tickers: set[str] = set()
    source = "embedded-fallback-list"
    local_csv = cache_dir / "listing_status.csv"
    if local_csv.is_file():
        tickers = _parse_listing_status(local_csv.read_text(encoding="utf-8", errors="replace"))
        if tickers:
            source = f"local:{local_csv}"
    if tickers:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source": source,
                    "tickers": sorted(tickers),
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        return tickers, source
    for url, parse in (
        (STOCK_LIST_URL, _parse_listing_status),
        (SP500_LIST_URL, _parse_sp500),
    ):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
            parsed = parse(body)
            if parsed:
                tickers = parsed
                source = url
                break
        except Exception as exc:  # network / parse problem -> try next source
            print(f"[warn] stock list fetch failed for {url}: {exc}", file=sys.stderr)
    if not tickers:
        tickers = set(FALLBACK_STOCK_TICKERS)
        source = "embedded-fallback-list"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "tickers": sorted(tickers),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return tickers, source


def _parse_listing_status(body: str) -> set[str]:
    """Alpha Vantage LISTING_STATUS CSV -> active Stock/ETF tickers."""
    tickers: set[str] = set()
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    if not rows:
        return tickers
    for row in rows[1:]:
        if len(row) < 4:
            continue
        symbol, asset_type, status = row[0], row[2], row[3]
        if status == "Active" and asset_type in ("Stock", "ETF"):
            tickers.add(symbol.strip().upper())
    return tickers


def _parse_sp500(body: str) -> set[str]:
    """S&P 500 constituents CSV -> tickers (smaller but reachable without a key)."""
    tickers: set[str] = set()
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    for row in rows[1:]:
        if row:
            tickers.add(row[0].strip().upper())
    return tickers


def load_crypto_tickers(cache_dir: Path, refresh: bool = False) -> tuple[set[str], str]:
    """Return (known crypto tickers, provenance) from the CoinPaprika ticker dump."""
    cache_file = cache_dir / "crypto_tickers.json"
    if cache_file.is_file() and not refresh:
        payload = json.loads(cache_file.read_text())
        fetched = datetime.fromisoformat(payload["fetched_at"])
        if (datetime.now(timezone.utc) - fetched).days < STOCK_LIST_TTL_DAYS:
            return set(payload["tickers"]), payload["source"]
    try:
        req = urllib.request.Request(CRYPTO_LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8")
        tickers = {t["symbol"].upper() for t in json.loads(body)}
        if tickers:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(
                    {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source": CRYPTO_LIST_URL,
                        "tickers": sorted(tickers),
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
            return tickers, CRYPTO_LIST_URL
    except Exception as exc:
        print(f"[warn] crypto list fetch failed: {exc}", file=sys.stderr)
    return set(), "unavailable"


def classify(base: str, known_stocks: set[str], known_crypto: set[str]) -> str:
    """Classify a normalized base ticker."""
    if base in known_stocks:
        return "stock_known"
    if base in PRE_IPO_TICKERS:
        return "preipo"
    if base in NON_EQUITY_TICKERS:
        return "non_equity"
    if base in known_crypto:
        return "crypto_known"
    if re.fullmatch(r"[A-Z]{1,5}", base):
        return "ticker_like"
    return "non_equity"


def normalize(exchange: str, symbol: str) -> dict:
    """Split a venue-native symbol into (base ticker, quote, deployer)."""
    base, quote, deployer = symbol, None, None
    if exchange == "hyperliquid_futures" and ":" in symbol:
        deployer, base = symbol.split(":", 1)
    elif exchange == "okx_futures":
        stem = symbol[:-5] if symbol.endswith("-SWAP") else symbol
        parts = stem.split("-")
        base = parts[0]
        quote = parts[1] if len(parts) > 1 else None
    elif exchange == "binance_futures":
        stem = symbol.split("_")[0]  # drop dated future suffix (BTCUSDT_250627)
        for suffix in ("USDT", "USDC", "FDUSD", "USD"):
            if stem.endswith(suffix) and len(stem) > len(suffix):
                base = stem[: -len(suffix)]
                quote = suffix
                break
        else:
            base = stem
    elif exchange == "lighter":
        if "_" in symbol:
            base, quote = symbol.split("_", 1)
    return {"base": base, "quote": quote, "deployer": deployer}


def price_of(path: Path, data_type: str) -> float | None:
    """Extract a comparable last price from a ticker / mark_price parquet file."""
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
    except Exception:
        return None
    column = "last_price" if data_type == "ticker" else "mark_price"
    if column not in table.column_names:
        return None
    values = [v for v in table.column(column).to_pylist() if v not in (None, "")]
    if not values:
        return None
    try:
        return float(values[-1])
    except (TypeError, ValueError):
        return None


def venue_price_data_type(exchange: str) -> str:
    return "mark_price" if exchange == "hyperliquid_futures" else "ticker"


def collect(client: CHDClient, venues, data_types):
    symbols: dict[str, dict[str, list[str]]] = {}
    counts: dict[str, dict[str, int]] = {}
    for venue in venues:
        symbols[venue] = {}
        counts[venue] = {}
        for data_type in data_types:
            syms = client.symbols(venue, data_type)
            symbols[venue][data_type] = syms
            counts[venue][data_type] = len(syms)
    return symbols, counts


def build_universe(symbols, known_stocks, known_crypto):
    per_venue = {}
    for venue, by_type in symbols.items():
        entries = []
        seen = set()
        for data_type, syms in by_type.items():
            for sym in syms:
                if sym in seen:
                    continue
                seen.add(sym)
                norm = normalize(venue, sym)
                base = norm["base"]
                kind = classify(base, known_stocks, known_crypto)
                if kind == "non_equity":
                    continue
                deployer = norm["deployer"]
                entries.append(
                    {
                        "symbol": sym,
                        "base": base,
                        "quote": norm["quote"],
                        "deployer": deployer,
                        "deployer_full_name": HL_DEPLOYERS.get(deployer, (None, None))[0]
                        if deployer
                        else None,
                        "kind": kind,
                        "data_types": sorted(
                            dt for dt, lst in by_type.items() if sym in set(lst)
                        ),
                    }
                )
        per_venue[venue] = sorted(entries, key=lambda e: (e["base"], e["symbol"]))
    return per_venue

def deployer_summary(per_venue) -> dict:
    """Per Hyperliquid HIP-3 deployer: how many equity-like symbols it carries."""
    out: dict[str, dict] = {}
    for entry in per_venue.get("hyperliquid_futures", []):
        deployer = entry.get("deployer")
        if not deployer:
            continue
        rec = out.setdefault(
            deployer,
            {
                "full_name": HL_DEPLOYERS.get(deployer, (None, None))[0],
                "category": HL_DEPLOYERS.get(deployer, (None, None))[1],
                "symbols": [],
            },
        )
        rec["symbols"].append(entry["symbol"])
    for rec in out.values():
        rec["symbols"].sort()
        rec["count"] = len(rec["symbols"])
    return out


def build_intersection(per_venue, min_venues: int):
    by_base: dict[str, dict[str, list[str]]] = {}
    kinds: dict[str, str] = {}
    for venue, entries in per_venue.items():
        for entry in entries:
            by_base.setdefault(entry["base"], {}).setdefault(venue, []).append(
                entry["symbol"]
            )
            kinds.setdefault(entry["base"], entry["kind"])
    out = []
    for base, venues in sorted(by_base.items()):
        if len(venues) < min_venues:
            continue
        out.append(
            {
                "base": base,
                "kind": kinds[base],
                "venues": {v: sorted(s) for v, s in sorted(venues.items())},
                "venue_count": len(venues),
            }
        )
    return out


def add_price_evidence(client, candidates, per_venue, hour_day, hour, max_candidates):
    """Download one price file per (candidate, venue) and compute price ratios."""
    evidence = {}
    for cand in candidates[:max_candidates]:
        base = cand["base"]
        per_venue_price = {}
        for venue, syms in cand["venues"].items():
            dt = venue_price_data_type(venue)
            for sym in syms:
                try:
                    path = client.download(venue, hour_day, hour, sym, dt)
                except (CHDNotFound, Exception):
                    continue
                px = price_of(path, dt)
                if px:
                    per_venue_price[venue] = {"symbol": sym, "price": px, "data_type": dt}
                    break
        prices = [v["price"] for v in per_venue_price.values()]
        ratios = {}
        if len(prices) >= 2:
            med = sorted(prices)[len(prices) // 2]
            for venue, v in per_venue_price.items():
                ratios[venue] = round(v["price"] / med, 6)
        flags = []
        if ratios:
            lo, hi = min(ratios.values()), max(ratios.values())
            if abs(hi - lo) > 0.05:
                flags.append("price_ratio_spread_gt_5pct")
            if any(abs(r - 1.0) > 0.05 for r in ratios.values()):
                flags.append("ratio_not_near_1")
        cand["price_evidence"] = {
            "sample_hour": f"{hour_day}/{hour:02d}",
            "per_venue": per_venue_price,
            "price_ratio_vs_median": ratios,
            "flags": flags,
        }
        evidence[base] = cand["price_evidence"]
    return evidence


def render_markdown(payload) -> str:
    lines = ["# CHD 标的宇宙（股票 / pre-IPO 永续）", ""]
    lines.append(f"生成时间：{payload['generated_at']}")
    lines.append(f"股票名单来源：{payload['sources']['stock_list']}")
    lines.append("")
    lines.append("## 各场所股票类标的数")
    lines.append("")
    lines.append("| 场所 | orderbook 符号数 | trades 符号数 | 股票类标的数 |")
    lines.append("|---|---:|---:|---:|")
    for venue, stats in payload["venue_summary"].items():
        lines.append(
            f"| {venue} | {stats['orderbook']} | {stats['trades']} | {stats['equity_like']} |"
        )
    lines.append("")
    lines.append("## 跨场所同名交集（>=3 家）")
    lines.append("")
    inter = payload["intersection"]
    lines.append(f"共 {len(inter)} 个 base ticker，覆盖 >= 3 家场所。")
    lines.append("")
    lines.append("| base | 分类 | 场所数 | 各场所符号 | 价格比（中位数基准） |")
    lines.append("|---|---|---:|---|---|")
    for item in inter:
        venues = item["venues"]
        sym_map = "; ".join(f"{v}:{'/'.join(s)}" for v, s in venues.items())
        ratios = item.get("price_evidence", {}).get("price_ratio_vs_median", {})
        ratio_txt = ", ".join(f"{v}={r}" for v, r in ratios.items()) or "-"
        lines.append(
            f"| {item['base']} | {item['kind']} | {item['venue_count']} | {sym_map} | {ratio_txt} |"
        )
    lines.append("")
    lines.append("## 每场所股票类标的清单")
    lines.append("")
    for venue, entries in payload["venues"].items():
        lines.append(f"### {venue}（{len(entries)} 个）")
        lines.append("")
        by_base: dict[str, list[str]] = {}
        kinds: dict[str, str] = {}
        for e in entries:
            by_base.setdefault(e["base"], []).append(e["symbol"])
            kinds[e["base"]] = e["kind"]
        lines.append("| base | 分类 | 场所符号 |")
        lines.append("|---|---|---|")
        for base, syms in sorted(by_base.items()):
            lines.append(f"| {base} | {kinds[base]} | {' / '.join(syms)} |")
        lines.append("")
    lines.append("")
    lines.append("## Hyperliquid HIP-3 deployer 覆盖")
    lines.append("")
    lines.append("| deployer | 名称 | 类别 | 股票类符号数 | 示例 |")
    lines.append("|---|---|---|---:|---|")
    for name, rec in sorted(payload.get("deployer_summary", {}).items()):
        sample = ", ".join(rec["symbols"][:8])
        lines.append(
            f"| {name} | {rec['full_name'] or '-'} | {rec['category'] or '-'} | "
            f"{rec['count']} | {sample} |"
        )
    lines.append("")
    lines.append("## 口径说明")
    lines.append("")
    for note in payload["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default="data/chd_cache")
    ap.add_argument("--out-json", default="data/chd_universe.json")
    ap.add_argument("--out-md", default="docs/CHD_UNIVERSE.md")
    ap.add_argument("--venues", default=",".join(VENUES))
    ap.add_argument("--data-types", default=",".join(DATA_TYPES))
    ap.add_argument("--min-venues", type=int, default=3)
    ap.add_argument("--price-evidence", dest="price_evidence", action="store_true", default=True)
    ap.add_argument("--no-price-evidence", dest="price_evidence", action="store_false")
    ap.add_argument("--max-price-evidence", type=int, default=500)
    ap.add_argument("--evidence-date", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--evidence-hour", type=int, default=None)
    ap.add_argument("--refresh-stock-list", action="store_true")
    args = ap.parse_args()

    venues = [v.strip() for v in args.venues.split(",") if v.strip()]
    data_types = [d.strip() for d in args.data_types.split(",") if d.strip()]

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    client = CHDClient(cache_dir=cache_dir)
    known_stocks, stock_source = load_stock_tickers(cache_dir, refresh=args.refresh_stock_list)
    known_crypto, crypto_source = load_crypto_tickers(cache_dir, refresh=args.refresh_stock_list)

    symbols, counts = collect(client, venues, data_types)
    per_venue = build_universe(symbols, known_stocks, known_crypto)
    inter = build_intersection(per_venue, args.min_venues)

    if args.price_evidence and inter:
        day = args.evidence_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hour = args.evidence_hour
        if hour is None:
            # walk back a few hours until at least one price file exists
            for back in range(0, 8):
                cand = datetime.fromisoformat(day) - timedelta(hours=back)
                d, h = cand.strftime("%Y-%m-%d"), cand.hour
                try:
                    client.download(
                        "lighter", d, h, "NVDA", venue_price_data_type("lighter")
                    )
                except Exception:
                    continue
                day, hour = d, h
                break
        add_price_evidence(client, inter, per_venue, day, hour, args.max_price_evidence)

    payload = {
        "deployer_summary": deployer_summary(per_venue),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "chd_api": client.base_url,
            "stock_list": stock_source,
            "crypto_list": crypto_source,
            "hl_deployers": "https://api.hyperliquid.xyz/info {type:perpDexs}",
        },
        "venue_summary": {
            venue: {
                "orderbook": counts[venue].get("orderbook", 0),
                "trades": counts[venue].get("trades", 0),
                "equity_like": len(per_venue[venue]),
            }
            for venue in venues
        },
        "venues": per_venue,
        "intersection": inter,
        "notes": [
            "base ticker 由场所原生符号归一化得到：HL 取 deployer 前缀后部分，OKX 取 -QUOTE-SWAP 前缀，"
            "Binance 去掉 USDT/USDC 等计价后缀与 _YYMMDD 交割后缀，Lighter 去掉 _USDC 后缀。",
            "同名不代表同一底层：HL 同一 ticker 存在多个 deployer（xyz / io / flx / cash / km / para / vntl），"
            "属于不同发行方与不同定价口径；price_evidence 仅给出同小时价格比，不构成等价证明。",
            "kind=ticker_like 表示形态像美股代码但不在 Alpha Vantage 上市名单中，需人工复核。",
            "pre-ipo 名单基于 HL/其他场所的显式命名（OPENAI / ANTHROPIC / OAI / ANTH / SPACEX 等）。",
            "NON_EQUITY_TICKERS 为手写排除表（加密货币 / 商品 / 外汇 / 指数合约），存在漏判可能。",
        ],
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {out_json} and {out_md}")
    print(f"intersection size: {len(inter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
