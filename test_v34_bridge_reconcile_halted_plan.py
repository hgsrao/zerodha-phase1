"""Tests for v34_bridge_reconcile_halted_plan.py.

No network, no credentials, no real broker calls anywhere in this file -
a fake engine/plan/store exercise the real run_reconcile() core against
synthetic fixtures only, mirroring the existing test_v34_bridge_clear_halt.py
/ test_v34_bridge_resolve_entry_submit_halt.py style.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v34_bridge_rebalance_plan import (
    LegStatus,
    RebalanceDiff,
    RebalancePlan,
    RebalancePlanStatus,
)
from v34_bridge_reconcile_halted_plan import run_reconcile
from v34_p02_state import PositionStatus


def _diff(enters, target_id="V11_2026-08-17_TEST"):
    return RebalanceDiff(target_id=target_id, enters=enters, exits={}, keep=[], computed_from_current={})


def _plan(*, status, enter_status, enter_retry_count=None, exit_status=None,
          target_id="V11_2026-08-17_TEST", enters=None, signal_date=None):
    enters = enters if enters is not None else {s: 10 for s in enter_status}
    now = datetime(2026, 8, 17, 5, 19, 0, tzinfo=timezone.utc)
    return RebalancePlan(
        target_id=target_id,
        diff=_diff(enters, target_id=target_id),
        status=status,
        exit_status=exit_status or {},
        enter_status=dict(enter_status),
        created_at=now,
        last_broker_check_at=now,
        enter_retry_count=dict(enter_retry_count or {s: 0 for s in enter_status}),
        signal_date=signal_date if signal_date is not None else now.date(),
        approved_at=now,
    )


class _FakeAudit:
    def __init__(self):
        self.entries = []

    def log(self, event_type, **fields):
        self.entries.append((event_type, fields))


class _FakeStore:
    def __init__(self, all_plans=None):
        self.saved = []
        # EA1-R1 step 6: defaults to empty - "no other plan exists on
        # disk" - matching every existing test's real intent (a single
        # isolated plan, nothing to compare staleness against). Tests
        # exercising the staleness gate itself pass all_plans explicitly.
        self._all_plans = all_plans if all_plans is not None else []

    def save(self, plan):
        self.saved.append(plan)

    def list_all_plans(self):
        return self._all_plans


class _FakeEngineStore:
    def __init__(self):
        self.saved = 0

    def save(self, state):
        self.saved += 1


def _ctx(status, entry_order_id=None):
    return SimpleNamespace(status=status, entry_order_id=entry_order_id)


def _engine(active_trades=None):
    return SimpleNamespace(
        state=SimpleNamespace(active_trades=active_trades or {}),
        audit=_FakeAudit(),
        store=_FakeEngineStore(),
    )


def _run(engine, plan, store, *, note="test note", assume_yes=True, input_fn=None):
    out, err = io.StringIO(), io.StringIO()
    rc = run_reconcile(
        engine, plan, store, note=note, assume_yes=assume_yes, out=out, err=err,
        input_fn=input_fn or (lambda _: "y"),
    )
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------

def test_not_halted_is_a_clean_noop():
    plan = _plan(status=RebalancePlanStatus.ENTERING, enter_status={"SBIN": LegStatus.PENDING})
    engine, store = _engine(), _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert "not HALTED" in out
    assert store.saved == []
    assert plan.status == RebalancePlanStatus.ENTERING  # untouched


def test_refuses_when_exit_legs_are_not_terminal():
    plan = _plan(
        status=RebalancePlanStatus.HALTED,
        enter_status={"SBIN": LegStatus.SUBMITTED},
        exit_status={"OLDPOS": LegStatus.SUBMITTED},
    )
    engine, store = _engine(), _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 1
    assert "non-terminal exit leg" in err
    assert store.saved == []
    assert plan.status == RebalancePlanStatus.HALTED  # untouched


def test_refuses_when_no_non_terminal_enter_legs():
    plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.DECLINED})
    engine, store = _engine(), _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 1
    assert "nothing to reconcile" in err
    assert store.saved == []


def test_refuses_when_active_position_still_resolving():
    plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.SUBMITTED})
    engine = _engine(active_trades={"SBIN": _ctx(PositionStatus.ENTRY_PENDING)})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 1
    assert "still" in err.lower() or "resolving" in err.lower()
    assert store.saved == []
    assert plan.status == RebalancePlanStatus.HALTED


def test_declines_interactive_prompt_leaves_plan_untouched():
    plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING})
    engine, store = _engine(), _FakeStore()
    rc, out, err = _run(engine, plan, store, assume_yes=False, input_fn=lambda _: "n")
    assert rc == 1
    assert "Not reconciled" in out
    assert store.saved == []
    assert plan.status == RebalancePlanStatus.HALTED


# ---------------------------------------------------------------------------
# Real reconciliation outcomes - the three cases found in the actual
# 2026-08-17 Terminal A/B plan files.
# ---------------------------------------------------------------------------

def test_vanished_submitted_leg_with_retry_available_is_retried():
    # Terminal A's exact shape: 4 legs SUBMITTED, engine now healthy with
    # empty active_trades (matches the real post-clearance bot_state.json),
    # zero retries used yet.
    plan = _plan(
        status=RebalancePlanStatus.HALTED,
        enter_status={"BAJFINANCE": LegStatus.SUBMITTED, "SUNPHARMA": LegStatus.SUBMITTED},
        enter_retry_count={"BAJFINANCE": 0, "SUNPHARMA": 0},
    )
    engine = _engine(active_trades={})  # both vanished - matches real post-clearance state
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.status == RebalancePlanStatus.ENTERING
    assert plan.enter_status["BAJFINANCE"] == LegStatus.PENDING
    assert plan.enter_status["SUNPHARMA"] == LegStatus.PENDING
    assert plan.enter_retry_count["BAJFINANCE"] == 1
    assert plan.enter_retry_count["SUNPHARMA"] == 1
    assert store.saved == [plan]
    assert engine.audit.entries[0][0] == "OPERATOR_RECONCILED_HALTED_PLAN"
    assert engine.audit.entries[0][1]["resolutions"] == {"BAJFINANCE": "RETRY", "SUNPHARMA": "RETRY"}
    assert engine.store.saved == 1


def test_vanished_submitted_leg_with_retry_already_spent_is_declined_and_can_finalize():
    plan = _plan(
        status=RebalancePlanStatus.HALTED,
        enter_status={"ADANIENT": LegStatus.SUBMITTED},
        enter_retry_count={"ADANIENT": 1},  # already used its one retry
    )
    engine = _engine(active_trades={})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.enter_status["ADANIENT"] == LegStatus.DECLINED
    # Only enter leg, now terminal -> plan should auto-finalize.
    assert plan.status == RebalancePlanStatus.PARTIAL


def test_confirmed_position_is_marked_confirmed():
    plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"INFY": LegStatus.SUBMITTED})
    engine = _engine(active_trades={"INFY": _ctx(PositionStatus.MANAGING)})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.enter_status["INFY"] == LegStatus.CONFIRMED
    assert plan.status == RebalancePlanStatus.COMPLETE  # only leg, terminal, and CONFIRMED


def test_pending_legs_are_left_untouched_but_plan_still_unlatches():
    # Terminal B's exact shape: two SUBMITTED (both vanished, shadow
    # declined), two still PENDING (never even attempted).
    plan = _plan(
        status=RebalancePlanStatus.HALTED,
        enter_status={
            "ADANIENT": LegStatus.SUBMITTED, "HINDALCO": LegStatus.SUBMITTED,
            "LAURUSLABS": LegStatus.PENDING, "SHRIRAMFIN": LegStatus.PENDING,
        },
        enter_retry_count={"ADANIENT": 0, "HINDALCO": 0, "LAURUSLABS": 0, "SHRIRAMFIN": 0},
    )
    engine = _engine(active_trades={})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.status == RebalancePlanStatus.ENTERING  # not finalized - PENDING legs remain
    assert plan.enter_status["ADANIENT"] == LegStatus.PENDING  # retried
    assert plan.enter_status["HINDALCO"] == LegStatus.PENDING  # retried
    assert plan.enter_status["LAURUSLABS"] == LegStatus.PENDING  # left alone
    assert plan.enter_status["SHRIRAMFIN"] == LegStatus.PENDING  # left alone
    assert plan.enter_retry_count["LAURUSLABS"] == 0  # never touched
    assert plan.enter_retry_count["SHRIRAMFIN"] == 0  # never touched


def test_mixed_confirmed_and_declined_finalizes_partial():
    plan = _plan(
        status=RebalancePlanStatus.HALTED,
        enter_status={"A": LegStatus.SUBMITTED, "B": LegStatus.SUBMITTED},
        enter_retry_count={"A": 1, "B": 1},
    )
    engine = _engine(active_trades={"A": _ctx(PositionStatus.MANAGING)})  # B vanished
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.enter_status["A"] == LegStatus.CONFIRMED
    assert plan.enter_status["B"] == LegStatus.DECLINED
    assert plan.status == RebalancePlanStatus.PARTIAL


def test_never_submitted_entry_submit_leg_is_left_as_is():
    # The real case found live 2026-08-19: engine.step() aborted on a
    # DIFFERENT symbol's hard halt before ever reaching this one - it sits
    # at the pre-side-effect ENTRY_SUBMIT status with no order_id at all.
    plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"SUNPHARMA": LegStatus.SUBMITTED},
                 enter_retry_count={"SUNPHARMA": 1})  # already used its retry in an earlier round
    engine = _engine(active_trades={"SUNPHARMA": _ctx(PositionStatus.ENTRY_SUBMIT, entry_order_id=None)})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.status == RebalancePlanStatus.ENTERING  # not finalized - still SUBMITTED, not terminal
    assert plan.enter_status["SUNPHARMA"] == LegStatus.SUBMITTED  # untouched
    assert plan.enter_retry_count["SUNPHARMA"] == 1  # untouched - no retry consumed
    assert engine.audit.entries[0][1]["resolutions"] == {"SUNPHARMA": "LEAVE_SUBMITTED"}


def test_entry_submit_leg_with_an_order_id_is_still_refused():
    # Defensive: the safe case requires BOTH ENTRY_SUBMIT status AND a
    # None order_id - if an order_id is somehow present, this is NOT the
    # provably-never-submitted case, and must still be refused.
    plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"SUNPHARMA": LegStatus.SUBMITTED})
    engine = _engine(active_trades={"SUNPHARMA": _ctx(PositionStatus.ENTRY_SUBMIT, entry_order_id="ORD123")})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 1
    assert "still" in err.lower() or "resolving" in err.lower()
    assert store.saved == []
    assert plan.status == RebalancePlanStatus.HALTED


def test_real_terminal_a_second_round_shape_end_to_end():
    """Exact reproduction of what was found live: after the SBIN
    engine-level hard halt was resolved (v34_bridge_resolve_entry_submit_
    halt.py), BAJFINANCE/LAURUSLABS/SBIN had all already used their one
    retry from round 1 and vanished from active_trades (shadow-declined
    before the halt interrupted the cycle), while SUNPHARMA never got
    reached at all this round."""
    plan = _plan(
        target_id="V11_2026-08-17_10440cc4f4044c2f",
        status=RebalancePlanStatus.HALTED,
        enter_status={
            "BAJFINANCE": LegStatus.SUBMITTED, "LAURUSLABS": LegStatus.SUBMITTED,
            "SBIN": LegStatus.SUBMITTED, "SUNPHARMA": LegStatus.SUBMITTED,
        },
        enter_retry_count={"BAJFINANCE": 1, "LAURUSLABS": 1, "SBIN": 1, "SUNPHARMA": 1},
        enters={"BAJFINANCE": 21, "LAURUSLABS": 13, "SBIN": 24, "SUNPHARMA": 12},
    )
    engine = _engine(active_trades={"SUNPHARMA": _ctx(PositionStatus.ENTRY_SUBMIT, entry_order_id=None)})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.enter_status["BAJFINANCE"] == LegStatus.DECLINED
    assert plan.enter_status["LAURUSLABS"] == LegStatus.DECLINED
    assert plan.enter_status["SBIN"] == LegStatus.DECLINED
    assert plan.enter_status["SUNPHARMA"] == LegStatus.SUBMITTED  # left as-is
    assert plan.status == RebalancePlanStatus.ENTERING  # SUNPHARMA still non-terminal


def test_real_terminal_a_shape_end_to_end():
    """Exact reproduction of the real V11_2026-08-17_10440cc4f4044c2f.json
    on-disk shape (4 legs, all SUBMITTED, all retries unused, engine now
    healthy with empty active_trades) - proves the tool resolves the
    actual incident, not just a simplified synthetic case."""
    plan = _plan(
        target_id="V11_2026-08-17_10440cc4f4044c2f",
        status=RebalancePlanStatus.HALTED,
        enter_status={
            "BAJFINANCE": LegStatus.SUBMITTED, "LAURUSLABS": LegStatus.SUBMITTED,
            "SBIN": LegStatus.SUBMITTED, "SUNPHARMA": LegStatus.SUBMITTED,
        },
        enter_retry_count={"BAJFINANCE": 0, "LAURUSLABS": 0, "SBIN": 0, "SUNPHARMA": 0},
        enters={"BAJFINANCE": 21, "LAURUSLABS": 13, "SBIN": 24, "SUNPHARMA": 12},
    )
    engine = _engine(active_trades={})
    store = _FakeStore()
    rc, out, err = _run(engine, plan, store)
    assert rc == 0
    assert plan.status == RebalancePlanStatus.ENTERING
    for symbol in ("BAJFINANCE", "LAURUSLABS", "SBIN", "SUNPHARMA"):
        assert plan.enter_status[symbol] == LegStatus.PENDING
        assert plan.enter_retry_count[symbol] == 1
    assert "Next step" in out and "v34_bridge_runner_main.py" in out


# ---------------------------------------------------------------------------
# EA1-R1 step 6: staleness gate (REARM/ABANDON). Owner-authorized rule,
# 2026-08-19: stale only once a NEWER monthly rebalance already exists
# on disk for this terminal - not R0 §7's literal same-day formula.
# ---------------------------------------------------------------------------

from datetime import date as _date


class TestStalenessGate:
    def test_a_superseded_plan_is_abandoned_not_resumed(self, tmp_path):
        plan = _plan(
            status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING},
            target_id="V11_2026-08-17_OLD", signal_date=_date(2026, 8, 17),
        )
        newer_plan = _plan(
            status=RebalancePlanStatus.PARTIAL, enter_status={"RELIANCE": LegStatus.CONFIRMED},
            target_id="V11_2026-09-15_NEW", signal_date=_date(2026, 9, 15),
        )
        engine, store = _engine(), _FakeStore(all_plans=[plan, newer_plan])
        rc, out, err = _run(engine, plan, store)
        assert rc == 0
        assert "SUPERSEDED" in out
        assert "V11_2026-09-15_NEW" in out
        # Never touched - stays HALTED, no leg mutation, no engine calls.
        assert plan.status == RebalancePlanStatus.HALTED
        assert plan.enter_status["SBIN"] == LegStatus.PENDING
        assert store.saved == []

    def test_abandon_is_recorded_in_the_sidecar_with_a_real_reason(self, tmp_path):
        plan = _plan(
            status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING},
            target_id="V11_2026-08-17_OLD", signal_date=_date(2026, 8, 17),
        )
        newer_plan = _plan(
            status=RebalancePlanStatus.PARTIAL, enter_status={"RELIANCE": LegStatus.CONFIRMED},
            target_id="V11_2026-09-15_NEW", signal_date=_date(2026, 9, 15),
        )
        engine, store = _engine(), _FakeStore(all_plans=[plan, newer_plan])

        class _RecordingSidecar:
            def __init__(self):
                self.calls = []

            def log_abandoned(self, target_id, *, reason, **extra):
                self.calls.append((target_id, reason, extra))

        sidecar = _RecordingSidecar()
        out, err = io.StringIO(), io.StringIO()
        rc = run_reconcile(engine, plan, store, note="stale, moving on", assume_yes=True, out=out, err=err, sidecar=sidecar)
        assert rc == 0
        assert len(sidecar.calls) == 1
        target_id, reason, extra = sidecar.calls[0]
        assert target_id == "V11_2026-08-17_OLD"
        assert "V11_2026-09-15_NEW" in reason

    def test_abandon_declines_without_confirmation(self, tmp_path):
        plan = _plan(
            status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING},
            target_id="V11_2026-08-17_OLD", signal_date=_date(2026, 8, 17),
        )
        newer_plan = _plan(
            status=RebalancePlanStatus.PARTIAL, enter_status={"RELIANCE": LegStatus.CONFIRMED},
            target_id="V11_2026-09-15_NEW", signal_date=_date(2026, 9, 15),
        )
        engine, store = _engine(), _FakeStore(all_plans=[plan, newer_plan])
        rc, out, err = _run(engine, plan, store, assume_yes=False, input_fn=lambda _: "n")
        assert rc == 1
        assert "Not abandoned" in out
        assert plan.status == RebalancePlanStatus.HALTED

    def test_a_not_yet_superseded_plan_proceeds_to_normal_reconciliation(self, tmp_path):
        """No newer plan on disk at all - the real 2026-08-19 case for
        both real terminals - must reach the ordinary per-leg resolution
        path completely unaffected by this gate."""
        plan = _plan(status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING})
        engine, store = _engine(), _FakeStore(all_plans=[plan])  # only itself on disk
        rc, out, err = _run(engine, plan, store)
        assert rc == 0
        assert "SUPERSEDED" not in out
        assert plan.status == RebalancePlanStatus.ENTERING  # ordinary REARM path still works

    def test_an_older_plan_on_disk_does_not_trigger_supersession(self, tmp_path):
        """Only a STRICTLY NEWER signal_date counts - an older, already-
        finished plan sitting in the same directory must never be
        mistaken for a superseding one."""
        plan = _plan(
            status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING},
            target_id="V11_2026-08-17_CURRENT", signal_date=_date(2026, 8, 17),
        )
        older_plan = _plan(
            status=RebalancePlanStatus.PARTIAL, enter_status={"RELIANCE": LegStatus.CONFIRMED},
            target_id="V11_2026-07-15_OLDER", signal_date=_date(2026, 7, 15),
        )
        engine, store = _engine(), _FakeStore(all_plans=[plan, older_plan])
        rc, out, err = _run(engine, plan, store)
        assert rc == 0
        assert "SUPERSEDED" not in out
        assert plan.status == RebalancePlanStatus.ENTERING

    def test_real_store_integration_detects_a_genuinely_newer_plan_on_disk(self, tmp_path):
        """The one test in this class using the REAL RebalancePlanStore
        (not the fake) - proves list_all_plans() itself, wired through
        run_reconcile(), actually finds a newer plan file written to the
        real filesystem, not just a hand-fed fake list."""
        from v34_bridge_rebalance_plan import RebalancePlanStore

        real_store = RebalancePlanStore(tmp_path)
        plan = _plan(
            status=RebalancePlanStatus.HALTED, enter_status={"SBIN": LegStatus.PENDING},
            target_id="V11_2026-08-17_REALOLD", signal_date=_date(2026, 8, 17),
        )
        newer_plan = _plan(
            status=RebalancePlanStatus.PARTIAL, enter_status={"RELIANCE": LegStatus.CONFIRMED},
            target_id="V11_2026-09-15_REALNEW", signal_date=_date(2026, 9, 15),
        )
        real_store.save(plan)
        real_store.save(newer_plan)

        engine = _engine()
        rc, out, err = _run(engine, plan, real_store)
        assert rc == 0
        assert "SUPERSEDED" in out
        assert "V11_2026-09-15_REALNEW" in out
        # Confirm the plan file on disk was genuinely never touched.
        reloaded = real_store.load("V11_2026-08-17_REALOLD")
        assert reloaded.status == RebalancePlanStatus.HALTED
