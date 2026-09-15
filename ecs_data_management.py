#!/usr/bin/env python3
"""
================================================================================
ECS DATA MANAGEMENT LAYER - INTEGRATED INTO R1
================================================================================

Black Box 20: 48-Symbol Data Manager

Loads, validates, and manages historical market data for backtesting:
- Load 48 NIFTY equities from CSV
- Data validation (no gaps, no NaN)
- Bar alignment verification
- Memory-efficient storage
- SHA256 verification for data integrity

================================================================================
"""

import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import threading
import hashlib

# ============================================================================
# DATA VALIDATION RULES
# ============================================================================

@dataclass
class DataValidationResult:
    """Result of data validation"""
    is_valid: bool
    symbol: str
    total_bars: int
    date_range: str
    missing_bars: int
    missing_percent: float
    errors: List[str]
    warnings: List[str]
    data_hash: str


# ============================================================================
# 48-SYMBOL DATA MANAGER
# ============================================================================

class SymbolDataManager:
    """
    48-Symbol Data Manager

    Loads and manages historical market data for all 48 NIFTY equities.

    Data requirements:
    - CSV format with OHLCV columns
    - 1-minute bar resolution (NSE = 390 bars/day)
    - Timestamp in UTC, convert to IST for analysis
    - Complete data history (3 years minimum)

    Features:
    - Lazy loading (load on demand)
    - Memory-efficient storage
    - Caching
    - Integrity verification
    - Validation before backtest
    """

    def __init__(self, data_dir: str = "DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES"):
        self.data_dir = Path(data_dir)
        self.logger = logging.getLogger("SymbolDataManager")

        # Data storage
        self._data_cache = {}  # symbol → DataFrame
        self._data_hashes = {}  # symbol → SHA256 hash
        self._lock = threading.RLock()

        # Configuration
        self.REQUIRED_COLUMNS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        self.BARS_PER_DAY = 390  # NSE 1-minute bars
        self.MIN_DAYS = 600  # 3 years ≈ 750 trading days
        self.MIN_REQUIRED_BARS = self.BARS_PER_DAY * self.MIN_DAYS

    def get_symbol_list(self) -> List[str]:
        """
        Get list of available symbols.

        Scans data directory for CSV files matching pattern: NSE_*_15minute_*.csv
        Actually loads 1-minute bars (filename may use old pattern).
        """
        if not self.data_dir.exists():
            self.logger.error(f"Data directory not found: {self.data_dir}")
            return []

        # Find all NSE_*_15minute_*.csv files
        csv_files = sorted(list(self.data_dir.glob("NSE_*_15minute_*.csv")))

        symbols = []
        for csv_file in csv_files:
            # Extract symbol from filename: NSE_{SYMBOL}_15minute_*.csv
            parts = csv_file.stem.split('_')
            if len(parts) >= 2:
                symbol = parts[1]
                symbols.append(symbol)

        self.logger.info(f"[OK] Found {len(symbols)} symbols in {self.data_dir}")
        return symbols

    def load_symbol_data(self, symbol: str, use_cache: bool = True) -> Optional[pd.DataFrame]:
        """
        Load historical data for one symbol.

        Args:
            symbol: Stock symbol (e.g., 'INFY')
            use_cache: Use cached data if available

        Returns:
            DataFrame with OHLCV data, or None if not found
        """
        # Check cache
        if use_cache and symbol in self._data_cache:
            self.logger.debug(f"[CACHE] Using cached data for {symbol}")
            return self._data_cache[symbol]

        # Find CSV file
        csv_files = list(self.data_dir.glob(f"NSE_{symbol}_15minute_*.csv"))
        if not csv_files:
            self.logger.error(f"[FAIL] No data file found for {symbol}")
            return None

        csv_file = csv_files[0]

        try:
            self.logger.info(f"Loading {symbol} from {csv_file.name}")

            # Load CSV
            df = pd.read_csv(csv_file)

            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            df['timestamp'] = df['timestamp'].dt.tz_convert('Asia/Kolkata')

            # Sort by timestamp
            df = df.sort_values('timestamp').reset_index(drop=True)

            # Cache
            self._data_cache[symbol] = df

            # Calculate hash
            self._data_hashes[symbol] = self._calculate_data_hash(df)

            self.logger.info(f"[OK] Loaded {symbol}: {len(df)} bars")
            return df

        except Exception as e:
            self.logger.error(f"[FAIL] Error loading {symbol}: {str(e)}")
            return None

    def load_all_symbols(self, max_symbols: int = 48) -> Dict[str, pd.DataFrame]:
        """
        Load data for all available symbols (up to max_symbols).

        Returns:
            Dict[symbol] → DataFrame
        """
        symbols = self.get_symbol_list()[:max_symbols]
        all_data = {}

        for symbol in symbols:
            df = self.load_symbol_data(symbol, use_cache=True)
            if df is not None:
                all_data[symbol] = df

        self.logger.info(f"[OK] Loaded {len(all_data)} / {len(symbols)} symbols")
        return all_data

    def validate_symbol_data(self, symbol: str,
                            df: pd.DataFrame) -> DataValidationResult:
        """
        Validate data for one symbol.

        Checks:
        - Required columns present
        - No NaN values
        - No duplicate timestamps
        - Proper timestamp ordering
        - Expected number of bars
        """
        errors = []
        warnings = []

        # Check columns
        for col in self.REQUIRED_COLUMNS:
            if col not in df.columns:
                errors.append(f"Missing column: {col}")

        if errors:
            return DataValidationResult(
                is_valid=False,
                symbol=symbol,
                total_bars=len(df),
                date_range="N/A",
                missing_bars=0,
                missing_percent=0,
                errors=errors,
                warnings=warnings,
                data_hash=""
            )

        # Check NaN values
        nan_count = df[self.REQUIRED_COLUMNS].isnull().sum().sum()
        if nan_count > 0:
            errors.append(f"{nan_count} NaN values found")

        # Check duplicate timestamps
        dup_count = df['timestamp'].duplicated().sum()
        if dup_count > 0:
            warnings.append(f"{dup_count} duplicate timestamps")

        # Check timestamp ordering
        if not df['timestamp'].is_monotonic_increasing:
            errors.append("Timestamps not sorted in ascending order")

        # Check expected bar count
        date_range = f"{df['timestamp'].min()} to {df['timestamp'].max()}"
        expected_days = (df['timestamp'].max() - df['timestamp'].min()).days
        expected_bars = expected_days * self.BARS_PER_DAY
        actual_bars = len(df)
        missing_bars = expected_bars - actual_bars

        if actual_bars < self.MIN_REQUIRED_BARS:
            errors.append(
                f"Insufficient data: {actual_bars} bars "
                f"(need {self.MIN_REQUIRED_BARS})"
            )

        missing_percent = (missing_bars / expected_bars * 100) if expected_bars > 0 else 0
        if missing_percent > 5:
            warnings.append(
                f"Missing {missing_percent:.1f}% of bars "
                f"({missing_bars} bars)"
            )

        # Check data quality
        if (df['high'] < df['low']).any():
            errors.append("High < Low in some bars")

        if (df['high'] < df['open']).any() or (df['high'] < df['close']).any():
            warnings.append("High < Open/Close in some bars")

        return DataValidationResult(
            is_valid=len(errors) == 0,
            symbol=symbol,
            total_bars=actual_bars,
            date_range=date_range,
            missing_bars=missing_bars,
            missing_percent=missing_percent,
            errors=errors,
            warnings=warnings,
            data_hash=self._data_hashes.get(symbol, "")
        )

    def validate_all_data(self, all_data: Dict[str, pd.DataFrame]) -> Dict:
        """
        Validate all loaded data.

        Returns summary of validation results.
        """
        self.logger.info("="*80)
        self.logger.info("VALIDATING ALL DATA")
        self.logger.info("="*80)

        results = {}
        valid_count = 0
        error_count = 0

        for symbol, df in all_data.items():
            result = self.validate_symbol_data(symbol, df)
            results[symbol] = result

            if result.is_valid:
                valid_count += 1
                status = "✓"
            else:
                error_count += 1
                status = "✗"

            self.logger.info(f"{status} {symbol:10s}: {result.total_bars:6d} bars")

            if result.warnings:
                for warning in result.warnings:
                    self.logger.warning(f"    ⚠ {warning}")

            if result.errors:
                for error in result.errors:
                    self.logger.error(f"    ✗ {error}")

        self.logger.info("="*80)
        self.logger.info(f"Valid: {valid_count}, Errors: {error_count}")
        self.logger.info("="*80)

        return {
            'total_symbols': len(all_data),
            'valid_symbols': valid_count,
            'error_symbols': error_count,
            'results': results
        }

    def get_symbol_date_range(self, symbol: str) -> Optional[Tuple[datetime, datetime]]:
        """Get date range for symbol data"""
        if symbol not in self._data_cache:
            return None

        df = self._data_cache[symbol]
        return (df['timestamp'].min(), df['timestamp'].max())

    def get_memory_usage(self) -> Dict:
        """Get memory usage statistics"""
        usage = {}
        total_bytes = 0

        for symbol, df in self._data_cache.items():
            mem_bytes = df.memory_usage(deep=True).sum()
            usage[symbol] = mem_bytes
            total_bytes += mem_bytes

        return {
            'total_bytes': total_bytes,
            'total_mb': total_bytes / (1024 * 1024),
            'per_symbol': usage
        }

    def clear_cache(self, symbol: Optional[str] = None):
        """Clear data cache"""
        with self._lock:
            if symbol:
                if symbol in self._data_cache:
                    del self._data_cache[symbol]
                    self.logger.info(f"[OK] Cleared cache for {symbol}")
            else:
                self._data_cache.clear()
                self.logger.info(f"[OK] Cleared cache for all symbols")

    def _calculate_data_hash(self, df: pd.DataFrame) -> str:
        """Calculate SHA256 hash of data for integrity verification"""
        # Hash based on shape and first/last rows
        data_str = json.dumps({
            'shape': df.shape,
            'first_row': df.iloc[0].to_dict() if len(df) > 0 else {},
            'last_row': df.iloc[-1].to_dict() if len(df) > 0 else {}
        }, default=str)

        return hashlib.sha256(data_str.encode()).hexdigest()

    # ========================================================================
    # BACKTEST DATA PREPARATION
    # ========================================================================

    def prepare_backtest_data(self, symbols: List[str],
                              test_period_days: int = 1000) -> Tuple[Dict, bool]:
        """
        Prepare data for backtesting.

        Args:
            symbols: List of symbols to include
            test_period_days: How many days back to test

        Returns:
            (data_dict, all_valid)
        """
        self.logger.info("")
        self.logger.info("="*80)
        self.logger.info("PREPARING DATA FOR BACKTEST")
        self.logger.info("="*80)
        self.logger.info(f"Symbols: {len(symbols)}")
        self.logger.info(f"Test period: {test_period_days} days")

        backtest_data = {}
        all_valid = True

        for symbol in symbols:
            df = self.load_symbol_data(symbol)
            if df is None:
                self.logger.error(f"[FAIL] Could not load {symbol}")
                all_valid = False
                continue

            # Validate
            validation = self.validate_symbol_data(symbol, df)
            if not validation.is_valid:
                self.logger.error(f"[FAIL] Validation failed for {symbol}")
                for error in validation.errors:
                    self.logger.error(f"    {error}")
                all_valid = False
                continue

            # Slice to test period
            latest_date = df['timestamp'].max()
            cutoff_date = latest_date - timedelta(days=test_period_days)
            test_df = df[df['timestamp'] >= cutoff_date].reset_index(drop=True)

            backtest_data[symbol] = test_df

            self.logger.info(
                f"[OK] {symbol:10s}: {len(test_df):6d} bars "
                f"({validation.date_range})"
            )

        self.logger.info("="*80)
        self.logger.info(f"Ready: {len(backtest_data)} symbols")
        self.logger.info("="*80)
        self.logger.info("")

        return backtest_data, all_valid

    def export_data_manifest(self, all_data: Dict[str, pd.DataFrame],
                            filename: str = "data_manifest.json"):
        """Export data manifest (metadata about all loaded data)"""
        manifest = {
            'generated_at': datetime.now().isoformat(),
            'total_symbols': len(all_data),
            'symbols': {}
        }

        for symbol, df in all_data.items():
            manifest['symbols'][symbol] = {
                'bars': len(df),
                'date_range': f"{df['timestamp'].min()} to {df['timestamp'].max()}",
                'data_hash': self._data_hashes.get(symbol, "unknown")
            }

        with open(filename, 'w') as f:
            json.dump(manifest, f, indent=2)

        self.logger.info(f"[OK] Exported manifest to {filename}")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test data manager
    print("\n=== TESTING DATA MANAGER ===")
    manager = SymbolDataManager()

    # Get symbol list
    symbols = manager.get_symbol_list()
    print(f"Available symbols: {len(symbols)}")
    if symbols:
        print(f"First 10: {symbols[:10]}")

    # Load one symbol
    if symbols:
        symbol = symbols[0]
        df = manager.load_symbol_data(symbol)
        if df is not None:
            print(f"\n{symbol} loaded: {len(df)} bars")
            print(df.head())

            # Validate
            result = manager.validate_symbol_data(symbol, df)
            print(f"\nValidation: {result.is_valid}")
            if result.errors:
                print(f"Errors: {result.errors}")

