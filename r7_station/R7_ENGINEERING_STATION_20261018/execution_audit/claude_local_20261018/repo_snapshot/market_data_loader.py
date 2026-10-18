#!/usr/bin/env python3
"""Market data loader for the Revision 2 engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class MarketUniverseStatus:
    symbol_count: int
    loaded_symbols: int
    missing_symbols: List[str]
    valid: bool
    synthetic: bool
    message: str


class MarketDataLoader:
    SYMBOLS = [
        "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
        "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
        "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
        "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
        "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
        "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
        "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID",
        "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
        "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN",
        "TRENT", "ULTRACEMCO", "WIPRO",
    ]

    REQUIRED_COLS = {"open", "high", "low", "close", "volume"}

    def __init__(self, data_dir: Optional[str] = None, synthetic_if_missing: bool = False):
        self.data_dir = Path(data_dir) if data_dir else Path.cwd()
        self.synthetic_if_missing = synthetic_if_missing

    def _synthetic_series(self, symbol: str, rows: int = 250) -> pd.DataFrame:
        base = datetime(2024, 1, 1)
        price = 100.0 + (sum(ord(ch) for ch in symbol) % 80)
        timestamps = [base + timedelta(minutes=15 * i) for i in range(rows)]
        rows_data = []
        for i in range(rows):
            drift = (i / max(rows, 1)) * 0.12
            shock = ((i % 7) - 3) * 0.008
            close = price * (1 + drift + shock)
            open_ = close * (1 - 0.003)
            high = max(open_, close) * (1 + 0.008)
            low = min(open_, close) * (1 - 0.008)
            volume = 1200 + (i * 17) % 8000
            rows_data.append({
                "timestamp": timestamps[i],
                "open": round(open_, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": int(volume),
            })
        df = pd.DataFrame(rows_data)
        df["symbol"] = symbol
        return df

    def _resolve_csv(self, symbol: str) -> Optional[Path]:
        if not self.data_dir.exists():
            return None

        # Exact canonical filename first — this is the only safe match for
        # short symbols like "LT", which a substring wildcard (`*LT*`) would
        # also match inside unrelated files such as "...ULTRACEMCO...".
        exact_candidates = [
            self.data_dir / f"NSE_{symbol}_minute_2023-07-03_2026-08-24.csv",
        ]
        for candidate in exact_candidates:
            if candidate.is_file():
                return candidate
        exact_matches = [p for p in self.data_dir.rglob(f"NSE_{symbol}_minute_*.csv") if p.is_file()]
        if exact_matches:
            return exact_matches[0]

        # Fallback for other on-disk layouts, but anchored to underscore/
        # dash/dot boundaries so "LT" can't match inside "ULTRACEMCO".
        import re
        token_pattern = re.compile(rf"(^|[_\-.]){re.escape(symbol)}([_\-.]|$)", re.IGNORECASE)
        for match in self.data_dir.rglob("*.csv"):
            if match.is_file() and token_pattern.search(match.stem):
                return match
        return None

    def _load_symbol_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        path = self._resolve_csv(symbol)
        if path is None:
            return None
        df = pd.read_csv(path)
        if df.empty:
            return None
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        elif "date" in df.columns:
            df["timestamp"] = pd.to_datetime(df["date"], errors="coerce")
        else:
            return None
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df

    def _validate_dataframe(self, df: pd.DataFrame) -> bool:
        if df is None or df.empty:
            return False
        if not {col.lower() for col in df.columns}.issuperset(self.REQUIRED_COLS):
            return False
        cols = {col.lower(): col for col in df.columns}
        required = ["open", "high", "low", "close", "volume"]
        if not all(col in cols for col in required):
            return False
        df = df.sort_values("timestamp").reset_index(drop=True)
        return bool(len(df) > 0 and df["timestamp"].is_monotonic_increasing)

    def load_universe(self) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        missing: List[str] = []
        synthetic_used = False
        for symbol in self.SYMBOLS:
            df = self._load_symbol_csv(symbol)
            if df is None:
                if self.synthetic_if_missing:
                    df = self._synthetic_series(symbol)
                    synthetic_used = True
                else:
                    missing.append(symbol)
                    continue
            elif not self._validate_dataframe(df):
                if self.synthetic_if_missing:
                    df = self._synthetic_series(symbol)
                    synthetic_used = True
                else:
                    missing.append(symbol)
                    continue
            frames[symbol] = df

        if missing:
            joined = ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
            if self.synthetic_if_missing:
                return frames
            raise ValueError(f"missing real market data for required symbols: {joined}")

        if synthetic_used:
            raise ValueError("Synthetic fallback is prohibited for production data validation")

        return frames

    def validate_market_universe(self, frames: Dict[str, pd.DataFrame]) -> MarketUniverseStatus:
        loaded = sorted(frames.keys())
        missing = [s for s in self.SYMBOLS if s not in loaded]
        valid = len(loaded) == len(self.SYMBOLS) and len(missing) == 0
        synthetic = any("synthetic" in str(type(df)) for df in frames.values())
        if valid and not synthetic:
            message = "48-symbol universe loaded and validated"
        elif synthetic:
            message = "Synthetic fallback detected; real market data required for production validation"
        else:
            message = "Missing real market data: production validation failed"
        return MarketUniverseStatus(
            symbol_count=len(self.SYMBOLS),
            loaded_symbols=len(loaded),
            missing_symbols=missing,
            valid=valid and not synthetic,
            synthetic=synthetic,
            message=message,
        )


@dataclass
class MarketTick:
    """One bar, shaped the way a live/delayed feed would hand it to the runtime."""

    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class SingleSymbolReplayFeed:
    """Replays one symbol's real, frozen historical bars in timestamp order.

    This is NOT a live feed. No Kite credentials, websocket, or network call
    is involved anywhere in this class. It exists to let a paper-mode session
    be driven by real market data (real prices, real timestamps, real volume)
    until a live or delayed Kite ticker is actually wired in. Every tick it
    yields is read from disk, never fabricated.
    """

    def __init__(self, symbol: str, data_dir: Optional[str] = None, max_bars: Optional[int] = None):
        self.symbol = symbol
        self.loader = MarketDataLoader(data_dir, synthetic_if_missing=False)
        self.max_bars = max_bars
        self._df: Optional[pd.DataFrame] = None

    def load(self) -> pd.DataFrame:
        df = self.loader._load_symbol_csv(self.symbol)
        if df is None:
            raise FileNotFoundError(
                f"no real historical CSV found for {self.symbol} under {self.loader.data_dir}"
            )
        if not self.loader._validate_dataframe(df):
            raise ValueError(f"historical data for {self.symbol} failed schema/ordering validation")
        if self.max_bars:
            df = df.tail(self.max_bars).reset_index(drop=True)
        self._df = df
        return df

    def __iter__(self):
        if self._df is None:
            self.load()
        cols = {c.lower(): c for c in self._df.columns}
        for _, row in self._df.iterrows():
            yield MarketTick(
                symbol=self.symbol,
                timestamp=str(row[cols.get("timestamp", "timestamp")]),
                open=float(row[cols["open"]]),
                high=float(row[cols["high"]]),
                low=float(row[cols["low"]]),
                close=float(row[cols["close"]]),
                volume=int(row[cols["volume"]]),
            )


if __name__ == "__main__":
    loader = MarketDataLoader(synthetic_if_missing=True)
    frames = loader.load_universe()
    print(loader.validate_market_universe(frames))
    print(len(frames['INFY']))
