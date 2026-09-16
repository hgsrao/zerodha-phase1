#!/usr/bin/env python3
"""
Download Nifty 50 and India VIX data from Zerodha KiteConnect API.

Professional data source for grid synchronization testing.

Usage:
  python3 scripts/download_nifty_from_zerodha.py [--api-key KEY] [--access-token TOKEN]

Environment variables:
  KITE_API_KEY: Zerodha API key
  KITE_ACCESS_TOKEN: Zerodha access token
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

try:
    from kiteconnect import KiteConnect
except ImportError:
    print("✗ kiteconnect not found. Install with: pip install kiteconnect")
    sys.exit(1)


def download_nifty_from_zerodha(
    api_key: str = None,
    access_token: str = None,
    start_date: str = "2023-07-03",
    end_date: str = "2026-08-24",
    interval: str = "60",  # 60 = 1 minute
) -> pd.DataFrame:
    """
    Download Nifty 50 historical minute data from Zerodha.

    Args:
        api_key: Zerodha API key
        access_token: Zerodha access token
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        interval: Candle interval ('1', '3', '5', '10', '15', '30', '60' minutes)

    Returns:
        pd.DataFrame with OHLCV data
    """

    # Use environment variables if not provided
    api_key = api_key or os.getenv("KITE_API_KEY")
    access_token = access_token or os.getenv("KITE_ACCESS_TOKEN")

    if not api_key or not access_token:
        print("✗ Zerodha credentials not found.")
        print("\nProvide via:")
        print("  1. Environment variables: export KITE_API_KEY=... KITE_ACCESS_TOKEN=...")
        print("  2. Command-line args: download_nifty_from_zerodha.py --api-key KEY --access-token TOKEN")
        print("  3. Zerodha web dashboard to generate a new access token")
        sys.exit(1)

    print("\n" + "="*100)
    print("Connecting to Zerodha KiteConnect API...")
    print("="*100)

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        # Test connection
        profile = kite.profile()
        print(f"✓ Connected as: {profile.get('user_name', 'Unknown')}")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("\nTroubleshooting:")
        print("  - Verify API key and access token are correct")
        print("  - Check that access token hasn't expired (valid for 6 months)")
        print("  - Generate new token at: https://kite.zerodha.com/settings/developer/tokens")
        sys.exit(1)

    print(f"\nDownloading Nifty 50 ({start_date} to {end_date}, {interval}min candles)...")

    try:
        # Nifty 50 instrument token on Zerodha
        # The NSE Nifty 50 index token is 256265 (but varies by broker setup)
        # Using the common instrument name approach
        from_date = datetime.strptime(start_date, "%Y-%m-%d")
        to_date = datetime.strptime(end_date, "%Y-%m-%d")

        # Nifty 50 Index - common instrument token
        NIFTY_INSTRUMENT_TOKEN = 256265  # NSE Nifty 50

        print(f"  Fetching data for instrument token: {NIFTY_INSTRUMENT_TOKEN}")

        # Zerodha has a limit of 60 days per request, so we need to chunk
        all_candles = []
        current_date = from_date

        while current_date < to_date:
            chunk_end = min(current_date + timedelta(days=59), to_date)

            print(f"  Fetching {current_date.date()} to {chunk_end.date()}...", end=" ", flush=True)

            try:
                candles = kite.historical_data(
                    instrument_token=NIFTY_INSTRUMENT_TOKEN,
                    from_date=current_date.date(),
                    to_date=chunk_end.date(),
                    interval=interval,
                )
                print(f"✓ ({len(candles)} candles)")
                all_candles.extend(candles)
            except Exception as e:
                print(f"✗ ({e})")

            current_date = chunk_end + timedelta(days=1)

        if not all_candles:
            print("✗ No data retrieved. Check instrument token and Zerodha permissions.")
            sys.exit(1)

        print(f"\n✓ Downloaded {len(all_candles):,} Nifty 50 candles")

        # Convert to DataFrame
        df = pd.DataFrame(all_candles)
        df["timestamp"] = pd.to_datetime(df["date"])
        df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        df["symbol"] = "NIFTY50"

        return df

    except Exception as e:
        print(f"✗ Download failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def download_vix_from_zerodha(
    api_key: str = None,
    access_token: str = None,
    start_date: str = "2023-07-03",
    end_date: str = "2026-08-24",
    interval: str = "60",
) -> pd.DataFrame:
    """Download India VIX from Zerodha."""

    api_key = api_key or os.getenv("KITE_API_KEY")
    access_token = access_token or os.getenv("KITE_ACCESS_TOKEN")

    if not api_key or not access_token:
        print("✗ Zerodha credentials required for VIX download")
        return None

    print(f"\nDownloading India VIX ({start_date} to {end_date})...")

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        # India VIX instrument token
        VIX_INSTRUMENT_TOKEN = 256423  # NSE India VIX

        from_date = datetime.strptime(start_date, "%Y-%m-%d")
        to_date = datetime.strptime(end_date, "%Y-%m-%d")

        all_candles = []
        current_date = from_date

        while current_date < to_date:
            chunk_end = min(current_date + timedelta(days=59), to_date)

            try:
                candles = kite.historical_data(
                    instrument_token=VIX_INSTRUMENT_TOKEN,
                    from_date=current_date.date(),
                    to_date=chunk_end.date(),
                    interval=interval,
                )
                all_candles.extend(candles)
            except Exception:
                pass  # VIX might not be available; skip silently

            current_date = chunk_end + timedelta(days=1)

        if not all_candles:
            print("⚠️  VIX data unavailable; will create synthetic VIX from Nifty volatility")
            return None

        print(f"✓ Downloaded {len(all_candles):,} VIX candles")

        df = pd.DataFrame(all_candles)
        df["timestamp"] = pd.to_datetime(df["date"])
        df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        df["symbol"] = "INDIAVIX"

        return df

    except Exception as e:
        print(f"⚠️  VIX download failed ({e}); will create synthetic VIX")
        return None


def create_synthetic_vix(nifty_df: pd.DataFrame) -> pd.DataFrame:
    """Create synthetic VIX from Nifty volatility."""
    print("\nCreating synthetic India VIX from Nifty volatility...")

    df = nifty_df.copy()
    df["returns"] = df["close"].pct_change()
    df["volatility"] = df["returns"].rolling(window=20).std()
    df["close"] = 20.0 * df["volatility"] * 100  # Scale to VIX range
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df["close"].rolling(2).max()
    df["low"] = df["close"].rolling(2).min()
    df["volume"] = 1000000

    vix_df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    vix_df["symbol"] = "INDIAVIX"

    print(f"✓ Synthetic VIX: {len(vix_df)} rows, range {vix_df['close'].min():.1f}-{vix_df['close'].max():.1f}")
    return vix_df


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Download Nifty 50 and VIX from Zerodha")
    parser.add_argument("--api-key", help="Zerodha API key")
    parser.add_argument("--access-token", help="Zerodha access token")
    parser.add_argument("--start", default="2023-07-03", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-08-24", help="End date (YYYY-MM-DD)")

    args = parser.parse_args()

    print("\n" + "🔄 ZERODHA NIFTY 50 & VIX DOWNLOAD".center(100))
    print("="*100)

    # Download Nifty
    nifty_df = download_nifty_from_zerodha(
        api_key=args.api_key,
        access_token=args.access_token,
        start_date=args.start,
        end_date=args.end,
    )

    if nifty_df is None or len(nifty_df) == 0:
        print("✗ Failed to download Nifty data")
        sys.exit(1)

    # Save Nifty
    os.makedirs("/home/shrinivas/ECS_Project_external_engine/data", exist_ok=True)
    nifty_path = "/home/shrinivas/ECS_Project_external_engine/data/NSE_NIFTY50_minute.csv"
    nifty_df.to_csv(nifty_path, index=False)
    print(f"✓ Saved Nifty 50 to {nifty_path}")

    # Download or create VIX
    vix_df = download_vix_from_zerodha(
        api_key=args.api_key,
        access_token=args.access_token,
        start_date=args.start,
        end_date=args.end,
    )

    if vix_df is None:
        vix_df = create_synthetic_vix(nifty_df)

    vix_path = "/home/shrinivas/ECS_Project_external_engine/data/NSE_INDIAVIX_minute.csv"
    vix_df.to_csv(vix_path, index=False)
    print(f"✓ Saved VIX to {vix_path}")

    print(f"\n" + "="*100)
    print("SUCCESS: Data ready for grid synchronization")
    print("="*100)
    print(f"  Nifty 50: {len(nifty_df):,} bars")
    print(f"  VIX:      {len(vix_df):,} bars")
    print(f"\nNext step: Run 62-trade test with grid synchronization")
    print("="*100 + "\n")


if __name__ == "__main__":
    main()
