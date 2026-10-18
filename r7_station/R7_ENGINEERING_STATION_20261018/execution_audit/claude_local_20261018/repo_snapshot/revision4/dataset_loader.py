"""
Load real NSE data from manifest and CSV files.
"""

import json
import os
from typing import Dict, List, Tuple
import pandas as pd
from revision4.contracts import Bar


class ManifestDataLoader:
    """Load 48-symbol data from manifest."""

    def __init__(self, manifest_path: str, data_dir: str):
        self.manifest_path = manifest_path
        self.data_dir = data_dir
        self.manifest = self._load_manifest()
        self.symbol_files = {f['symbol']: f for f in self.manifest['files']}

    def _load_manifest(self) -> dict:
        """Load and parse manifest JSON."""
        with open(self.manifest_path) as f:
            return json.load(f)

    def load_symbol_data(self, symbol: str) -> pd.DataFrame:
        """Load one symbol's CSV into DataFrame."""
        if symbol not in self.symbol_files:
            raise ValueError(f"Symbol {symbol} not in manifest")

        file_info = self.symbol_files[symbol]
        csv_path = os.path.join(self.data_dir, file_info['filename'])

        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Data file not found: {csv_path}")

        # Load CSV (assume standard NSE format)
        df = pd.read_csv(csv_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        return df.sort_values('timestamp')

    def load_all_symbols(self) -> Dict[str, pd.DataFrame]:
        """Load all 48 symbols."""
        data = {}
        for symbol in self.symbol_files.keys():
            try:
                data[symbol] = self.load_symbol_data(symbol)
            except Exception as e:
                print(f"Warning: Failed to load {symbol}: {e}")
        return data

    def get_symbols(self) -> List[str]:
        """Return list of 48 symbols."""
        return sorted(self.symbol_files.keys())

    def get_bar(self, symbol: str, timestamp: str) -> Bar:
        """Get one bar for symbol at timestamp."""
        df = self.load_symbol_data(symbol)
        row = df[df['timestamp'] == pd.to_datetime(timestamp, utc=True)]
        if row.empty:
            raise ValueError(f"No bar for {symbol} at {timestamp}")

        r = row.iloc[0]
        return Bar(
            timestamp=timestamp,
            symbol=symbol,
            open=float(r['open']),
            high=float(r['high']),
            low=float(r['low']),
            close=float(r['close']),
            volume=int(r['volume']),
        )
