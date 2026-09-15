"""V11 -> P02 Rebalance Bridge — the trigger (produces R0 §10's plans).

The bridge's own half of the pipeline: a short-lived, periodically-fired
script (per R0 §13's resolution - NOT a second always-on process) that
computes today's TargetPortfolio (R1), diffs it against the broker's
actual current holdings (Step A), and writes/resumes a RebalancePlan
(R0 §4/§10) into the same shared directory the runner daemon polls
(v34_bridge_runner_entrypoint.py) - the durable file-based command queue
this session chose as the bridge<->runner IPC mechanism. No new protocol,
no live API between the two halves - a directory both sides already know
how to read and write durably.

This module never drives the plan forward - R0 §13's resolved division
of labor: only the runner (holding the real TradingEngineV34P02 instance)
calls request_entry()/request_exit(). This module computes and writes,
then exits. It has no idea whether a runner is even currently running,
and does not need to - resolve_or_create_plan() (R0 §10) already makes
this idempotent regardless of trigger/runner timing.

Deliberately pure and fully parameterized, matching every other piece of
this bridge (R1, Step A, RebalancePlan itself): quotes and
current_portfolio are supplied by the caller, not fetched here. Fetching
REAL quotes and REAL current holdings from Kite is a separate, explicitly
flagged gap - see v34_bridge_trigger_main.py's docstring - not built
silently alongside this orchestration logic, the same discipline
v34_bridge_runner_main.py's _build_engine() already established for the
runner side.

No broker/network calls. No P02 import.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Tuple

from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import RebalancePlan, RebalancePlanStore, resolve_or_create_plan
from v34_bridge_target_portfolio import build_target_portfolio


def run_trigger(
    *,
    quotes: Mapping[str, Dict[str, Any]],
    current_portfolio: Mapping[str, int],
    signal_date: date,
    current_trading_day: date,
    store: RebalancePlanStore,
    positions: int = 4,
) -> Tuple[RebalancePlan, str]:
    """One trigger run: TargetPortfolio (R1) -> RebalanceDiff (Step A) ->
    resolve_or_create_plan() (R0 §10). Returns (plan, outcome) exactly as
    resolve_or_create_plan() does - "CREATED" | "NOOP_ALREADY_TERMINAL" |
    "REFUSED_HALTED" | "RESUMED" - so a caller (v34_bridge_trigger_main.py)
    can log/alert appropriately without re-deriving that meaning from
    plan.status itself.

    `signal_date` and `current_trading_day` are deliberately two separate
    parameters, not one collapsed into the other: R0 §7's staleness rule
    is exactly the comparison between them (is_target_stale), and a
    same-day trigger run supplies the same value for both - collapsing
    them would make it impossible to express the stale case this
    function is required to refuse (via create_rebalance_plan's
    StaleTargetError, propagated here unchanged) on a next-day resume of
    an old signal that never got a plan started.

    Never drives the resulting plan forward - see module docstring."""
    target = build_target_portfolio(quotes=quotes, signal_date=signal_date, positions=positions)
    diff = compute_rebalance_diff(current_portfolio=current_portfolio, target=target)
    return resolve_or_create_plan(store, diff, target=target, current_trading_day=current_trading_day)
