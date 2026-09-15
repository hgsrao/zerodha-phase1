"""Tests for v34_bridge_authorizer_registry_store.py.

Real tmp_path-backed files throughout (real fsync+replace), including a
genuine crash/restart simulation (a fresh AuthorizerRegistryStore
instance pointed at the same path, not the same Python object surviving)
- matching the exact proof pattern test_v34_bridge_rebalance_plan.py
already established for RebalancePlanStore.
"""

import json
from decimal import Decimal

import pytest

from v34_bridge_authorizer_registry_store import (
    AuthorizerRegistryStateError,
    AuthorizerRegistryStore,
)
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_state import EntryReservation

FINGERPRINT = {
    "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
    "product": "CNC", "order_type": "LIMIT", "quantity": 10, "price": "2500.00", "tag": "V3.4_P02_ENTRY",
}


def _reservation(symbol="RELIANCE", sector="ENERGY", fingerprint=None):
    return EntryReservation(
        symbol=symbol, sector=sector, reserved_capital=Decimal("25000.00"),
        reserved_at="2026-08-15T09:20:00+00:00", entry_fingerprint=fingerprint,
    )


class TestLoadOfMissingFile:
    def test_returns_none(self, tmp_path):
        store = AuthorizerRegistryStore(tmp_path / "registry.json")
        assert store.load() is None


class TestSaveThenLoadRoundTrip:
    def test_empty_registry(self, tmp_path):
        store = AuthorizerRegistryStore(tmp_path / "registry.json")
        store.save(AuthorizerRegistry())
        loaded = store.load()
        assert loaded == AuthorizerRegistry()

    def test_unsubmitted_reservation_survives_round_trip(self, tmp_path):
        store = AuthorizerRegistryStore(tmp_path / "registry.json")
        registry = AuthorizerRegistry(reservations={"RELIANCE": _reservation()})
        store.save(registry)
        loaded = store.load()
        assert loaded.reservations["RELIANCE"].symbol == "RELIANCE"
        assert loaded.reservations["RELIANCE"].sector == "ENERGY"
        assert loaded.reservations["RELIANCE"].reserved_capital == Decimal("25000.00")
        assert loaded.reservations["RELIANCE"].entry_fingerprint is None

    def test_fingerprinted_reservation_survives_round_trip(self, tmp_path):
        store = AuthorizerRegistryStore(tmp_path / "registry.json")
        registry = AuthorizerRegistry(reservations={"RELIANCE": _reservation(fingerprint=FINGERPRINT)})
        store.save(registry)
        loaded = store.load()
        assert loaded.reservations["RELIANCE"].entry_fingerprint == FINGERPRINT

    def test_held_symbols_survive_round_trip(self, tmp_path):
        store = AuthorizerRegistryStore(tmp_path / "registry.json")
        registry = AuthorizerRegistry(held_symbols={"INFY": "TECH", "TCS": "TECH"})
        store.save(registry)
        loaded = store.load()
        assert loaded.held_symbols == {"INFY": "TECH", "TCS": "TECH"}

    def test_a_real_file_actually_exists_on_disk(self, tmp_path):
        path = tmp_path / "registry.json"
        store = AuthorizerRegistryStore(path)
        store.save(AuthorizerRegistry())
        assert path.exists()


class TestCrashAndRestart:
    def test_a_fresh_store_instance_sees_exactly_what_was_saved_before_the_crash(self, tmp_path):
        path = tmp_path / "registry.json"
        store_before_crash = AuthorizerRegistryStore(path)
        registry = AuthorizerRegistry(
            reservations={
                "RELIANCE": _reservation(symbol="RELIANCE", sector="ENERGY", fingerprint=FINGERPRINT),
                "LAURUSLABS": _reservation(symbol="LAURUSLABS", sector="PHARMA"),  # unsubmitted
            },
            held_symbols={"INFY": "TECH"},
        )
        store_before_crash.save(registry)
        del store_before_crash  # simulate the process dying

        store_after_restart = AuthorizerRegistryStore(path)
        recovered = store_after_restart.load()
        assert recovered is not None
        assert recovered.reservations["RELIANCE"].entry_fingerprint == FINGERPRINT
        assert recovered.reservations["LAURUSLABS"].entry_fingerprint is None
        assert recovered.held_symbols == {"INFY": "TECH"}


class TestFailClosedOnCorruption:
    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="corrupt JSON"):
            store.load()

    def test_non_object_top_level_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="expected an object"):
            store.load()

    def test_missing_reservations_key_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"held_symbols": {}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="missing required field"):
            store.load()

    def test_missing_held_symbols_key_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"reservations": {}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="missing required field"):
            store.load()

    def test_reservations_not_an_object_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"reservations": ["not", "a", "dict"], "held_symbols": {}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="reservations: expected an object"):
            store.load()

    def test_held_symbols_not_an_object_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"reservations": {}, "held_symbols": ["nope"]}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="held_symbols: expected an object"):
            store.load()

    def test_a_non_string_sector_value_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"reservations": {}, "held_symbols": {"INFY": 123}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="non-empty sector string"):
            store.load()

    def test_reservation_key_symbol_mismatch_raises(self, tmp_path):
        path = tmp_path / "registry.json"
        bad_reservation = _reservation(symbol="RELIANCE").to_dict()
        path.write_text(json.dumps({"reservations": {"WRONGKEY": bad_reservation}, "held_symbols": {}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="disagrees with"):
            store.load()

    def test_a_malformed_entry_reservation_field_propagates_as_authorizer_registry_state_error(self, tmp_path):
        path = tmp_path / "registry.json"
        bad_reservation = _reservation(symbol="RELIANCE").to_dict()
        bad_reservation["reserved_capital"] = "not-a-number"
        path.write_text(json.dumps({"reservations": {"RELIANCE": bad_reservation}, "held_symbols": {}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="reservations\\['RELIANCE'\\]"):
            store.load()

    def test_a_missing_entry_reservation_field_propagates(self, tmp_path):
        path = tmp_path / "registry.json"
        bad_reservation = _reservation(symbol="RELIANCE").to_dict()
        del bad_reservation["sector"]
        path.write_text(json.dumps({"reservations": {"RELIANCE": bad_reservation}, "held_symbols": {}}), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="missing required field"):
            store.load()


class TestStructuralInvariantSymbolCannotBeBothReservedAndHeld:
    def test_load_refuses_a_file_with_the_same_symbol_in_both_dicts(self, tmp_path):
        path = tmp_path / "registry.json"
        reservation = _reservation(symbol="RELIANCE").to_dict()
        path.write_text(json.dumps({
            "reservations": {"RELIANCE": reservation},
            "held_symbols": {"RELIANCE": "ENERGY"},
        }), encoding="utf-8")
        store = AuthorizerRegistryStore(path)
        with pytest.raises(AuthorizerRegistryStateError, match="structurally impossible"):
            store.load()

    def test_save_refuses_an_in_memory_registry_with_the_same_symbol_in_both_dicts(self, tmp_path):
        # AuthorizerRegistry is frozen (no __post_init__ of its own to
        # catch this) - constructible directly with an invalid shape, so
        # this store must catch it defensively before ever writing it.
        store = AuthorizerRegistryStore(tmp_path / "registry.json")
        invalid_registry = AuthorizerRegistry(
            reservations={"RELIANCE": _reservation(symbol="RELIANCE")},
            held_symbols={"RELIANCE": "ENERGY"},
        )
        with pytest.raises(AuthorizerRegistryStateError, match="structurally impossible|Refusing to save"):
            store.save(invalid_registry)
        assert not (tmp_path / "registry.json").exists()  # never durably written
