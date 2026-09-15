#!/usr/bin/env python3
"""
DOWNLOAD REAL NIFTY 50 DATA FROM KITE CONNECT
==============================================

Downloads actual NIFTY 50 1-minute bar data from Kite Connect.
NO synthetic data. NO fake data. ONLY real market data.

This creates a CSV file that the test uses instead of fetching live.
Ensures consistency and reproducibility.

Usage:
    $env:KITE_API_KEY = "your_key"
    $env:KITE_ACCESS_TOKEN = "your_token"
    python DOWNLOAD_REAL_NIFTY50_DATA.py --date 2026-09-15

Or download multiple days:
    python DOWNLOAD_REAL_NIFTY50_DATA.py --from-date 2026-09-10 --to-date 2026-09-15

Output:
    data/NIFTY50_REAL_2026-09-15.csv
    ├─ timestamp, open, high, low, close, volume
    └─ 375 rows (6.25 hours × 60 minutes)
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
import pandas as pd

from kiteconnect import KiteConnect

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('NIFTY50_DOWNLOADER')

# Data directory
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# ============================================================================
# NIFTY 50 DATA DOWNLOADER
# ============================================================================

class Nifty50Downloader:
    """Download real NIFTY 50 data from Kite Connect"""

    def __init__(self, api_key: str, access_token: str):
        """Initialize Kite connection"""
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.nifty_token = None
        logger.info("Kite Connect initialized")

    def get_nifty_instrument_token(self) -> Optional[int]:
        """Get NIFTY 50 instrument token from Kite"""
        try:
            logger.info("Fetching NSE instruments...")
            instruments = self.kite.instruments("NSE")

            # Find NIFTY 50 INDEX
            for instrument in instruments:
                if "NIFTY 50" in instrument['tradingsymbol'].upper():
                    self.nifty_token = instrument['instrument_token']
                    logger.info(f"Found NIFTY 50: token={self.nifty_token}, "
                               f"symbol={instrument['tradingsymbol']}")
                    return self.nifty_token

            logger.error("NIFTY 50 not found in instruments")
            return None

        except Exception as e:
            logger.error(f"Error fetching instruments: {e}")
            return None

    def download_minute_bars(self, date: datetime) -> Optional[pd.DataFrame]:
        """
        Download 1-minute NIFTY 50 bars for a specific date.

        NOTE: This downloads REAL data from Kite Connect.
        NO synthetic data. NO fake data.
        """
        if not self.nifty_token:
            logger.error("NIFTY token not set. Call get_nifty_instrument_token() first.")
            return None

        try:
            from_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
            to_date = date.replace(hour=23, minute=59, second=59, microsecond=999999)

            logger.info(f"Downloading NIFTY 50 bars for {date.strftime('%Y-%m-%d')}...")
            logger.info(f"From: {from_date}")
            logger.info(f"To: {to_date}")

            # Fetch historical data from Kite
            # This is REAL data from NSE, not simulated
            bars = self.kite.historical_data(
                instrument_token=self.nifty_token,
                from_date=from_date,
                to_date=to_date,
                interval="minute"  # 1-minute bars
            )

            if not bars:
                logger.warning(f"No data returned for {date.strftime('%Y-%m-%d')}")
                return None

            # Convert to DataFrame
            df = pd.DataFrame(bars)

            # Rename columns to match our format
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
            df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']

            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            logger.info(f"Downloaded {len(df)} bars for {date.strftime('%Y-%m-%d')}")
            logger.info(f"First bar: {df['timestamp'].iloc[0]} | "
                       f"Close: {df['close'].iloc[0]:.2f}")
            logger.info(f"Last bar: {df['timestamp'].iloc[-1]} | "
                       f"Close: {df['close'].iloc[-1]:.2f}")

            return df

        except Exception as e:
            logger.error(f"Error downloading bars: {e}")
            return None

    def download_multiple_days(self, from_date: datetime, to_date: datetime) -> Optional[pd.DataFrame]:
        """Download NIFTY 50 bars for multiple days"""

        all_bars = []
        current_date = from_date

        while current_date <= to_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                logger.info(f"Skipping {current_date.strftime('%Y-%m-%d')} (weekend)")
                current_date += timedelta(days=1)
                continue

            # Download for this day
            bars = self.download_minute_bars(current_date)

            if bars is not None and len(bars) > 0:
                all_bars.append(bars)
                logger.info(f"✓ Downloaded {len(bars)} bars for {current_date.strftime('%Y-%m-%d')}")
            else:
                logger.warning(f"✗ No bars for {current_date.strftime('%Y-%m-%d')} (market closed?)")

            current_date += timedelta(days=1)

        if not all_bars:
            logger.error("No data downloaded for any day")
            return None

        # Combine all days
        combined = pd.concat(all_bars, ignore_index=True)
        logger.info(f"Total bars across all days: {len(combined)}")

        return combined

    def save_to_csv(self, df: pd.DataFrame, date: datetime) -> Path:
        """Save downloaded data to CSV"""

        filename = f"NIFTY50_REAL_{date.strftime('%Y-%m-%d')}.csv"
        filepath = DATA_DIR / filename

        # Save to CSV
        df.to_csv(filepath, index=False)
        logger.info(f"Saved to: {filepath}")

        # Verify the file
        verify_df = pd.read_csv(filepath)
        logger.info(f"Verified: {len(verify_df)} rows in CSV")

        return filepath

    def load_from_csv(self, date: datetime) -> Optional[pd.DataFrame]:
        """Load previously downloaded data from CSV"""

        filename = f"NIFTY50_REAL_{date.strftime('%Y-%m-%d')}.csv"
        filepath = DATA_DIR / filename

        if not filepath.exists():
            logger.warning(f"File not found: {filepath}")
            return None

        df = pd.read_csv(filepath)
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        logger.info(f"Loaded {len(df)} bars from {filepath}")
        return df


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """Command-line interface"""

    parser = argparse.ArgumentParser(
        description="Download real NIFTY 50 data from Kite Connect"
    )
    parser.add_argument(
        "--date",
        type=str,
        help="Single date to download (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--from-date",
        type=str,
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--to-date",
        type=str,
        help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--load",
        type=str,
        help="Load existing CSV (YYYY-MM-DD)"
    )

    args = parser.parse_args()

    # Get credentials from environment
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key or not access_token:
        logger.error("ERROR: Set KITE_API_KEY and KITE_ACCESS_TOKEN")
        sys.exit(1)

    # Initialize downloader
    downloader = Nifty50Downloader(api_key, access_token)

    # Get instrument token
    if not downloader.get_nifty_instrument_token():
        logger.error("Failed to get NIFTY 50 instrument token")
        sys.exit(1)

    # Handle load option
    if args.load:
        try:
            load_date = datetime.strptime(args.load, "%Y-%m-%d")
            df = downloader.load_from_csv(load_date)

            if df is None:
                sys.exit(1)

            logger.info("\n" + "="*80)
            logger.info("LOADED DATA SUMMARY")
            logger.info("="*80)
            logger.info(f"Date: {args.load}")
            logger.info(f"Total bars: {len(df)}")
            logger.info(f"Period: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
            logger.info(f"Open: {df['open'].iloc[0]:.2f}")
            logger.info(f"High: {df['high'].max():.2f}")
            logger.info(f"Low: {df['low'].min():.2f}")
            logger.info(f"Close: {df['close'].iloc[-1]:.2f}")
            logger.info(f"Avg Volume: {df['volume'].mean():.0f}")
            logger.info("="*80)

            return
        except Exception as e:
            logger.error(f"Error loading: {e}")
            sys.exit(1)

    # Handle single date
    if args.date:
        try:
            date = datetime.strptime(args.date, "%Y-%m-%d")
            df = downloader.download_minute_bars(date)

            if df is None:
                logger.error("Download failed")
                sys.exit(1)

            filepath = downloader.save_to_csv(df, date)

            logger.info("\n" + "="*80)
            logger.info("DOWNLOAD COMPLETE")
            logger.info("="*80)
            logger.info(f"Date: {args.date}")
            logger.info(f"Total bars: {len(df)}")
            logger.info(f"File: {filepath}")
            logger.info(f"Size: {filepath.stat().st_size / 1024:.1f} KB")
            logger.info(f"Open: {df['open'].iloc[0]:.2f}")
            logger.info(f"High: {df['high'].max():.2f}")
            logger.info(f"Low: {df['low'].min():.2f}")
            logger.info(f"Close: {df['close'].iloc[-1]:.2f}")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Error: {e}")
            sys.exit(1)

    # Handle date range
    elif args.from_date and args.to_date:
        try:
            from_date = datetime.strptime(args.from_date, "%Y-%m-%d")
            to_date = datetime.strptime(args.to_date, "%Y-%m-%d")

            df = downloader.download_multiple_days(from_date, to_date)

            if df is None:
                logger.error("Download failed")
                sys.exit(1)

            # Save combined data
            date_range = f"{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}"
            filename = f"NIFTY50_REAL_{date_range}.csv"
            filepath = DATA_DIR / filename

            df.to_csv(filepath, index=False)
            logger.info(f"Saved combined data to: {filepath}")

            logger.info("\n" + "="*80)
            logger.info("DOWNLOAD COMPLETE")
            logger.info("="*80)
            logger.info(f"Date range: {args.from_date} to {args.to_date}")
            logger.info(f"Total bars: {len(df)}")
            logger.info(f"File: {filepath}")
            logger.info(f"Size: {filepath.stat().st_size / 1024:.1f} KB")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Error: {e}")
            sys.exit(1)

    else:
        logger.info("No options provided. Use --help for usage.")
        parser.print_help()


if __name__ == "__main__":
    main()
