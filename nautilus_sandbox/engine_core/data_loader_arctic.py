"""Box 2 (DataIngestion) -- ArcticDB-backed market data storage/retrieval.

DataIngestionBox's own job (revision2/boxes.py) is an allow/deny symbol
filter, not data loading -- that's market_data_loader.py. This module
replaces THAT CSV-scanning loader with ArcticDB (Man Group's high-
performance time-series DataFrame store), which is what the "prefer
mature external repositories... instead of rebuilding" principle actually
calls for here: ingesting and re-reading millions of OHLCV rows across 48
symbols from a real columnar time-series store instead of re-parsing CSV
text on every run.

Uses a local, serverless LMDB-backed Arctic instance (lmdb:// URI) -- no
external database server process, consistent with this being a single-
process backtesting/calibration engine, not a distributed system.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from arcticdb import Arctic

from revision2_external.data_certification_pandera import certify_bars

LIBRARY_NAME = "revision2_bars"


def open_arctic(db_path: str) -> Arctic:
    Path(db_path).mkdir(parents=True, exist_ok=True)
    ac = Arctic(f"lmdb://{db_path}")
    if LIBRARY_NAME not in ac.list_libraries():
        ac.create_library(LIBRARY_NAME)
    return ac


class ArcticMarketDataLoader:
    """Drop-in loader for the same role as MarketDataLoader, backed by
    ArcticDB instead of re-reading CSVs every call."""

    def __init__(self, db_path: str, csv_source_dir: str) -> None:
        self.arctic = open_arctic(db_path)
        self.library = self.arctic[LIBRARY_NAME]
        self.csv_source_dir = Path(csv_source_dir)

    def ingest_symbol(self, symbol: str, csv_filename: str, force: bool = False) -> Dict[str, float]:
        """Reads the source CSV once, certifies it (revision2_external.
        data_certification_pandera), and writes it into Arctic. Idempotent:
        skips re-ingestion if the symbol is already stored, unless force."""
        if not force and self.library.has_symbol(symbol):
            return {"ingested": False, "reason": "already present"}
        raw = pd.read_csv(self.csv_source_dir / csv_filename)
        certified, audit = certify_bars(raw)
        t0 = time.perf_counter()
        self.library.write(symbol, certified)
        write_seconds = time.perf_counter() - t0
        return {"ingested": True, "rows": len(certified), "write_seconds": write_seconds, **audit}

    def load_symbol(self, symbol: str, tail: Optional[int] = None) -> Optional[pd.DataFrame]:
        if not self.library.has_symbol(symbol):
            return None
        frame = self.library.read(symbol).data
        return frame.tail(tail).reset_index(drop=True) if tail else frame

    def symbols(self) -> List[str]:
        return sorted(self.library.list_symbols())
