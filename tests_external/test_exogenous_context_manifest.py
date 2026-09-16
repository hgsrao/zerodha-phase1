import json

import pandas as pd
import pytest

from revision2.exogenous_context_manifest import (
    ExogenousContextManifest,
    build_exogenous_context_manifest,
    verify_exogenous_context_manifest,
)


def _write_feed(path, timestamp_column="timestamp"):
    pd.DataFrame({timestamp_column: ["2024-01-01T09:15:00+05:30"], "close": [100.0]}).to_csv(path, index=False)


def test_context_manifest_round_trip_and_verification(tmp_path):
    nifty = tmp_path / "nifty.csv"
    vix = tmp_path / "vix.csv"
    _write_feed(nifty)
    _write_feed(vix, "date")
    manifest = build_exogenous_context_manifest([
        ("NIFTY", str(nifty), "timestamp"),
        ("VIX", str(vix), "date"),
    ])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest.as_dict()))
    loaded = ExogenousContextManifest.load(path)
    assert verify_exogenous_context_manifest(loaded).valid


def test_context_manifest_fails_on_modified_feed(tmp_path):
    nifty = tmp_path / "nifty.csv"
    _write_feed(nifty)
    manifest = build_exogenous_context_manifest([("NIFTY", str(nifty), "timestamp")])
    nifty.write_text(nifty.read_text() + "2024-01-01T09:30:00+05:30,101\n")
    result = verify_exogenous_context_manifest(manifest)
    assert not result.valid
    assert result.mismatched == ["NIFTY"]


def test_context_manifest_fails_when_declared_identity_is_altered(tmp_path):
    nifty = tmp_path / "nifty.csv"
    _write_feed(nifty)
    manifest = build_exogenous_context_manifest([("NIFTY", str(nifty), "timestamp")])
    forged = ExogenousContextManifest(manifest.files, "0" * 64)
    result = verify_exogenous_context_manifest(forged)
    assert not result.valid
    assert result.mismatched == ["MANIFEST_IDENTITY"]
