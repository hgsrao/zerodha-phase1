"""V11 -> P02 Rebalance Bridge — `RebalancePlan` durable state (R0 §4, §7, §10).

This, not `RebalanceDiff`, is the thing that must survive a crash. A
`RebalanceDiff` is a pure recomputation - free to redo from a fresh
broker snapshot any time. `RebalancePlan` tracks "what have I already
told P02 to do, and how far did it get" - the one piece of information
that genuinely cannot be safely rederived, because re-submitting a leg
already `CONFIRMED` at the broker would be a real duplicate action.

Per R0 §2: the bridge never persists `CurrentPortfolio` as an ongoing
source of truth - `diff.computed_from_current` is retained only as the
snapshot a *specific* diff was computed against (for later staleness
comparison, R0 §8/§12), never re-trusted as current fact on restart. A
fresh broker snapshot is always the one that matters.

No broker/network calls. No P02 import. No V11 import beyond the R1
`TargetPortfolio`/Step-A `RebalanceDiff` types this builds on. Durable
persistence uses the same temp-file + fsync + os.replace pattern
documented throughout this project's engine/runner state stores.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from v34_bridge_rebalance_diff import RebalanceDiff

if TYPE_CHECKING:
    # Deferred for the same reason as v34_bridge_rebalance_diff.py's own
    # TargetPortfolio import: only used in two function type hints
    # (is_target_stale, create_rebalance_plan) that the always-on runner
    # container never calls - those are the bridge trigger script's job.
    # Evaluated as a string at runtime regardless, per the __future__
    # import above; importing this at module level would exist only to
    # pull TargetPortfolio's full research/backtest dependency chain into
    # a container that never needs it.
    from v34_bridge_target_portfolio import TargetPortfolio


class RebalancePlanStateError(RuntimeError):
    """Persisted RebalancePlan state is missing, malformed, or violates an
    invariant (e.g. an exit leg marked DECLINED, which is structurally
    impossible per R0 §2/§5 - exits are never gated). Fail closed."""


class StaleTargetError(RuntimeError):
    """Refused to create a RebalancePlan from a TargetPortfolio that is no
    longer valid for the current trading day (R0 §7)."""


class LegStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"   # ENTER legs only - exits are never gated (R0 §2/§5)
    FAILED = "FAILED"


TERMINAL_LEG_STATUSES = {LegStatus.CONFIRMED, LegStatus.DECLINED, LegStatus.FAILED}


class RebalancePlanStatus(str, Enum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    EXITING = "EXITING"
    EXITS_COMPLETE = "EXITS_COMPLETE"
    ENTERING = "ENTERING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    HALTED = "HALTED"


TERMINAL_PLAN_STATUSES = {RebalancePlanStatus.COMPLETE, RebalancePlanStatus.PARTIAL}

# Distinct from TERMINAL_PLAN_STATUSES above on purpose: that set drives
# resolve_or_create_plan()'s create-vs-resume idempotency decision (R0
# §10), where HALTED is deliberately NOT terminal - it requires operator
# reconciliation, never a silent no-op create/resume. This set answers a
# different question - the runner's own "does this plan need another
# drive_plan_one_cycle() call" decision - where HALTED, like COMPLETE/
# PARTIAL, needs no more automatic cycles (drive_plan_one_cycle() already
# no-ops on all three; see v34_bridge_runner_core.py). Single source of
# truth for that set, shared by RebalancePlanStore.list_active_plans()
# below and v34_bridge_runner_core.py, so the two can never drift apart.
NO_FURTHER_ACTION_PLAN_STATUSES = TERMINAL_PLAN_STATUSES | {RebalancePlanStatus.HALTED}

# Every status only reachable by having passed through approve_plan()
# first (v34_bridge_rebalance_transitions.py) - used by RebalancePlan's
# own __post_init__ to fail closed on a persisted plan claiming to be
# past CREATED without ever having been approved. HALTED is deliberately
# excluded: it has no precondition (R0 §4 - "any state -> HALTED") and is
# reachable even from a plan that was never approved.
_REQUIRES_APPROVAL_STATUSES = {
    RebalancePlanStatus.APPROVED, RebalancePlanStatus.EXITING, RebalancePlanStatus.EXITS_COMPLETE,
    RebalancePlanStatus.ENTERING, RebalancePlanStatus.COMPLETE, RebalancePlanStatus.PARTIAL,
}


@dataclass
class RebalancePlan:
    """Mutable, durable state - unlike TargetPortfolio/RebalanceDiff (frozen
    value objects), a RebalancePlan genuinely evolves as legs are submitted
    and resolved. Persisted before any P02 call is made (store-first, same
    discipline P02 itself uses for TradeContext/request_entry)."""
    target_id: str
    diff: RebalanceDiff
    status: RebalancePlanStatus
    exit_status: Dict[str, LegStatus]
    enter_status: Dict[str, LegStatus]
    created_at: datetime
    last_broker_check_at: datetime
    # R0 §6's bounded-retry-once policy needs durable control state, not
    # telemetry: unlike a decline reason (redundant with P02's own audit
    # log, safe to lose), the retry count is the one thing standing between
    # "retry once, then terminally decline" and a runner that forgets a
    # retry already happened across a crash and issues a second one -
    # exactly the state-drift class this project defends against
    # everywhere else. Lives here, not in a sidecar store, so it is written
    # atomically with the leg status it governs (same temp+fsync+replace
    # call) and can never desync from it. Added deliberately, as a versioned
    # schema extension of R0 §4 - not a silent patch - once §6's retry
    # policy was actually implemented and this gap was found.
    enter_retry_count: Dict[str, int]
    # signal_date is what target_id's own hash was computed over
    # (v34_bridge_target_portfolio._compute_target_id) but was never
    # itself persisted on the plan - a real gap, found while designing
    # the approval gate below: re-checking R0 §7 staleness at approval
    # time (not just at creation) requires knowing what signal date this
    # plan is FOR after the originating TargetPortfolio object itself has
    # long been discarded. Required, no default - always known at
    # creation (create_rebalance_plan() populates it from
    # target.signal_date), same as target_id itself.
    signal_date: date
    # Populated only by approve_plan() (v34_bridge_rebalance_transitions.py),
    # atomically with the CREATED -> APPROVED transition - the
    # human-in-the-loop gate (this session's Phase-1 default: gated, not
    # fully automatic). None here means "no human has approved this yet";
    # begin_exiting() requires APPROVED, so a None approved_at is
    # structurally equivalent to "no exit has ever been submitted."
    approved_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        declined_exits = [s for s, status in self.exit_status.items() if status == LegStatus.DECLINED]
        if declined_exits:
            raise RebalancePlanStateError(
                f"RebalancePlan {self.target_id}: exit leg(s) {declined_exits} marked DECLINED - "
                "structurally impossible, exits are never gated by P02 (R0 §2/§5)."
            )
        unknown_retry_symbols = set(self.enter_retry_count) - set(self.enter_status)
        if unknown_retry_symbols:
            raise RebalancePlanStateError(
                f"RebalancePlan {self.target_id}: enter_retry_count has symbol(s) "
                f"{sorted(unknown_retry_symbols)} that are not enter legs of this plan."
            )
        if self.status in _REQUIRES_APPROVAL_STATUSES and self.approved_at is None:
            raise RebalancePlanStateError(
                f"RebalancePlan {self.target_id}: status is {self.status.value} but "
                "approved_at is unset - every status past CREATED (other than HALTED, "
                "reachable from any state per R0 §4) requires having passed through "
                "approve_plan() first."
            )
        if self.status == RebalancePlanStatus.CREATED and self.approved_at is not None:
            raise RebalancePlanStateError(
                f"RebalancePlan {self.target_id}: status is CREATED but approved_at is "
                f"set ({self.approved_at.isoformat()}) - the two are set atomically by "
                "approve_plan(); CREATED must never carry an approval timestamp."
            )
        negative_counts = {s: c for s, c in self.enter_retry_count.items() if c < 0}
        if negative_counts:
            raise RebalancePlanStateError(
                f"RebalancePlan {self.target_id}: enter_retry_count has negative value(s): {negative_counts}."
            )

    def is_complete(self) -> bool:
        """R0 §9's rebalance-complete definition, restricted to the
        no-legs-outstanding half (the broker-reality-matches-achievable-
        target half is the runner's job to check, not this plan's own
        state - this plan only knows what it submitted, not what the
        broker currently shows)."""
        all_legs = list(self.exit_status.values()) + list(self.enter_status.values())
        return all(status in TERMINAL_LEG_STATUSES for status in all_legs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "diff": {
                "target_id": self.diff.target_id,
                "computed_from_current": dict(self.diff.computed_from_current),
                "keep": sorted(self.diff.keep),
                "exits": dict(self.diff.exits),
                "enters": dict(self.diff.enters),
            },
            "status": self.status.value,
            "exit_status": {symbol: status.value for symbol, status in self.exit_status.items()},
            "enter_status": {symbol: status.value for symbol, status in self.enter_status.items()},
            "enter_retry_count": dict(self.enter_retry_count),
            "signal_date": self.signal_date.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at is not None else None,
            "created_at": self.created_at.isoformat(),
            "last_broker_check_at": self.last_broker_check_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RebalancePlan":
        if not isinstance(raw, dict):
            raise RebalancePlanStateError("RebalancePlan: expected an object.")
        required = {
            "target_id", "diff", "status", "exit_status", "enter_status",
            "enter_retry_count", "signal_date", "approved_at",
            "created_at", "last_broker_check_at",
        }
        missing = required.difference(raw)
        if missing:
            raise RebalancePlanStateError(f"RebalancePlan: missing required field(s) {sorted(missing)}.")

        diff_raw = raw["diff"]
        if not isinstance(diff_raw, dict):
            raise RebalancePlanStateError("RebalancePlan.diff: expected an object.")
        diff_required = {"target_id", "computed_from_current", "keep", "exits", "enters"}
        diff_missing = diff_required.difference(diff_raw)
        if diff_missing:
            raise RebalancePlanStateError(f"RebalancePlan.diff: missing required field(s) {sorted(diff_missing)}.")
        try:
            diff = RebalanceDiff(
                target_id=diff_raw["target_id"],
                computed_from_current=dict(diff_raw["computed_from_current"]),
                keep=frozenset(diff_raw["keep"]),
                exits=dict(diff_raw["exits"]),
                enters=dict(diff_raw["enters"]),
            )
        except (TypeError, ValueError) as exc:
            raise RebalancePlanStateError(f"RebalancePlan.diff: malformed field values: {exc}") from exc

        if raw["target_id"] != diff.target_id:
            raise RebalancePlanStateError(
                f"RebalancePlan.target_id ({raw['target_id']!r}) disagrees with diff.target_id ({diff.target_id!r})."
            )

        try:
            status = RebalancePlanStatus(raw["status"])
        except ValueError as exc:
            raise RebalancePlanStateError(f"RebalancePlan.status: invalid value {raw['status']!r}.") from exc

        def _parse_leg_statuses(field_name: str, value: Any) -> Dict[str, LegStatus]:
            if not isinstance(value, dict):
                raise RebalancePlanStateError(f"RebalancePlan.{field_name}: expected an object.")
            parsed: Dict[str, LegStatus] = {}
            for symbol, status_value in value.items():
                try:
                    parsed[symbol] = LegStatus(status_value)
                except ValueError as exc:
                    raise RebalancePlanStateError(
                        f"RebalancePlan.{field_name}[{symbol!r}]: invalid leg status {status_value!r}."
                    ) from exc
            return parsed

        exit_status = _parse_leg_statuses("exit_status", raw["exit_status"])
        enter_status = _parse_leg_statuses("enter_status", raw["enter_status"])

        retry_raw = raw["enter_retry_count"]
        if not isinstance(retry_raw, dict):
            raise RebalancePlanStateError("RebalancePlan.enter_retry_count: expected an object.")
        enter_retry_count: Dict[str, int] = {}
        for symbol, count_value in retry_raw.items():
            if not isinstance(count_value, int) or isinstance(count_value, bool):
                raise RebalancePlanStateError(
                    f"RebalancePlan.enter_retry_count[{symbol!r}]: expected an integer, got {count_value!r}."
                )
            enter_retry_count[symbol] = count_value

        try:
            created_at = datetime.fromisoformat(raw["created_at"])
            last_broker_check_at = datetime.fromisoformat(raw["last_broker_check_at"])
        except (TypeError, ValueError) as exc:
            raise RebalancePlanStateError(f"RebalancePlan: malformed timestamp: {exc}") from exc

        try:
            signal_date = date.fromisoformat(raw["signal_date"])
        except (TypeError, ValueError) as exc:
            raise RebalancePlanStateError(f"RebalancePlan.signal_date: malformed date: {exc}") from exc

        approved_at_raw = raw["approved_at"]
        if approved_at_raw is None:
            approved_at = None
        else:
            try:
                approved_at = datetime.fromisoformat(approved_at_raw)
            except (TypeError, ValueError) as exc:
                raise RebalancePlanStateError(f"RebalancePlan.approved_at: malformed timestamp: {exc}") from exc

        return cls(
            target_id=raw["target_id"], diff=diff, status=status,
            exit_status=exit_status, enter_status=enter_status,
            enter_retry_count=enter_retry_count, signal_date=signal_date, approved_at=approved_at,
            created_at=created_at, last_broker_check_at=last_broker_check_at,
        )


def is_target_stale(signal_date: date, *, current_trading_day: date) -> bool:
    """R0 §7's exact formula: is_stale = current_trading_day != signal_date.

    Takes the bare date rather than a full TargetPortfolio - that object
    is discarded once create_rebalance_plan() returns, and signal_date is
    the only part of it RebalancePlan itself persists (see
    RebalancePlan.signal_date). Sharing this one function is what lets
    create_rebalance_plan() and approve_plan()
    (v34_bridge_rebalance_transitions.py - re-checks staleness at
    approval time, not just at creation) apply the identical formula
    rather than two independently-written comparisons that could drift
    apart."""
    return current_trading_day != signal_date


def create_rebalance_plan(diff: RebalanceDiff, *, target: TargetPortfolio, current_trading_day: date) -> RebalancePlan:
    """Construct a fresh RebalancePlan in CREATED status, every leg PENDING.
    Refuses (StaleTargetError) rather than create a plan from a target that
    is no longer valid for today - R0 §7: "the bridge refuses to *create* a
    RebalancePlan from a stale TargetPortfolio - hard error, not a warning."
    """
    if diff.target_id != target.target_id:
        raise RebalancePlanStateError(
            f"diff.target_id ({diff.target_id!r}) does not match target.target_id ({target.target_id!r})."
        )
    if is_target_stale(target.signal_date, current_trading_day=current_trading_day):
        raise StaleTargetError(
            f"Refusing to create a RebalancePlan for {target.target_id}: "
            f"signal_date {target.signal_date.isoformat()} != current trading day "
            f"{current_trading_day.isoformat()}."
        )

    now = datetime.now(timezone.utc)
    return RebalancePlan(
        target_id=diff.target_id,
        diff=diff,
        status=RebalancePlanStatus.CREATED,
        exit_status={symbol: LegStatus.PENDING for symbol in diff.exits},
        enter_status={symbol: LegStatus.PENDING for symbol in diff.enters},
        enter_retry_count={symbol: 0 for symbol in diff.enters},
        signal_date=target.signal_date,
        approved_at=None,
        created_at=now,
        last_broker_check_at=now,
    )


class RebalancePlanStore:
    """File-backed durable store, one JSON file per target_id. Durable-write
    discipline: write to a temp file, fsync, then os.replace - the same
    pattern documented throughout this project's engine/runner state
    stores, so a crash mid-write can never leave a half-written plan file."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path_for(self, target_id: str) -> Path:
        safe_name = target_id.replace("/", "_").replace("\\", "_")
        return self.directory / f"{safe_name}.json"

    def load(self, target_id: str) -> Optional[RebalancePlan]:
        path = self._path_for(target_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RebalancePlan.from_dict(raw)

    def save(self, plan: RebalancePlan) -> None:
        path = self._path_for(plan.target_id)
        tmp_path = path.with_name(path.name + ".tmp")
        data = json.dumps(plan.to_dict(), indent=2, sort_keys=True)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)

    def list_active_plans(self) -> List[RebalancePlan]:
        """Every persisted plan not in NO_FURTHER_ACTION_PLAN_STATUSES -
        i.e. every plan a caller might still need to drive forward. Purely
        mechanical (reads whatever is on disk, no policy about how many
        there *should* be) - a caller wanting to enforce "at most one
        active plan at a time" (the runner does; see
        v34_bridge_runner_entrypoint.py) applies that policy itself on the
        result, since that is a runner-level decision, not a storage one."""
        active = []
        for path in sorted(self.directory.glob("*.json")):
            plan = self.load(path.stem)
            if plan is not None and plan.status not in NO_FURTHER_ACTION_PLAN_STATUSES:
                active.append(plan)
        return active

    def list_all_plans(self) -> List[RebalancePlan]:
        """EA1-R1, 2026-08-19: every persisted plan regardless of status -
        list_active_plans() above deliberately excludes terminal/HALTED
        plans, which is exactly wrong for a caller that needs to look AT
        a HALTED plan (or compare it against newer ones). Same purely
        mechanical, no-policy-baked-in shape as list_active_plans() -
        added for v34_bridge_reconcile_halted_plan.py's staleness check
        (is a NEWER signal_date already on disk for this terminal), kept
        here rather than duplicated because "read every plan file" is
        storage-layer work, not policy."""
        plans = []
        for path in sorted(self.directory.glob("*.json")):
            plan = self.load(path.stem)
            if plan is not None:
                plans.append(plan)
        return plans


def resolve_or_create_plan(
    store: RebalancePlanStore, diff: RebalanceDiff, *, target: TargetPortfolio, current_trading_day: date,
) -> Tuple[RebalancePlan, str]:
    """R0 §10's exact idempotency rule. Returns (plan, outcome) where
    outcome tells the caller what happened, without requiring it to
    re-derive that from plan.status itself:

    - "CREATED": no existing plan, a fresh one was made and persisted.
    - "NOOP_ALREADY_TERMINAL": an existing COMPLETE/PARTIAL plan for this
      exact target_id already exists - returned as-is, nothing re-submitted.
    - "REFUSED_HALTED": an existing plan for this target_id is HALTED -
      requires deliberate operator reconciliation, never auto-resumed.
    - "RESUMED": an existing plan is mid-flight (CREATED/EXITING/
      EXITS_COMPLETE/ENTERING) - this is the restart case (R0 §8), not a
      new-plan case; returned as-is for the caller to resume sequencing.
    """
    existing = store.load(diff.target_id)
    if existing is None:
        plan = create_rebalance_plan(diff, target=target, current_trading_day=current_trading_day)
        store.save(plan)
        return plan, "CREATED"
    if existing.status in TERMINAL_PLAN_STATUSES:
        return existing, "NOOP_ALREADY_TERMINAL"
    if existing.status == RebalancePlanStatus.HALTED:
        return existing, "REFUSED_HALTED"
    return existing, "RESUMED"
