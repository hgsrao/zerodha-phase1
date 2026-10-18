#!/usr/bin/env python3
"""
Create Synthetic Nifty 50 and VIX from existing 48-symbol portfolio.

Uses weighted average price movement of all symbols as Nifty proxy.
Calculates VIX from portfolio volatility.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")

import pandas as pd
import numpy as np
from datetime import datetime

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest


def create_synthetic_nifty_vix():
    """Create synthetic Nifty 50 and VIX from 48-symbol portfolio."""

    print("\n" + "="*120)
    print("Creating Synthetic Nifty 50 & VIX from 48-Symbol Portfolio".center(120))
    print("="*120 + "\n")

    # Load manifest and data
    try:
        print("Loading 48-symbol data...")
        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
        loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
        registry = CanonicalParameterRegistry()
        symbols_spec = registry.params.get("symbols_to_trade")

        if symbols_spec and symbols_spec.default:
            if isinstance(symbols_spec.default, list):
                symbols = symbols_spec.default
            else:
                symbols = symbols_spec.default.split(",")
                symbols = [s.strip() for s in symbols if s.strip()]
        else:
            # Hardcoded 48-symbol list
            symbols = ["ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
                      "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA",
                      "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
                      "HDFCLIFE", "HINDALCO", "HINDUNILVR", "INFY", "ITC", "JSWSTEEL",
                      "KOTAKBANK", "LT", "LTIM", "MARUTI", "NESTLEIND", "NTPC",
                      "ONGC", "POWERGRID", "RELIANCE", "SBICARD", "SBILIFE", "SBIN",
                      "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATAPOWER", "TCS", "TECHM",
                      "TITAN", "TORRENTPHARMA", "UltraCemet", "ULTRACEMCO", "WIPRO", "YESBANK"]

        print(f"✓ Loaded {len(symbols)} symbols")
    except Exception as e:
        print(f"✗ Failed to load: {e}")
        return False

    # Load all symbol data
    all_data = {}
    print("\nLoading symbol data...")
    for i, symbol in enumerate(symbols, 1):
        try:
            df = loader._load_symbol_csv(symbol)
            if df is not None and len(df) > 0:
                all_data[symbol] = df.copy()
                print(f"  [{i:2d}/{len(symbols)}] {symbol:12s} ✓ ({len(df):,} bars)")
            else:
                print(f"  [{i:2d}/{len(symbols)}] {symbol:12s} ✗ (no data)")
        except Exception as e:
            print(f"  [{i:2d}/{len(symbols)}] {symbol:12s} ✗ ({e})")

    if not all_data:
        print("✗ No symbol data loaded")
        return False

    print(f"\n✓ Successfully loaded {len(all_data)} symbols")

    # Find common time index
    print("\nFinding common timestamps...")
    all_timestamps = set()
    for df in all_data.values():
        all_timestamps.update(df["timestamp"].unique())
    all_timestamps = sorted(list(all_timestamps))
    print(f"✓ Found {len(all_timestamps):,} common timestamps")

    # Create Nifty 50 as weighted average
    print("\nCreating synthetic Nifty 50...")

    nifty_closes = []
    nifty_volumes = []
    nifty_timestamps = []

    for ts in all_timestamps:
        prices = []
        volumes = []

        for symbol, df in all_data.items():
            row = df[df["timestamp"] == ts]
            if len(row) > 0:
                prices.append(float(row["close"].iloc[0]))
                volumes.append(float(row["volume"].iloc[0]))

        if len(prices) > 0:
            # Weighted average: normalize to 100 base
            avg_price = np.mean(prices)
            avg_volume = np.mean(volumes)

            nifty_closes.append(avg_price)
            nifty_volumes.append(avg_volume)
            nifty_timestamps.append(ts)

    # Create Nifty dataframe
    nifty_df = pd.DataFrame({
        "timestamp": nifty_timestamps,
        "close": nifty_closes,
        "volume": nifty_volumes,
    })

    # Calculate OHLC
    nifty_df["returns"] = nifty_df["close"].pct_change()
    nifty_df["open"] = nifty_df["close"].shift(1).fillna(nifty_df["close"])
    nifty_df["high"] = nifty_df[["open", "close"]].max(axis=1)
    nifty_df["low"] = nifty_df[["open", "close"]].min(axis=1)

    nifty_df = nifty_df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    nifty_df["symbol"] = "NIFTY50"

    print(f"✓ Created Nifty 50: {len(nifty_df):,} bars")
    print(f"  Price range: {nifty_df['close'].min():.2f} - {nifty_df['close'].max():.2f}")

    # Create VIX from volatility
    print("\nCreating synthetic India VIX...")

    nifty_df["volatility"] = nifty_df["returns"].rolling(window=20).std()
    vix_closes = 20.0 * nifty_df["volatility"] * 100  # Scale to VIX range

    vix_df = pd.DataFrame({
        "timestamp": nifty_df["timestamp"],
        "open": vix_closes.shift(1).fillna(vix_closes),
        "high": vix_closes.rolling(2).max(),
        "low": vix_closes.rolling(2).min(),
        "close": vix_closes,
        "volume": 1000000,
    })
    vix_df["symbol"] = "INDIAVIX"

    # Fill NaN
    vix_df = vix_df.fillna(method='ffill').fillna(method='bfill')

    print(f"✓ Created VIX: {len(vix_df):,} bars")
    print(f"  VIX range: {vix_df['close'].min():.1f} - {vix_df['close'].max():.1f}")

    # Save to data directory
    os.makedirs("/home/shrinivas/ECS_Project_external_engine/data", exist_ok=True)

    nifty_path = "/home/shrinivas/ECS_Project_external_engine/data/NSE_NIFTY50_minute.csv"
    nifty_df.to_csv(nifty_path, index=False)
    print(f"\n✓ Saved Nifty 50 to {nifty_path}")

    vix_path = "/home/shrinivas/ECS_Project_external_engine/data/NSE_INDIAVIX_minute.csv"
    vix_df.to_csv(vix_path, index=False)
    print(f"✓ Saved VIX to {vix_path}")

    print("\n" + "="*120)
    print("✓ SYNTHETIC DATA READY FOR GRID SYNCHRONIZATION TEST")
    print("="*120 + "\n")

    return True


if __name__ == "__main__":
    success = create_synthetic_nifty_vix()
    sys.exit(0 if success else 1)
