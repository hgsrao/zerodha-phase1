import json
from pathlib import Path

import v34_observatory_v3 as observatory


def test_snapshot_reads_production_and_shadow_sources(tmp_path: Path):
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
    result = observatory.snapshot(tmp_path)
    assert result["state"]["status"] == "FLAT"
    assert result["shadow"]["decision"] == "BLOCK"
    assert result["shadow_available"] is True
    assert result["execution_disabled"] is True


def test_missing_shadow_is_explicitly_unavailable(tmp_path: Path):
    result = observatory.snapshot(tmp_path)
    assert result["shadow"] == {}
    assert result["shadow_available"] is False


def test_newest_session_log_is_selected(tmp_path: Path):
    first = tmp_path / "session_logs" / "one" / "bot_production.log"
    second = tmp_path / "session_logs" / "two" / "bot_production.log"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("old", encoding="utf-8")
    second.write_text("new", encoding="utf-8")
    first.touch()
    second.touch()
    # Deterministically make the second path newer on filesystems with coarse clocks.
    import os
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))
    assert observatory.newest_session_log(tmp_path) == second


def test_source_has_no_broker_or_write_capabilities():
    source = Path(observatory.__file__).read_text(encoding="utf-8")
    forbidden = (
        "kiteconnect",
        ".place_order(",
        ".modify_order(",
        ".cancel_order(",
        "request_entry(",
        "write_text(",
        "open("
    )
    assert all(token not in source.lower() for token in forbidden)


def test_pinned_session_log_wins_over_unrelated_log(tmp_path: Path):
    pinned = tmp_path / "session_logs" / "production" / "bot_production.log"
    unrelated = tmp_path / "session_logs" / "test" / "bot_production.log"
    pinned.parent.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    pinned.write_text("production", encoding="utf-8")
    unrelated.write_text("test", encoding="utf-8")
    (tmp_path / "observation_suite_runtime.json").write_text(
        json.dumps({"session_log": str(pinned.relative_to(tmp_path))}), encoding="utf-8"
    )
    assert observatory.newest_session_log(tmp_path) == pinned
