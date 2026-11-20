#!/usr/bin/env python
"""Probe CryptoHFTData archive coverage: earliest hour per venue/data type + missing hours.

Only cheap existence probes are used (`GET /download?file=...` with a streamed body);
no parquet body is downloaded. Results are written to data/chd_coverage.json and are the
evidence behind docs/CHD_DATA_SURVEY.md.

Usage:
    python scripts/chd_coverage.py
    python scripts/chd_coverage.py --venues lighter,okx_futures --sample-days 3
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nt_pairs.chd import CHDClient  # noqa: E402

VENUES = ["hyperliquid_futures", "lighter", "okx_futures", "binance_futures"]

# crypto anchor and equity anchor per venue
REFERENCE = {
    "hyperliquid_futures": {"crypto": "BTC", "equity": "XYZ:SOXL"},
    "lighter": {"crypto": "BTC", "equity": "SOXL"},
    "okx_futures": {"crypto": "BTC-USDT-SWAP", "equity": "SOXL-USDT-SWAP"},
    "binance_futures": {"crypto": "BTCUSDT", "equity": "SOXLUSDT"},
}

DATA_TYPES = ["orderbook", "trades", "ticker", "mark_price", "open_interest", "liquidations"]
SAMPLE_DATA_TYPES = ["orderbook", "trades"]

EARLIEST = "2025-06-01"


def has_file(client, venue, day, symbol, data_type) -> bool:
    """True when the symbol has a file in the given day (two probe hours)."""
    for hour in (12, 0):
        if client.object_exists(venue, day, hour, symbol, data_type):
            return True
    return False


def earliest_day(client, venue, symbol, data_type, low: str, high: str) -> str | None:
    """Binary search the earliest day that has a file (None when nothing found)."""
    if not has_file(client, venue, high, symbol, data_type):
        return None
    lo, hi = datetime.fromisoformat(low), datetime.fromisoformat(high)
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) / 2
        mid_day = mid.strftime("%Y-%m-%d")
        if has_file(client, venue, mid_day, symbol, data_type):
            hi = mid
        else:
            lo = mid
    return hi.strftime("%Y-%m-%d")


def first_hour(client, venue, day, symbol, data_type) -> int | None:
    for hour in range(24):
        if client.object_exists(venue, day, hour, symbol, data_type):
            return hour
    return None


def missing_hours(client, venue, day, symbol, data_type) -> list[int]:
    return [
        hour
        for hour in range(24)
        if not client.object_exists(venue, day, hour, symbol, data_type)
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default="data/chd_cache")
    ap.add_argument("--out", default="data/chd_coverage.json")
    ap.add_argument("--venues", default=",".join(VENUES))
    ap.add_argument("--data-types", default=",".join(DATA_TYPES))
    ap.add_argument("--sample-days", type=int, default=4)
    ap.add_argument("--earliest", default=EARLIEST)
    args = ap.parse_args()

    client = CHDClient(cache_dir=args.cache_dir)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    venues = [v.strip() for v in args.venues.split(",") if v.strip()]
    data_types = [d.strip() for d in args.data_types.split(",") if d.strip()]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "earliest_probe_lower_bound": args.earliest,
        "venues": {},
    }
    for venue in venues:
        refs = REFERENCE.get(venue, {})
        entry = {"reference_symbols": refs, "data_types": {}}
        for data_type in data_types:
            record = {}
            for kind, symbol in refs.items():
                try:
                    day = earliest_day(
                        client, venue, symbol, data_type, args.earliest, today
                    )
                except Exception as exc:
                    record[kind] = {"error": str(exc)}
                    continue
                if day is None:
                    record[kind] = {"earliest_day": None}
                    continue
                info = {"earliest_day": day}
                if kind == "crypto":
                    info["earliest_hour"] = first_hour(
                        client, venue, day, symbol, data_type
                    )
                record[kind] = info
            entry["data_types"][data_type] = record

            if data_type in SAMPLE_DATA_TYPES:
                symbol = refs.get("crypto")
                start = datetime.fromisoformat(
                    record.get("crypto", {}).get("earliest_day") or args.earliest
                )
                end = datetime.fromisoformat(today)
                span = max((end - start).days, 1)
                offsets = sorted(
                    {
                        int(span * i / max(args.sample_days - 1, 1))
                        for i in range(args.sample_days)
                    }
                )
                samples = []
                for off in offsets:
                    day = (start + timedelta(days=off)).strftime("%Y-%m-%d")
                    miss = missing_hours(client, venue, day, symbol, data_type)
                    samples.append(
                        {"day": day, "missing_hours": miss, "missing_count": len(miss)}
                    )
                entry["data_types"][data_type]["samples"] = samples
                entry["data_types"][data_type]["sample_missing_total"] = sum(
                    s["missing_count"] for s in samples
                )
        result["venues"][venue] = entry
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"{venue}: done", file=sys.stderr)

    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
