import sys
sys.path.insert(0, ".")

import shutil
import time

import pandas as pd
import pytest

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.data_loader_arctic import ArcticMarketDataLoader


@pytest.fixture()
def arctic_db_path(tmp_path):
    path = str(tmp_path / "arctic_test_db")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _manifest():
    return DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")


def test_real_symbol_round_trips_through_arctic(arctic_db_path):
    manifest = _manifest()
    symbol = manifest.files[0].symbol
    filename = manifest.files[0].filename

    loader = ArcticMarketDataLoader(arctic_db_path, manifest.data_dir)
    result = loader.ingest_symbol(symbol, filename)
    assert result["ingested"] is True
    assert result["rows"] > 1000

    stored = loader.load_symbol(symbol)
    assert stored is not None
    assert len(stored) == result["rows"]
    assert list(stored.columns[:5]) == ["timestamp", "open", "high", "low", "close"] or "close" in stored.columns
    assert stored["close"].gt(0).all()


def test_ingestion_is_idempotent_unless_forced(arctic_db_path):
    manifest = _manifest()
    symbol, filename = manifest.files[0].symbol, manifest.files[0].filename
    loader = ArcticMarketDataLoader(arctic_db_path, manifest.data_dir)
    first = loader.ingest_symbol(symbol, filename)
    second = loader.ingest_symbol(symbol, filename)
    assert first["ingested"] is True
    assert second["ingested"] is False
    forced = loader.ingest_symbol(symbol, filename, force=True)
    assert forced["ingested"] is True


def test_reading_from_arctic_is_faster_than_reparsing_the_csv(arctic_db_path):
    manifest = _manifest()
    symbol, filename = manifest.files[0].symbol, manifest.files[0].filename
    loader = ArcticMarketDataLoader(arctic_db_path, manifest.data_dir)
    loader.ingest_symbol(symbol, filename)

    csv_loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

    t0 = time.perf_counter()
    for _ in range(5):
        csv_loader._load_symbol_csv(symbol)
    csv_seconds = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(5):
        loader.load_symbol(symbol)
    arctic_seconds = time.perf_counter() - t0

    assert arctic_seconds < csv_seconds, f"expected arctic ({arctic_seconds:.4f}s) faster than CSV re-parse ({csv_seconds:.4f}s) over 5 reads"
