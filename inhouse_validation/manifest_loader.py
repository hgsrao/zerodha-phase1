"""Manifest-verified 48-symbol data loader for sealed validation replays.

Loads all 48 files declared by the canonical manifest and verifies every
SHA-256 hash before processing. Fails closed on any mismatch.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class ManifestFile:
    """One declared file entry from the manifest."""
    filename: str
    symbol: str
    sha256: str
    first_timestamp: str
    last_timestamp: str
    row_count: int
    size_bytes: int


@dataclass
class DataManifest:
    """Validated manifest for 48-symbol dataset."""
    built_at: str
    data_dir: str
    files: List[ManifestFile]
    manifest_hash: str  # SHA-256 of the entire manifest

    def get_symbols(self) -> List[str]:
        return [f.symbol for f in self.files]

    def get_file(self, symbol: str) -> Optional[ManifestFile]:
        for f in self.files:
            if f.symbol == symbol:
                return f
        return None


class ManifestLoader:
    """Load and verify manifest, then load/hash all declared files."""

    def __init__(self, manifest_path: str):
        self.manifest_path = Path(manifest_path)
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> DataManifest:
        """Load and parse manifest JSON."""
        with open(self.manifest_path, 'r') as f:
            data = json.load(f)

        files = [
            ManifestFile(
                filename=entry['filename'],
                symbol=entry['symbol'],
                sha256=entry['sha256'],
                first_timestamp=entry['first_timestamp'],
                last_timestamp=entry['last_timestamp'],
                row_count=entry['row_count'],
                size_bytes=entry['size_bytes'],
            )
            for entry in data.get('files', [])
        ]

        # Compute manifest hash (for audit trail)
        manifest_json = json.dumps(data, sort_keys=True, separators=(',', ':'), default=str)
        manifest_hash = hashlib.sha256(manifest_json.encode('utf-8')).hexdigest()

        return DataManifest(
            built_at=data.get('built_at', ''),
            data_dir=data.get('data_dir', ''),
            files=files,
            manifest_hash=manifest_hash,
        )

    def verify_and_load(self, use_nse_data_dir: bool = True) -> Dict[str, pd.DataFrame]:
        """
        Verify all 48 file hashes and load into memory.

        Args:
            use_nse_data_dir: If True, use $NSE_DATA_DIR env var; else use manifest dir.

        Returns:
            Dict[symbol -> DataFrame] with all 48 symbols.

        Raises:
            RuntimeError: If any file is missing, altered, or hash mismatches.
        """
        # Determine data directory
        if use_nse_data_dir:
            data_dir = os.environ.get('NSE_DATA_DIR', self.manifest.data_dir)
        else:
            data_dir = self.manifest.data_dir

        if not data_dir:
            raise RuntimeError("No data directory specified (NSE_DATA_DIR or manifest data_dir)")

        data_dir = Path(data_dir)
        if not data_dir.exists():
            raise RuntimeError(f"Data directory does not exist: {data_dir}")

        # Verify and load all 48 files
        results = {}
        for i, file_entry in enumerate(self.manifest.files, 1):
            file_path = data_dir / file_entry.filename
            symbol = file_entry.symbol

            # Check file exists
            if not file_path.exists():
                raise RuntimeError(
                    f"[{i}/48] Missing file for {symbol}: {file_path}"
                )

            # Verify hash
            file_hash = self._compute_file_hash(file_path)
            if file_hash != file_entry.sha256:
                raise RuntimeError(
                    f"[{i}/48] Hash mismatch for {symbol}:\n"
                    f"  Expected: {file_entry.sha256}\n"
                    f"  Got:      {file_hash}"
                )

            # Load CSV
            print(f"[LOAD {i:02d}/48] {symbol:15s} ✓ hash verified")
            df = pd.read_csv(file_path)
            results[symbol] = df

        return results

    @staticmethod
    def _compute_file_hash(file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def get_dataset_hash(self) -> str:
        """Return the manifest's dataset hash (all 48 files)."""
        return self.manifest.manifest_hash

    def get_symbols(self) -> List[str]:
        """Return list of all 48 symbols in order."""
        return self.manifest.get_symbols()
