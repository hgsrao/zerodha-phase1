"""Tests for v34_bridge_rebalance_plan.py.

Covers the actual crash-recovery question: RebalancePlanStore uses a real
tmp_path directory (real files, real fsync+replace), and the idempotency
tests simulate "container crashes mid-rebalance, restarts, reads the same
plan back" using a fresh RebalancePlanStore instance pointed at the same
directory - proving persistence survives a process restart, not just an
in-memory object's lifetime.
"""

from datetime import date, datetime, timezone

import pytest

from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import (
    LegStatus,
    RebalancePlan,
    RebalancePlanStateError,
    RebalancePlanStatus,
    RebalancePlanStore,
    StaleTargetError,
    create_rebalance_plan,
    is_target_stale,
    resolve_or_create_plan,
)
from v34_bridge_target_portfolio import build_target_portfolio

# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - see that file's docstring for why (50-symbol universe).
YESTERDAYS_REAL_QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
}


@pytest.fixture(scope="module")
def real_target():
    return build_target_portfolio(quotes=YESTERDAYS_REAL_QUOTES, signal_date=date(2026, 8, 14))


@pytest.fixture()
def real_diff(real_target):
    current = {"RELIANCE": 50, "LAURUSLABS": 10, "HINDALCO": 25}
    return compute_rebalance_diff(current_portfolio=current, target=real_target)


class TestIsTargetStale:
    def test_same_day_is_not_stale(self, real_target):
        assert is_target_stale(real_target.signal_date, current_trading_day=date(2026, 8, 14)) is False

    def test_next_day_is_stale(self, real_target):
        assert is_target_stale(real_target.signal_date, current_trading_day=date(2026, 8, 15)) is True


class TestCreateRebalancePlan:
    def test_happy_path_creates_created_status_with_every_leg_pending(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert plan.status == RebalancePlanStatus.CREATED
        assert plan.target_id == real_target.target_id
        assert set(plan.exit_status) == set(real_diff.exits)
        assert set(plan.enter_status) == set(real_diff.enters)
        assert all(status == LegStatus.PENDING for status in plan.exit_status.values())
        assert all(status == LegStatus.PENDING for status in plan.enter_status.values())
        assert plan.is_complete() is False  # legs are PENDING, not terminal

    def test_stale_target_is_refused_not_created(self, real_diff, real_target):
        with pytest.raises(StaleTargetError, match="Refusing to create"):
            create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 15))

    def test_target_id_mismatch_between_diff_and_target_is_refused(self, real_diff):
        from v34_bridge_target_portfolio import build_target_portfolio
        different_target = build_target_portfolio(
            quotes=YESTERDAYS_REAL_QUOTES, signal_date=date(2026, 8, 13),  # different date -> different target_id
        )
        with pytest.raises(RebalancePlanStateError, match="does not match"):
            create_rebalance_plan(real_diff, target=different_target, current_trading_day=date(2026, 8, 13))


class TestEnterRetryCountSchema:
    """R0 §6's retry count: durable control state, added as a deliberate
    schema extension once the retry policy was actually implemented (see
    v34_bridge_rebalance_transitions.py's retry_enter_leg())."""

    def test_create_rebalance_plan_initializes_every_enter_leg_to_zero(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert set(plan.enter_retry_count) == set(real_diff.enters)
        assert all(count == 0 for count in plan.enter_retry_count.values())

    def test_a_symbol_not_an_enter_leg_is_structurally_rejected(self, real_diff):
        with pytest.raises(RebalancePlanStateError, match="not enter legs"):
            RebalancePlan(
                target_id=real_diff.target_id, diff=real_diff, status=RebalancePlanStatus.CREATED,
                exit_status={symbol: LegStatus.PENDING for symbol in real_diff.exits},
                enter_status={symbol: LegStatus.PENDING for symbol in real_diff.enters},
                enter_retry_count={"NOTANENTERLEG": 0}, signal_date=date(2026, 8, 14),
                created_at=datetime.now(timezone.utc), last_broker_check_at=datetime.now(timezone.utc),
            )

    def test_a_negative_retry_count_is_structurally_rejected(self, real_diff):
        symbol = next(iter(real_diff.enters))
        with pytest.raises(RebalancePlanStateError, match="negative value"):
            RebalancePlan(
                target_id=real_diff.target_id, diff=real_diff, status=RebalancePlanStatus.CREATED,
                exit_status={s: LegStatus.PENDING for s in real_diff.exits},
                enter_status={s: LegStatus.PENDING for s in real_diff.enters},
                enter_retry_count={symbol: -1}, signal_date=date(2026, 8, 14),
                created_at=datetime.now(timezone.utc), last_broker_check_at=datetime.now(timezone.utc),
            )

    def test_serialization_round_trip_preserves_retry_counts(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        symbol = next(iter(real_diff.enters))
        plan.enter_retry_count[symbol] = 1
        restored = RebalancePlan.from_dict(plan.to_dict())
        assert restored.enter_retry_count[symbol] == 1
        assert restored.enter_retry_count == plan.enter_retry_count

    def test_missing_enter_retry_count_field_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        del raw["enter_retry_count"]
        with pytest.raises(RebalancePlanStateError, match="missing required field"):
            RebalancePlan.from_dict(raw)

    def test_non_integer_retry_count_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        symbol = next(iter(real_diff.enters))
        raw["enter_retry_count"][symbol] = "one"
        with pytest.raises(RebalancePlanStateError, match="expected an integer"):
            RebalancePlan.from_dict(raw)


class TestApprovalGateSchema:
    """signal_date and approved_at: added together for the human-in-the-
    loop approval gate (v34_bridge_rebalance_transitions.approve_plan()).
    signal_date closes a real gap - re-checking R0 §7 staleness at
    approval time needs the signal date after the originating
    TargetPortfolio object is long gone; approved_at is the durable
    record that a human actually did it."""

    def test_create_rebalance_plan_populates_signal_date_and_leaves_approved_at_none(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert plan.signal_date == real_target.signal_date
        assert plan.approved_at is None

    def test_a_status_past_created_without_approved_at_is_structurally_rejected(self, real_diff):
        with pytest.raises(RebalancePlanStateError, match="approved_at is unset"):
            RebalancePlan(
                target_id=real_diff.target_id, diff=real_diff, status=RebalancePlanStatus.APPROVED,
                exit_status={s: LegStatus.PENDING for s in real_diff.exits},
                enter_status={s: LegStatus.PENDING for s in real_diff.enters},
                enter_retry_count={s: 0 for s in real_diff.enters}, signal_date=date(2026, 8, 14),
                approved_at=None,
                created_at=datetime.now(timezone.utc), last_broker_check_at=datetime.now(timezone.utc),
            )

    def test_created_with_approved_at_already_set_is_structurally_rejected(self, real_diff):
        # The reverse forgery direction - a persisted file claiming to
        # still be CREATED but already carrying an approval timestamp
        # (e.g. a hand-edited or corrupted plan file) must also be
        # refused: approve_plan() sets status and approved_at together,
        # so this combination can never arise through the real API.
        with pytest.raises(RebalancePlanStateError, match="CREATED but approved_at is"):
            RebalancePlan(
                target_id=real_diff.target_id, diff=real_diff, status=RebalancePlanStatus.CREATED,
                exit_status={s: LegStatus.PENDING for s in real_diff.exits},
                enter_status={s: LegStatus.PENDING for s in real_diff.enters},
                enter_retry_count={s: 0 for s in real_diff.enters}, signal_date=date(2026, 8, 14),
                approved_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc), last_broker_check_at=datetime.now(timezone.utc),
            )

    def test_halted_without_approved_at_is_allowed(self, real_diff):
        # HALTED has no precondition (R0 §4: "any state -> HALTED") and is
        # reachable even from a plan that was never approved - it must be
        # exempt from the "requires approval" invariant, unlike every
        # other status past CREATED.
        plan = RebalancePlan(
            target_id=real_diff.target_id, diff=real_diff, status=RebalancePlanStatus.HALTED,
            exit_status={s: LegStatus.PENDING for s in real_diff.exits},
            enter_status={s: LegStatus.PENDING for s in real_diff.enters},
            enter_retry_count={s: 0 for s in real_diff.enters}, signal_date=date(2026, 8, 14),
            approved_at=None,
            created_at=datetime.now(timezone.utc), last_broker_check_at=datetime.now(timezone.utc),
        )
        assert plan.status == RebalancePlanStatus.HALTED
        assert plan.approved_at is None

    def test_missing_signal_date_field_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        del raw["signal_date"]
        with pytest.raises(RebalancePlanStateError, match="missing required field"):
            RebalancePlan.from_dict(raw)

    def test_missing_approved_at_field_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        del raw["approved_at"]
        with pytest.raises(RebalancePlanStateError, match="missing required field"):
            RebalancePlan.from_dict(raw)

    def test_malformed_signal_date_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        raw["signal_date"] = "not-a-date"
        with pytest.raises(RebalancePlanStateError, match="malformed date"):
            RebalancePlan.from_dict(raw)


class TestIsComplete:
    def test_all_terminal_legs_is_complete(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        for symbol in plan.exit_status:
            plan.exit_status[symbol] = LegStatus.CONFIRMED
        for symbol in plan.enter_status:
            plan.enter_status[symbol] = LegStatus.DECLINED
        assert plan.is_complete() is True

    def test_one_pending_leg_is_not_complete(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        for symbol in plan.exit_status:
            plan.exit_status[symbol] = LegStatus.CONFIRMED
        # enter_status left PENDING
        assert plan.is_complete() is False


class TestDeclinedExitInvariant:
    def test_a_declined_exit_leg_is_structurally_rejected(self, real_diff):
        with pytest.raises(RebalancePlanStateError, match="structurally impossible"):
            RebalancePlan(
                target_id=real_diff.target_id, diff=real_diff, status=RebalancePlanStatus.EXITING,
                exit_status={symbol: LegStatus.DECLINED for symbol in real_diff.exits},
                enter_status={symbol: LegStatus.PENDING for symbol in real_diff.enters},
                enter_retry_count={symbol: 0 for symbol in real_diff.enters}, signal_date=date(2026, 8, 14),
                created_at=datetime.now(timezone.utc), last_broker_check_at=datetime.now(timezone.utc),
            )


class TestSerializationRoundTrip:
    def test_to_dict_from_dict_round_trip_preserves_everything(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        plan.status = RebalancePlanStatus.EXITING
        plan.approved_at = datetime.now(timezone.utc)
        plan.exit_status["RELIANCE"] = LegStatus.SUBMITTED
        restored = RebalancePlan.from_dict(plan.to_dict())
        assert restored.target_id == plan.target_id
        assert restored.status == RebalancePlanStatus.EXITING
        assert restored.exit_status["RELIANCE"] == LegStatus.SUBMITTED
        assert restored.signal_date == plan.signal_date
        assert restored.approved_at == plan.approved_at
        assert restored.diff.target_id == plan.diff.target_id
        assert dict(restored.diff.exits) == dict(plan.diff.exits)
        assert dict(restored.diff.enters) == dict(plan.diff.enters)

    def test_missing_required_field_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        del raw["status"]
        with pytest.raises(RebalancePlanStateError, match="missing required field"):
            RebalancePlan.from_dict(raw)

    def test_invalid_status_value_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        raw["status"] = "MADE_UP_STATUS"
        with pytest.raises(RebalancePlanStateError, match="invalid value"):
            RebalancePlan.from_dict(raw)

    def test_invalid_leg_status_value_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        raw["exit_status"]["RELIANCE"] = "NOT_A_REAL_STATUS"
        with pytest.raises(RebalancePlanStateError, match="invalid leg status"):
            RebalancePlan.from_dict(raw)

    def test_target_id_disagreement_between_plan_and_diff_fails_closed(self, real_diff, real_target):
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        raw = plan.to_dict()
        raw["target_id"] = "TAMPERED_ID"
        with pytest.raises(RebalancePlanStateError, match="disagrees with"):
            RebalancePlan.from_dict(raw)


class TestRebalancePlanStoreRealFilePersistence:
    def test_save_then_load_round_trips_through_a_real_file(self, tmp_path, real_diff, real_target):
        store = RebalancePlanStore(tmp_path)
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        store.save(plan)

        # A real file must actually exist on disk.
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

        loaded = store.load(plan.target_id)
        assert loaded is not None
        assert loaded.target_id == plan.target_id
        assert loaded.status == plan.status

    def test_load_of_a_never_saved_target_id_returns_none(self, tmp_path):
        store = RebalancePlanStore(tmp_path)
        assert store.load("V11_2099-01-01_deadbeefdeadbeef") is None

    def test_crash_and_restart_a_fresh_store_instance_sees_the_same_plan(self, tmp_path, real_diff, real_target):
        # Simulates the actual scenario the user asked about: the process
        # (container) dies, a brand new process starts, constructs a fresh
        # RebalancePlanStore pointed at the same directory, and must see
        # exactly what was durably saved before the crash - not an
        # in-memory object surviving, a real file surviving.
        store_before_crash = RebalancePlanStore(tmp_path)
        plan = create_rebalance_plan(real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        plan.status = RebalancePlanStatus.EXITING
        plan.approved_at = datetime.now(timezone.utc)
        plan.exit_status["RELIANCE"] = LegStatus.SUBMITTED
        store_before_crash.save(plan)
        del store_before_crash  # simulate the process/container dying

        store_after_restart = RebalancePlanStore(tmp_path)
        recovered = store_after_restart.load(plan.target_id)
        assert recovered is not None
        assert recovered.status == RebalancePlanStatus.EXITING
        assert recovered.exit_status["RELIANCE"] == LegStatus.SUBMITTED
        # A leg that was never touched is still exactly PENDING, not lost.
        assert recovered.exit_status["LAURUSLABS"] == LegStatus.PENDING


class TestResolveOrCreatePlanIdempotency:
    def test_no_existing_plan_creates_and_persists_one(self, tmp_path, real_diff, real_target):
        store = RebalancePlanStore(tmp_path)
        plan, outcome = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert outcome == "CREATED"
        assert store.load(real_diff.target_id) is not None

    def test_complete_existing_plan_is_a_noop_never_resubmitted(self, tmp_path, real_diff, real_target):
        store = RebalancePlanStore(tmp_path)
        plan, _ = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        for symbol in plan.exit_status:
            plan.exit_status[symbol] = LegStatus.CONFIRMED
        for symbol in plan.enter_status:
            plan.enter_status[symbol] = LegStatus.CONFIRMED
        plan.status = RebalancePlanStatus.COMPLETE
        plan.approved_at = datetime.now(timezone.utc)
        store.save(plan)

        # The exact same target presented again - e.g. a duplicate trigger,
        # or a naive restart that doesn't check state first.
        again, outcome = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert outcome == "NOOP_ALREADY_TERMINAL"
        assert again.status == RebalancePlanStatus.COMPLETE

    def test_halted_existing_plan_is_refused_not_auto_resumed(self, tmp_path, real_diff, real_target):
        store = RebalancePlanStore(tmp_path)
        plan, _ = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        plan.status = RebalancePlanStatus.HALTED
        store.save(plan)

        again, outcome = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert outcome == "REFUSED_HALTED"
        assert again.status == RebalancePlanStatus.HALTED

    def test_mid_flight_existing_plan_resumes_not_recreates(self, tmp_path, real_diff, real_target):
        store = RebalancePlanStore(tmp_path)
        plan, _ = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        plan.status = RebalancePlanStatus.EXITING
        plan.approved_at = datetime.now(timezone.utc)
        plan.exit_status["RELIANCE"] = LegStatus.CONFIRMED
        store.save(plan)

        resumed, outcome = resolve_or_create_plan(store, real_diff, target=real_target, current_trading_day=date(2026, 8, 14))
        assert outcome == "RESUMED"
        assert resumed.status == RebalancePlanStatus.EXITING
        # The already-confirmed leg's progress is preserved, not reset -
        # this is exactly what prevents a restart from re-submitting it.
        assert resumed.exit_status["RELIANCE"] == LegStatus.CONFIRMED
