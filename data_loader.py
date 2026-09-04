#!/usr/bin/env python3
"""
================================================================================
DATA LOADER - PHASE 2 BACKTEST
================================================================================

Loads frozen 3-year historical OHLCV data for 5-symbol paper trading backtest.

Symbols: INFY, TCS, RELIANCE, SUNPHARMA, HDFCLIFE
Period: 3 years (Jan 2022 - Dec 2024)
Frequency: 15-minute bars

================================================================================
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
import json
import os

class DataLoader:
    """
    Loads and validates frozen historical OHLCV data for backtesting.
    """

    def __init__(self, data_folder: str = "./data"):
        """
        Initialize data loader.

        Args:
            data_folder: Path to frozen data files
        """
        self.data_folder = data_folder
        self.data_cache = {}
        self.symbols = ['INFY', 'TCS', 'RELIANCE', 'SUNPHARMA', 'HDFCLIFE']

    def load_symbol_data(self, symbol: str, start_date: str = "2022-01-01",
                        end_date: str = "2024-12-31") -> pd.DataFrame:
        """
        Load OHLCV data for a symbol.

        Args:
            symbol: Stock symbol (e.g., 'INFY')
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with OHLCV data
        """

        # Try to load from cache first
        if symbol in self.data_cache:
            return self.data_cache[symbol]

        # Try to load from CSV file
        csv_path = os.path.join(self.data_folder, f"{symbol}_15min.csv")

        try:
            df = pd.read_csv(csv_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)

            # Filter by date range
            df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]

            # Validate required columns
            required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in required_cols):
                raise ValueError(f"Missing columns in {symbol} data")

            # Cache it
            self.data_cache[symbol] = df

            return df

        except FileNotFoundError:
            # If file not found, generate synthetic data for testing
            return self._generate_synthetic_data(symbol, start_date, end_date)

    def _generate_synthetic_data(self, symbol: str, start_date: str,
                                 end_date: str) -> pd.DataFrame:
        """
        Generate synthetic OHLCV data for testing.
        (When frozen data is not available)

        Args:
            symbol: Stock symbol
            start_date: Start date
            end_date: End date

        Returns:
            Synthetic DataFrame
        """

        # Price ranges for different symbols
        price_ranges = {
            'INFY': (1200, 1800),
            'TCS': (3200, 4200),
            'RELIANCE': (2000, 2800),
            'SUNPHARMA': (600, 900),
            'HDFCLIFE': (580, 850)
        }

        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)

        # Generate 15-minute bars
        timestamps = pd.date_range(start=start, end=end, freq='15min')

        # Filter business hours only (9:15 AM to 3:30 PM IST)
        mask = (timestamps.hour >= 9) | ((timestamps.hour == 9) & (timestamps.minute >= 15))
        mask = mask & ((timestamps.hour < 15) | ((timestamps.hour == 15) & (timestamps.minute <= 30)))
        timestamps = timestamps[mask]

        # Generate synthetic OHLCV
        np.random.seed(hash(symbol) % 2**32)

        base_price = price_ranges.get(symbol, (1000, 1500))[0]
        prices = base_price + np.cumsum(np.random.randn(len(timestamps)) * 5)

        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': prices + np.random.randn(len(timestamps)) * 2,
            'high': prices + np.abs(np.random.randn(len(timestamps)) * 3),
            'low': prices - np.abs(np.random.randn(len(timestamps)) * 3),
            'close': prices,
            'volume': np.random.randint(1000, 10000, len(timestamps))
        })

        # Ensure OHLC integrity
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)

        return df

    def load_all_symbols(self, start_date: str = "2022-01-01",
                        end_date: str = "2024-12-31") -> Dict[str, pd.DataFrame]:
        """
        Load data for all 5 symbols.

        Args:
            start_date: Start date
            end_date: End date

        Returns:
            Dict with symbol -> DataFrame mapping
        """

        data = {}
        for symbol in self.symbols:
            try:
                data[symbol] = self.load_symbol_data(symbol, start_date, end_date)
                print(f"✅ Loaded {symbol}: {len(data[symbol])} bars")
            except Exception as e:
                print(f"❌ Failed to load {symbol}: {e}")

        return data

    def validate_data(self, data: Dict[str, pd.DataFrame]) -> bool:
        """
        Validate data integrity.

        Args:
            data: Symbol -> DataFrame mapping

        Returns:
            True if valid, False otherwise
        """

        for symbol, df in data.items():
            # Check for missing values
            if df.isnull().any().any():
                print(f"❌ {symbol}: Missing values found")
                return False

            # Check OHLC ordering
            if not (df['low'] <= df['open']).all() or not (df['open'] <= df['high']).all():
                print(f"❌ {symbol}: OHLC ordering invalid")
                return False

            # Check for zero volume
            if (df['volume'] <= 0).any():
                print(f"❌ {symbol}: Zero volume bars found")
                return False

        print("✅ All data validated successfully")
        return True

    def get_bar(self, symbol: str, data: Dict[str, pd.DataFrame],
               idx: int) -> Optional[dict]:
        """
        Get a single bar at index.

        Args:
            symbol: Stock symbol
            data: Symbol -> DataFrame mapping
            idx: Bar index

        Returns:
            Bar data as dict, or None if index out of range
        """

        if symbol not in data:
            return None

        df = data[symbol]
        if idx >= len(df):
            return None

        row = df.iloc[idx]
        return {
            'timestamp': row['timestamp'],
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': int(row['volume'])
        }


if __name__ == "__main__":
    # Test the data loader
    loader = DataLoader()

    print("\n" + "="*80)
    print("DATA LOADER TEST - 5 Symbol, 3-Year Historical Data")
    print("="*80)

    # Load data
    data = loader.load_all_symbols(
        start_date="2022-01-01",
        end_date="2024-12-31"
    )

    # Validate
    print("\nValidating data...")
    loader.validate_data(data)

    # Show summary
    print("\nData Summary:")
    for symbol, df in data.items():
        print(f"  {symbol}: {len(df)} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")

    print("\n" + "="*80 + "\n")
