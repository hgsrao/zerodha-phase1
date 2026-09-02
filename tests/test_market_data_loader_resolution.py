"""Regression coverage for the exact-file-matching and universe-validator
fixes: a substring wildcard previously let short symbols like "LT" match
unrelated files (e.g. "...ULTRACEMCO..."), and validate_market_universe had
drifted onto the wrong class entirely.
"""

import os
import tempfile
import unittest

import pandas as pd

from market_data_loader import MarketDataLoader, MarketUniverseStatus


def _write_csv(path, rows=5):
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=rows, freq="min"),
        "open": [100.0] * rows, "high": [101.0] * rows, "low": [99.0] * rows,
        "close": [100.5] * rows, "volume": [1000] * rows,
    })
    df.to_csv(path, index=False)


class TestExactSymbolFileMatching(unittest.TestCase):
    def test_short_symbol_does_not_match_substring_in_longer_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A file whose name contains "LT" as a substring but is not LT.
            _write_csv(os.path.join(tmp, "NSE_ULTRACEMCO_minute_2023-07-03_2026-08-24.csv"))
            loader = MarketDataLoader(tmp)
            self.assertIsNone(loader._resolve_csv("LT"))

    def test_exact_canonical_filename_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_csv(os.path.join(tmp, "NSE_LT_minute_2023-07-03_2026-08-24.csv"))
            _write_csv(os.path.join(tmp, "NSE_ULTRACEMCO_minute_2023-07-03_2026-08-24.csv"))
            loader = MarketDataLoader(tmp)
            resolved = loader._resolve_csv("LT")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.name, "NSE_LT_minute_2023-07-03_2026-08-24.csv")

    def test_validate_market_universe_is_on_market_data_loader(self):
        loader = MarketDataLoader(".")
        self.assertTrue(hasattr(loader, "validate_market_universe"))
        status = loader.validate_market_universe({})
        self.assertIsInstance(status, MarketUniverseStatus)
        self.assertFalse(status.valid)


if __name__ == "__main__":
    unittest.main()
