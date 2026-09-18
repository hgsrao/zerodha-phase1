#!/usr/bin/env python3
"""
Phase 1: NSE Parquet Data Lake Ingestion (2018-2026)
Downloads 5 years of 1-minute OHLCV data for institutional backtesting
Gas Turbines (Fast MIS) + Steam Turbines (Swing CNC)
"""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from kiteconnect import KiteConnect

# ============================================================================
# 1. CREDENTIALS (from environment or defaults)
# ============================================================================
API_KEY = os.getenv("KITE_API_KEY", "")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")

if not API_KEY or not ACCESS_TOKEN:
    print("❌ ERROR: Set KITE_API_KEY and KITE_ACCESS_TOKEN environment variables")
    print("   Windows: setx KITE_API_KEY your_key && setx KITE_ACCESS_TOKEN your_token")
    sys.exit(1)

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# Verify connection
try:
    profile = kite.profile()
    print(f"✓ Kite authenticated | Account: {profile.get('user_id')}")
except Exception as e:
    print(f"❌ Kite authentication failed: {e}")
    sys.exit(1)

# ============================================================================
# 2. FLEET CONFIGURATION
# ============================================================================
FLEET_SYMBOLS = [
    # Gas Turbines (Fast MIS - Intraday)
    "RELIANCE", "TCS", "INFY", "TATAMOTORS", "MARUTI",
    # Steam Turbines (Slow CNC - Swing)
    "HDFCBANK", "ICICIBANK", "SBIN", "BAJFINANCE",
    "TATASTEEL", "BEL", "HINDALCO", "LT"
]

RAW_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\data\raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

print(f"\n📁 Data lake: {RAW_DIR}")
print(f"📊 Fleet: {len(FLEET_SYMBOLS)} symbols")
print("=" * 75)

# ============================================================================
# 3. DOWNLOAD FUNCTION (60-day chunks, rate-limited)
# ============================================================================
def download_symbol_history(symbol: str, token: int, years: int = 5):
    """Download 1-minute candles in 60-day chunks (Kite API limit)"""
    print(f"\n[→] Downloading {symbol} (Token: {token}) | {years} years...")

    to_date = datetime.now()
    from_date = to_date - timedelta(days=365 * years)

    chunk_days = 60  # Kite historical limit per request
    current_from = from_date
    all_candles = []
    chunk_count = 0

    while current_from < to_date:
        current_to = min(current_from + timedelta(days=chunk_days), to_date)
        chunk_str_from = current_from.strftime("%Y-%m-%d")
        chunk_str_to = current_to.strftime("%Y-%m-%d")

        try:
            records = kite.historical_data(
                instrument_token=token,
                from_date=chunk_str_from,
                to_date=chunk_str_to,
                interval="minute"
            )
            if records:
                all_candles.extend(records)
                chunk_count += 1
                bar_count = len(records)
                print(f"  ✓ Chunk {chunk_count:2d}: {bar_count:>6} bars ({chunk_str_from} → {chunk_str_to})")
        except Exception as e:
            print(f"  ⚠ Error [{chunk_str_from}]: {str(e)[:60]}")

        current_from = current_to + timedelta(minutes=1)
        time.sleep(0.4)  # Respect Kite rate limit (2.5 req/sec = 0.4s between)

    # ========================================================================
    # 4. SAVE TO PARQUET (compressed, deduplicated)
    # ========================================================================
    if all_candles:
        df = pd.DataFrame(all_candles)
        df['date'] = pd.to_datetime(df['date'])

        # Remove duplicates & sort
        df.sort_values('date', inplace=True)
        df.drop_duplicates(subset=['date'], inplace=True)
        df.set_index('date', inplace=True)

        # Verify data integrity
        print(f"  📋 Records: {len(df):,} | Duplicates removed")
        print(f"  📅 Date range: {df.index.min().date()} → {df.index.max().date()}")

        # Save compressed Parquet
        out_path = RAW_DIR / f"{symbol}_1min.parquet"
        df.to_parquet(out_path, engine="pyarrow", compression="snappy")

        file_size_mb = out_path.stat().st_size / (1024**2)
        print(f"  💾 Saved: {out_path.name} ({file_size_mb:.2f} MB)")
        print(f"[✓] {symbol} COMPLETE\n")

        return df
    else:
        print(f"[!] No records fetched for {symbol}\n")
        return None

# ============================================================================
# 5. MAIN INGESTION LOOP
# ============================================================================
def main():
    print("\n" + "=" * 75)
    print("🔄 NSE PARQUET DATA LAKE INGESTION")
    print("=" * 75)

    print("\n[*] Fetching NSE instrument master from Kite...")
    try:
        instruments = kite.instruments("NSE")
        inst_map = {inst['tradingsymbol']: inst['instrument_token'] for inst in instruments}
        print(f"  ✓ Loaded {len(inst_map):,} NSE equity instruments")
    except Exception as e:
        print(f"❌ Failed to fetch instruments: {e}")
        sys.exit(1)

    successful = []
    failed = []

    for symbol in FLEET_SYMBOLS:
        if symbol in inst_map:
            token = inst_map[symbol]
            try:
                df = download_symbol_history(symbol, token, years=5)
                if df is not None:
                    successful.append((symbol, len(df)))
            except Exception as e:
                print(f"[!] Exception for {symbol}: {e}")
                failed.append(symbol)
        else:
            print(f"[!] {symbol} not found in NSE master")
            failed.append(symbol)

    # ========================================================================
    # 6. SUMMARY
    # ========================================================================
    print("\n" + "=" * 75)
    print("📊 INGESTION SUMMARY")
    print("=" * 75)
    print(f"\n✅ Success: {len(successful)}/{len(FLEET_SYMBOLS)}")
    for sym, count in successful:
        print(f"  ✓ {sym:15s} → {count:>8,} bars")

    if failed:
        print(f"\n❌ Failed: {len(failed)}")
        for sym in failed:
            print(f"  ✗ {sym}")

    print(f"\n📁 Data lake: {RAW_DIR}")
    print("=" * 75)

if __name__ == "__main__":
    main()
