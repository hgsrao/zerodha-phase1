"""Frozen identity checks for the external Grid context feeds.

The stock-data manifest seals the traded universe.  This companion module
does the same for the Nifty 50 and India VIX feeds used by Grid *shadow*
telemetry.  Context is never substituted with synthetic data or a neutral VIX
value: missing or altered context fails verification before a run starts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd


@dataclass(frozen=True)
class ContextFileRecord:
    name: str
    path: str
    sha256: str
    size_bytes: int
    row_count: int
    timestamp_column: str
    first_timestamp: str
    last_timestamp: str


@dataclass(frozen=True)
class ExogenousContextManifest:
    files: List[ContextFileRecord]
    manifest_hash: str

    def as_dict(self) -> Dict:
        return {
            "files": [asdict(record) for record in self.files],
            "manifest_hash": self.manifest_hash,
        }

    @staticmethod
    def load(path: str | Path) -> "ExogenousContextManifest":
        payload = json.loads(Path(path).read_text())
        records = [ContextFileRecord(**record) for record in payload["files"]]
        return ExogenousContextManifest(records, payload["manifest_hash"])


@dataclass(frozen=True)
class ContextVerificationResult:
    valid: bool
    checked_files: int
    mismatched: List[str]
    missing: List[str]
    message: str


def _sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_exogenous_context_manifest(records: List[tuple[str, str, str]]) -> ExogenousContextManifest:
    """Build a manifest from ``(name, path, timestamp_column)`` records."""
    built: List[ContextFileRecord] = []
    for name, raw_path, timestamp_column in sorted(records):
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"context feed missing: {name}: {path}")
        timestamps = pd.read_csv(path, usecols=[timestamp_column])[timestamp_column]
        if timestamps.empty:
            raise ValueError(f"context feed is empty: {name}")
        built.append(ContextFileRecord(
            name=name,
            path=str(path),
            sha256=_sha256(path),
            size_bytes=path.stat().st_size,
            row_count=len(timestamps),
            timestamp_column=timestamp_column,
            first_timestamp=str(timestamps.iloc[0]),
            last_timestamp=str(timestamps.iloc[-1]),
        ))
    return ExogenousContextManifest(built, _manifest_hash(built))


def _manifest_hash(records: List[ContextFileRecord]) -> str:
    body = json.dumps([asdict(record) for record in records], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def verify_exogenous_context_manifest(manifest: ExogenousContextManifest) -> ContextVerificationResult:
    """Re-hash every declared context file and fail closed on any mismatch."""
    missing: List[str] = []
    mismatched: List[str] = []
    if manifest.manifest_hash != _manifest_hash(manifest.files):
        mismatched.append("MANIFEST_IDENTITY")
    for record in manifest.files:
        path = Path(record.path)
        if not path.is_file():
            missing.append(record.name)
        elif _sha256(path) != record.sha256:
            mismatched.append(record.name)
    valid = not missing and not mismatched
    message = (
        f"{len(manifest.files)} context feeds verified byte-identical"
        if valid else f"context integrity check failed: {len(mismatched)} mismatched, {len(missing)} missing"
    )
    return ContextVerificationResult(valid, len(manifest.files), mismatched, missing, message)
