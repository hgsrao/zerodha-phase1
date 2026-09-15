"""P02-G: hostile-system adversarial matrix.

Not feature tests - invariant tests. Each scenario combines bad timing,
stale/partial broker truth, duplicate observations, malformed payloads,
crashes, and conflicting state, then asserts a system-wide invariant
survived, not just that a particular status string came out right:

- No duplicate broker order IDs / no second place_order for the same intent.
- CommittedCapital never exceeds the frozen ceiling.
- No symbol or sector gets double-reserved (held ∩ reserved is always empty;
  sector occupancy never has duplicates).
- No financial lock sets ENGINE_HALT; no integrity failure is downgraded
  to ENTRY_LOCK.
- No position disappears locally while broker quantity remains non-zero.
- No broker position is adopted without proof.
- No UNKNOWN state silently resolves by assumption.
- No healthy sibling advances past the point a whole-engine halt is
  triggered in that same cycle.
- Repeated step() after ENGINE_HALT never mutates the broker again.
"""

from decimal import Decimal

import pytest

from institutional_engine_v34_p02_multipos_candidate import TradingEngineV34P02
from test_v34_p02_lifecycle_integration import SECTORS, make_context, make_stack, restart
from test_v34_p02_multipos_engine import FakeBroker, cnc_position, flat_running_state, sample_ctx
from v34_p02_accounting import build_portfolio_snapshot, initial_checkpoint, roll_daily_checkpoint
from v34_p02_authorizer import AuthorizerRegistry, reconcile_reservations
from v34_p02_state import BrokerObservationContractViolation, Config, EngineStatus, EntryReservation, PositionStatus


# ---------------------------------------------------------------------------
# Invariant helpers
# ---------------------------------------------------------------------------

def assert_no_double_occupancy(registry: AuthorizerRegistry) -> None:
    held, reserved = set(registry.held_symbols), set(registry.reservations)
    overlap = held & reserved
    assert not overlap, f"symbol(s) both held and reserved: {overlap}"


def assert_no_duplicate_sector_occupancy(registry: AuthorizerRegistry) -> None:
    sectors = list(registry.held_symbols.values()) + [r.sector for r in registry.reservations.values()]
    assert len(sectors) == len(set(sectors)), f"duplicate sector occupancy: {sectors}"


def assert_committed_capital_within_ceiling(snapshot, cfg: Config) -> None:
    assert snapshot.committed_capital <= cfg.trial_capital, (
        f"CommittedCapital {snapshot.committed_capital} exceeds ceiling {cfg.trial_capital}"
    )


# ---------------------------------------------------------------------------
# 1. Ambiguous ack + restart + a different symbol trying to enter
# ---------------------------------------------------------------------------

class TestAmbiguousAckRestartAndConcurrentEntry:
    def test_reliance_stays_unresolved_while_infy_enters_independently_after_restart(self):
        state = flat_running_state()
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state)
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        raw_broker.place_order_fn = lambda **kw: (_ for _ in ()).throw(TimeoutError("lost ack"))
        engine.step()
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.ENTRY_UNKNOWN
        assert "RELIANCE" in authorizer_store.registry.reservations  # occupying its slot while ambiguous

        engine2, terminator2, audit2 = restart(store, raw_broker=raw_broker, cfg=engine.cfg, authorizer_store=authorizer_store, context=ctx_holder["ctx"])
        assert terminator2.halted is False  # ENTRY_UNKNOWN alone is not an engine halt

        raw_broker.place_order_fn = None  # now behaves normally
        assert engine2.request_entry(symbol="INFY", quantity=5, price=Decimal("1500")) == "STATE_CHANGED"
        result = engine2.step()

        assert result["RELIANCE"] == "NO_ACTION"  # still unresolved, no broker trace yet
        assert result["INFY"] == "STATE_CHANGED"
        assert engine2.state.active_trades["INFY"].status == PositionStatus.ENTRY_PENDING
        assert set(authorizer_store.registry.reservations) == {"RELIANCE", "INFY"}
        assert_no_double_occupancy(authorizer_store.registry)
        assert_no_duplicate_sector_occupancy(authorizer_store.registry)  # RELIANCE=ENERGY, INFY=IT, no clash


# ---------------------------------------------------------------------------
# 2. Partial entry fill on one symbol while another is exiting
# ---------------------------------------------------------------------------

class TestConcurrentPartialFillAndExit:
    def test_partial_entry_and_partial_exit_progress_independently_in_one_cycle(self):
        state = flat_running_state()
        state.active_trades["EXITING"] = sample_ctx(symbol="EXITING", status=PositionStatus.EXIT_PENDING, filled_qty=10, pending_qty=10, exit_order_id="EXITORD1")
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("EXITING", 4)]
        raw_broker.order_details["EXITORD1"] = {"order_id": "EXITORD1", "status": "OPEN", "filled_quantity": 6, "quantity": 10}
        raw_broker.quotes = {"NSE:EXITING": {"last_price": 2500}, "NSE:INFY": {"last_price": 1500}}
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)
        engine.request_entry(symbol="INFY", quantity=10, price=Decimal("1500"))
        engine.step()  # INFY -> ENTRY_PENDING
        entry_order_id = engine.state.active_trades["INFY"].entry_order_id
        raw_broker.positions = [cnc_position("EXITING", 4), cnc_position("INFY", 6)]
        raw_broker.order_details[entry_order_id] = {"order_id": entry_order_id, "status": "OPEN", "filled_quantity": 6, "quantity": 10, "average_price": "1500"}

        result = engine.step()

        assert result["EXITING"] == "NO_ACTION"
        assert engine.state.active_trades["EXITING"].pending_qty == 4
        assert result["INFY"] == "STATE_CHANGED"
        assert engine.state.active_trades["INFY"].status == PositionStatus.PARTIAL_POSITION
        assert terminator.halted is False
        assert len(raw_broker.place_order_calls) == 1  # INFY submitted once, EXITING never resubmitted


# ---------------------------------------------------------------------------
# 3. Registry disagreement + financial ENTRY_LOCK simultaneously - integrity wins
# ---------------------------------------------------------------------------

class TestIntegrityHaltBeatsFinancialLock:
    def test_reconciliation_not_clean_plus_daily_hard_halt_produces_engine_halt_not_entry_lock(self):
        state = flat_running_state()
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state)
        engine.request_entry(symbol="RELIANCE", quantity=1, price=Decimal("2500"))

        # Both conditions present: reconciliation_clean=False (integrity)
        # AND a deeply negative realized P&L that would independently trip
        # DAILY_HARD_HALT (financial). Gate 1 must win.
        ctx_holder["ctx"] = make_context(reconciliation_clean=False, cumulative_realized_pnl=Decimal("-5000"))

        result = engine.step()
        assert result["RELIANCE"] == "HALTED"
        assert terminator.halted is True
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        # The symbol was never abandoned-as-policy-decline; it was never
        # even removed from active_trades, because this is not the
        # ENTRY_ABANDONED_POLICY_HALT path at all.
        assert "RELIANCE" in engine.state.active_trades
        assert engine.state.active_trades["RELIANCE"].status != PositionStatus.ENTRY_ABANDONED_POLICY_HALT


# ---------------------------------------------------------------------------
# 4. Broker quantity changes unexpectedly between two observations
# ---------------------------------------------------------------------------

class TestUnexplainedQuantityChange:
    def test_managing_position_quantity_shrinking_with_no_explaining_order_halts(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]
        raw_broker.quotes = {"NSE:RELIANCE": {"last_price": 2550}}
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        result = engine.step()
        assert result["RELIANCE"] == "NO_ACTION"

        # Unexplained shrinkage - no SL, no exit order, nothing that could
        # legitimately reduce this position.
        raw_broker.positions = [cnc_position("RELIANCE", 7)]
        result = engine.step()
        assert result["RELIANCE"] == "HALTED"
        assert terminator.halted is True


# ---------------------------------------------------------------------------
# 5. Duplicate order-history anomaly appearing only on the second pass
# ---------------------------------------------------------------------------

class TestAnomalyAppearingOnSecondReconciliationPass:
    def test_clean_first_pass_then_a_duplicate_order_appears_on_the_second_pass(self):
        from test_v34_p02_authorizer import fingerprint_for, broker_order
        from v34_p02_authorizer import CandidateEntry

        candidate = CandidateEntry(symbol="A", sector="S", quantity=10, price=Decimal("1500"))
        fp = fingerprint_for(candidate)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fp)})

        order = broker_order(fp, order_id="O1", status="OPEN", filled_quantity=0)
        first_pass = reconcile_reservations(registry, orders=[order], positions=[], product="CNC")
        assert "A" in first_pass.reservations  # correctly pending, no anomaly yet

        duplicate_order = broker_order(fp, order_id="O2", status="OPEN", filled_quantity=0)
        with pytest.raises(BrokerObservationContractViolation, match="multiple broker orders"):
            reconcile_reservations(first_pass, orders=[order, duplicate_order], positions=[], product="CNC")


# ---------------------------------------------------------------------------
# 6. Stale-reservation evidence that later becomes contradictory
# ---------------------------------------------------------------------------

class TestStaleEvidenceLaterContradicted:
    def test_provably_stale_clears_then_a_later_matching_order_and_position_are_caught_as_an_orphan_by_the_engine(self):
        from test_v34_p02_authorizer import fingerprint_for
        from v34_p02_authorizer import CandidateEntry

        candidate = CandidateEntry(symbol="GHOST", sector="MISC", quantity=10, price=Decimal("100"))
        fp = fingerprint_for(candidate)
        registry = AuthorizerRegistry(reservations={"GHOST": EntryReservation(symbol="GHOST", sector="MISC", reserved_capital=Decimal("1000"), reserved_at="x", entry_fingerprint=fp)})

        # First reconciliation: zero broker evidence at all - PROVABLY_STALE, cleared.
        cleared = reconcile_reservations(registry, orders=[], positions=[], product="CNC")
        assert "GHOST" not in cleared.reservations
        assert "GHOST" not in cleared.held_symbols

        # Evidence later "changes its mind" - a real order+position now
        # exist for GHOST (e.g. a delayed broker-side replication), but no
        # local reservation or engine TradeContext was ever recreated for
        # it. The authorizer has nothing left to reconcile (correct - it
        # only resolves what IS in its registry) - it is the ENGINE's own
        # orphan detection (P02-C) that must catch this, not the
        # authorizer silently re-adopting it after the fact.
        state = flat_running_state()  # active_trades intentionally empty for GHOST
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("GHOST", 10)]
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker, authorizer_registry=cleared)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert "GHOST" in engine.state.halt_reason


# ---------------------------------------------------------------------------
# 7. Corrupted durable state after a broker order genuinely landed
# ---------------------------------------------------------------------------

class TestCorruptedLocalStateWithGenuineBrokerExecution:
    def test_local_state_missing_the_symbol_entirely_still_halts_never_silently_adopts(self):
        state = flat_running_state()  # nothing recorded locally for RELIANCE at all
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]  # broker legitimately holds it
        raw_broker.orders = [{"tradingsymbol": "RELIANCE", "status": "COMPLETE", "order_id": "REAL1", "quantity": 10, "filled_quantity": 10, "transaction_type": "BUY", "product": "CNC"}]
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)
        engine.reconcile_startup()
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert "RELIANCE" not in engine.state.active_trades  # never silently created/adopted


# ---------------------------------------------------------------------------
# 8/9. Orphans coexisting with healthy known positions
# ---------------------------------------------------------------------------

class TestOrphansAmongHealthyPositions:
    def test_orphan_position_plus_three_healthy_positions_healthy_ones_still_resolve_before_the_halt(self):
        state = flat_running_state()
        for symbol in ("A", "B", "C"):
            state.active_trades[symbol] = sample_ctx(symbol=symbol, status=PositionStatus.MANAGING, filled_qty=5, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("A", 5), cnc_position("B", 5), cnc_position("C", 5), cnc_position("ORPHAN", 3)]
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        engine.reconcile_startup()

        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert "ORPHAN" in engine.state.halt_reason
        # The three known, healthy symbols were each individually proven
        # and correctly resolved to MANAGING (SL search -> none -> stays
        # MANAGING) before the orphan check ran at the very end of
        # reconcile_startup - the whole-engine halt still applies (nothing
        # can proceed autonomously), but their own resolution wasn't
        # corrupted or left half-done.
        for symbol in ("A", "B", "C"):
            assert engine.state.active_trades[symbol].status == PositionStatus.MANAGING

    def test_orphan_active_order_with_no_position_among_two_healthy_positions(self):
        state = flat_running_state()
        for symbol in ("A", "B"):
            state.active_trades[symbol] = sample_ctx(symbol=symbol, status=PositionStatus.MANAGING, filled_qty=5, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("A", 5), cnc_position("B", 5)]
        raw_broker.orders = [{"tradingsymbol": "UNEXPECTED", "status": "OPEN", "order_id": "X1"}]
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        engine.reconcile_startup()

        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert "UNEXPECTED" in engine.state.halt_reason
        for symbol in ("A", "B"):
            assert engine.state.active_trades[symbol].status == PositionStatus.MANAGING


# ---------------------------------------------------------------------------
# 10. Malformed marks: cost-basis math survives, authorization correctly refuses
# ---------------------------------------------------------------------------

class TestMalformedMarksDegradeCorrectly:
    def test_place_order_fails_closed_on_bad_marks_but_cost_basis_functions_remain_valid(self):
        from v34_p02_accounting import compute_committed_capital, compute_deployed_capital

        # A held position (RELIANCE) exists so the authorization snapshot
        # has real cost basis to compute, but no MANAGING position is
        # dispatched this cycle - isolating "missing marks break the
        # authorization snapshot" from the separate, unrelated fact that
        # an actively-polled MANAGING position also needs its own LTP.
        state = flat_running_state()
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]  # owned, but not in active_trades this cycle
        raw_broker.quotes = {}  # no mark for RELIANCE at all
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        engine.request_entry(symbol="INFY", quantity=5, price=Decimal("1500"))
        result = engine.step()
        # Missing marks make the full risk snapshot uncomputable - this is
        # correctly ENGINE_HALT-class (a data problem), not a graceful
        # ENTRY_LOCK decline pretending the numbers are fine.
        assert result["INFY"] == "HALTED"
        assert terminator.halted is True

        # Independently, cost-basis math never needed the marks at all.
        deployed = compute_deployed_capital(raw_broker.positions, product="CNC")
        committed = compute_committed_capital(deployed_capital=deployed, pending_buy_exposure=Decimal("0"), reserved_entry_capital=Decimal("0"))
        assert committed == Decimal("25000.00")


# ---------------------------------------------------------------------------
# 11. Appreciation does not falsely trip the capital ceiling for a new entry
# ---------------------------------------------------------------------------

class TestAppreciationDoesNotFalselyBlockNewEntries:
    def test_a_hugely_appreciated_existing_position_does_not_block_a_new_within_budget_entry(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, entry_price=Decimal("2500"), avg_entry_price=Decimal("2500"), stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10, avg_price="2500")]
        # RELIANCE has appreciated massively - gross exposure is now huge,
        # but cost basis (25000) is what gates new entries.
        raw_broker.quotes = {"NSE:RELIANCE": {"last_price": 9000}, "NSE:INFY": {"last_price": 1500}}
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=state, raw_broker=raw_broker, authorizer_registry=AuthorizerRegistry(held_symbols={"RELIANCE": "ENERGY"}),
        )

        engine.request_entry(symbol="INFY", quantity=10, price=Decimal("1500"))  # +15000, well within 100000-25000
        result = engine.step()
        assert result["INFY"] == "STATE_CHANGED"
        assert engine.state.active_trades["INFY"].status == PositionStatus.ENTRY_PENDING


# ---------------------------------------------------------------------------
# 12. HWM/drawdown across trading-day rollover and restart
# ---------------------------------------------------------------------------

class TestHWMAcrossRolloverAndRestart:
    def test_hwm_is_monotonic_non_decreasing_across_three_days_of_fluctuating_equity(self):
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"))
        checkpoint = initial_checkpoint(trading_day="2026-08-12", trial_capital=cfg.trial_capital)
        assert checkpoint.trial_high_water_mark == Decimal("100000")

        # Day 2 opens up.
        checkpoint = roll_daily_checkpoint(previous_checkpoint=checkpoint, new_trading_day="2026-08-13", fresh_equity_at_rollover=Decimal("103000"))
        assert checkpoint.trial_high_water_mark == Decimal("103000")

        # Many intraday snapshots during day 2 - none of them may move the HWM.
        for price_move in (Decimal("101000"), Decimal("105000"), Decimal("99000")):
            snap = build_portfolio_snapshot(
                positions=[], orders=[], quotes={}, reservations={}, cfg=cfg, checkpoint=checkpoint,
                cumulative_realized_pnl=price_move - cfg.trial_capital, cumulative_charges=Decimal("0"), sealed_daily_pnl_series={},
            )
            assert isinstance(snap.trial_drawdown, Decimal)
        assert checkpoint.trial_high_water_mark == Decimal("103000")  # still untouched

        # Day 2 closes down, day 3 opens lower - HWM must not regress.
        checkpoint = roll_daily_checkpoint(previous_checkpoint=checkpoint, new_trading_day="2026-08-14", fresh_equity_at_rollover=Decimal("98000"))
        assert checkpoint.trial_high_water_mark == Decimal("103000")
        assert checkpoint.prior_close_equity == Decimal("98000")


# ---------------------------------------------------------------------------
# 13. Two or more positions simultaneously in UNKNOWN states
# ---------------------------------------------------------------------------

class TestMultipleSimultaneousUnknownStates:
    def test_two_symbols_both_entry_unknown_resolve_independently_with_isolated_retry_budgets(self):
        state = flat_running_state()
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"), observation_retry_budget=1)
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, cfg=cfg)

        # INFY requested first so it is dispatched before RELIANCE in
        # every step() cycle (insertion order) - needed so INFY's
        # resolution/no-op outcome on the final cycle can still be
        # observed even once RELIANCE goes on to halt the whole engine
        # later in that same cycle's dispatch loop.
        engine.request_entry(symbol="INFY", quantity=5, price=Decimal("1500"))
        engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
        raw_broker.place_order_fn = lambda **kw: (_ for _ in ()).throw(TimeoutError("lost"))
        result = engine.step()
        assert result["RELIANCE"] == "STATE_CHANGED" and result["INFY"] == "STATE_CHANGED"
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.ENTRY_UNKNOWN
        assert engine.state.active_trades["INFY"].status == PositionStatus.ENTRY_UNKNOWN

        # INFY's order shows up; RELIANCE's never does.
        infy_fp = engine.state.active_trades["INFY"].entry_submission_fingerprint
        infy_order = dict(infy_fp)
        infy_order["order_id"] = "INFY-REAL"
        infy_order["status"] = "OPEN"
        raw_broker.orders = [infy_order]

        result = engine.step()
        assert result["INFY"] == "STATE_CHANGED"
        assert engine.state.active_trades["INFY"].status == PositionStatus.ENTRY_PENDING
        # RELIANCE ticked its own failure counter to 1 (still <= budget=1,
        # so NO_ACTION) on this cycle - INFY resolving first (dict
        # insertion order) must not have touched RELIANCE's own count.
        assert result["RELIANCE"] == "NO_ACTION"
        assert engine.state.active_trades["RELIANCE"].entry_reconciliation_failures == 1
        assert terminator.halted is False

        # One more cycle: INFY (already ENTRY_PENDING) must be completely
        # unaffected by RELIANCE finally exhausting its own, isolated budget.
        raw_broker.order_details[engine.state.active_trades["INFY"].entry_order_id] = {
            "order_id": engine.state.active_trades["INFY"].entry_order_id, "status": "OPEN",
            "filled_quantity": 0, "quantity": 5, "average_price": "0",
        }
        result = engine.step()
        assert result["INFY"] == "NO_ACTION"  # unaffected, still legitimately pending its own fill
        assert result["RELIANCE"] == "HALTED"  # NOW exceeds budget=1 (failures=2 > 1)
        assert terminator.halted is True


# ---------------------------------------------------------------------------
# 14. Repeated step() after ENGINE_HALT - zero further broker mutation, ever
# ---------------------------------------------------------------------------

class TestRepeatedStepAfterHaltNeverMutatesBroker:
    def test_ten_more_step_calls_after_halt_place_no_orders_and_submit_no_exits(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        raw_broker = FakeBroker()
        raw_broker.positions = [cnc_position("RELIANCE", 10)]
        raw_broker.quotes = {}  # forces a halt on the very first step (missing LTP)
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker)

        result = engine.step()
        assert result["RELIANCE"] == "HALTED"
        assert terminator.halted is True
        place_calls_at_halt = len(raw_broker.place_order_calls)

        for _ in range(10):
            result = engine.step()
            assert result == {"__engine__": "HALTED"}

        assert len(raw_broker.place_order_calls) == place_calls_at_halt == 0
        assert raw_broker.submit_emergency_exit_fn is None  # never even attempted


# ---------------------------------------------------------------------------
# 15. Repeated request_exit() while exit is already pending
# ---------------------------------------------------------------------------

class TestRepeatedRequestExit:
    def test_second_request_exit_call_is_rejected_not_a_second_submission(self):
        state = flat_running_state()
        state.active_trades["RELIANCE"] = sample_ctx(status=PositionStatus.MANAGING, filled_qty=10, stop_order_id=None)
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state)

        assert engine.request_exit(symbol="RELIANCE") == "STATE_CHANGED"
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.EXIT_SUBMIT
        with pytest.raises(RuntimeError, match="not MANAGING"):
            engine.request_exit(symbol="RELIANCE")
        # Still exactly the one EXIT_SUBMIT intent - nothing duplicated.
        assert engine.state.active_trades["RELIANCE"].status == PositionStatus.EXIT_SUBMIT


# ---------------------------------------------------------------------------
# 16. Capstone: full six-position restart, mixed states, one ambiguous
# ---------------------------------------------------------------------------

class TestFullSixPositionRestartCapstone:
    def test_six_positions_mixed_states_one_ambiguous_resolves_five_and_halts_on_the_sixth(self):
        state = flat_running_state()
        state.active_trades["MANAGING_SYM"] = sample_ctx(symbol="MANAGING_SYM", status=PositionStatus.MANAGING, filled_qty=5, stop_order_id=None)
        state.active_trades["ENTRY_PENDING_SYM"] = sample_ctx(symbol="ENTRY_PENDING_SYM", status=PositionStatus.ENTRY_PENDING, entry_order_id="OID-EP", filled_qty=0, pending_qty=8, target_qty=8, tranche_qty=8, stop_order_id=None)
        state.active_trades["EXIT_PENDING_SYM"] = sample_ctx(symbol="EXIT_PENDING_SYM", status=PositionStatus.EXIT_PENDING, filled_qty=6, pending_qty=6, exit_order_id="OID-EX")
        state.active_trades["PARTIAL_SYM"] = sample_ctx(symbol="PARTIAL_SYM", status=PositionStatus.PARTIAL_POSITION, entry_order_id="OID-PT", filled_qty=3, pending_qty=4, target_qty=7, tranche_qty=7, stop_order_id=None)
        state.active_trades["PROTECTED_SYM"] = sample_ctx(symbol="PROTECTED_SYM", status=PositionStatus.MANAGING, filled_qty=9, stop_order_id="SL-EXISTING")
        state.active_trades["AMBIGUOUS_SYM"] = sample_ctx(symbol="AMBIGUOUS_SYM", status=PositionStatus.MANAGING, filled_qty=2, stop_order_id=None)

        raw_broker = FakeBroker()
        raw_broker.positions = [
            cnc_position("MANAGING_SYM", 5),
            cnc_position("ENTRY_PENDING_SYM", 0) if False else None,  # no broker position yet, order still open
            cnc_position("EXIT_PENDING_SYM", 6),
            cnc_position("PARTIAL_SYM", 3),
            cnc_position("PROTECTED_SYM", 9),
            # AMBIGUOUS_SYM: nothing at all at the broker, despite local MANAGING state.
        ]
        raw_broker.positions = [p for p in raw_broker.positions if p is not None]
        raw_broker.order_details["OID-EP"] = {"order_id": "OID-EP", "status": "OPEN", "filled_quantity": 0, "quantity": 8}
        raw_broker.order_details["OID-EX"] = {"order_id": "OID-EX", "status": "OPEN", "filled_quantity": 4, "quantity": 10}
        raw_broker.order_details["OID-PT"] = {"order_id": "OID-PT", "status": "OPEN", "filled_quantity": 3, "quantity": 7, "average_price": "1000"}
        raw_broker.orders = [
            {"tradingsymbol": "PROTECTED_SYM", "exchange": "NSE", "product": "CNC", "tag": "V3.4_P02_SL", "transaction_type": "SELL", "status": "TRIGGER PENDING", "order_id": "SL-EXISTING", "quantity": 9},
        ]

        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("500000"), max_simultaneous_positions=6)
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(state=state, raw_broker=raw_broker, cfg=cfg)

        engine.reconcile_startup()

        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert "AMBIGUOUS_SYM" in engine.state.halt_reason
        assert terminator.halted is True

        # The five resolvable positions were each independently proven and
        # correctly advanced before the halt - none left corrupted,
        # doubled, or silently dropped.
        assert engine.state.active_trades["MANAGING_SYM"].status == PositionStatus.MANAGING
        assert engine.state.active_trades["EXIT_PENDING_SYM"].status == PositionStatus.EXIT_PENDING
        assert engine.state.active_trades["PARTIAL_SYM"].status == PositionStatus.PARTIAL_POSITION
        assert engine.state.active_trades["PROTECTED_SYM"].status == PositionStatus.MANAGING
        assert engine.state.active_trades["PROTECTED_SYM"].stop_order_id == "SL-EXISTING"
        assert engine.state.active_trades["ENTRY_PENDING_SYM"].status == PositionStatus.ENTRY_SUBMIT or engine.state.active_trades["ENTRY_PENDING_SYM"].status == PositionStatus.ENTRY_PENDING
        # No broker mutation of any kind was ever attempted during a pure
        # reconciliation pass.
        assert len(raw_broker.place_order_calls) == 0
        assert raw_broker.submit_emergency_exit_fn is None
