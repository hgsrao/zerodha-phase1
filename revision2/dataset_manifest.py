"""A frozen, hashed manifest of the 48-symbol historical dataset.

This exists so a calibration run can prove — not just assert — that the
data it trained, validated, and tested against is exactly the data it
claims to be, byte for byte, and that no file changed between when the
manifest was built and when a run reads it. It is the first building block
of Stage E (train/validation/test sealing): the sealing itself (date-range
boundaries, single-use test access) is layered on top of this in a
separate module, not here — this module only answers "is this the data I
think it is?"
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from market_data_loader import MarketDataLoader


@dataclass(frozen=True)
class FileRecord:
    symbol: str
    filename: str
    sha256: str
    size_bytes: int
    row_count: int
    first_timestamp: str
    last_timestamp: str


@dataclass(frozen=True)
class DatasetManifest:
    data_dir: str
    built_at: str
    symbol_count: int
    files: List[FileRecord]
    manifest_hash: str

    def as_dict(self) -> Dict:
        return {
            "data_dir": self.data_dir,
            "built_at": self.built_at,
            "symbol_count": self.symbol_count,
            "manifest_hash": self.manifest_hash,
            "files": [asdict(f) for f in self.files],
        }

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True))

    @staticmethod
    def load(path: str) -> "DatasetManifest":
        data = json.loads(Path(path).read_text())
        files = [FileRecord(**f) for f in data["files"]]
        return DatasetManifest(
            data_dir=data["data_dir"], built_at=data["built_at"], symbol_count=data["symbol_count"],
            files=files, manifest_hash=data["manifest_hash"],
        )


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(data_dir: str, symbols: Optional[List[str]] = None) -> DatasetManifest:
    """Hashes every symbol's real CSV on disk — reads and hashes the exact
    bytes a training run would read, not a cached or derived summary."""
    loader = MarketDataLoader(data_dir)
    symbols = symbols or loader.SYMBOLS

    records: List[FileRecord] = []
    for symbol in sorted(symbols):
        path = loader._resolve_csv(symbol)
        if path is None:
            raise FileNotFoundError(f"no real historical CSV found for {symbol} under {data_dir}")
        sha256 = _sha256_file(path)
        size_bytes = path.stat().st_size
        df = pd.read_csv(path, usecols=["timestamp"])
        records.append(FileRecord(
            symbol=symbol, filename=path.name, sha256=sha256, size_bytes=size_bytes,
            row_count=len(df), first_timestamp=str(df["timestamp"].iloc[0]) if len(df) else "",
            last_timestamp=str(df["timestamp"].iloc[-1]) if len(df) else "",
        ))

    payload = json.dumps([asdict(r) for r in records], sort_keys=True, default=str)
    manifest_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return DatasetManifest(
        data_dir=str(Path(data_dir).resolve()),
        built_at=datetime.now(timezone.utc).isoformat(),
        symbol_count=len(records),
        files=records,
        manifest_hash=manifest_hash,
    )


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    checked_files: int
    mismatched: List[str]
    missing: List[str]
    message: str


def verify_manifest(manifest: DatasetManifest, data_dir: Optional[str] = None) -> VerificationResult:
    """Re-hashes the files on disk right now and compares against the
    frozen manifest — this is the actual seal-integrity check a calibration
    run should call before touching any data, not just at manifest-build
    time."""
    loader = MarketDataLoader(data_dir or manifest.data_dir)
    mismatched: List[str] = []
    missing: List[str] = []

    for record in manifest.files:
        path = loader._resolve_csv(record.symbol)
        if path is None:
            missing.append(record.symbol)
            continue
        actual_hash = _sha256_file(path)
        if actual_hash != record.sha256:
            mismatched.append(record.symbol)

    valid = not mismatched and not missing
    if valid:
        message = f"{len(manifest.files)} files verified byte-identical to the frozen manifest"
    else:
        message = f"integrity check failed: {len(mismatched)} mismatched, {len(missing)} missing"

    return VerificationResult(valid=valid, checked_files=len(manifest.files), mismatched=mismatched, missing=missing, message=message)
