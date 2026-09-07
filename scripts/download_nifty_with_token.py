#!/usr/bin/env python3
"""
Download REAL Nifty 50 and VIX using provided request_token.
"""

import sys
import os
from datetime import datetime, timedelta
import pandas as pd
import time

print("Checking KiteConnect library...")
try:
    from kiteconnect import KiteConnect
    print("✓ kiteconnect available\n")
except ImportError:
    print("Installing kiteconnect...")
    os.system("pip install --break-system-packages kiteconnect -q 2>/dev/null")
    from kiteconnect import KiteConnect
    print("✓ Installed\n")


def download_with_token(request_token):
    """Download Nifty using provided request_token."""

    print("="*100)
    print("DOWNLOADING REAL NIFTY 50 & VIX WITH PROVIDED TOKEN")
    print("="*100 + "\n")

    API_KEY = "f5qmn3ug0i6brql3"
    API_SECRET = "f26rzpezo9ksp0fwpv8vgmkanitedgu"

    print(f"[1] Initializing KiteConnect...")
    kite = KiteConnect(api_key=API_KEY)

    print(f"[2] Exchanging request_token for access_token...")
    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]
        kite.set_access_token(access_token)
        print(f"✓ Access token obtained\n")
    except Exception as e:
        print(f"✗ Failed: {e}")
        return False

    # Verify connection
    print(f"[3] Verifying connection...")
    try:
        profile = kite.profile()
        print(f"✓ Connected as: {profile.get('user_name', 'Unknown')}\n")
    except Exception as e:
        print(f"✗ Failed: {e}")
        return False

    # Download Nifty 50
    print(f"[4] Downloading Nifty 50 (2023-07-03 to 2026-08-24)...")
    print("    (This may take 5-15 minutes)\n")

    NIFTY_TOKEN = 256265
    from_date = datetime(2023, 7, 3)
    to_date = datetime(2026, 8, 24)

    all_candles = []
    current_date = from_date
    chunk = 0

    while current_date < to_date:
        chunk_end = min(current_date + timedelta(days=59), to_date)
        chunk += 1

        print(f"  [{chunk:2d}] {current_date.date()} to {chunk_end.date()}...", end=" ", flush=True)

        try:
            candles = kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=current_date.date(),
                to_date=chunk_end.date(),
                interval="60"
            )
            print(f"✓ {len(candles):6d} bars")
            all_candles.extend(candles)
            time.sleep(0.2)

        except Exception as e:
            print(f"✗ {str(e)[:40]}")
            time.sleep(1)

        current_date = chunk_end + timedelta(days=1)

    if not all_candles:
        print("\n✗ No Nifty data retrieved")
        return False

    print(f"\n✓ Downloaded {len(all_candles):,} Nifty 50 candles\n")

    # Save Nifty
    nifty_df = pd.DataFrame(all_candles)
    nifty_df["timestamp"] = pd.to_datetime(nifty_df["date"])
    nifty_df = nifty_df[["timestamp", "open", "high", "low", "close", "volume"]]

    os.makedirs("data", exist_ok=True)
    nifty_path = "data/NSE_NIFTY50_REAL_minute.csv"
    nifty_df.to_csv(nifty_path, index=False)

    print(f"[5] Saved Nifty 50")
    print(f"    File: {nifty_path}")
    print(f"    Rows: {len(nifty_df):,}")
    print(f"    Date: {nifty_df['timestamp'].min()} to {nifty_df['timestamp'].max()}")
    print(f"    Price: ₹{nifty_df['close'].min():.2f} - ₹{nifty_df['close'].max():.2f}\n")

    # Download VIX
    print(f"[6] Downloading India VIX...")

    VIX_TOKEN = 256423
    all_vix = []
    current_date = from_date
    chunk = 0

    while current_date < to_date:
        chunk_end = min(current_date + timedelta(days=59), to_date)
        chunk += 1

        print(f"  [{chunk:2d}] {current_date.date()} to {chunk_end.date()}...", end=" ", flush=True)

        try:
            candles = kite.historical_data(
                instrument_token=VIX_TOKEN,
                from_date=current_date.date(),
                to_date=chunk_end.date(),
                interval="60"
            )
            print(f"✓ {len(candles):6d} bars")
            all_vix.extend(candles)
            time.sleep(0.2)

        except Exception as e:
            print(f"✗ {str(e)[:40]}")
            time.sleep(1)

        current_date = chunk_end + timedelta(days=1)

    if all_vix:
        print(f"\n✓ Downloaded {len(all_vix):,} VIX candles\n")

        vix_df = pd.DataFrame(all_vix)
        vix_df["timestamp"] = pd.to_datetime(vix_df["date"])
        vix_df = vix_df[["timestamp", "open", "high", "low", "close", "volume"]]

        vix_path = "data/NSE_INDIAVIX_REAL_minute.csv"
        vix_df.to_csv(vix_path, index=False)

        print(f"[7] Saved India VIX")
        print(f"    File: {vix_path}")
        print(f"    Rows: {len(vix_df):,}")
        print(f"    VIX: {vix_df['close'].min():.2f} - {vix_df['close'].max():.2f}\n")
    else:
        print(f"[7] VIX not available (will use Nifty volatility)\n")
        vix_df = None

    print("="*100)
    print("✓✓✓ REAL DATA DOWNLOAD COMPLETE ✓✓✓")
    print("="*100)
    print(f"\nFiles ready:")
    print(f"  ✓ Nifty 50: data/NSE_NIFTY50_REAL_minute.csv ({len(nifty_df):,} rows)")
    if vix_df is not None:
        print(f"  ✓ India VIX: data/NSE_INDIAVIX_REAL_minute.csv ({len(vix_df):,} rows)")
    print(f"\nNext step: Run grid sync test with REAL data")
    print("="*100 + "\n")

    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 download_nifty_with_token.py <request_token>")
        print("\nExample:")
        print("  python3 download_nifty_with_token.py W1iHkXbElmSWFTeS0JVf2mqtE0FWsKmK")
        sys.exit(1)

    request_token = sys.argv[1].strip()
    print(f"Request token: {request_token}\n")

    success = download_with_token(request_token)
    sys.exit(0 if success else 1)
