"""Tests for v34_bridge_engine_adapters.py."""

from datetime import date

import pytest

from v34_bridge_authorizer_registry_store import AuthorizerRegistryStore as DurableAuthorizerRegistryStore
from v34_bridge_botstate_store import BotStateStore
from v34_bridge_engine_adapters import EngineAuthorizerRegistryAdapter, EngineBotStateStoreAdapter
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_state import BotState, EngineStatus, EntryReservation


class TestEngineBotStateStoreAdapter:
    def test_missing_file_constructs_a_fresh_startup_state_for_the_given_date(self, tmp_path):
        adapter = EngineBotStateStoreAdapter(BotStateStore(tmp_path / "state.json"))
        state = adapter.load(date(2026, 8, 15))
        assert state.trading_day == "2026-08-15"
        assert state.status == EngineStatus.STARTUP
        assert state.active_trades == {}

    def test_missing_file_with_no_date_supplied_raises(self, tmp_path):
        adapter = EngineBotStateStoreAdapter(BotStateStore(tmp_path / "state.json"))
        with pytest.raises(RuntimeError, match="no date was supplied"):
            adapter.load(None)

    def test_an_existing_persisted_state_is_returned_regardless_of_the_date_argument(self, tmp_path):
        durable = BotStateStore(tmp_path / "state.json")
        durable.save(BotState(trading_day="2026-08-10", status=EngineStatus.RUNNING))
        adapter = EngineBotStateStoreAdapter(durable)
        state = adapter.load(date(2026, 8, 15))  # a different date - must not override what's persisted
        assert state.trading_day == "2026-08-10"
        assert state.status == EngineStatus.RUNNING

    def test_save_writes_through_to_the_durable_store(self, tmp_path):
        durable = BotStateStore(tmp_path / "state.json")
        adapter = EngineBotStateStoreAdapter(durable)
        adapter.save(BotState(trading_day="2026-08-15", status=EngineStatus.RUNNING))
        reloaded = durable.load()
        assert reloaded.trading_day == "2026-08-15"
        assert reloaded.status == EngineStatus.RUNNING

    def test_last_loaded_state_captures_what_was_on_disk_at_load_time(self, tmp_path):
        durable = BotStateStore(tmp_path / "state.json")
        durable.save(BotState(trading_day="2026-08-10", status=EngineStatus.RUNNING))
        adapter = EngineBotStateStoreAdapter(durable)
        loaded = adapter.load(date(2026, 8, 15))
        loaded.status = EngineStatus.STARTUP  # caller mutates the returned object in place
        # The diagnostic snapshot must reflect what was ACTUALLY persisted,
        # not be silently mutated alongside the caller's own copy.
        assert adapter.last_loaded_state.status == EngineStatus.RUNNING


class TestEngineAuthorizerRegistryAdapter:
    def test_missing_file_starts_with_an_empty_registry(self, tmp_path):
        durable = DurableAuthorizerRegistryStore(tmp_path / "registry.json")
        adapter = EngineAuthorizerRegistryAdapter(durable)
        assert adapter.registry == AuthorizerRegistry()

    def test_an_existing_persisted_registry_is_loaded_into_the_mirror_at_construction(self, tmp_path):
        durable = DurableAuthorizerRegistryStore(tmp_path / "registry.json")
        reservation = EntryReservation(symbol="RELIANCE", sector="ENERGY", reserved_capital="25000", reserved_at="2026-08-15T09:20:00+00:00", entry_fingerprint=None)
        durable.save(AuthorizerRegistry(reservations={"RELIANCE": reservation}))
        adapter = EngineAuthorizerRegistryAdapter(durable)
        assert "RELIANCE" in adapter.registry.reservations

    def test_load_returns_the_current_mirror(self, tmp_path):
        durable = DurableAuthorizerRegistryStore(tmp_path / "registry.json")
        adapter = EngineAuthorizerRegistryAdapter(durable)
        assert adapter.load() is adapter.registry

    def test_save_persists_first_then_updates_the_mirror(self, tmp_path):
        durable = DurableAuthorizerRegistryStore(tmp_path / "registry.json")
        adapter = EngineAuthorizerRegistryAdapter(durable)
        new_registry = AuthorizerRegistry(held_symbols={"RELIANCE": "ENERGY"})
        adapter.save(new_registry)
        assert adapter.registry == new_registry
        assert durable.load() == new_registry

    def test_a_failed_durable_save_leaves_the_mirror_untouched(self, tmp_path, monkeypatch):
        durable = DurableAuthorizerRegistryStore(tmp_path / "registry.json")
        adapter = EngineAuthorizerRegistryAdapter(durable)
        original_mirror = adapter.registry

        def failing_save(registry):
            raise OSError("simulated disk failure")
        monkeypatch.setattr(durable, "save", failing_save)

        with pytest.raises(OSError, match="simulated disk failure"):
            adapter.save(AuthorizerRegistry(held_symbols={"RELIANCE": "ENERGY"}))
        assert adapter.registry is original_mirror  # never got ahead of disk
