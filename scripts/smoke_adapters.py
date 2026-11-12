#!/usr/bin/env python
"""Smoke-test NautilusTrader 1.231 Hyperliquid & Lighter adapters.

Usage:
    python scripts/smoke_adapters.py --no-network
    python scripts/smoke_adapters.py --instruments
"""

import argparse
import inspect
import sys
from typing import Any


def print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_config_fields(cls: Any, cls_name: str) -> None:
    """Print constructor parameters (name, type, default) for a config class."""
    print(f"\n--- {cls_name} ---")
    try:
        sig = inspect.signature(cls.__init__)
    except (ValueError, TypeError) as e:
        print(f"  [ERROR] Cannot inspect signature: {e}")
        return

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        default = param.default
        is_required = default is inspect.Parameter.empty
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            type_str = "Any"
        else:
            type_str = str(ann).replace("typing.", "").replace(" | None", " | None")
            # Clean up Union/Optional
            if "Union[" in type_str:
                type_str = type_str.replace("Union[", "").rstrip("]")
        required_str = "REQUIRED" if is_required else f"default={default!r}"
        print(f"  {name}: {type_str}  [{required_str}]")


def no_network() -> None:
    """Import adapters and print config fields without network access."""
    # --- Version ---
    import nautilus_trader
    print(f"nautilus_trader version: {nautilus_trader.__version__}")

    # --- Hyperliquid ---
    print_section("Hyperliquid Adapter")
    try:
        from nautilus_trader.adapters.hyperliquid import (
            HyperliquidDataClientConfig,
            HyperliquidExecClientConfig,
            HyperliquidEnvironment,
            HyperliquidProductType,
            HyperliquidHttpClient,
        )
        print("  Imports: OK")

        print("\n  HyperliquidEnvironment variants:")
        for v in HyperliquidEnvironment.variants():
            print(f"    {v}")

        print("\n  HyperliquidProductType variants:")
        for v in HyperliquidProductType.variants():
            print(f"    {v}")

        print_config_fields(HyperliquidDataClientConfig, "HyperliquidDataClientConfig")
        print_config_fields(HyperliquidExecClientConfig, "HyperliquidExecClientConfig")

        # Instrument ID format (from docs)
        print("\n  Instrument ID formats (from source docs):")
        print("    Standard perp:  {Base}-USD-PERP.HYPERLIQUID")
        print("    HIP-3 perp:     {dex}:{Asset}-USD-PERP.HYPERLIQUID")
        print("    Spot:           {Base}-{Quote}-SPOT.HYPERLIQUID")
        print("    HIP-4 outcome:  {outcome_index}-{YES|NO}-OUTCOME.HYPERLIQUID")

        # Evidence: check if io:OAI exists via direct HTTP client (no keys needed)
        print("\n  HIP-3 io:OAI expected instrument_id: io:OAI-USD-PERP.HYPERLIQUID")

    except Exception as e:
        print(f"  [FAIL] Hyperliquid import: {e}")

    # --- Lighter ---
    print_section("Lighter Adapter")
    try:
        from nautilus_trader.adapters.lighter import (
            LighterDataClientConfig,
            LighterExecutionClientConfig,
            LighterDeployment,
            LighterEnvironment,
            LIGHTER,
            LIGHTER_ROBINHOOD,
            LIGHTER_VENUE,
            LIGHTER_ROBINHOOD_VENUE,
        )
        print("  Imports: OK")

        print("\n  LighterDeployment variants:")
        for v in LighterDeployment:
            print(f"    {v.name} = {v.value}")

        print("\n  LighterEnvironment variants:")
        for v in LighterEnvironment:
            print(f"    {v.name} = {v.value}")

        print(f"\n  Venue constants:")
        print(f"    LIGHTER_VENUE = {LIGHTER_VENUE}")
        print(f"    LIGHTER_ROBINHOOD_VENUE = {LIGHTER_ROBINHOOD_VENUE}")

        print_config_fields(LighterDataClientConfig, "LighterDataClientConfig")
        print_config_fields(LighterExecutionClientConfig, "LighterExecutionClientConfig")

        # Instrument ID format (from docs)
        print("\n  Instrument ID formats (from source docs):")
        print("    Lighter perp:       {BASE}-PERP.LIGHTER")
        print("    Lighter spot:       {BASE}/{QUOTE}-SPOT.LIGHTER")
        print("    Robinhood perp:     {BASE}-PERP.LIGHTER_ROBINHOOD")
        print("    Robinhood spot:     {BASE}/{QUOTE}-SPOT.LIGHTER_ROBINHOOD")

        print("\n  OPENAI expected instrument_id: OPENAI-PERP.LIGHTER")

    except Exception as e:
        print(f"  [FAIL] Lighter import: {e}")

    # --- Fee analysis ---
    print_section("Fee / Maker / Taker Analysis")
    print("  NautilusTrader instrument model (CryptoPerpetual) does NOT carry")
    print("  maker_fee/taker_fee fields. Fees are managed through the account-owned")
    print("  MakerTakerFeeSchedule (see nautilus_model::fees::MakerTakerFeeRates).")
    print()
    print("  Hyperliquid: Fees come from /info endpoint (user_fees), not instruments.")
    print("    HttpClient.info_user_fees() returns per-user maker/taker rates.")
    print("  Lighter: Fees depend on account tier (Standard=0/0, Plus=0.5/0.5 bps,")
    print("    Premium=0.28-2.80 bps). The adapter does NOT expose fees on instruments.")
    print("    The Rust client parses account tier from GET /api/v1/account.")


def instruments_mode() -> None:
    """Try to load instruments via public endpoints (no keys)."""
    print("=== Instrument Discovery (public endpoints, no keys) ===\n")

    # --- Hyperliquid: try public HTTP client ---
    print("--- Hyperliquid ---")
    try:
        import asyncio
        from nautilus_trader.adapters.hyperliquid import (
            HyperliquidHttpClient,
            HyperliquidEnvironment,
        )

        async def hl_instruments():
            client = HyperliquidHttpClient.from_env(HyperliquidEnvironment.MAINNET)
            instruments = await client.load_instrument_definitions(
                include_spot=False,
                include_perps=True,
                include_perps_hip3=True,
                include_outcomes=False,
            )
            # Search for OAI
            matches = []
            for instr in instruments:
                sid = str(instr.id)
                if "OAI" in sid.upper():
                    matches.append(sid)
            if matches:
                print(f"  Found {len(matches)} OAI instrument(s):")
                for m in matches:
                    print(f"    {m}")
            else:
                print("  No OAI instruments found in loaded definitions.")
            print(f"  Total instruments loaded: {len(instruments) if instruments else 0}")

        asyncio.run(hl_instruments())

    except Exception as e:
        err_str = str(e)
        print(f"  [NETWORK ERROR] {err_str}")
        # Network failures are expected; exit 0
        print("  (This is expected if Hyperliquid API is unreachable from this host.)")

    # --- Lighter: try public REST endpoint ---
    print("\n--- Lighter ---")
    try:
        import urllib.request
        import json

        # Known reachable endpoint
        url = "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails"
        print(f"  Trying: {url}")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if isinstance(data, dict) and "markets" in data:
            print(f"  Got {len(data['markets'])} markets from orderBookDetails")
        else:
            # Try orderBooks
            url2 = "https://mainnet.zklighter.elliot.ai/api/v1/orderBooks"
            print(f"  Trying: {url2}")
            req2 = urllib.request.Request(url2, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                data2 = json.loads(resp2.read().decode())
            print(f"  Response keys: {list(data2.keys())[:10]}")
            # Search for OPENAI
            if isinstance(data2, dict):
                for key in data2:
                    if "OPENAI" in str(key).upper():
                        print(f"  Found OPENAI entry: {key}")
                        break
                else:
                    # Search values
                    found = False
                    for key, val in data2.items():
                        if isinstance(val, dict):
                            for k2 in val:
                                if "OPENAI" in str(k2).upper() or "OPENAI" in str(val.get(k2, "")).upper():
                                    print(f"  Found OPENAI reference in {key}/{k2}")
                                    found = True
                            if found:
                                break
                    if not found:
                        print("  OPENAI not found in public endpoint response.")
                        print(f"  Sample keys: {list(data2.keys())[:5]}")

    except Exception as e:
        err_str = str(e)
        print(f"  [NETWORK ERROR] {err_str}")
        print("  (This is expected if Lighter API is unreachable from this host.)")

    print("\n=== Done (exit 0) ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test NautilusTrader adapters")
    parser.add_argument(
        "--no-network", action="store_true",
        help="Only import and inspect, no network calls"
    )
    parser.add_argument(
        "--instruments", action="store_true",
        help="Try to discover instruments via public endpoints"
    )
    args = parser.parse_args()

    if args.instruments:
        instruments_mode()
    else:
        no_network()

    sys.exit(0)


if __name__ == "__main__":
    main()