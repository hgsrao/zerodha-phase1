"""Tests for v34_bridge_runner_startup.py — Phase 3.6 acceptance tests.

Real engine, real durable stores (tmp_path-backed files), a fake Kite
connection shaped like the real SDK (FakeKiteConnectWithOrders, reused
from test_v34_bridge_kite_broker_client.py, itself built on Phase 1's
own FakeKiteConnect) - proving the whole production stack composes
correctly, not just each sealed component in isolation.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import DEFAULT_ENTRY_TAG, DEFAULT_EXIT_TAG
from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_authorizer_registry_store import AuthorizerRegistryStore as DurableAuthorizerRegistryStore
from v34_bridge_botstate_store import BotStateStore
from v34_bridge_daily_accounting_store import DailyAccountingStore
from v34_bridge_runner_lock import RunnerLockHeldError
from v34_bridge_runner_startup import ProductionRunnerPaths, build_production_engine
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_state import BotState, Config, EngineStatus, EntryReservation

MARKET_HOURS_UTC = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)  # 10:30 IST, a Friday
SECTORS = {"RELIANCE": "ENERGY", "INFY": "IT"}


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    defaults.update(overrides)
    return Config(**defaults)


def make_paths(tmp_path):
    return ProductionRunnerPaths(data_dir=tmp_path / "data")


def build(tmp_path, *, kite=None, live_trading_enabled=False, now=MARKET_HOURS_UTC, config=None, **overrides):
    kite = kite or FakeKiteConnectWithOrders()
    if not kite.virtual_contract_note_response:
        kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
    return build_production_engine(
        paths=make_paths(tmp_path), kite=kite, live_trading_enabled=live_trading_enabled,
        cfg=config or cfg(), sector_lookup=SECTORS, clock=FakeClock(now), **overrides,
    )


def _position(symbol, qty, avg_price="2500.00", product="CNC"):
    return {"tradingsymbol": symbol, "exchange": "NSE", "product": product, "quantity": qty, "average_price": avg_price}


def _order(order_id, *, symbol="RELIANCE", transaction_type="BUY", tag=DEFAULT_ENTRY_TAG, status="COMPLETE", quantity=10, filled_quantity=10, price=2500.0, product="CNC", order_type="LIMIT", exchange="NSE"):
    return {
        "order_id": order_id, "tradingsymbol": symbol, "transaction_type": transaction_type, "product": product,
        "status": status, "quantity": quantity, "filled_quantity": filled_quantity, "price": price, "tag": tag,
        "order_type": order_type, "exchange": exchange,
    }


def _trade(trade_id, order_id, *, symbol="RELIANCE", transaction_type="BUY", quantity=10, price="2500.00", fill_time="2026-08-14 10:00:00"):
    return {
        "trade_id": trade_id, "order_id": order_id, "tradingsymbol": symbol, "transaction_type": transaction_type,
        "quantity": quantity, "average_price": price, "fill_timestamp": fill_time,
    }


class TestFreshBootDayOne:
    def test_builds_a_running_engine_with_no_priors(self, tmp_path):
        engine = build(tmp_path)
        assert engine.terminator.halted is False
        assert engine.state.status == EngineStatus.RUNNING
        assert engine.state.active_trades == {}

    def test_all_durable_files_now_exist(self, tmp_path):
        build(tmp_path)
        paths = make_paths(tmp_path)
        assert paths.bot_state.exists()
        assert paths.authorizer_registry.exists()
        assert paths.daily_accounting.exists()

    def test_startup_reconciliation_passed_is_audited(self, tmp_path):
        engine = build(tmp_path)
        records = [json.loads(line) for line in make_paths(tmp_path).audit_log.read_text(encoding="utf-8").splitlines()]
        event_types = [r["event_type"] for r in records]
        assert "STARTUP_RECONCILIATION_PASSED" in event_types
        assert "AUTHORIZER_RESERVATIONS_RECONCILED_AT_STARTUP" in event_types


class TestForcedStartupReconciliation:
    def test_a_persisted_running_state_is_forced_through_startup_and_reconciled(self, tmp_path):
        paths = make_paths(tmp_path)
        BotStateStore(paths.bot_state).save(BotState(trading_day="2026-08-14", status=EngineStatus.RUNNING))
        engine = build(tmp_path)
        records = [json.loads(line) for line in paths.audit_log.read_text(encoding="utf-8").splitlines()]
        event_types = [r["event_type"] for r in records]
        forced_idx = event_types.index("STARTUP_FORCED_FROM_PERSISTED_STATE")
        passed_idx = event_types.index("STARTUP_RECONCILIATION_PASSED")
        assert forced_idx < passed_idx  # forced-to-STARTUP happened strictly before reconciliation ran
        assert records[forced_idx]["fields"]["loaded_status"] == "RUNNING"
        assert engine.state.status == EngineStatus.RUNNING  # ends up RUNNING again - trivially reconciled (no open positions)

    def test_the_loaded_status_and_reason_are_preserved_in_the_audit_trail_not_erased(self, tmp_path):
        paths = make_paths(tmp_path)
        BotStateStore(paths.bot_state).save(
            BotState(trading_day="2026-08-14", status=EngineStatus.RECONCILING, halt_reason=None)
        )
        build(tmp_path)
        records = [json.loads(line) for line in paths.audit_log.read_text(encoding="utf-8").splitlines()]
        forced = next(r for r in records if r["event_type"] == "STARTUP_FORCED_FROM_PERSISTED_STATE")
        assert forced["fields"]["loaded_status"] == "RECONCILING"
        assert forced["fields"]["loaded_trading_day"] == "2026-08-14"


class TestReservationRecovery:
    def test_a_fingerprinted_reservation_matching_a_complete_order_and_position_is_promoted_to_held(self, tmp_path):
        paths = make_paths(tmp_path)
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "product": "CNC",
            "order_type": "LIMIT", "quantity": 10, "price": "2500", "tag": DEFAULT_ENTRY_TAG,  # canonical_decimal_string form - no trailing zeros
        }
        reservation = EntryReservation(symbol="RELIANCE", sector="ENERGY", reserved_capital=Decimal("25000"), reserved_at="2026-08-14T09:20:00+00:00", entry_fingerprint=fingerprint)
        DurableAuthorizerRegistryStore(paths.authorizer_registry).save(AuthorizerRegistry(reservations={"RELIANCE": reservation}))

        kite = FakeKiteConnectWithOrders()
        kite.orders_response = [_order("ORD-1")]
        kite.positions_response = {"net": [_position("RELIANCE", 10)], "day": []}

        engine = build(tmp_path, kite=kite)
        registry = engine.broker.authorizer_store.registry
        assert "RELIANCE" not in registry.reservations  # no longer just "reserved"
        assert registry.held_symbols.get("RELIANCE") == "ENERGY"  # promoted to held, proven by fresh broker evidence

    def test_a_provably_stale_fingerprinted_reservation_is_cleared_not_left_reserved(self, tmp_path):
        paths = make_paths(tmp_path)
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "product": "CNC",
            "order_type": "LIMIT", "quantity": 10, "price": "2500", "tag": DEFAULT_ENTRY_TAG,
        }
        reservation = EntryReservation(symbol="RELIANCE", sector="ENERGY", reserved_capital=Decimal("25000"), reserved_at="2026-08-14T09:20:00+00:00", entry_fingerprint=fingerprint)
        DurableAuthorizerRegistryStore(paths.authorizer_registry).save(AuthorizerRegistry(reservations={"RELIANCE": reservation}))
        # No matching order anywhere in today's broker order history - the
        # crash happened strictly between mark_reservation_submitted and
        # the real broker call.
        kite = FakeKiteConnectWithOrders()
        engine = build(tmp_path, kite=kite)
        registry = engine.broker.authorizer_store.registry
        assert registry.reservations == {}
        assert registry.held_symbols == {}


class TestMalformedDurableStatesPreventConstruction:
    def test_malformed_bot_state_prevents_construction_before_any_broker_write_authority_is_usable(self, tmp_path):
        paths = make_paths(tmp_path)
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.bot_state.write_text("{not valid json", encoding="utf-8")
        kite = FakeKiteConnectWithOrders()
        with pytest.raises(Exception):
            build(tmp_path, kite=kite)
        assert kite.place_order_calls == []

    def test_malformed_authorizer_registry_prevents_normal_startup(self, tmp_path):
        paths = make_paths(tmp_path)
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.authorizer_registry.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(Exception):
            build(tmp_path)

    def test_malformed_daily_accounting_state_prevents_authorization(self, tmp_path):
        paths = make_paths(tmp_path)
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.daily_accounting.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(Exception):
            build(tmp_path)


class TestPersistenceFailureRemainsFatal:
    def test_an_injected_authorizer_registry_save_failure_during_reservation_reconciliation_propagates(self, tmp_path, monkeypatch):
        import v34_bridge_authorizer_registry_store as authreg_module

        def failing_save(self, registry):
            raise OSError("simulated disk failure")
        monkeypatch.setattr(authreg_module.AuthorizerRegistryStore, "save", failing_save)

        with pytest.raises(OSError, match="simulated disk failure"):
            build(tmp_path)

    def test_an_injected_bot_state_save_failure_propagates_uncaught(self, tmp_path, monkeypatch):
        import v34_bridge_botstate_store as botstate_module

        def failing_save(self, state):
            raise OSError("simulated disk failure")
        monkeypatch.setattr(botstate_module.BotStateStore, "save", failing_save)

        paths = make_paths(tmp_path)
        BotStateStore.__new__  # noqa: B018 (no-op, keeps the import used)
        # Persist a genuinely restarted RUNNING state first (with the real
        # save, before monkeypatching would block even that) so the forced
        # STARTUP transition's own store.save() call is what fails.
        monkeypatch.undo()
        BotStateStore(paths.bot_state).save(BotState(trading_day="2026-08-14", status=EngineStatus.RUNNING))
        monkeypatch.setattr(botstate_module.BotStateStore, "save", failing_save)

        with pytest.raises(OSError, match="simulated disk failure"):
            build(tmp_path)


class TestLockLifecycle:
    def test_a_second_construction_attempt_is_refused_while_the_first_still_holds_the_lock(self, tmp_path):
        engine = build(tmp_path)
        with pytest.raises(RunnerLockHeldError):
            build(tmp_path)
        engine.lock_provider.release()  # cleanup for test hygiene

    def test_releasing_the_lock_allows_a_fresh_construction_to_succeed(self, tmp_path):
        engine = build(tmp_path)
        engine.lock_provider.release()
        second_engine = build(tmp_path)  # must not raise
        assert second_engine.terminator.halted is False
        second_engine.lock_provider.release()


class TestKillSwitch:
    def test_an_active_kill_switch_declines_a_new_entry_with_zero_broker_orders(self, tmp_path):
        engine = build(tmp_path)
        make_paths(tmp_path).kill_switch.write_text("halted for review", encoding="utf-8")

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        result = engine.step()
        # ENTRY_LOCK-class (spec §2): the engine abandons this one entry
        # attempt via EntryPolicyDeclinedError caught internally by
        # _step_entry_submit() - never a whole-engine halt, never raised
        # out of step() itself.
        assert result["RELIANCE"] == "ENTRY_ABANDONED_POLICY_HALT"
        assert engine.state.status == EngineStatus.RUNNING  # unaffected - ENTRY_LOCK, not ENGINE_HALT
        assert "RELIANCE" not in engine.state.active_trades
        assert engine.broker.raw_broker.kite.place_order_calls == []

    def test_no_kill_switch_file_allows_authorization_to_proceed_to_the_broker_call(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.place_order_response = "REAL-ORDER-1"
        engine = build(tmp_path, kite=kite, live_trading_enabled=True)
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        engine.step()
        assert len(kite.place_order_calls) == 1


class TestLiveTradingDisabledEndToEnd:
    def test_zero_kite_place_order_calls_through_the_fully_integrated_runner(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        engine = build(tmp_path, kite=kite, live_trading_enabled=False)
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        engine.step()  # SAFETY_HALT inside KiteBrokerClient -> trigger_hard_halt
        assert kite.place_order_calls == []
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert engine.terminator.halted is True


class TestOvernightBasisSurvivesRestart:
    def test_a_day_one_entry_basis_is_available_to_a_later_days_sell_accounting(self, tmp_path):
        day1 = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)
        kite = FakeKiteConnectWithOrders()
        kite.orders_response = [_order("ORD-A", status="COMPLETE")]
        kite.trades_response = [_trade("T1", "ORD-A", quantity=10, price="2500", fill_time="2026-08-14 10:00:00")]
        engine1 = build(tmp_path, kite=kite, now=day1)
        engine1.broker.context_provider()  # forces the accounting refresh that books T1's basis
        engine1.lock_provider.release()

        state_after_day1 = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert state_after_day1.open_position_basis["RELIANCE"].entry_trading_day == "2026-08-14"

        # "Restart" on day 3 - a fresh build_production_engine call, fresh
        # in-memory objects, same durable files. Today's broker response
        # shows ONLY the SELL side (T1/ORD-A are gone from today's
        # day-scoped trades()/orders(), exactly like real Kite).
        day3 = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)
        kite2 = FakeKiteConnectWithOrders()
        kite2.orders_response = [_order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE")]
        kite2.trades_response = [_trade("T2", "ORD-B", transaction_type="SELL", quantity=10, price="2650", fill_time="2026-08-17 10:00:00")]
        engine2 = build(tmp_path, kite=kite2, now=day3, dp_charge_per_symbol=Decimal("15.93"))
        engine2.broker.context_provider()

        final_state = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert final_state.open_position_basis == {}  # cycle closed
        assert final_state.cumulative_realized_pnl == Decimal("1500")  # (2650-2500)*10
        assert ("2026-08-17", "RELIANCE") in final_state.dp_charges_booked  # genuinely overnight - DP charge applied
        engine2.lock_provider.release()


class TestPartialFillRestartDedup:
    def test_a_partial_fill_observed_before_a_restart_is_not_double_counted_after(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.orders_response = [_order("ORD-A", status="OPEN", quantity=100, filled_quantity=40)]
        kite.trades_response = [_trade("T1", "ORD-A", quantity=40, price="2500")]
        engine1 = build(tmp_path, kite=kite)
        engine1.broker.context_provider()
        state_before_restart = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert state_before_restart.open_position_basis["RELIANCE"].quantity == 40
        engine1.lock_provider.release()

        # Restart. Today's broker response now shows BOTH the already-seen
        # T1 AND a new T2 - exactly how a real day-scoped trades() call
        # would look after a genuine process crash-and-restart mid-day.
        kite2 = FakeKiteConnectWithOrders()
        kite2.orders_response = [_order("ORD-A", status="COMPLETE", quantity=100, filled_quantity=100)]
        kite2.trades_response = [
            _trade("T1", "ORD-A", quantity=40, price="2500"),
            _trade("T2", "ORD-A", quantity=60, price="2500", fill_time="2026-08-14 10:01:00"),
        ]
        engine2 = build(tmp_path, kite=kite2)
        engine2.broker.context_provider()
        final_state = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert final_state.open_position_basis["RELIANCE"].quantity == 100  # 40+60, not 40+40+60
        assert {"T1", "T2"}.issubset(final_state.booked_trade_ids)
        engine2.lock_provider.release()


class TestDailyRolloverIntegration:
    def test_a_restart_on_a_later_day_rolls_the_checkpoint_and_seals_the_prior_day(self, tmp_path):
        day1 = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)
        engine1 = build(tmp_path, now=day1)
        state_day1 = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert state_day1.trading_day == "2026-08-14"
        engine1.lock_provider.release()

        day2 = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)  # a Monday - a later trading day
        engine2 = build(tmp_path, now=day2)
        state_day2 = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert state_day2.trading_day == "2026-08-17"
        assert "2026-08-14" in state_day2.sealed_daily_pnl_series  # sealed exactly once
        assert state_day2.checkpoint.trading_day == "2026-08-17"
        engine2.lock_provider.release()

    def test_a_new_day_does_not_retain_yesterdays_entries_today_or_turnover(self, tmp_path):
        day1 = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)
        kite1 = FakeKiteConnectWithOrders()
        kite1.orders_response = [_order("ORD-A", status="COMPLETE")]
        kite1.trades_response = [_trade("T1", "ORD-A", quantity=10, price="2500")]
        engine1 = build(tmp_path, kite=kite1, now=day1)
        ctx1 = engine1.broker.context_provider()
        assert ctx1.entries_today == 1
        engine1.lock_provider.release()

        day2 = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)
        kite2 = FakeKiteConnectWithOrders()  # a fresh day - no orders/trades of its own yet
        engine2 = build(tmp_path, kite=kite2, now=day2)
        ctx2 = engine2.broker.context_provider()
        assert ctx2.entries_today == 0
        assert ctx2.turnover_today == Decimal("0")
        engine2.lock_provider.release()

    def test_a_carried_overnight_position_is_never_reclassified_as_a_new_entry_on_the_new_day(self, tmp_path):
        day1 = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)
        kite1 = FakeKiteConnectWithOrders()
        kite1.orders_response = [_order("ORD-A", status="COMPLETE")]
        kite1.trades_response = [_trade("T1", "ORD-A", quantity=10, price="2500")]
        engine1 = build(tmp_path, kite=kite1, now=day1)
        engine1.broker.context_provider()
        engine1.lock_provider.release()

        day2 = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)
        kite2 = FakeKiteConnectWithOrders()
        # The position is still open (visible in positions()) but ORD-A/T1
        # do not reappear in today's (day-scoped) orders()/trades().
        kite2.positions_response = {"net": [_position("RELIANCE", 10)], "day": []}
        engine2 = build(tmp_path, kite=kite2, now=day2)
        ctx2 = engine2.broker.context_provider()
        assert ctx2.entries_today == 0
        state = DailyAccountingStore(make_paths(tmp_path).daily_accounting).load()
        assert "RELIANCE" in state.open_position_basis  # still tracked as open, just not as a "new entry today"
        engine2.lock_provider.release()


class TestHardHaltAlertAuditSemantics:
    def test_a_startup_reconciliation_failure_halts_the_engine_even_if_alert_and_audit_both_fail(self, tmp_path, monkeypatch):
        # An orphaned broker position with no matching local TradeContext
        # forces trigger_hard_halt() during reconcile_startup() (step 10).
        # trigger_hard_halt()'s OWN audit.log()/alert.send() calls (traced
        # in Phase 3.3/3.4A) are shielded by the frozen engine's own
        # local try/except - this test makes THOSE specific calls fail
        # without touching this module's own earlier, ordinary diagnostic
        # audit.log() calls (DP_CHARGE_CONFIGURED and SHADOW_MODE_ACTIVE
        # at step 5/6, AUTHORIZER_RESERVATIONS_RECONCILED_AT_STARTUP at
        # step 8), none of which has any such shield and are correctly
        # NOT expected to survive a disk failure - see this module's own
        # docstring: it invents no new "audit must never fail" policy
        # beyond what the frozen engine itself already decided at each
        # call site.
        import v34_bridge_alert_sink as alert_module
        import v34_bridge_audit_sink as audit_module

        original_audit_log = audit_module.AuditSink.log
        call_count = {"n": 0}

        def selectively_failing_audit_log(self, event_type, **fields):
            call_count["n"] += 1
            if call_count["n"] <= 3:  # this module's own DP_CHARGE_CONFIGURED + SHADOW_MODE_ACTIVE + step-8 calls - let all three succeed normally
                return original_audit_log(self, event_type, **fields)
            raise OSError("simulated audit disk failure")  # the frozen engine's own trigger_hard_halt() call

        def failing_alert_send(self, level, message):
            raise OSError("simulated alert disk failure")  # trigger_hard_halt()'s only alert call site

        monkeypatch.setattr(audit_module.AuditSink, "log", selectively_failing_audit_log)
        monkeypatch.setattr(alert_module.AlertSink, "send", failing_alert_send)

        kite = FakeKiteConnectWithOrders()
        kite.positions_response = {"net": [_position("RELIANCE", 10)], "day": []}  # orphan - no local TradeContext
        engine = build(tmp_path, kite=kite)  # must not raise despite both failures above
        assert engine.terminator.halted is True
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT


class TestDpChargeEndToEndFailClosed:
    """Pre-Live Gate 3 requirement #5: simulate an overnight position that
    closes after a restart with no DP-charge rate configured, through the
    FULLY INTEGRATED runner path (a real build_production_engine() call,
    not a unit test reaching directly into v34_bridge_daily_accounting.py)
    - must refuse before any engine is ever returned, structurally
    guaranteeing no new-entry authorization could follow it."""

    def test_an_overnight_close_with_no_dp_rate_raises_before_any_engine_is_returned(self, tmp_path):
        from v34_bridge_daily_accounting import DailyAccountingReconciliationError

        # Day 1: a real entry, durably booked via the integrated path -
        # the SAME setup TestOvernightBasisSurvivesRestart above uses for
        # its own (successful) proof, so this test isolates exactly one
        # variable: no dp_charge_per_symbol on the restart.
        day1 = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)
        kite1 = FakeKiteConnectWithOrders()
        kite1.orders_response = [_order("ORD-A", status="COMPLETE")]
        kite1.trades_response = [_trade("T1", "ORD-A", quantity=10, price="2500", fill_time="2026-08-14 10:00:00")]
        engine1 = build(tmp_path, kite=kite1, now=day1)
        engine1.broker.context_provider()
        engine1.lock_provider.release()

        # "Restart" on a later day - a genuinely overnight/demat-debited
        # close, DP-triggering - with dp_charge_per_symbol deliberately
        # omitted (simulating a misconfigured/absent production value
        # slipping past v34_bridge_runner_main.py's own Gate-3 fail-
        # closed env-var check, e.g. a future caller that bypasses it).
        day3 = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)
        kite2 = FakeKiteConnectWithOrders()
        kite2.orders_response = [_order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE")]
        kite2.trades_response = [_trade("T2", "ORD-B", transaction_type="SELL", quantity=10, price="2650", fill_time="2026-08-17 10:00:00")]

        with pytest.raises(DailyAccountingReconciliationError, match="DP charge"):
            build(tmp_path, kite=kite2, now=day3, dp_charge_per_symbol=None)
        # No `engine` object exists in this scope at all - build_production_
        # engine() raised instead of returning one, which is itself the
        # proof that no new-entry authorization could possibly follow:
        # there is no engine to call request_entry() on.

    def test_the_lock_is_not_leaked_by_the_failed_attempt(self, tmp_path):
        from v34_bridge_daily_accounting import DailyAccountingReconciliationError

        day1 = datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)
        kite1 = FakeKiteConnectWithOrders()
        kite1.orders_response = [_order("ORD-A", status="COMPLETE")]
        kite1.trades_response = [_trade("T1", "ORD-A", quantity=10, price="2500", fill_time="2026-08-14 10:00:00")]
        engine1 = build(tmp_path, kite=kite1, now=day1)
        engine1.broker.context_provider()
        engine1.lock_provider.release()

        day3 = datetime(2026, 8, 17, 5, 0, 0, tzinfo=timezone.utc)
        kite2 = FakeKiteConnectWithOrders()
        kite2.orders_response = [_order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE")]
        kite2.trades_response = [_trade("T2", "ORD-B", transaction_type="SELL", quantity=10, price="2650", fill_time="2026-08-17 10:00:00")]

        with pytest.raises(DailyAccountingReconciliationError):
            build(tmp_path, kite=kite2, now=day3, dp_charge_per_symbol=None)

        # The lock-leak guard (build_production_engine()'s own try/except
        # around steps 7-13) must have released it - a fresh construction
        # attempt (this time with the rate correctly supplied) must
        # succeed, not raise RunnerLockHeldError.
        kite3 = FakeKiteConnectWithOrders()
        kite3.orders_response = [_order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE")]
        kite3.trades_response = [_trade("T2", "ORD-B", transaction_type="SELL", quantity=10, price="2650", fill_time="2026-08-17 10:00:00")]
        engine3 = build(tmp_path, kite=kite3, now=day3, dp_charge_per_symbol=Decimal("15.34"))
        assert engine3.terminator.halted is False
        engine3.lock_provider.release()


class TestDpChargePerSymbolIdentityThreading:
    """Pre-Live Gate 3 requirement #3: prove dp_charge_per_symbol reaches
    reconcile_daily_accounting unchanged - no float conversion, no second
    configuration read - at BOTH call sites inside build_production_
    engine() (the step-11 startup refresh, and _context_provider()'s own
    later refresh)."""

    def test_the_same_object_reaches_reconcile_and_persist_at_both_call_sites(self, tmp_path, monkeypatch):
        import v34_bridge_runner_startup as startup_module

        sentinel = Decimal("15.34")
        captured = []
        original = startup_module.reconcile_and_persist

        def spy(*args, **kwargs):
            captured.append(kwargs["dp_charge_per_symbol"])
            return original(*args, **kwargs)
        monkeypatch.setattr(startup_module, "reconcile_and_persist", spy)

        kite = FakeKiteConnectWithOrders()
        engine = build(tmp_path, kite=kite, dp_charge_per_symbol=sentinel)
        assert len(captured) == 1  # step 11's own startup refresh
        assert captured[0] is sentinel  # identity, not merely equality - proves no re-parse/re-construction

        engine.broker.context_provider()  # the second call site
        assert len(captured) == 2
        assert captured[1] is sentinel

    def test_none_also_threads_through_unchanged_when_not_supplied(self, tmp_path, monkeypatch):
        import v34_bridge_runner_startup as startup_module

        captured = []
        original = startup_module.reconcile_and_persist

        def spy(*args, **kwargs):
            captured.append(kwargs["dp_charge_per_symbol"])
            return original(*args, **kwargs)
        monkeypatch.setattr(startup_module, "reconcile_and_persist", spy)

        build(tmp_path)  # dp_charge_per_symbol left at its default (None)
        assert captured == [None]


class TestShadowModeEndToEnd:
    """EA-1 (Execution Authority Gate, Stage 1) end-to-end proof, through
    the real, fully integrated engine - not just ShadowModeBrokerClient
    in isolation (already covered by test_v34_bridge_shadow_broker_
    client.py)."""

    def test_defaults_to_off_normal_engines_are_unaffected(self, tmp_path):
        engine = build(tmp_path)
        assert engine.broker.raw_broker.__class__.__name__ == "KiteBrokerClient"

    def test_shadow_mode_true_wraps_the_raw_broker(self, tmp_path):
        engine = build(tmp_path, shadow_mode=True)
        assert engine.broker.raw_broker.__class__.__name__ == "ShadowModeBrokerClient"

    def test_shadow_mode_with_live_trading_enabled_true_refuses_construction(self, tmp_path):
        # Defense-in-depth, structural: ShadowModeBrokerClient itself
        # refuses to wrap a broker whose own live_trading_enabled isn't
        # exactly False - this combination never silently "just works"
        # as ordinary live trading with an inert shadow flag.
        with pytest.raises(RuntimeError, match="live_trading_enabled"):
            build(tmp_path, shadow_mode=True, live_trading_enabled=True)

    def test_a_real_authorized_entry_is_declined_not_halted_and_zero_orders_placed(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        kite.place_order_response = "SHOULD-NEVER-BE-CALLED"
        engine = build(tmp_path, kite=kite, shadow_mode=True, live_trading_enabled=False)

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        result = engine.step()

        assert result["RELIANCE"] == "ENTRY_ABANDONED_POLICY_HALT"
        assert engine.state.status == EngineStatus.RUNNING  # NOT halted - the whole point of EA-1
        assert engine.terminator.halted is False
        assert "RELIANCE" not in engine.state.active_trades
        assert kite.place_order_calls == []  # zero real orders, structurally

        records = [json.loads(line) for line in make_paths(tmp_path).audit_log.read_text(encoding="utf-8").splitlines()]
        would_submit = [r for r in records if r["event_type"] == "WOULD_SUBMIT"]
        assert len(would_submit) == 1
        assert would_submit[0]["fields"]["symbol"] == "RELIANCE"
        assert would_submit[0]["fields"]["side"] == "BUY"

    def test_a_second_entry_signal_for_the_same_symbol_on_a_later_cycle_is_not_blocked(self, tmp_path):
        # Proves the reservation-cleanup fix end-to-end, through the real
        # engine's own request_entry()/step() dispatch, not just at the
        # ShadowModeBrokerClient unit level.
        kite = FakeKiteConnectWithOrders()
        engine = build(tmp_path, kite=kite, shadow_mode=True)

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        first = engine.step()
        assert first["RELIANCE"] == "ENTRY_ABANDONED_POLICY_HALT"

        # A brand new entry attempt for the SAME symbol, a later cycle -
        # must reach WOULD_SUBMIT again, not decline via SYMBOL_ALREADY_HELD.
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2510"))
        second = engine.step()
        assert second["RELIANCE"] == "ENTRY_ABANDONED_POLICY_HALT"

        records = [json.loads(line) for line in make_paths(tmp_path).audit_log.read_text(encoding="utf-8").splitlines()]
        would_submit = [r for r in records if r["event_type"] == "WOULD_SUBMIT"]
        assert len(would_submit) == 2  # both signals captured, not just one
        assert kite.place_order_calls == []

    def test_a_kill_switch_declined_entry_never_reaches_would_submit_at_all(self, tmp_path):
        # ENTRY_LOCK-class declines (kill switch, cooldown, capital, etc.)
        # happen INSIDE the frozen KiteBrokerAdapterMultiPos.place_order()
        # itself, before it ever reaches the raw broker - shadow mode's
        # own WOULD_SUBMIT logic must never fire for these; they were
        # never actually authorized in the first place.
        engine = build(tmp_path, shadow_mode=True)
        make_paths(tmp_path).kill_switch.write_text("halted for review", encoding="utf-8")

        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        result = engine.step()
        assert result["RELIANCE"] == "ENTRY_ABANDONED_POLICY_HALT"

        records = [json.loads(line) for line in make_paths(tmp_path).audit_log.read_text(encoding="utf-8").splitlines()]
        assert not any(r["event_type"] == "WOULD_SUBMIT" for r in records)

    def test_shadow_mode_active_is_logged_at_startup(self, tmp_path):
        build(tmp_path, shadow_mode=True)
        records = [json.loads(line) for line in make_paths(tmp_path).audit_log.read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "SHADOW_MODE_ACTIVE")
        assert event["fields"]["shadow_mode"] is True

    def test_shadow_mode_active_is_logged_false_when_off(self, tmp_path):
        build(tmp_path)  # shadow_mode defaults to False
        records = [json.loads(line) for line in make_paths(tmp_path).audit_log.read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "SHADOW_MODE_ACTIVE")
        assert event["fields"]["shadow_mode"] is False


class TestDpChargeConfiguredAuditRecord:
    """Pre-Live Gate 3's own forensic-record request: DP_CHARGE_CONFIGURED
    is logged early, unconditionally, with the configured boolean and the
    (public-tariff) amount - never the environment-variable source."""

    def test_configured_true_and_amount_logged_when_supplied(self, tmp_path):
        paths = make_paths(tmp_path)
        build(tmp_path, dp_charge_per_symbol=Decimal("15.34"))
        records = [json.loads(line) for line in paths.audit_log.read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "DP_CHARGE_CONFIGURED")
        assert event["fields"]["configured"] is True
        assert event["fields"]["amount"] == "15.34"

    def test_configured_false_and_amount_none_when_not_supplied(self, tmp_path):
        paths = make_paths(tmp_path)
        build(tmp_path)  # dp_charge_per_symbol defaults to None
        records = [json.loads(line) for line in paths.audit_log.read_text(encoding="utf-8").splitlines()]
        event = next(r for r in records if r["event_type"] == "DP_CHARGE_CONFIGURED")
        assert event["fields"]["configured"] is False
        assert event["fields"]["amount"] is None

    def test_it_is_logged_even_when_the_engine_halts_on_load(self, tmp_path):
        paths = make_paths(tmp_path)
        BotStateStore(paths.bot_state).save(
            BotState(trading_day="2026-08-14", status=EngineStatus.RECONCILIATION_HALT, clearance_required=True)
        )
        build(tmp_path, dp_charge_per_symbol=Decimal("15.34"))
        records = [json.loads(line) for line in paths.audit_log.read_text(encoding="utf-8").splitlines()]
        assert any(r["event_type"] == "DP_CHARGE_CONFIGURED" for r in records)

    def test_it_is_logged_exactly_once_per_startup(self, tmp_path):
        paths = make_paths(tmp_path)
        build(tmp_path, dp_charge_per_symbol=Decimal("15.34"))
        records = [json.loads(line) for line in paths.audit_log.read_text(encoding="utf-8").splitlines()]
        matching = [r for r in records if r["event_type"] == "DP_CHARGE_CONFIGURED"]
        assert len(matching) == 1


class TestAccountingContextRefreshFailureVisibility:
    """EA1-R1: a failure fetching orders/trades inside _context_provider()
    must be visible in the real audit trail before it propagates - it
    still propagates unchanged (this is not a new degrade/retry policy,
    the frozen engine's own place_order() exception handling still
    governs the outcome), only the visibility is new."""

    def test_a_real_trades_failure_is_logged_then_still_propagates(self, tmp_path):
        kite = FakeKiteConnectWithOrders()
        engine = build(tmp_path, kite=kite)
        try:
            kite.raise_on["trades"] = RuntimeError("simulated Kite failure")
            with pytest.raises(RuntimeError, match="simulated Kite failure"):
                engine.broker.context_provider()

            audit_path = make_paths(tmp_path).audit_log
            events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            failures = [e for e in events if e.get("event_type") == "ACCOUNTING_CONTEXT_REFRESH_FAILED"]
            assert len(failures) == 1
            assert failures[0]["fields"]["exception_class"] == "RuntimeError"
            assert "simulated Kite failure" in failures[0]["fields"]["exception_message"]
        finally:
            engine.lock_provider.release()

    def test_a_clean_refresh_logs_nothing(self, tmp_path):
        engine = build(tmp_path)
        try:
            engine.broker.context_provider()
            audit_path = make_paths(tmp_path).audit_log
            events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            assert not any(e.get("event_type") == "ACCOUNTING_CONTEXT_REFRESH_FAILED" for e in events)
        finally:
            engine.lock_provider.release()
