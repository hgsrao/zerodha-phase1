#!/usr/bin/env python3
"""
Download REAL Nifty 50 and India VIX from Zerodha KiteConnect API.

Uses provided API credentials:
  API Key: f5qmn3ug0i6brql3
  Client ID: CE2003
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
    print("✗ kiteconnect not found")
    print("Installing...")
    os.system("pip install --break-system-packages kiteconnect -q")
    from kiteconnect import KiteConnect
    print("✓ Installed\n")


def download_nifty_real():
    """Download real Nifty 50 data from Zerodha."""

    print("="*100)
    print("DOWNLOADING REAL NIFTY 50 DATA FROM ZERODHA")
    print("="*100 + "\n")

    API_KEY = "f5qmn3ug0i6brql3"
    CLIENT_ID = "CE2003"

    print(f"API Key: {API_KEY}")
    print(f"Client ID: {CLIENT_ID}\n")

    # Initialize Kite
    print("[1] Initializing KiteConnect...")
    kite = KiteConnect(api_key=API_KEY)

    # Get login URL
    login_url = kite.login_url()
    print(f"✓ Login URL generated\n")

    print("[2] MANUAL LOGIN REQUIRED")
    print("─" * 100)
    print(f"1. Open this URL in your browser:\n   {login_url}\n")
    print("2. Login with your Zerodha credentials")
    print("3. You will be redirected to a page with request_token")
    print("4. Copy the request_token and paste it below\n")
    print("─" * 100 + "\n")

    # Get request token from user
    request_token = input("Enter request_token (from redirect URL): ").strip()

    if not request_token:
        print("✗ No request token provided")
        return False

    print(f"\n[3] Exchanging request_token for access_token...")
    try:
        # You need API secret for this, which we have
        API_SECRET = "f26rzpezo9ksp0fwpv8vgmkanitedgu"

        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]

        kite.set_access_token(access_token)
        print(f"✓ Access token obtained: {access_token[:20]}...\n")

    except Exception as e:
        print(f"✗ Failed to generate session: {e}")
        print("  Make sure request_token is correct")
        return False

    # Get profile to verify connection
    print("[4] Verifying connection...")
    try:
        profile = kite.profile()
        print(f"✓ Connected as: {profile.get('user_name', 'Unknown')}\n")
    except Exception as e:
        print(f"✗ Failed to get profile: {e}")
        return False

    # Download Nifty 50
    print("[5] Downloading Nifty 50 data...")
    print("    (This may take 5-10 minutes for 3 years of data)\n")

    NIFTY_TOKEN = 256265  # NSE Nifty 50

    from_date = datetime(2023, 7, 3)
    to_date = datetime(2026, 8, 24)

    all_candles = []
    current_date = from_date
    chunk_count = 0

    while current_date < to_date:
        chunk_end = min(current_date + timedelta(days=59), to_date)
        chunk_count += 1

        print(f"  [{chunk_count}] {current_date.date()} to {chunk_end.date()}...", end=" ", flush=True)

        try:
            candles = kite.historical_data(
                instrument_token=NIFTY_TOKEN,
                from_date=current_date.date(),
                to_date=chunk_end.date(),
                interval="60"  # 1-minute
            )
            print(f"✓ ({len(candles)} bars)")
            all_candles.extend(candles)
            time.sleep(0.5)  # Rate limiting

        except Exception as e:
            print(f"✗ ({e})")
            time.sleep(2)

    if not all_candles:
        print("\n✗ No data retrieved")
        return False

    print(f"\n✓ Downloaded {len(all_candles):,} Nifty 50 candles\n")

    # Convert to DataFrame
    nifty_df = pd.DataFrame(all_candles)
    nifty_df["timestamp"] = pd.to_datetime(nifty_df["date"])
    nifty_df = nifty_df[["timestamp", "open", "high", "low", "close", "volume"]].copy()

    # Save
    os.makedirs("data", exist_ok=True)
    nifty_path = "data/NSE_NIFTY50_REAL_minute.csv"
    nifty_df.to_csv(nifty_path, index=False)

    print(f"[6] Saved Nifty 50")
    print(f"    Path: {nifty_path}")
    print(f"    Rows: {len(nifty_df):,}")
    print(f"    Date range: {nifty_df['timestamp'].min()} to {nifty_df['timestamp'].max()}")
    print(f"    Price range: ₹{nifty_df['close'].min():.2f} - ₹{nifty_df['close'].max():.2f}\n")

    # Download VIX
    print("[7] Downloading India VIX data...")

    VIX_TOKEN = 256423  # NSE India VIX

    all_vix = []
    current_date = from_date
    chunk_count = 0

    while current_date < to_date:
        chunk_end = min(current_date + timedelta(days=59), to_date)
        chunk_count += 1

        print(f"  [{chunk_count}] {current_date.date()} to {chunk_end.date()}...", end=" ", flush=True)

        try:
            candles = kite.historical_data(
                instrument_token=VIX_TOKEN,
                from_date=current_date.date(),
                to_date=chunk_end.date(),
                interval="60"
            )
            print(f"✓ ({len(candles)} bars)")
            all_vix.extend(candles)
            time.sleep(0.5)

        except Exception as e:
            print(f"✗ ({e})")
            time.sleep(2)

    if all_vix:
        print(f"\n✓ Downloaded {len(all_vix):,} VIX candles\n")

        vix_df = pd.DataFrame(all_vix)
        vix_df["timestamp"] = pd.to_datetime(vix_df["date"])
        vix_df = vix_df[["timestamp", "open", "high", "low", "close", "volume"]].copy()

        vix_path = "data/NSE_INDIAVIX_REAL_minute.csv"
        vix_df.to_csv(vix_path, index=False)

        print(f"[8] Saved India VIX")
        print(f"    Path: {vix_path}")
        print(f"    Rows: {len(vix_df):,}")
        print(f"    VIX range: {vix_df['close'].min():.2f} - {vix_df['close'].max():.2f}\n")
    else:
        print("[8] VIX data unavailable (will use synthetic)\n")
        vix_df = None

    print("="*100)
    print("✓ DOWNLOAD COMPLETE - REAL DATA READY")
    print("="*100)
    print(f"\nFiles created:")
    print(f"  ✓ data/NSE_NIFTY50_REAL_minute.csv ({len(nifty_df):,} rows)")
    if vix_df is not None:
        print(f"  ✓ data/NSE_INDIAVIX_REAL_minute.csv ({len(vix_df):,} rows)")
    print(f"\nNext: Run grid sync test with REAL Nifty 50 data")
    print("="*100 + "\n")

    return True


if __name__ == "__main__":
    success = download_nifty_real()
    sys.exit(0 if success else 1)
