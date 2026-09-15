"""Tests for v34_bridge_daily_accounting.py.

The restart-equivalence test (TestRestartEquivalence) is the headline
invariant: running continuously through a sequence of fills and
restarting halfway through that same sequence must produce the same
DailyAccountingState either way.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import DEFAULT_ENTRY_TAG, DEFAULT_EXIT_TAG
from v34_bridge_daily_accounting import (
    DailyAccountingReconciliationError,
    build_authorization_context,
    compute_daily_entry_stats,
    reconcile_and_persist,
    reconcile_daily_accounting,
)
from v34_bridge_daily_accounting_state import PositionBasis, initial_daily_accounting_state
from v34_bridge_daily_accounting_store import DailyAccountingStore
from v34_p02_state import Config

NOW_UTC = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)  # 15:30 IST


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    defaults.update(overrides)
    return Config(**defaults)


def order(order_id, *, symbol="RELIANCE", transaction_type="BUY", tag=DEFAULT_ENTRY_TAG, status="COMPLETE", product="CNC"):
    return {"order_id": order_id, "tradingsymbol": symbol, "transaction_type": transaction_type, "product": product, "status": status, "tag": tag}


def trade(trade_id, order_id, *, symbol="RELIANCE", transaction_type="BUY", quantity=50, price="2500.00", fill_time="2026-08-15 15:00:00"):
    return {
        "trade_id": trade_id, "order_id": order_id, "tradingsymbol": symbol, "transaction_type": transaction_type,
        "quantity": quantity, "average_price": price, "fill_timestamp": fill_time,
    }


def flat_charge_calculator(calls=None):
    """0.1% of notional, deterministic - a fake standing in for
    get_virtual_contract_note_for_one_order(): one order's params in,
    one response item out."""
    def calculator(params):
        if calls is not None:
            calls.append(params)
        notional = Decimal(str(params["quantity"])) * Decimal(str(params["average_price"]))  # confirmed field name, live - see _order_params_for_charges' own Gate 2 correction history
        return {"order_id": params["order_id"], "charges": {"total": str(notional * Decimal("0.001"))}}
    return calculator


def fresh_state(day="2026-08-15"):
    return initial_daily_accounting_state(trading_day=day, trial_capital=Decimal("100000"))


# ---------------------------------------------------------------------------
# compute_daily_entry_stats
# ---------------------------------------------------------------------------

class TestComputeDailyEntryStats:
    def test_no_orders_or_trades_is_all_zero_and_none(self):
        entries, turnover, seconds = compute_daily_entry_stats(orders=[], trades=[], cfg=cfg(), now=NOW_UTC)
        assert entries == 0
        assert turnover == Decimal("0")
        assert seconds is None

    def test_a_single_fully_filled_entry_counts_once(self):
        orders = [order("ORD-1")]
        trades = [trade("T1", "ORD-1", quantity=50, price="2500")]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg(), now=NOW_UTC)
        assert entries == 1
        assert turnover == Decimal("125000")  # 50 * 2500, from the trade, not any order field
        assert seconds is not None

    def test_a_partially_filled_order_with_multiple_trade_chunks_still_counts_as_one_entry(self):
        orders = [order("ORD-1")]
        trades = [
            trade("T1", "ORD-1", quantity=20, price="100", fill_time="2026-08-15 15:00:00"),
            trade("T2", "ORD-1", quantity=30, price="101", fill_time="2026-08-15 15:01:00"),
        ]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg(), now=NOW_UTC)
        assert entries == 1  # one order_id, regardless of chunk count
        assert turnover == Decimal("20") * Decimal("100") + Decimal("30") * Decimal("101")

    def test_a_cancelled_zero_fill_order_never_counts(self):
        orders = [order("ORD-1", status="CANCELLED")]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=[], cfg=cfg(), now=NOW_UTC)
        assert entries == 0
        assert turnover == Decimal("0")

    def test_exit_trades_never_count_as_entries(self):
        orders = [order("ORD-1", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [trade("T1", "ORD-1", transaction_type="SELL", quantity=50, price="2600")]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg(), now=NOW_UTC)
        assert entries == 0
        assert turnover == Decimal("0")

    def test_a_foreign_tag_is_excluded(self):
        orders = [order("ORD-1", tag="SOME_OTHER_STRATEGY")]
        trades = [trade("T1", "ORD-1")]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg(), now=NOW_UTC)
        assert entries == 0

    def test_a_product_mismatch_is_excluded(self):
        orders = [order("ORD-1", product="MIS")]
        trades = [trade("T1", "ORD-1")]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg(product="CNC"), now=NOW_UTC)
        assert entries == 0

    def test_seconds_since_last_entry_uses_the_latest_qualifying_fill(self):
        # NOW_UTC = 2026-08-15 10:00:00 UTC = 15:30 IST.
        orders = [order("ORD-1")]
        trades = [
            trade("T1", "ORD-1", quantity=20, fill_time="2026-08-15 14:00:00"),  # 90 min before 15:30 IST
            trade("T2", "ORD-1", quantity=30, fill_time="2026-08-15 14:55:00"),  # 35 min before 15:30 IST
        ]
        entries, turnover, seconds = compute_daily_entry_stats(orders=orders, trades=trades, cfg=cfg(), now=NOW_UTC)
        assert seconds == Decimal("2100")  # 35 minutes, the LATEST fill, not the first

    def test_a_trade_referencing_an_unknown_order_raises(self):
        with pytest.raises(DailyAccountingReconciliationError, match="does not appear"):
            compute_daily_entry_stats(orders=[], trades=[trade("T1", "GHOST-ORDER")], cfg=cfg(), now=NOW_UTC)


# ---------------------------------------------------------------------------
# reconcile_daily_accounting - realized P&L / basis
# ---------------------------------------------------------------------------

class TestReconcileHappyPath:
    def test_a_full_entry_then_full_exit_realizes_gross_pnl_and_bills_charges(self):
        state = fresh_state()
        orders = [order("ORD-A"), order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [
            trade("T1", "ORD-A", quantity=50, price="2500"),
            trade("T2", "ORD-B", transaction_type="SELL", quantity=50, price="2600"),
        ]
        calls = []
        new_state = reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator(calls))
        assert new_state.cumulative_realized_pnl == Decimal("5000")  # (2600-2500)*50, GROSS only
        assert new_state.open_position_basis == {}  # cycle closed
        assert "T1" in new_state.booked_trade_ids and "T2" in new_state.booked_trade_ids
        assert "ORD-A" in new_state.accounted_charge_by_order_id and "ORD-B" in new_state.accounted_charge_by_order_id
        assert new_state.cumulative_charges > Decimal("0")
        assert len(calls) == 2  # one charges call per newly-touched order
        # Same-day round trip - no DP charge, even though it's a SELL.
        assert new_state.dp_charges_booked == frozenset()

    def test_charges_are_tracked_separately_from_realized_pnl_never_netted(self):
        state = fresh_state()
        orders = [order("ORD-A"), order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500"), trade("T2", "ORD-B", transaction_type="SELL", quantity=50, price="2600")]
        new_state = reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())
        assert new_state.cumulative_realized_pnl == Decimal("5000")  # unaffected by charges

    def test_an_open_entry_with_no_exit_yet_leaves_basis_open_and_no_realized_pnl(self):
        state = fresh_state()
        orders = [order("ORD-A")]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500")]
        new_state = reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())
        assert new_state.cumulative_realized_pnl == Decimal("0")
        assert new_state.open_position_basis["RELIANCE"] == PositionBasis(
            entry_order_id="ORD-A", quantity=50, avg_price=Decimal("2500"), entry_trading_day="2026-08-15",
        )


class TestIncrementalChargesOnPartialFills:
    def test_charges_are_booked_incrementally_as_each_new_chunk_arrives_not_deferred_to_terminal(self):
        state = fresh_state()
        calls = []
        calculator = flat_charge_calculator(calls)

        # First chunk: order still OPEN (not terminal).
        state = reconcile_daily_accounting(
            state, orders=[order("ORD-A", status="OPEN")], trades=[trade("T1", "ORD-A", quantity=20, price="100")],
            cfg=cfg(), charge_calculator=calculator,
        )
        first_charge = state.accounted_charge_by_order_id["ORD-A"]
        assert first_charge > Decimal("0")  # charged immediately, not deferred until terminal
        assert len(calls) == 1
        assert calls[0]["quantity"] == 20

        # Second chunk arrives, order now COMPLETE.
        state = reconcile_daily_accounting(
            state, orders=[order("ORD-A", status="COMPLETE")],
            trades=[trade("T1", "ORD-A", quantity=20, price="100"), trade("T2", "ORD-A", quantity=30, price="100", fill_time="2026-08-15 15:01:00")],
            cfg=cfg(), charge_calculator=calculator,
        )
        assert len(calls) == 2
        assert calls[1]["quantity"] == 50  # recomputed against the CURRENT aggregate (20+30), not just the delta
        final_charge = state.accounted_charge_by_order_id["ORD-A"]
        assert final_charge > first_charge
        # The order's total booked charge matches charging the full 50 qty once - no double counting.
        assert final_charge == Decimal("50") * Decimal("100") * Decimal("0.001")

    def test_an_order_with_no_new_trades_this_cycle_is_not_recharged(self):
        state = fresh_state()
        calls = []
        calculator = flat_charge_calculator(calls)
        state = reconcile_daily_accounting(
            state, orders=[order("ORD-A", status="OPEN")], trades=[trade("T1", "ORD-A", quantity=20, price="100")],
            cfg=cfg(), charge_calculator=calculator,
        )
        assert len(calls) == 1
        # Same trade, no new information - reconciling again must not call the calculator again.
        reconcile_daily_accounting(
            state, orders=[order("ORD-A", status="OPEN")], trades=[trade("T1", "ORD-A", quantity=20, price="100")],
            cfg=cfg(), charge_calculator=calculator,
        )
        assert len(calls) == 1


class TestPartialFillsUnderOneOrder:
    def test_three_chunks_under_one_exit_order_become_three_separate_realized_pnl_events(self):
        state = fresh_state()
        state = reconcile_daily_accounting(
            state, orders=[order("ORD-A")], trades=[trade("T1", "ORD-A", quantity=100, price="100")],
            cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )
        orders = [order("ORD-A"), order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE")]
        trades = [
            trade("T1", "ORD-A", quantity=100, price="100"),
            trade("T2", "ORD-B", transaction_type="SELL", quantity=20, price="100", fill_time="2026-08-15 15:00:00"),
            trade("T3", "ORD-B", transaction_type="SELL", quantity=30, price="101", fill_time="2026-08-15 15:01:00"),
            trade("T4", "ORD-B", transaction_type="SELL", quantity=50, price="102", fill_time="2026-08-15 15:02:00"),
        ]
        new_state = reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())
        expected_pnl = (Decimal("100") - Decimal("100")) * 20 + (Decimal("101") - Decimal("100")) * 30 + (Decimal("102") - Decimal("100")) * 50
        assert new_state.cumulative_realized_pnl == expected_pnl
        assert new_state.open_position_basis == {}
        assert {"T2", "T3", "T4"}.issubset(new_state.booked_trade_ids)

    def test_charges_billed_exactly_once_for_the_multi_fill_exit_order_in_one_reconciliation_pass(self):
        state = fresh_state()
        orders = [order("ORD-A"), order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE")]
        trades = [
            trade("T1", "ORD-A", quantity=100, price="100"),
            trade("T2", "ORD-B", transaction_type="SELL", quantity=40, price="100"),
            trade("T3", "ORD-B", transaction_type="SELL", quantity=60, price="101"),
        ]
        calls = []
        reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator(calls))
        exit_calls = [c for c in calls if c["order_id"] == "ORD-B"]
        assert len(exit_calls) == 1
        assert exit_calls[0]["quantity"] == 100  # aggregate of both fills


class TestFailClosed:
    def test_a_sell_trade_with_no_known_open_basis_raises(self):
        state = fresh_state()
        orders = [order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [trade("T1", "ORD-B", transaction_type="SELL", quantity=50, price="2600")]
        with pytest.raises(DailyAccountingReconciliationError, match="no known open position basis"):
            reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())

    def test_a_sell_quantity_exceeding_the_open_basis_raises(self):
        state = fresh_state()
        orders = [order("ORD-A"), order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500"), trade("T2", "ORD-B", transaction_type="SELL", quantity=999, price="2600")]
        with pytest.raises(DailyAccountingReconciliationError, match="exceeds"):
            reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())

    def test_a_trade_disagreeing_transaction_type_with_its_order_raises(self):
        state = fresh_state()
        orders = [order("ORD-A", transaction_type="BUY")]
        trades = [trade("T1", "ORD-A", transaction_type="SELL", quantity=50, price="2500")]
        with pytest.raises(DailyAccountingReconciliationError):
            reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())

    def test_a_charge_calculator_response_missing_charges_total_raises(self):
        state = fresh_state()
        orders = [order("ORD-A")]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500")]
        def bad_calculator(params):
            return {"order_id": "ORD-A"}  # no "charges" key
        with pytest.raises(DailyAccountingReconciliationError, match="charges.total"):
            reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(product="CNC"), charge_calculator=bad_calculator)

    def test_a_charges_decrease_raises_instead_of_silently_subtracting(self):
        state = fresh_state()
        def decreasing_calculator(params):
            return {"order_id": params["order_id"], "charges": {"total": "1.00"}}
        state = reconcile_daily_accounting(
            state, orders=[order("ORD-A", status="OPEN")], trades=[trade("T1", "ORD-A", quantity=20, price="100")],
            cfg=cfg(), charge_calculator=decreasing_calculator,
        )
        assert state.accounted_charge_by_order_id["ORD-A"] == Decimal("1.00")

        def lower_calculator(params):
            return {"order_id": params["order_id"], "charges": {"total": "0.50"}}
        with pytest.raises(DailyAccountingReconciliationError, match="LOWER"):
            reconcile_daily_accounting(
                state, orders=[order("ORD-A", status="OPEN")],
                trades=[trade("T1", "ORD-A", quantity=20, price="100"), trade("T2", "ORD-A", quantity=10, price="100", fill_time="2026-08-15 15:01:00")],
                cfg=cfg(), charge_calculator=lower_calculator,
            )


class TestForeignOrdersIgnored:
    def test_a_differently_tagged_order_and_its_trades_have_zero_effect(self):
        state = fresh_state()
        orders = [order("ORD-X", tag="SOME_OTHER_BOT")]
        trades = [trade("T1", "ORD-X", quantity=999, price="1")]
        new_state = reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())
        assert new_state == state


class TestCrossDayPositionBasis:
    def test_an_entry_from_a_prior_day_is_still_usable_for_a_later_exit(self):
        # Day 1: entry only, durably persisted (today's trades() would
        # NOT show this fill on day 2 - Kite's trades() is day-scoped).
        day1_state = fresh_state(day="2026-08-15")
        day1_state = reconcile_daily_accounting(
            day1_state, orders=[order("ORD-A")], trades=[trade("T1", "ORD-A", quantity=50, price="2500")],
            cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )
        # Day 3: only day 3's own orders/trades are visible - ORD-A/T1
        # are nowhere in this call's inputs, only in day1_state's durable
        # open_position_basis. day1_state.trading_day is still "2026-08-15"
        # here because rolling it to "today" is Phase 3.6's own wiring
        # job, not something this pure function does on its own -
        # simulated here by constructing a state that already carries the
        # correct trading_day forward.
        from dataclasses import replace
        day3_state = replace(day1_state, trading_day="2026-08-17")
        day3_orders = [order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        day3_trades = [trade("T2", "ORD-B", transaction_type="SELL", quantity=50, price="2650", fill_time="2026-08-17 10:00:00")]
        result_state = reconcile_daily_accounting(
            day3_state, orders=day3_orders, trades=day3_trades, cfg=cfg(),
            charge_calculator=flat_charge_calculator(), dp_charge_per_symbol=Decimal("15.93"),
        )
        assert result_state.cumulative_realized_pnl == Decimal("7500")  # (2650-2500)*50
        assert result_state.open_position_basis == {}


class TestDpCharges:
    def test_a_same_day_round_trip_never_triggers_a_dp_charge(self):
        state = fresh_state()
        orders = [order("ORD-A"), order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500"), trade("T2", "ORD-B", transaction_type="SELL", quantity=50, price="2600")]
        # No dp_charge_per_symbol supplied - must not raise, because no
        # DP-triggering event should be detected for a same-day cycle.
        new_state = reconcile_daily_accounting(state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())
        assert new_state.dp_charges_booked == frozenset()

    def test_an_overnight_close_with_no_dp_rate_supplied_fails_closed(self):
        from dataclasses import replace
        day1_state = reconcile_daily_accounting(
            fresh_state(day="2026-08-15"), orders=[order("ORD-A")], trades=[trade("T1", "ORD-A", quantity=50, price="2500")],
            cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )
        day2_state = replace(day1_state, trading_day="2026-08-16")
        orders = [order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [trade("T2", "ORD-B", transaction_type="SELL", quantity=50, price="2600", fill_time="2026-08-16 10:00:00")]
        with pytest.raises(DailyAccountingReconciliationError, match="DP charge"):
            reconcile_daily_accounting(day2_state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())

    def test_an_overnight_close_with_a_dp_rate_supplied_books_it_exactly_once(self):
        from dataclasses import replace
        day1_state = reconcile_daily_accounting(
            fresh_state(day="2026-08-15"), orders=[order("ORD-A")], trades=[trade("T1", "ORD-A", quantity=50, price="2500")],
            cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )
        day2_state = replace(day1_state, trading_day="2026-08-16")
        orders = [order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG)]
        trades = [
            trade("T2", "ORD-B", transaction_type="SELL", quantity=30, price="2600", fill_time="2026-08-16 10:00:00"),
            trade("T3", "ORD-B", transaction_type="SELL", quantity=20, price="2600", fill_time="2026-08-16 10:01:00"),
        ]
        before_charges = day2_state.cumulative_charges
        result_state = reconcile_daily_accounting(
            day2_state, orders=orders, trades=trades, cfg=cfg(),
            charge_calculator=flat_charge_calculator(), dp_charge_per_symbol=Decimal("15.93"),
        )
        assert ("2026-08-16", "RELIANCE") in result_state.dp_charges_booked
        # Booked exactly once even though TWO SELL trades closed it.
        exchange_charges_delta = Decimal("50") * Decimal("2600") * Decimal("0.001")
        assert result_state.cumulative_charges == before_charges + exchange_charges_delta + Decimal("15.93")


class TestReconcileAndPersist:
    def test_persists_the_new_state_before_returning(self, tmp_path):
        store = DailyAccountingStore(tmp_path / "accounting.json")
        state = fresh_state()
        orders = [order("ORD-A")]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500")]
        returned = reconcile_and_persist(store, state, orders=orders, trades=trades, cfg=cfg(), charge_calculator=flat_charge_calculator())
        assert store.load() == returned


# ---------------------------------------------------------------------------
# build_authorization_context
# ---------------------------------------------------------------------------

class TestBuildAuthorizationContext:
    def test_assembles_durable_and_fresh_fields_together(self):
        state = fresh_state()
        state = reconcile_daily_accounting(
            state, orders=[order("ORD-A")], trades=[trade("T1", "ORD-A", quantity=50, price="2500")],
            cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )
        orders = [order("ORD-A"), order("ORD-C")]
        trades = [trade("T1", "ORD-A", quantity=50, price="2500"), trade("T3", "ORD-C", quantity=10, price="1000", fill_time="2026-08-15 14:58:00")]
        ctx = build_authorization_context(
            state, orders=orders, trades=trades, cfg=cfg(), now=NOW_UTC,
            reconciliation_clean=True, kill_switch_active=False, sector_lookup={"RELIANCE": "ENERGY"},
        )
        assert ctx.reconciliation_clean is True
        assert ctx.kill_switch_active is False
        assert ctx.sector_lookup == {"RELIANCE": "ENERGY"}
        assert ctx.entries_today == 2  # ORD-A and ORD-C, both qualifying BUY orders
        assert ctx.cumulative_realized_pnl == state.cumulative_realized_pnl
        assert ctx.checkpoint == state.checkpoint


# ---------------------------------------------------------------------------
# Restart equivalence - the headline invariant.
# ---------------------------------------------------------------------------

class TestRestartEquivalence:
    def test_continuous_run_equals_restart_mid_sequence(self, tmp_path):
        orders = [
            order("ORD-A"),
            order("ORD-B", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE"),
            order("ORD-C", symbol="INFY"),
            order("ORD-D", symbol="INFY", transaction_type="SELL", tag=DEFAULT_EXIT_TAG, status="COMPLETE"),
        ]
        trades_full_sequence = [
            trade("T1", "ORD-A", quantity=50, price="2500", fill_time="2026-08-15 09:20:00"),
            trade("T2", "ORD-B", transaction_type="SELL", quantity=20, price="2600", fill_time="2026-08-15 10:00:00"),
            trade("T3", "ORD-B", transaction_type="SELL", quantity=30, price="2610", fill_time="2026-08-15 10:05:00"),
            trade("T4", "ORD-C", symbol="INFY", quantity=15, price="1500", fill_time="2026-08-15 11:00:00"),
            trade("T5", "ORD-D", symbol="INFY", transaction_type="SELL", quantity=15, price="1480", fill_time="2026-08-15 12:00:00"),
        ]

        # Scenario (a): one continuous, uninterrupted reconciliation call
        # sees the entire sequence at once.
        continuous_state = fresh_state()
        continuous_state = reconcile_daily_accounting(
            continuous_state, orders=orders, trades=trades_full_sequence, cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )

        # Scenario (b): restart halfway through. First call only sees
        # T1-T3 (as if that's all that had happened before the crash);
        # persist; construct a genuinely fresh store/state (simulating a
        # new process); second call sees the FULL sequence again (T1-T3
        # reappear in orders()/trades() exactly as a real restart would
        # re-observe today's broker book) plus the new T4/T5.
        store = DailyAccountingStore(tmp_path / "accounting.json")
        state_before_crash = fresh_state()
        partial_orders = orders[:2]
        partial_trades = trades_full_sequence[:3]
        state_before_crash = reconcile_and_persist(
            store, state_before_crash, orders=partial_orders, trades=partial_trades, cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )
        del store, state_before_crash  # simulate the process actually exiting

        store_after_restart = DailyAccountingStore(tmp_path / "accounting.json")
        state_after_restart = store_after_restart.load()
        restarted_state = reconcile_daily_accounting(
            state_after_restart, orders=orders, trades=trades_full_sequence, cfg=cfg(), charge_calculator=flat_charge_calculator(),
        )

        assert restarted_state == continuous_state
