"""Fail-closed Stage-C TRAIN export: nothing is written unless every quarantine check holds."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = _load("scripts/diagnostics/export_stage_c_train.py", "export_stage_c_train")
audit = _load("scripts/diagnostics/r5_entry_edge_audit.py", "r5_entry_edge_audit_for_export")

PARAMS = {"entry_confidence_threshold": 0.28, "max_hold_bars": 89}
TRAIN = ["2024-01-02", "2024-01-03", "2024-01-04"]
VALID = ["2025-07-01", "2025-07-02"]


def _trade(tid, day, t="10:00"):
    return {"trade_id": tid, "entry_timestamp": f"{day} {t}:00+05:30", "exit_timestamp": f"{day} 10:05:00+05:30"}


def _source():
    return {"params": dict(PARAMS), "aggregate": {"score": -1.0},
            "blocks": [{"block": 1, "sessions": TRAIN[:2], "metrics": {"net_pnl": -5.0},
                        "trades": [_trade("a", TRAIN[0]), _trade("b", TRAIN[1])]},
                       {"block": 2, "sessions": TRAIN[2:], "trades": [_trade("c", TRAIN[2])]}]}


@pytest.fixture(autouse=True)
def small_train(monkeypatch):
    monkeypatch.setattr(exporter, "EXPECTED_TRAIN_SESSIONS", len(TRAIN))


def test_clean_source_exports_only_blocks_sessions_and_trades():
    doc = exporter.build_export(_source(), PARAMS, TRAIN, VALID)
    assert doc["export_kind"] == audit.STAGE_C_EXPORT_KIND and doc["train_sessions"] == 3
    assert sum(len(b["trades"]) for b in doc["blocks"]) == 3
    assert set(doc["blocks"][0]) == {"block", "sessions", "trades"}          # no metrics carried over
    assert "aggregate" not in doc


@pytest.mark.parametrize("mutate, error", [
    (lambda s: s["params"].update(max_hold_bars=90), "PARAMS_MISMATCH"),
    (lambda s: s["params"].update(extra=1), "PARAMS_MISMATCH"),
    (lambda s: s["blocks"][1]["sessions"].append("2025-07-01"), "VALIDATION_SESSION_PRESENT"),
    (lambda s: s["blocks"][1]["trades"].append(_trade("v", "2025-07-02")), "VALIDATION_TRADE_PRESENT"),
    (lambda s: s["blocks"][1]["trades"].append(_trade("o", "2025-08-01")), "TRADE_OUT_OF_TRAIN_WINDOW"),
    (lambda s: s["blocks"][0]["trades"].append(_trade("x", TRAIN[2])), "TRADE_OUTSIDE_ITS_BLOCK"),
    (lambda s: s["blocks"].pop(), "TRAIN_COVERAGE_MISMATCH"),
    (lambda s: s["blocks"][1]["sessions"].append("2023-08-31"), "SESSION_OUT_OF_TRAIN_WINDOW"),
    (lambda s: s["blocks"][1]["sessions"].append(TRAIN[0]), "SESSION_REPEATED_ACROSS_BLOCKS"),
    (lambda s: s.pop("blocks"), "SCHEMA_UNSUPPORTED"),
])
def test_every_quarantine_violation_fails_closed(mutate, error):
    source = copy.deepcopy(_source())
    mutate(source)
    with pytest.raises(SystemExit, match=error):
        exporter.build_export(source, PARAMS, TRAIN, VALID)


def test_train_count_and_list_overlap_are_enforced(monkeypatch):
    monkeypatch.setattr(exporter, "EXPECTED_TRAIN_SESSIONS", 453)
    with pytest.raises(SystemExit, match="TRAIN_SESSION_COUNT"):
        exporter.build_export(_source(), PARAMS, TRAIN, VALID)
    with pytest.raises(SystemExit, match="SESSION_LISTS_OVERLAP"):
        exporter.build_export(_source(), PARAMS, TRAIN, VALID + [TRAIN[0]])


def test_cli_writes_export_with_provenance_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "EXPORT_ROOT", tmp_path / "exports")
    files = {"src.json": _source(), "params.json": PARAMS, "train.json": TRAIN, "valid.json": {"sessions": VALID}}
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data))
    argv = ["--source", str(tmp_path / "src.json"), "--expected-params", str(tmp_path / "params.json"),
            "--train-sessions", str(tmp_path / "train.json"), "--validation-sessions", str(tmp_path / "valid.json"),
            "--label", "t7"]
    assert exporter.main(argv) == 0
    out = tmp_path / "exports/t7.json"
    doc = json.loads(out.read_text())
    assert doc["provenance"]["source_sha256"] == exporter._sha(tmp_path / "src.json")
    assert (tmp_path / "exports/t7.sha256").read_text().split()[0] == exporter._sha(out)
    with pytest.raises(SystemExit):                                          # no silent overwrite
        exporter.main(argv)
    bad = dict(_source(), params={**PARAMS, "max_hold_bars": 1})
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="PARAMS_MISMATCH"):
        exporter.main(argv[:1] + [str(tmp_path / "bad.json")] + argv[2:-1] + ["t8"])
    assert not (tmp_path / "exports/t8.json").exists()                       # nothing written on failure


def test_diagnostic_accepts_only_verified_exports_for_non_protocol_blocks():
    protocol = {"sampling_plan": {"stage_a": [], "stage_b": []}}
    doc = exporter.build_export(_source(), PARAMS, TRAIN, VALID)
    block = doc["blocks"][0]
    with pytest.raises(SystemExit, match="STAGE_C_EXPORT_WITHOUT_PROVENANCE"):
        audit._block_for(doc, protocol, block)
    doc["provenance"] = {"source_sha256": "x"}
    assert audit._block_for(doc, protocol, block) == {"block": 1, "sessions": TRAIN[:2]}
    with pytest.raises(SystemExit, match="OUT_OF_WINDOW"):
        audit._block_for(doc, protocol, {"block": 9, "sessions": ["2025-07-01"]})
    with pytest.raises(SystemExit, match="BLOCK_NOT_IN_PROTOCOL"):             # an ordinary result: sealed only
        audit._block_for({"blocks": []}, protocol, block)
