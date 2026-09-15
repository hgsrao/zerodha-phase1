"""Tests for v34_bridge_runner_core._try_auto_resolve_entry_submit_halt
(EA1-R1 step 4, the submission-phase half of the exception-classification
fix). Direct successor to the real 2026-08-17 SUNPHARMA and 2026-08-19
SBIN/SHRIRAMFIN incidents - all three were real Kite 429s during actual
order submission that used to require the owner to run
v34_bridge_resolve_entry_submit_halt.py by hand.

Reuses test_v34_bridge_resolve_entry_submit_halt.py's own real-engine
harness (build_halted_engine) rather than hand-rolled fakes - this
function's whole point is deciding WHEN to call that already-thoroughly-
tested script's run_resolve(), so testing it against the SAME real,
correctly-shaped halted engine that script's own tests use is the most
faithful proof available.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from test_v34_bridge_resolve_entry_submit_halt import build_halted_engine, cfg, make_paths
from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from test_v34_p02_multipos_engine import FakeClock
from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import LegStatus, RebalancePlan, RebalancePlanStatus, RebalancePlanStore, create_rebalance_plan
from v34_bridge_rebalance_transitions import approve_plan
from v34_bridge_runner_core import _try_auto_resolve_entry_submit_halt, drive_plan_one_cycle
from v34_bridge_runner_startup import build_production_engine
from v34_p02_state import EngineStatus, PositionStatus, TradeContext


def _audit_has(engine, event_type: str) -> bool:
    """The real AuditSink (unlike the FakeAudit test double elsewhere in
    this project) has no .has() convenience - it's a real, durable,
    append-only JSONL file. Reading it back is the same discipline this
    whole project already uses to inspect real audit trails."""
    if not engine.audit.path.exists():
        return False
    for line in engine.audit.path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if json.loads(line).get("event_type") == event_type:
            return True
    return False


# ---------------------------------------------------------------------------
# Direct unit tests against _try_auto_resolve_entry_submit_halt itself
# ---------------------------------------------------------------------------

class TestAutoResolveSucceedsOnTheRealTerminalAShape:
    def test_a_genuine_429_during_submission_auto_clears(self, tmp_path):
        engine = build_halted_engine(tmp_path)  # exact real SUNPHARMA/429 shape
        try:
            assert engine.state.halt_source == "ENTRY_SUBMIT"
            resolved = _try_auto_resolve_entry_submit_halt(engine)
            assert resolved is True
            assert "SUNPHARMA" not in engine.state.active_trades
            assert engine.terminator.halted is False
            assert engine.state.clearance_required is False
            assert _audit_has(engine, "AUTO_RESOLVED_ENTRY_SUBMIT_HALT")
        finally:
            engine.lock_provider.release()

    def test_engine_reaches_running_again_on_the_very_next_step_no_restart_needed(self, tmp_path):
        engine = build_halted_engine(tmp_path)
        try:
            assert _try_auto_resolve_entry_submit_halt(engine) is True
            assert engine.state.status == EngineStatus.STARTUP  # clear_halt_and_reconcile()'s own result
            engine.step()  # the SAME engine object, SAME process - no restart
            assert engine.state.status == EngineStatus.RUNNING
            assert engine.terminator.halted is False
        finally:
            engine.lock_provider.release()


class TestAutoResolveRefusesAndFallsBackCleanly:
    def test_refuses_a_non_entry_submit_halt_source(self, tmp_path):
        engine = build_halted_engine(tmp_path, halt_source="RESILIENCE_LAYER")
        try:
            resolved = _try_auto_resolve_entry_submit_halt(engine)
            assert resolved is False
            assert "SUNPHARMA" in engine.state.active_trades  # untouched
            assert engine.terminator.halted is True  # still halted - correctly deferred to a human
            assert _audit_has(engine, "AUTO_RESOLVE_ENTRY_SUBMIT_HALT_REFUSED") is False  # never even attempted
        finally:
            engine.lock_provider.release()

    def test_refuses_when_a_live_matching_order_actually_exists(self, tmp_path):
        # The exact case run_resolve() itself is most careful about -
        # reality changed since the halt, a real order might exist.
        kite = FakeKiteConnectWithOrders()
        kite.orders_response = [{
            "order_id": "REAL-ORDER-1", "tradingsymbol": "SUNPHARMA", "exchange": "NSE",
            "transaction_type": "BUY", "product": "CNC", "order_type": "LIMIT",
            "quantity": 12, "price": 1910.4, "tag": "V3.4_P02_ENTRY", "status": "OPEN",
        }]
        engine = build_halted_engine(tmp_path, kite=kite)
        try:
            resolved = _try_auto_resolve_entry_submit_halt(engine)
            assert resolved is False
            assert "SUNPHARMA" in engine.state.active_trades  # untouched - nothing was guessed
            assert engine.terminator.halted is True
            assert _audit_has(engine, "AUTO_RESOLVE_ENTRY_SUBMIT_HALT_REFUSED")
        finally:
            engine.lock_provider.release()

    def test_refuses_when_more_than_one_candidate_exists(self, tmp_path):
        # A multi-position halt is deliberately left for a human - not
        # this function's call to make.
        second_trade = TradeContext(
            symbol="BAJFINANCE", status=PositionStatus.ENTRY_SUBMITTING,
            entry_price=Decimal("1086.9"), target_qty=21, tranche_qty=21,
            entry_tag="V3.4_P02_ENTRY", entry_order_id=None,
            entry_submission_fingerprint={
                "exchange": "NSE", "order_type": "LIMIT", "price": "1086.9", "product": "CNC",
                "quantity": 21, "tag": "V3.4_P02_ENTRY", "tradingsymbol": "BAJFINANCE", "transaction_type": "BUY",
            },
        )
        engine = build_halted_engine(tmp_path)
        try:
            engine.state.active_trades["BAJFINANCE"] = second_trade
            resolved = _try_auto_resolve_entry_submit_halt(engine)
            assert resolved is False
            assert set(engine.state.active_trades) == {"SUNPHARMA", "BAJFINANCE"}  # both untouched
            assert engine.terminator.halted is True
        finally:
            engine.lock_provider.release()


# ---------------------------------------------------------------------------
# End-to-end through drive_plan_one_cycle, proving the whole EA1-R1
# submission-phase fix works together, not just the helper in isolation.
# This is the fixed counterpart to test_ea1_incident_reproduction_
# 20260817.py's TestTerminalA429DuringSubmissionReproduction.
# ---------------------------------------------------------------------------

def _direct_plan(*, target_id, signal_date, enter_status, approved_at, created_at):
    """Constructs a RebalancePlan directly - bypasses build_target_
    portfolio()'s real momentum ranking entirely, which this test has no
    need of and which (as test_ea1_incident_reproduction_20260817.py's
    own comment found the hard way) refuses any quote dict that doesn't
    happen to match its own real top-N pick for the signal date."""
    from v34_bridge_rebalance_diff import RebalanceDiff
    enters = {"SUNPHARMA": 12}
    diff = RebalanceDiff(target_id=target_id, computed_from_current={}, keep=frozenset(), exits={}, enters=enters)
    return RebalancePlan(
        target_id=target_id, diff=diff, status=RebalancePlanStatus.ENTERING,
        exit_status={}, enter_status=dict(enter_status), created_at=created_at,
        last_broker_check_at=created_at, enter_retry_count={"SUNPHARMA": 0},
        signal_date=signal_date, approved_at=approved_at,
    )


class TestEndToEndPlanRecoversFromA429WithoutAnyOperatorAction:
    def test_full_cycle_a_429_during_submission_no_longer_permanently_kills_the_plan(self, tmp_path):
        from datetime import datetime, timezone
        engine = build_halted_engine(tmp_path)
        try:
            SIGNAL_DATE = date(2026, 8, 17)
            now = datetime(2026, 8, 17, 5, 19, 0, tzinfo=timezone.utc)
            plan = _direct_plan(
                target_id="V11_2026-08-17_TEST", signal_date=SIGNAL_DATE,
                enter_status={"SUNPHARMA": LegStatus.SUBMITTED},  # matches the real halted-state shape
                approved_at=now, created_at=now,
            )

            plan_store = RebalancePlanStore(tmp_path / "plans")
            plan_store.save(plan)

            # This one cycle: engine.step() observes the ambiguity-free
            # RECONCILIATION_HALT, auto-resolves it, and returns without
            # touching the plan at all - it stays exactly as it was.
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
            assert plan.status == RebalancePlanStatus.ENTERING  # NOT halted
            assert plan.enter_status["SUNPHARMA"] == LegStatus.SUBMITTED  # untouched this cycle

            # Next cycle: SUNPHARMA has vanished from active_trades (the
            # auto-resolve removed it) - the plan's own existing "vanished
            # -> retry once" logic picks it up completely normally.
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
            assert plan.enter_status["SUNPHARMA"] == LegStatus.PENDING
            assert plan.enter_retry_count["SUNPHARMA"] == 1
        finally:
            engine.lock_provider.release()
