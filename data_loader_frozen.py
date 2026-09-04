#!/usr/bin/env python3
"""Data loader - reads ONLY frozen NSE data, FAIL-CLOSED"""
import pandas as pd
from pathlib import Path

class FrozenDataLoader:
    def __init__(self):
        self.frozen_data_path = Path(
            "C:/Users/Dishan/Documents/Codex/Zerodha_live_bot_3.4_ENTRY_UNKNOWN/"
            "historical_data_zerodha_nifty48"
        )

    def load(self, symbol):
        """Load symbol data - FAIL CLOSED if missing"""
        pattern = f"NSE_{symbol}_15minute_*.csv"
        matches = list(self.frozen_data_path.glob(pattern))

        if not matches:
            raise FileNotFoundError(f"FATAL: Frozen data missing for {symbol}")

        df = pd.read_csv(matches[0])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df

    def load_multiple(self, symbols):
        """Load multiple symbols"""
        data = {}
        for symbol in symbols:
            data[symbol] = self.load(symbol)
        return data
