import json
from pathlib import Path

import v34_observatory_v4 as observatory


def _write_common(tmp_path: Path):
    (tmp_path / "bot_state_v34.json").write_text(
        json.dumps({"status": "FLAT", "active_trade": None}), encoding="utf-8"
    )
    (tmp_path / "shadow_strategy_telemetry.json").write_text(
        json.dumps({"mode": "READ_ONLY_SHADOW", "decision": "BLOCK"}), encoding="utf-8"
    )
    session = tmp_path / "session_logs" / "session_a"
    session.mkdir(parents=True)
    (session / "bot_production.log").write_text(
        "LIVE_TRADING_ENABLED = False\nLIVE ORDER EXECUTION IS DISABLED.\n",
        encoding="utf-8",
    )


def test_snapshot_preserves_v3_fields(tmp_path: Path):
    """v4 must not regress anything v3 already reported correctly."""
    _write_common(tmp_path)
    result = observatory.snapshot(tmp_path)
    assert result["state"]["status"] == "FLAT"
    assert result["shadow"]["decision"] == "BLOCK"
    assert result["shadow_available"] is True
    assert result["execution_disabled"] is True


def test_snapshot_reads_orb_telemetry(tmp_path: Path):
    _write_common(tmp_path)
    (tmp_path / "orb_shadow_telemetry.json").write_text(json.dumps({
        "decision": "HYPOTHETICAL_BUY",
        "symbols_with_established_range": ["INFY", "TCS"],
        "candidates": [{"symbol": "INFY", "last_price": 1520.0, "orb_high": 1500.0,
                        "orb_low": 1480.0, "breakout_strength_pct": 1.33}],
    }), encoding="utf-8")
    result = observatory.snapshot(tmp_path)
    assert result["orb"]["decision"] == "HYPOTHETICAL_BUY"
    assert len(result["orb"]["symbols_with_established_range"]) == 2
    assert result["orb"]["candidates"][0]["symbol"] == "INFY"


def test_snapshot_reads_entry_gate_telemetry(tmp_path: Path):
    _write_common(tmp_path)
    (tmp_path / "entry_gate_dry_run_telemetry.json").write_text(json.dumps({
        "candidates_found": 1,
        "evaluations": [{
            "candidate_source": "EXTERNAL_CROSS_SECTIONAL_12_1",
            "order_payload": {"tradingsymbol": "INFY", "quantity": 10},
            "risk_gate": {"quantity": 10, "final_stage_reached": "ALL_CONDITIONS_PASSED",
                         "allowed": True, "reason": None},
            "physical_dispatch_gate": {"status": "BLOCKED_LIVE_TRADING_DISABLED"},
        }],
    }), encoding="utf-8")
    result = observatory.snapshot(tmp_path)
    assert result["entry_gate"]["candidates_found"] == 1
    assert result["entry_gate"]["evaluations"][0]["risk_gate"]["allowed"] is True


def test_missing_orb_and_entry_gate_files_are_explicitly_empty(tmp_path: Path):
    _write_common(tmp_path)
    result = observatory.snapshot(tmp_path)
    assert result["orb"] == {}
    assert result["entry_gate"] == {}


def test_paths_report_all_four_sources(tmp_path: Path):
    _write_common(tmp_path)
    result = observatory.snapshot(tmp_path)
    assert set(result["paths"]) == {"state", "log", "shadow", "orb", "entry_gate"}


def test_source_has_no_broker_or_write_capabilities():
    source = Path(observatory.__file__).read_text(encoding="utf-8")
    forbidden = (
        "kiteconnect",
        ".place_order(",
        ".modify_order(",
        ".cancel_order(",
        "request_entry(",
        "write_text(",
        "open(",
    )
    assert all(token not in source.lower() for token in forbidden)
