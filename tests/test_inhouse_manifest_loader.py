"""Unit tests for manifest loader with hash verification."""

import json
import tempfile
from pathlib import Path

import pytest

from inhouse_validation.manifest_loader import ManifestLoader


def test_manifest_load_and_hash():
    """Test manifest loading and hash computation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_data = {
            "built_at": "2026-09-08T12:00:00+00:00",
            "data_dir": tmpdir,
            "files": [
                {
                    "filename": "NSE_SUNPHARMA_minute_2023-07-03_2026-08-24.csv",
                    "symbol": "SUNPHARMA",
                    "sha256": "abc123def456",
                    "first_timestamp": "2023-07-03T09:15:00+05:30",
                    "last_timestamp": "2026-08-24T15:14:00+05:30",
                    "row_count": 100000,
                    "size_bytes": 5000000,
                }
            ],
        }

        manifest_path = Path(tmpdir) / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f)

        loader = ManifestLoader(str(manifest_path))
        assert loader.manifest.built_at == "2026-09-08T12:00:00+00:00"
        assert len(loader.manifest.files) == 1
        assert loader.manifest.files[0].symbol == "SUNPHARMA"
        assert loader.get_dataset_hash() is not None  # Should compute hash


def test_manifest_missing_file_raises():
    """Test that missing manifest file raises error."""
    with pytest.raises(FileNotFoundError):
        ManifestLoader("/nonexistent/manifest.json")


def test_manifest_get_symbols():
    """Test extracting symbol list from manifest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_data = {
            "built_at": "2026-09-08T12:00:00+00:00",
            "data_dir": tmpdir,
            "files": [
                {
                    "filename": f"NSE_{sym}_minute.csv",
                    "symbol": sym,
                    "sha256": f"hash_{sym}",
                    "first_timestamp": "2023-07-03T09:15:00+05:30",
                    "last_timestamp": "2026-08-24T15:14:00+05:30",
                    "row_count": 100000,
                    "size_bytes": 5000000,
                }
                for sym in ["SUNPHARMA", "MAXHEALTH", "HDFCBANK"]
            ],
        }

        manifest_path = Path(tmpdir) / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f)

        loader = ManifestLoader(str(manifest_path))
        symbols = loader.get_symbols()
        assert set(symbols) == {"SUNPHARMA", "MAXHEALTH", "HDFCBANK"}


def test_manifest_get_file():
    """Test retrieving a specific file entry by symbol."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_data = {
            "built_at": "2026-09-08T12:00:00+00:00",
            "data_dir": tmpdir,
            "files": [
                {
                    "filename": "NSE_SUNPHARMA_minute.csv",
                    "symbol": "SUNPHARMA",
                    "sha256": "abc123",
                    "first_timestamp": "2023-07-03T09:15:00+05:30",
                    "last_timestamp": "2026-08-24T15:14:00+05:30",
                    "row_count": 100000,
                    "size_bytes": 5000000,
                }
            ],
        }

        manifest_path = Path(tmpdir) / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f)

        loader = ManifestLoader(str(manifest_path))
        file_entry = loader.manifest.get_file("SUNPHARMA")
        assert file_entry is not None
        assert file_entry.symbol == "SUNPHARMA"
        assert file_entry.sha256 == "abc123"

        # Non-existent symbol
        assert loader.manifest.get_file("NOTFOUND") is None
