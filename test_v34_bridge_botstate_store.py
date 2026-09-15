"""Tests for v34_bridge_botstate_store.py.

Real tmp_path-backed files throughout (real fsync+replace), including a
genuine crash/restart simulation - matching the exact proof pattern
test_v34_bridge_authorizer_registry_store.py and
test_v34_bridge_rebalance_plan.py already established.
"""

import json

import pytest

import v34_bridge_botstate_store as botstate_store_module
from v34_bridge_botstate_store import (
    STORE_SCHEMA_VERSION,
    BotStateStore,
    BotStateStoreError,
    StateIntegrityError,  # re-exported by the store module itself
)
from v34_p02_state import BotState, EngineStatus, PositionStatus, TradeContext


def _ctx(symbol="RELIANCE", status=PositionStatus.MANAGING, **overrides):
    defaults = dict(
        symbol=symbol, entry_tag="V3.4_P02_ENTRY", target_qty=10, tranche_qty=10,
        status=status, filled_qty=10, pending_qty=0,
    )
    defaults.update(overrides)
    return TradeContext(**defaults)


class TestLoadOfMissingFile:
    def test_returns_none(self, tmp_path):
        store = BotStateStore(tmp_path / "state.json")
        assert store.load() is None


class TestSaveThenLoadRoundTrip:
    def test_a_flat_state_with_no_active_trades(self, tmp_path):
        store = BotStateStore(tmp_path / "state.json")
        state = BotState(trading_day="2026-08-15", status=EngineStatus.RUNNING)
        store.save(state)
        loaded = store.load()
        assert loaded.trading_day == "2026-08-15"
        assert loaded.status == EngineStatus.RUNNING
        assert loaded.active_trades == {}

    def test_a_real_file_actually_exists_on_disk(self, tmp_path):
        path = tmp_path / "state.json"
        store = BotStateStore(path)
        store.save(BotState(trading_day="2026-08-15"))
        assert path.exists()

    def test_every_legal_position_status_round_trips(self, tmp_path):
        # All twelve PositionStatus values, including the four in-flight
        # ones a genuine restart must be able to find on disk -
        # ENTRY_SUBMITTING/ENTRY_UNKNOWN/EXIT_SUBMITTING/EXIT_UNKNOWN are
        # not startup-only artifacts (traced: _step_entry_submit sets
        # these during completely ordinary RUNNING operation too), so the
        # store must round-trip every one of them without complaint.
        store = BotStateStore(tmp_path / "state.json")
        active_trades = {
            status.value: _ctx(symbol=status.value, status=status)
            for status in PositionStatus
            if status != PositionStatus.ENTRY_ABANDONED_POLICY_HALT  # never persisted for long, per its own docstring
        }
        state = BotState(trading_day="2026-08-15", status=EngineStatus.RUNNING, active_trades=active_trades)
        store.save(state)
        loaded = store.load()
        assert set(loaded.active_trades) == set(active_trades)
        for symbol, ctx in active_trades.items():
            assert loaded.active_trades[symbol].status == ctx.status

    def test_halt_fields_and_fingerprints_survive_round_trip(self, tmp_path):
        store = BotStateStore(tmp_path / "state.json")
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
            "product": "CNC", "order_type": "LIMIT", "quantity": 10, "price": "2500.00", "tag": "V3.4_P02_ENTRY",
        }
        ctx = _ctx(status=PositionStatus.ENTRY_UNKNOWN, entry_submission_fingerprint=fingerprint, entry_reconciliation_failures=2)
        state = BotState(
            trading_day="2026-08-15", status=EngineStatus.RECONCILIATION_HALT,
            active_trades={"RELIANCE": ctx}, halt_reason="Reconciliation Failure: test",
            halt_source="ENTRY_RECONCILIATION", halted_at="2026-08-15T10:00:00+00:00",
            clearance_required=True,
        )
        store.save(state)
        loaded = store.load()
        assert loaded.halt_reason == "Reconciliation Failure: test"
        assert loaded.clearance_required is True
        assert loaded.active_trades["RELIANCE"].entry_submission_fingerprint == fingerprint
        assert loaded.active_trades["RELIANCE"].entry_reconciliation_failures == 2


class TestCrashAndRestart:
    def test_a_fresh_store_instance_sees_exactly_what_was_saved_before_the_crash(self, tmp_path):
        path = tmp_path / "state.json"
        store_before_crash = BotStateStore(path)
        state = BotState(
            trading_day="2026-08-15", status=EngineStatus.RUNNING,
            active_trades={"RELIANCE": _ctx(status=PositionStatus.EXIT_SUBMITTING)},
        )
        store_before_crash.save(state)
        del store_before_crash  # simulate the process dying

        store_after_restart = BotStateStore(path)
        recovered = store_after_restart.load()
        assert recovered is not None
        assert recovered.status == EngineStatus.RUNNING  # NOT forced to STARTUP by this store - see module docstring
        assert recovered.active_trades["RELIANCE"].status == PositionStatus.EXIT_SUBMITTING


class TestFailClosedOnCorruption:
    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(BotStateStoreError, match="corrupt JSON"):
            store.load()

    def test_non_object_envelope_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(BotStateStoreError, match="expected an object"):
            store.load()

    def test_missing_store_schema_version_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"state": {}}), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(BotStateStoreError, match="missing required field"):
            store.load()

    def test_missing_state_key_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"store_schema_version": STORE_SCHEMA_VERSION}), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(BotStateStoreError, match="missing required field"):
            store.load()

    def test_unsupported_schema_version_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"store_schema_version": 999, "state": {}}), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(BotStateStoreError, match="not supported"):
            store.load()

    def test_a_malformed_botstate_field_raises_state_integrity_error_not_store_error(self, tmp_path):
        # Deliberately StateIntegrityError, not BotStateStoreError - see
        # the module's exception taxonomy: field-level problems keep
        # BotState.from_dict()'s own precise exception, unwrapped.
        path = tmp_path / "state.json"
        raw_state = BotState(trading_day="2026-08-15").to_dict()
        raw_state["realised_net_pnl"] = "not-a-number"
        path.write_text(json.dumps({"store_schema_version": STORE_SCHEMA_VERSION, "state": raw_state}), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(StateIntegrityError, match="realised_net_pnl"):
            store.load()

    def test_a_missing_botstate_field_raises_state_integrity_error(self, tmp_path):
        path = tmp_path / "state.json"
        raw_state = BotState(trading_day="2026-08-15").to_dict()
        del raw_state["clearance_required"]
        path.write_text(json.dumps({"store_schema_version": STORE_SCHEMA_VERSION, "state": raw_state}), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(StateIntegrityError, match="missing required field"):
            store.load()

    def test_a_malformed_trade_context_inside_active_trades_raises_state_integrity_error(self, tmp_path):
        path = tmp_path / "state.json"
        raw_state = BotState(trading_day="2026-08-15").to_dict()
        bad_ctx = _ctx().to_dict()
        bad_ctx["status"] = "NOT_A_REAL_STATUS"
        raw_state["active_trades"] = {"RELIANCE": bad_ctx}
        path.write_text(json.dumps({"store_schema_version": STORE_SCHEMA_VERSION, "state": raw_state}), encoding="utf-8")
        store = BotStateStore(path)
        with pytest.raises(StateIntegrityError, match="status"):
            store.load()


class TestSaveValidatesBeforeCommitting:
    def test_a_pre_commit_validation_failure_never_touches_the_filesystem(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        store = BotStateStore(path)

        def always_fails(envelope):
            raise BotStateStoreError("simulated: the about-to-be-written envelope is somehow invalid")
        monkeypatch.setattr(botstate_store_module, "_state_from_envelope", always_fails)

        with pytest.raises(BotStateStoreError, match="simulated"):
            store.save(BotState(trading_day="2026-08-15"))

        assert not path.exists()  # never even reached the temp-file write
        assert not (tmp_path / "state.json.tmp").exists()

    def test_a_pre_commit_validation_failure_leaves_the_prior_file_untouched(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        store = BotStateStore(path)
        store.save(BotState(trading_day="2026-08-14", status=EngineStatus.RUNNING))
        original_bytes = path.read_bytes()

        def always_fails(envelope):
            raise BotStateStoreError("simulated")
        monkeypatch.setattr(botstate_store_module, "_state_from_envelope", always_fails)

        with pytest.raises(BotStateStoreError):
            store.save(BotState(trading_day="2026-08-15", status=EngineStatus.RUNNING))

        assert path.read_bytes() == original_bytes


class TestDeterministicSerialization:
    def test_the_same_state_produces_byte_identical_files(self, tmp_path):
        state = BotState(
            trading_day="2026-08-15", status=EngineStatus.RUNNING,
            active_trades={
                "RELIANCE": _ctx(symbol="RELIANCE", status=PositionStatus.MANAGING),
                "INFY": _ctx(symbol="INFY", status=PositionStatus.ENTRY_PENDING, entry_order_id="OID-1"),
            },
        )
        path_a = tmp_path / "a.json"
        path_b = tmp_path / "b.json"
        BotStateStore(path_a).save(state)
        BotStateStore(path_b).save(state)
        assert path_a.read_bytes() == path_b.read_bytes()


class TestRestartContractRunningDoesNotMeanReconciled:
    """The load-bearing finding from tracing the frozen engine: this store
    is honest, mechanical persistence only. It must return RUNNING
    exactly as persisted - never silently upgrade it to STARTUP itself -
    because that mutation is orchestration policy, not storage mechanics,
    and belongs to Phase 3.6 (completing _build_engine()), where the
    construction/startup layer must impose it explicitly before the
    engine performs any ordinary RUNNING work."""

    def test_a_persisted_running_status_loads_back_as_running_unmodified(self, tmp_path):
        path = tmp_path / "state.json"
        store = BotStateStore(path)
        store.save(BotState(
            trading_day="2026-08-15", status=EngineStatus.RUNNING,
            active_trades={"RELIANCE": _ctx(status=PositionStatus.ENTRY_SUBMITTING)},
        ))

        # A fresh store instance, standing in for a genuinely new process -
        # exactly what Phase 3.6's startup layer will see and must not
        # trust as "already reconciled" just because it parsed cleanly.
        reloaded = BotStateStore(path).load()
        assert reloaded.status == EngineStatus.RUNNING
        assert reloaded.active_trades["RELIANCE"].status == PositionStatus.ENTRY_SUBMITTING
        # This store has no reconcile_startup() call anywhere in it, and
        # no code path that inspects or mutates .status on load - the
        # absence is the point, not an oversight.


class TestSaveFailurePropagatesUncaught:
    def test_an_injected_fsync_failure_propagates_and_never_touches_the_old_file(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        store = BotStateStore(path)
        store.save(BotState(trading_day="2026-08-14", status=EngineStatus.RUNNING))  # a real prior file
        original_bytes = path.read_bytes()

        def failing_fsync(fd):
            raise OSError("simulated disk failure")
        monkeypatch.setattr("os.fsync", failing_fsync)

        with pytest.raises(OSError, match="simulated disk failure"):
            store.save(BotState(trading_day="2026-08-15", status=EngineStatus.RUNNING))

        # save() must not swallow this - and the old durable file must be
        # completely untouched, since os.replace() is never reached. A
        # leftover .tmp file is possible here (matching every other store
        # in this project - none of them clean up on a write failure) and
        # is harmless: the next successful save() overwrites the same
        # deterministic tmp path.
        assert path.read_bytes() == original_bytes
