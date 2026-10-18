from datetime import datetime, timezone

import pytest

from revision5.engine_state import (
    ENGINE_A,
    ENGINE_B,
    EngineStateStore,
    as_ist,
    engine_a_squareoff_due,
    entry_time_allowed,
)
from revision5.topology import (
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
)


def test_engine_a_entry_allowed_before_cutoff():
    allowed, reason = entry_time_allowed(
        ENGINE_A,
        datetime(2026, 7, 31, 14, 29, 59),
    )
    assert allowed is True
    assert reason == "ENTRY_TIME_ALLOWED"


def test_engine_a_entry_rejected_at_cutoff():
    allowed, reason = entry_time_allowed(
        ENGINE_A,
        datetime(2026, 7, 31, 14, 30),
    )
    assert allowed is False
    assert reason == "ENGINE_A_ENTRY_CUTOFF_14_30_IST"


def test_engine_b_not_subject_to_engine_a_cutoff():
    allowed, reason = entry_time_allowed(
        ENGINE_B,
        datetime(2026, 7, 31, 14, 45),
    )
    assert allowed is True
    assert reason == "ENTRY_TIME_ALLOWED"


def test_squareoff_due_exactly_at_1515():
    assert not engine_a_squareoff_due(
        datetime(2026, 7, 31, 15, 14, 59)
    )

    assert engine_a_squareoff_due(
        datetime(2026, 7, 31, 15, 15)
    )


def test_engine_a_fill_latch_is_one_fill_per_bay_per_day(tmp_path):
    store = EngineStateStore(tmp_path / "r5_state.sqlite3")
    fill_time = datetime(2026, 7, 31, 10, 30)

    assert store.consume_engine_a_fill(
        GTG2_TECH_TELECOM,
        fill_time,
    ) is True

    assert store.consume_engine_a_fill(
        GTG2_TECH_TELECOM,
        datetime(2026, 7, 31, 11, 15),
    ) is False

    assert store.engine_a_fill_used(
        GTG2_TECH_TELECOM,
        fill_time,
    ) is True


def test_engine_a_fill_latch_is_independent_by_bay(tmp_path):
    store = EngineStateStore(tmp_path / "r5_state.sqlite3")
    now = datetime(2026, 7, 31, 10, 30)

    assert store.consume_engine_a_fill(
        GTG1_HEAVY_INDUSTRY,
        now,
    )

    assert store.consume_engine_a_fill(
        GTG2_TECH_TELECOM,
        now,
    )


def test_engine_a_fill_latch_resets_by_trading_date(tmp_path):
    store = EngineStateStore(tmp_path / "r5_state.sqlite3")

    assert store.consume_engine_a_fill(
        GTG1_HEAVY_INDUSTRY,
        datetime(2026, 7, 30, 10, 0),
    )

    assert store.consume_engine_a_fill(
        GTG1_HEAVY_INDUSTRY,
        datetime(2026, 7, 31, 10, 0),
    )


def test_engine_a_fill_latch_survives_process_restart(tmp_path):
    db = tmp_path / "r5_state.sqlite3"

    first_process = EngineStateStore(db)

    assert first_process.consume_engine_a_fill(
        GTG1_HEAVY_INDUSTRY,
        datetime(2026, 7, 31, 10, 0),
    )

    # Simulated restart: fresh object, same persistent database.
    second_process = EngineStateStore(db)

    assert second_process.engine_a_fill_used(
        GTG1_HEAVY_INDUSTRY,
        datetime(2026, 7, 31, 12, 0),
    )

    assert second_process.consume_engine_a_fill(
        GTG1_HEAVY_INDUSTRY,
        datetime(2026, 7, 31, 12, 0),
    ) is False


def test_engine_b_does_not_consume_engine_a_latch(tmp_path):
    store = EngineStateStore(tmp_path / "r5_state.sqlite3")
    now = datetime(2026, 7, 31, 14, 45)

    allowed, reason = store.entry_allowed(
        ENGINE_B,
        GTG1_HEAVY_INDUSTRY,
        now,
    )

    assert allowed is True
    assert reason == "ENGINE_INTERLOCKS_CLEAR"
    assert not store.engine_a_fill_used(
        GTG1_HEAVY_INDUSTRY,
        now,
    )


def test_engine_a_combined_interlock_after_fill(tmp_path):
    store = EngineStateStore(tmp_path / "r5_state.sqlite3")
    now = datetime(2026, 7, 31, 10, 0)

    allowed, _ = store.entry_allowed(
        ENGINE_A,
        GTG1_HEAVY_INDUSTRY,
        now,
    )
    assert allowed

    assert store.consume_engine_a_fill(
        GTG1_HEAVY_INDUSTRY,
        now,
    )

    allowed, reason = store.entry_allowed(
        ENGINE_A,
        GTG1_HEAVY_INDUSTRY,
        datetime(2026, 7, 31, 11, 0),
    )

    assert allowed is False
    assert reason == "ENGINE_A_DAILY_BAY_FILL_ALREADY_USED"


def test_aware_utc_timestamp_is_converted_to_ist():
    # 09:00 UTC = 14:30 IST.
    dt = datetime(
        2026, 7, 31, 9, 0,
        tzinfo=timezone.utc,
    )

    assert as_ist(dt).hour == 14
    assert as_ist(dt).minute == 30

    allowed, reason = entry_time_allowed(ENGINE_A, dt)
    assert allowed is False
    assert reason == "ENGINE_A_ENTRY_CUTOFF_14_30_IST"


def test_invalid_engine_fails_closed(tmp_path):
    store = EngineStateStore(tmp_path / "r5_state.sqlite3")

    with pytest.raises(ValueError):
        store.entry_allowed(
            "ENGINE_X",
            GTG1_HEAVY_INDUSTRY,
            datetime(2026, 7, 31, 10, 0),
        )
