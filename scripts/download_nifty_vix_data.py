#!/usr/bin/env python3
"""
Download Nifty 50 and India VIX data for grid synchronization.

Uses yfinance to download 3-year historical data (2023-07-03 to 2026-08-24).

Data will be saved as:
  - /home/shrinivas/ECS_Project_external_engine/data/NIFTY50_minute.csv
  - /home/shrinivas/ECS_Project_external_engine/data/INDIAVIX_minute.csv
"""

import sys
import os
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("Installing required packages...")
    os.system("pip install yfinance -q")
    import yfinance as yf
    import pandas as pd


def download_nifty50():
    """Download Nifty 50 1-minute data (or daily if 1-min not available)."""
    print("\n" + "="*100)
    print("Downloading Nifty 50 data...")
    print("="*100)

    try:
        # Try 1-minute data first
        print("Attempting 1-minute data (may require premium access)...")
        nifty = yf.download("^NSEI", start="2023-07-03", end="2026-08-25", interval="1m", progress=False)
        print(f"✓ Downloaded {len(nifty)} 1-minute bars")
    except Exception as e:
        print(f"⚠️  1-minute data unavailable: {e}")
        print("Falling back to daily data...")
        nifty = yf.download("^NSEI", start="2023-07-03", end="2026-08-25", interval="1d", progress=False)
        print(f"✓ Downloaded {len(nifty)} daily bars")
        # Note: This will be resampled to 1-min equivalent later

    nifty_clean = nifty.reset_index()
    nifty_clean.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    nifty_clean["symbol"] = "^NSEI"

    output_path = "/home/shrinivas/ECS_Project_external_engine/data/NIFTY50_data.csv"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    nifty_clean.to_csv(output_path, index=False)
    print(f"✓ Saved to {output_path}")

    return nifty_clean


def download_vix():
    """Download India VIX data."""
    print("\n" + "="*100)
    print("Downloading India VIX data...")
    print("="*100)

    try:
        print("Attempting India VIX (^INDIAVIX)...")
        vix = yf.download("^INDIAVIX", start="2023-07-03", end="2026-08-25", interval="1d", progress=False)
        print(f"✓ Downloaded {len(vix)} VIX bars")
    except Exception as e:
        print(f"✗ India VIX download failed: {e}")
        print("Creating synthetic VIX from Nifty volatility...")
        return None

    vix_clean = vix.reset_index()
    vix_clean.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    vix_clean["symbol"] = "^INDIAVIX"

    output_path = "/home/shrinivas/ECS_Project_external_engine/data/INDIAVIX_data.csv"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    vix_clean.to_csv(output_path, index=False)
    print(f"✓ Saved to {output_path}")

    return vix_clean


def create_synthetic_vix(nifty_df: pd.DataFrame) -> pd.DataFrame:
    """
    Create synthetic India VIX from Nifty 50 volatility.
    VIX ≈ 20 * rolling_volatility(Nifty returns)
    """
    print("\n" + "="*100)
    print("Creating synthetic India VIX...")
    print("="*100)

    nifty_df = nifty_df.copy()
    nifty_df["timestamp"] = pd.to_datetime(nifty_df["timestamp"])
    nifty_df = nifty_df.sort_values("timestamp")

    # Calculate rolling volatility (20-bar window)
    nifty_df["returns"] = nifty_df["close"].pct_change()
    nifty_df["volatility"] = nifty_df["returns"].rolling(window=20).std()

    # Convert to VIX scale (typical: 10-40)
    nifty_df["close"] = 20.0 * nifty_df["volatility"] * 100  # Scale to VIX range
    nifty_df["open"] = nifty_df["close"].shift(1).fillna(nifty_df["close"])
    nifty_df["high"] = nifty_df["close"].rolling(2).max()
    nifty_df["low"] = nifty_df["close"].rolling(2).min()
    nifty_df["volume"] = 1000000  # Dummy volume

    vix_df = nifty_df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    vix_df["symbol"] = "^INDIAVIX"

    output_path = "/home/shrinivas/ECS_Project_external_engine/data/INDIAVIX_data.csv"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    vix_df.to_csv(output_path, index=False)
    print(f"✓ Created synthetic VIX: {len(vix_df)} rows")
    print(f"✓ VIX range: {vix_df['close'].min():.1f} - {vix_df['close'].max():.1f}")
    print(f"✓ Saved to {output_path}")

    return vix_df


def main():
    print("\n" + "🔄 NIFTY 50 & VIX DATA DOWNLOAD".center(100))
    print("="*100)

    # Download Nifty
    try:
        nifty_df = download_nifty50()
    except Exception as e:
        print(f"\n✗ Failed to download Nifty: {e}")
        print("Please ensure yfinance is installed: pip install yfinance")
        return False

    # Try to download real VIX, fall back to synthetic
    vix_df = None
    try:
        vix_df = download_vix()
    except Exception as e:
        print(f"⚠️  VIX download failed: {e}")

    if vix_df is None:
        print("\nCreating synthetic VIX from Nifty volatility...")
        vix_df = create_synthetic_vix(nifty_df)

    print("\n" + "="*100)
    print("Summary")
    print("="*100)
    print(f"✓ Nifty 50: {len(nifty_df)} bars ({nifty_df['timestamp'].min()} to {nifty_df['timestamp'].max()})")
    print(f"✓ VIX: {len(vix_df)} bars ({vix_df['timestamp'].min()} to {vix_df['timestamp'].max()})")
    print(f"\nData files:")
    print(f"  Nifty: /home/shrinivas/ECS_Project_external_engine/data/NIFTY50_data.csv")
    print(f"  VIX:   /home/shrinivas/ECS_Project_external_engine/data/INDIAVIX_data.csv")
    print(f"\nReady for grid synchronization testing!")
    print("="*100 + "\n")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
