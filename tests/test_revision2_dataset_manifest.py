"""Tests for the frozen, hashed 48-symbol dataset manifest — the first
building block of Stage E (train/validation/test sealing): proving the
data a calibration run touches is exactly the data it claims to be."""

import os
import tempfile
import unittest

import pandas as pd

from revision2.dataset_manifest import build_manifest, verify_manifest


def _write_symbol_csv(path, rows=50):
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01 09:15", periods=rows, freq="min"),
        "open": [100.0] * rows, "high": [101.0] * rows, "low": [99.0] * rows,
        "close": [100.5] * rows, "volume": [1000] * rows,
    })
    df.to_csv(path, index=False)


class TestDatasetManifest(unittest.TestCase):
    def _make_data_dir(self, tmp, symbols):
        for s in symbols:
            _write_symbol_csv(os.path.join(tmp, f"NSE_{s}_minute_2023-07-03_2026-08-24.csv"))

    def test_build_and_verify_round_trip(self):
        symbols = ["AAA", "BBB", "CCC"]
        with tempfile.TemporaryDirectory() as tmp:
            self._make_data_dir(tmp, symbols)
            manifest = build_manifest(tmp, symbols=symbols)
            self.assertEqual(manifest.symbol_count, 3)
            self.assertEqual({f.symbol for f in manifest.files}, set(symbols))

            result = verify_manifest(manifest, data_dir=tmp)
            self.assertTrue(result.valid, result.message)
            self.assertEqual(result.checked_files, 3)
            self.assertEqual(result.mismatched, [])
            self.assertEqual(result.missing, [])

    def test_save_and_load_round_trip(self):
        symbols = ["AAA", "BBB"]
        with tempfile.TemporaryDirectory() as tmp:
            self._make_data_dir(tmp, symbols)
            manifest = build_manifest(tmp, symbols=symbols)
            manifest_path = os.path.join(tmp, "manifest.json")
            manifest.save(manifest_path)

            from revision2.dataset_manifest import DatasetManifest
            reloaded = DatasetManifest.load(manifest_path)
            self.assertEqual(reloaded.manifest_hash, manifest.manifest_hash)
            self.assertEqual(len(reloaded.files), len(manifest.files))

    def test_verify_detects_a_modified_file(self):
        symbols = ["AAA"]
        with tempfile.TemporaryDirectory() as tmp:
            self._make_data_dir(tmp, symbols)
            manifest = build_manifest(tmp, symbols=symbols)

            # Mutate the file after the manifest was frozen.
            path = os.path.join(tmp, "NSE_AAA_minute_2023-07-03_2026-08-24.csv")
            with open(path, "a") as f:
                f.write("2024-01-01 10:00:00,999,999,999,999,999\n")

            result = verify_manifest(manifest, data_dir=tmp)
            self.assertFalse(result.valid)
            self.assertIn("AAA", result.mismatched)

    def test_verify_detects_a_missing_file(self):
        symbols = ["AAA", "BBB"]
        with tempfile.TemporaryDirectory() as tmp:
            self._make_data_dir(tmp, symbols)
            manifest = build_manifest(tmp, symbols=symbols)
            os.remove(os.path.join(tmp, "NSE_BBB_minute_2023-07-03_2026-08-24.csv"))

            result = verify_manifest(manifest, data_dir=tmp)
            self.assertFalse(result.valid)
            self.assertIn("BBB", result.missing)

    def test_manifest_hash_is_deterministic(self):
        symbols = ["AAA", "BBB"]
        with tempfile.TemporaryDirectory() as tmp:
            self._make_data_dir(tmp, symbols)
            m1 = build_manifest(tmp, symbols=symbols)
            m2 = build_manifest(tmp, symbols=symbols)
            self.assertEqual(m1.manifest_hash, m2.manifest_hash)


if __name__ == "__main__":
    unittest.main()
