"""Tests for v34_bridge_daily_accounting_state.py and
v34_bridge_daily_accounting_store.py. Real tmp_path-backed files
throughout, matching the crash/restart proof pattern every other store
in this project uses."""

import json
from decimal import Decimal

import pytest

from v34_bridge_daily_accounting_state import (
    DailyAccountingIntegrityError,
    DailyAccountingState,
    PositionBasis,
    initial_daily_accounting_state,
)
from v34_bridge_daily_accounting_store import (
    STORE_SCHEMA_VERSION,
    DailyAccountingStore,
    DailyAccountingStoreError,
)
from v34_p02_state import DailyAccountingCheckpoint


def _checkpoint(day="2026-08-15", equity="100000"):
    return DailyAccountingCheckpoint(
        trading_day=day, prior_close_equity=Decimal(equity),
        day_start_equity=Decimal(equity), trial_high_water_mark=Decimal(equity),
    )


class TestInitialDailyAccountingState:
    def test_day_one_bootstrap_is_all_zero(self):
        state = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000"))
        assert state.cumulative_realized_pnl == Decimal("0")
        assert state.cumulative_charges == Decimal("0")
        assert state.sealed_daily_pnl_series == {}
        assert state.booked_trade_ids == frozenset()
        assert state.checkpoint.trial_high_water_mark == Decimal("100000")


class TestPositionBasisRoundTrip:
    def test_to_dict_from_dict_round_trip(self):
        basis = PositionBasis(entry_order_id="ORD-1", quantity=50, avg_price=Decimal("2501.25"), entry_trading_day="2026-08-15")
        assert PositionBasis.from_dict(basis.to_dict()) == basis

    def test_missing_field_raises(self):
        with pytest.raises(DailyAccountingIntegrityError, match="missing required field"):
            PositionBasis.from_dict({"entry_order_id": "ORD-1"})

    def test_non_positive_quantity_raises(self):
        with pytest.raises(DailyAccountingIntegrityError, match="positive"):
            PositionBasis.from_dict({"entry_order_id": "ORD-1", "quantity": 0, "avg_price": "100", "entry_trading_day": "2026-08-15"})

    def test_open_position_basis_round_trips_inside_daily_accounting_state(self):
        state = DailyAccountingState(
            trading_day="2026-08-15", cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"),
            checkpoint=_checkpoint(), sealed_daily_pnl_series={}, booked_trade_ids=frozenset({"T1"}),
            accounted_charge_by_order_id={"ORD-1": Decimal("12.50")},
            dp_charges_booked=frozenset({("2026-08-15", "RELIANCE")}),
            open_position_basis={"RELIANCE": PositionBasis(entry_order_id="ORD-2", quantity=10, avg_price=Decimal("2500"), entry_trading_day="2026-08-14")},
        )
        assert DailyAccountingState.from_dict(state.to_dict()) == state


class TestAccountedChargeByOrderIdAndDpChargesBooked:
    def test_accounted_charge_by_order_id_round_trips(self):
        state = DailyAccountingState(
            trading_day="2026-08-15", cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("41.23"),
            checkpoint=_checkpoint(), accounted_charge_by_order_id={"ORD-A": Decimal("20.00"), "ORD-B": Decimal("21.23")},
        )
        assert DailyAccountingState.from_dict(state.to_dict()) == state

    def test_dp_charges_booked_round_trips_and_serializes_sorted(self):
        state = DailyAccountingState(
            trading_day="2026-08-15", cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"),
            checkpoint=_checkpoint(), dp_charges_booked=frozenset({("2026-08-15", "RELIANCE"), ("2026-08-14", "INFY")}),
        )
        restored = DailyAccountingState.from_dict(state.to_dict())
        assert restored == state
        assert state.to_dict()["dp_charges_booked"] == [["2026-08-14", "INFY"], ["2026-08-15", "RELIANCE"]]

    def test_duplicate_dp_charge_entry_raises(self):
        raw = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000")).to_dict()
        raw["dp_charges_booked"] = [["2026-08-15", "RELIANCE"], ["2026-08-15", "RELIANCE"]]
        with pytest.raises(DailyAccountingIntegrityError, match="duplicate"):
            DailyAccountingState.from_dict(raw)

    def test_negative_accounted_charge_raises(self):
        raw = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000")).to_dict()
        raw["accounted_charge_by_order_id"] = {"ORD-A": "-5"}
        with pytest.raises(DailyAccountingIntegrityError, match="non-negative"):
            DailyAccountingState.from_dict(raw)


class TestDailyAccountingStateRoundTrip:
    def test_to_dict_from_dict_round_trip(self):
        state = DailyAccountingState(
            trading_day="2026-08-15", cumulative_realized_pnl=Decimal("1234.56"),
            cumulative_charges=Decimal("78.90"), checkpoint=_checkpoint(),
            sealed_daily_pnl_series={"2026-08-14": Decimal("-500.25"), "2026-08-13": Decimal("300")},
            booked_trade_ids=frozenset({"T1", "T2", "T3"}),
        )
        restored = DailyAccountingState.from_dict(state.to_dict())
        assert restored == state

    def test_booked_trade_ids_serializes_deterministically_sorted(self):
        state = DailyAccountingState(
            trading_day="2026-08-15", cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"),
            checkpoint=_checkpoint(), sealed_daily_pnl_series={}, booked_trade_ids=frozenset({"T3", "T1", "T2"}),
        )
        assert state.to_dict()["booked_trade_ids"] == ["T1", "T2", "T3"]


class TestDailyAccountingStateFailClosedValidation:
    def test_missing_field_raises(self):
        with pytest.raises(DailyAccountingIntegrityError, match="missing required field"):
            DailyAccountingState.from_dict({"trading_day": "2026-08-15"})

    def test_non_dict_raises(self):
        with pytest.raises(DailyAccountingIntegrityError, match="expected an object"):
            DailyAccountingState.from_dict(["not", "a", "dict"])

    def test_non_finite_decimal_raises(self):
        raw = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000")).to_dict()
        raw["cumulative_realized_pnl"] = "NaN"
        with pytest.raises(DailyAccountingIntegrityError, match="finite"):
            DailyAccountingState.from_dict(raw)

    def test_duplicate_booked_trade_id_raises(self):
        raw = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000")).to_dict()
        raw["booked_trade_ids"] = ["T1", "T1"]
        with pytest.raises(DailyAccountingIntegrityError, match="duplicate entry"):
            DailyAccountingState.from_dict(raw)

    def test_malformed_sealed_series_value_raises(self):
        raw = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000")).to_dict()
        raw["sealed_daily_pnl_series"] = {"2026-08-14": "not-a-decimal"}
        with pytest.raises(DailyAccountingIntegrityError):
            DailyAccountingState.from_dict(raw)


class TestLoadOfMissingFile:
    def test_returns_none(self, tmp_path):
        store = DailyAccountingStore(tmp_path / "accounting.json")
        assert store.load() is None


class TestSaveThenLoadRoundTrip:
    def test_a_fresh_bootstrap_state(self, tmp_path):
        store = DailyAccountingStore(tmp_path / "accounting.json")
        state = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000"))
        store.save(state)
        assert store.load() == state

    def test_a_real_file_actually_exists_on_disk(self, tmp_path):
        path = tmp_path / "accounting.json"
        store = DailyAccountingStore(path)
        store.save(initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000")))
        assert path.exists()

    def test_a_state_with_booked_trades_and_sealed_history_round_trips(self, tmp_path):
        store = DailyAccountingStore(tmp_path / "accounting.json")
        state = DailyAccountingState(
            trading_day="2026-08-17", cumulative_realized_pnl=Decimal("2500.75"),
            cumulative_charges=Decimal("122.40"), checkpoint=_checkpoint(day="2026-08-17", equity="102500"),
            sealed_daily_pnl_series={"2026-08-14": Decimal("-500"), "2026-08-15": Decimal("300"), "2026-08-16": Decimal("0")},
            booked_trade_ids=frozenset({"T1", "T2", "T3", "T4"}),
        )
        store.save(state)
        assert store.load() == state


class TestCrashRestartSimulation:
    def test_a_fresh_store_instance_reads_what_a_prior_instance_wrote(self, tmp_path):
        path = tmp_path / "accounting.json"
        store_before_crash = DailyAccountingStore(path)
        state = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000"))
        store_before_crash.save(state)
        del store_before_crash

        store_after_restart = DailyAccountingStore(path)
        assert store_after_restart.load() == state

    def test_save_never_truncates_before_the_new_bytes_are_proven_loadable(self, tmp_path, monkeypatch):
        # An injected failure partway through save() must leave the
        # previous durable file completely untouched - temp-file +
        # os.replace discipline, proven not just asserted.
        path = tmp_path / "accounting.json"
        store = DailyAccountingStore(path)
        good_state = initial_daily_accounting_state(trading_day="2026-08-15", trial_capital=Decimal("100000"))
        store.save(good_state)
        original_bytes = path.read_bytes()

        def failing_fsync(fd):
            raise OSError("simulated disk failure")
        monkeypatch.setattr("os.fsync", failing_fsync)

        bad_state = DailyAccountingState(
            trading_day="2026-08-16", cumulative_realized_pnl=Decimal("999"), cumulative_charges=Decimal("0"),
            checkpoint=_checkpoint(day="2026-08-16"), sealed_daily_pnl_series={}, booked_trade_ids=frozenset(),
        )
        with pytest.raises(OSError, match="simulated disk failure"):
            store.save(bad_state)
        assert path.read_bytes() == original_bytes


class TestEnvelopeLevelFailures:
    def test_corrupt_json_raises_store_error(self, tmp_path):
        path = tmp_path / "accounting.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = DailyAccountingStore(path)
        with pytest.raises(DailyAccountingStoreError, match="corrupt JSON"):
            store.load()

    def test_non_dict_envelope_raises(self, tmp_path):
        path = tmp_path / "accounting.json"
        path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        store = DailyAccountingStore(path)
        with pytest.raises(DailyAccountingStoreError, match="expected an object"):
            store.load()

    def test_missing_envelope_field_raises(self, tmp_path):
        path = tmp_path / "accounting.json"
        path.write_text(json.dumps({"store_schema_version": STORE_SCHEMA_VERSION}), encoding="utf-8")
        store = DailyAccountingStore(path)
        with pytest.raises(DailyAccountingStoreError, match="missing required field"):
            store.load()

    def test_unsupported_schema_version_raises(self, tmp_path):
        path = tmp_path / "accounting.json"
        path.write_text(json.dumps({"store_schema_version": 999, "state": {}}), encoding="utf-8")
        store = DailyAccountingStore(path)
        with pytest.raises(DailyAccountingStoreError, match="not supported"):
            store.load()

    def test_field_level_corruption_raises_integrity_error_not_store_error(self, tmp_path):
        # A field-level problem inside "state" must raise
        # DailyAccountingIntegrityError, not DailyAccountingStoreError -
        # the envelope itself is fine; the content inside it is not.
        path = tmp_path / "accounting.json"
        envelope = {"store_schema_version": STORE_SCHEMA_VERSION, "state": {"trading_day": "2026-08-15"}}
        path.write_text(json.dumps(envelope), encoding="utf-8")
        store = DailyAccountingStore(path)
        with pytest.raises(DailyAccountingIntegrityError, match="missing required field"):
            store.load()
