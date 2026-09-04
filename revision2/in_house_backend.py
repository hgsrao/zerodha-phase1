"""Converts a real Revision2Orchestrator run into the backend-neutral
BackendEvent stream, for parity comparison against the Backtrader backend.

This is a pure post-processing step over the orchestrator's existing
`trace_sink` observability hook (see revision2/orchestrator.py's own
docstring: "it changes no control flow"). Nothing here alters a single
trading decision -- it only re-describes decisions already made.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.backend_contract import BackendEvent, canonical_parameter_snapshot, normalize_event_timestamp
from revision2.orchestrator import Revision2Orchestrator


def _leg_cost(price: float, quantity: int, side: str) -> float:
    """Identical formula to orchestrator.py's _transaction_costs() and
    portfolio_orchestrator.py's _leg_cost() -- kept identical deliberately
    so a parity divergence reflects real logic differences, not a
    mismatched cost model between the two engines being compared."""
    turnover = price * quantity
    cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
    if side == "SELL":
        cost += 0.00025 * turnover
    return cost


def run_in_house_events(
    symbol: str,
    bars: pd.DataFrame,
    registry: CanonicalParameterRegistry,
    calibration_overrides: Optional[Dict[str, Any]] = None,
    warmup: int = 60,
    starting_equity: float = 100_000.0,
) -> List[BackendEvent]:
    """Run the real single-symbol orchestrator and translate its trace_sink
    records into BackendEvent objects, in the same order they occurred."""
    snapshot = canonical_parameter_snapshot(registry, calibration_overrides)
    config_hash = snapshot["effective_config_sha256"]

    orch = Revision2Orchestrator(
        symbol, registry, calibration_overrides=calibration_overrides, starting_equity=starting_equity,
    )
    sink: List[Dict[str, Any]] = []
    orch.run(bars, warmup=warmup, trace_sink=sink)

    events: List[BackendEvent] = []
    sequence = 0
    close_side = {"BUY": "SELL", "SELL": "BUY"}
    open_side: Optional[str] = None  # side of the currently-open position, for exit-event side labeling
    pending_fill: Optional[Dict[str, Any]] = None  # entry fill economically due on the NEXT record

    def emit(event_type: str, event_timestamp: str, **kw) -> None:
        nonlocal sequence
        sequence += 1
        events.append(BackendEvent(
            backend="in_house", event_type=event_type, symbol=symbol,
            decision_timestamp=kw.pop("decision_timestamp", event_timestamp),
            event_timestamp=event_timestamp, sequence=sequence, config_hash=config_hash, **kw,
        ))

    def flush_pending_fill(event_timestamp: str) -> None:
        nonlocal pending_fill
        if pending_fill is None:
            return
        p = pending_fill
        emit("fill", event_timestamp, decision_timestamp=p["decision_ts"],
             side=p["side"], quantity=p["quantity"], price=p["price"], reason="filled")
        emit("cost", event_timestamp, decision_timestamp=p["decision_ts"],
             side=p["side"], quantity=p["quantity"], price=_leg_cost(p["price"], p["quantity"], p["side"]),
             reason="entry leg cost")
        pending_fill = None

    for record in sink:
        ts = normalize_event_timestamp(record["timestamp"])

        # An entry order placed on the PREVIOUS record economically fills
        # at bars.iloc[bar_idx + 1]["open"] -- this record IS that next
        # bar, so its own timestamp is the real fill bar. Flushing here,
        # before this record's own signal/gate_decision events, matches
        # Backtrader's natural notify_order() timing: it fires at the
        # start of bar t+1's processing, before that bar's own next().
        flush_pending_fill(ts)

        if "pa" in record:
            pa = record["pa"]
            emit("signal", ts, price=record["close"],
                 reason=f"direction={pa['direction']} confidence={pa['confidence']:.4f}")

        if "id" in record:
            idr = record["id"]
            emit("gate_decision", ts, reason=f"ID: {idr['reason']}", passed=idr["approved"])

        if record["stage"] == "rejected_safety_pre_sizing":
            emit("gate_decision", ts, reason="SafetyGates pre-sizing: rejected", passed=False)

        if record["stage"] == "rejected_safety_post_sizing":
            emit("gate_decision", ts, reason="SafetyGates post-sizing: rejected", passed=False)

        if "gates" in record:
            g = record["gates"]
            emit("gate_decision", ts, reason=f"18gates: {g['gate']}: {g['reason']}", passed=g["passed"])

        if record["stage"] == "rejected_execution_gate":
            emit("gate_decision", ts, reason="ExecutionGate: rejected", passed=False)

        if record["stage"] == "entry_filled":
            entry = record["entry"]
            open_side = entry["side"]
            # "order" is the plan's OWN intended price (MPC's effective_entry,
            # including its own slippage_cost_multiplier estimate) -- what
            # was actually submitted, before the broker's separate, real
            # fill-time slippage is applied. Using the broker's post-slippage
            # fill price here instead (as an earlier version of this
            # function did) compared it against the Backtrader side's
            # plan.entry_price and produced a spurious divergence: the two
            # numbers are genuinely different quantities in this pipeline
            # (MPC's slippage estimate vs the broker's real fill slippage),
            # not evidence of a real cross-engine bug.
            emit("order", ts, side=entry["side"], quantity=entry["quantity"], price=record["mpc"]["entry_price"], reason="submitted")
            # The fill itself is deferred to flush_pending_fill() on the
            # NEXT record: orchestrator.py computes this fill's price from
            # bars.iloc[bar_idx + 1]["open"] (a real next-bar-open market
            # order fill) but historically stamped the event at bar_idx's
            # OWN timestamp -- mislabeling a fill that economically happens
            # one bar later as if it were instantaneous. This surfaced as a
            # real divergence against Backtrader (whose notify_order()
            # naturally fires one bar after order submission) the first
            # time this comparison harness ran on a window with real
            # trades -- exactly the concern the conversation-handover
            # document raised independently ("A signal from the final bar
            # can select the next trading day's open... mark it against
            # the earlier bar before its fill time").
            pending_fill = {"side": entry["side"], "quantity": entry["quantity"], "price": entry["price"], "decision_ts": ts}

        if "exit" in record:
            ex = record["exit"]
            exit_side = close_side.get(open_side or ex["side"], "SELL")
            emit("exit", ts, side=exit_side, quantity=ex["quantity"], price=ex["exit_price"], reason=ex["reason"])
            emit("cost", ts, side=exit_side, quantity=ex["quantity"],
                 price=_leg_cost(ex["exit_price"], ex["quantity"], exit_side), reason="exit leg cost")
            open_side = None

        emit("equity", ts, price=record["equity"], reason="post-bar")

    if pending_fill is not None:
        # An entry on the LAST evaluated bar (orchestrator.py's loop stops
        # at bar_idx = n - 2) fills using bars.iloc[n - 1]["open"] -- a real
        # bar that exists in the source data but is never itself evaluated
        # as a decision bar, so there is no later sink record to flush
        # against. Read its timestamp directly from the source bars.
        last_bar_idx = sink[-1]["bar_idx"]
        target_idx = last_bar_idx + 1
        fallback_ts = (
            normalize_event_timestamp(bars.iloc[target_idx]["timestamp"])
            if target_idx < len(bars) else pending_fill["decision_ts"]
        )
        flush_pending_fill(fallback_ts)

    return events
