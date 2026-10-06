"""Deterministic, non-trading proof harness for the three control loops.

This module deliberately uses the production controller classes.  It is not a
market backtest, alpha claim, or a second paper broker.  Its purpose is to make
the ordering and boundaries of the feedback paths inspectable:

* completed outcomes affect only a later entry-quality snapshot;
* a path controller can arm a stricter stop only for a later bar; and
* portfolio and HMM recommendations are bounded one-way derates.

The HMM output remains explicitly shadow-only here, matching the orchestrator.
"""

from __future__ import annotations

from typing import Any, Dict, List

from revision2_external.closed_loop_control import ClosedLoopSupervisor, HMMRiskHysteresis
from revision2_external.continuous_exit_controller import ContinuousExitController


def build_closed_loop_cascade_dry_run() -> Dict[str, Any]:
    """Return a fixed, chronological controller trace without placing orders."""
    events: List[Dict[str, Any]] = []
    supervisor = ClosedLoopSupervisor()

    # A candidate cannot see any future outcomes.
    before = supervisor.entry_snapshot("BETA", "BUY", 100.0, 95.0, 110.0, 20, regime="normal")
    events.append({
        "sequence": 1, "event_type": "ENTRY_QUALITY_BEFORE_OUTCOMES",
        "profile": before["entry_quality"], "execution_affected": False,
    })

    # The slow loop receives twenty already-completed losses.  Twenty is the
    # same evidence scale used by the production partial-pooling profile; this
    # is not a five-trade shortcut or a replacement for real data.
    for number in range(20):
        supervisor.record_outcome({
            "symbol": "BETA", "side": "BUY", "net_pnl": -10.0,
            "trade_id": f"beta-completed-{number + 1}", "reason": "stop",
        }, regime="normal")
    after = supervisor.entry_snapshot("BETA", "BUY", 100.0, 95.0, 110.0, 20, regime="normal")
    events.append({
        "sequence": 2, "event_type": "ENTRY_QUALITY_AFTER_COMPLETED_OUTCOMES",
        "profile": after["entry_quality"], "execution_affected": False,
    })

    # Fast portfolio loop: normal exposure is a no-op.  It is included so the
    # final cascade has all three production-loop inputs.
    portfolio = supervisor.observe_portfolio_risk(10_000.0, 100_000.0, 0.50)
    events.append({
        "sequence": 3, "event_type": "PORTFOLIO_RISK_OBSERVATION",
        "observation": portfolio, "execution_affected": False,
    })

    # HMM hysteresis requires sustained stress.  This is a fixed posterior
    # feed, not an HMM fit and not an assertion that a real market is stressed.
    hysteresis = HMMRiskHysteresis(smoothing_alpha=1.0, confirmation_bars=3)
    hmm_updates = []
    for bar_number in range(1, 4):
        update = hysteresis.update({"available": True, "stress_probability": 0.90})
        hmm_updates.append(update)
        events.append({
            "sequence": 3 + bar_number, "event_type": "HMM_REGIME_RISK_SHADOW",
            "bar": bar_number, "observation": update,
            "execution_affected": False,
            "reason": "SHADOW_ONLY_NOT_WIRED_TO_ORDER_SIZE",
        })

    # Trade-path / exit controller: the closing price makes a ratcheted stop
    # proposal.  The prior stop is deliberately *not* checked against this
    # same bar; any resulting stop is armed only for a future OHLC bar.
    controller = ContinuousExitController(
        kp=0.12, ki=0.04, kd=0.06, clamp=0.10, atr_droop_mult=1.0, baseline_window=5,
    )
    state = controller.open_position("BUY", 100.0, 95.0, 110.0, max_hold_bars=20)
    armed_state = controller.update("ALPHA", state, 0.80, 0.80, 104.0, 2.0)
    armed_stop = float(armed_state.current_stop_price)
    events.append({
        "sequence": 7, "event_type": "TRADE_PATH_STOP_ARMED_NEXT_BAR",
        "controller_telemetry": dict(armed_state.last_telemetry),
        "stop_armed_from_close": armed_stop,
        "same_bar_exit_checked": False,
        "execution_affected": False,
    })
    next_bar = {"open": 103.0, "high": 104.0, "low": armed_stop - 0.01, "close": 102.0}
    next_bar_would_trigger = float(next_bar["open"]) <= armed_stop or float(next_bar["low"]) <= armed_stop
    events.append({
        "sequence": 8, "event_type": "TRADE_PATH_NEXT_BAR_CHECK",
        "prior_armed_stop": armed_stop, "bar": next_bar,
        "would_trigger_prior_stop": next_bar_would_trigger,
        "execution_affected": False,
    })

    entry_derate = float(after["entry_quality"]["suggested_entry_derate"])
    portfolio_derate = float(portfolio["suggested_new_risk_derate"])
    hmm_derate = float(hmm_updates[-1]["suggested_hysteresis_derate"])
    proposal = min(1.0, entry_derate * portfolio_derate * hmm_derate)
    events.append({
        "sequence": 9, "event_type": "CASCADE_PROPOSAL_SHADOW",
        "base_risk_multiplier": 1.0,
        "entry_quality_derate": entry_derate,
        "portfolio_risk_derate": portfolio_derate,
        "hmm_hysteresis_derate": hmm_derate,
        "combined_proposed_risk_multiplier": proposal,
        "execution_affected": False,
        "reason": "NO_CONTROLLER_OUTPUT_IS_AUTHORIZED_TO_RESIZE_ORDERS_YET",
    })

    return {
        "status": "DRY_RUN_COMPLETE",
        "purpose": "controller sequencing and boundedness proof; not market validation",
        "pid_gains_dynamic": False,
        "pid_dynamic_state": "rolling setpoints, PID terms, stop state, and loop recommendations",
        "hmm_execution_status": "SHADOW_ONLY",
        "events": events,
    }


def build_stress_test_dry_run() -> Dict[str, Any]:
    """Exercise fail-closed boundaries using deterministic controller inputs.

    Every result is a recommendation or a paper-execution *would* outcome.
    This function intentionally has no broker, market-data, or order side
    effects.
    """
    events: List[Dict[str, Any]] = []
    supervisor = ClosedLoopSupervisor()

    hard_limit = supervisor.observe_portfolio_risk(100_000.0, 100_000.0, 0.50)
    events.append({"scenario": "PORTFOLIO_HARD_LIMIT", "observation": hard_limit})

    # First two observations establish the required three-bar evidence, then
    # a third latches. A subsequent 0.65 reading is deliberately inside the
    # hysteresis deadband: it must not release the brake.
    hysteresis = HMMRiskHysteresis(smoothing_alpha=1.0, confirmation_bars=3)
    latch_trace = [hysteresis.update({"available": True, "stress_probability": 0.76}) for _ in range(3)]
    deadband = hysteresis.update({"available": True, "stress_probability": 0.65})
    events.append({
        "scenario": "HMM_HYSTERESIS_DEADBAND_SHADOW", "latch_trace": latch_trace,
        "deadband_observation": deadband, "execution_affected": False,
    })

    # Two losses are intentionally insufficient to dominate the neutral
    # partial-pooling prior used by the entry-quality loop.
    before = supervisor.entry_snapshot("NEW", "BUY", 100.0, 95.0, 110.0, 20)["entry_quality"]
    for number in range(2):
        supervisor.record_outcome({
            "symbol": "NEW", "side": "BUY", "net_pnl": -10.0,
            "trade_id": f"new-{number}", "reason": "stop",
        })
    after_two_losses = supervisor.entry_snapshot("NEW", "BUY", 100.0, 95.0, 110.0, 20)["entry_quality"]
    events.append({
        "scenario": "ENTRY_QUALITY_PARTIAL_POOLING", "before": before,
        "after_two_losses": after_two_losses, "execution_affected": False,
    })

    # This calls the production controller's causally safe stop checker. It
    # models a stop armed on an earlier bar and an adverse next-bar gap.
    controller = ContinuousExitController(
        kp=0.12, ki=0.04, kd=0.06, clamp=0.10, atr_droop_mult=1.0, baseline_window=5,
    )
    state = controller.open_position("BUY", 105.0, 100.0, 115.0, max_hold_bars=20)
    state.shadow_stop_price = 100.0
    gap = controller.check_shadow_stop(
        state, {"open": 98.0, "high": 99.0, "low": 97.0, "close": 98.0}, "dry-run-next-bar",
    )
    events.append({"scenario": "NEXT_BAR_STOP_GAP", "result": gap, "execution_affected": False})

    # This is the exact final integer boundary used by the production sizing
    # module (max(0, int(quantity))). It demonstrates that a fractional
    # proposal cannot be rounded up into an unauthorised minimum order.
    base_quantity = 1
    combined_derate = 0.40
    final_quantity = max(0, int(base_quantity * combined_derate))
    events.append({
        "scenario": "MINIMUM_SIZE_REJECTION", "base_quantity": base_quantity,
        "combined_derate": combined_derate, "final_quantity": final_quantity,
        "would_submit_order": final_quantity > 0,
    })

    contradiction = min(1.0, 1.0 * hard_limit["suggested_new_risk_derate"] * deadband["suggested_hysteresis_derate"])
    events.append({
        "scenario": "CONTRADICTORY_SIGNALS", "strong_entry_derate": 1.0,
        "portfolio_derate": hard_limit["suggested_new_risk_derate"],
        "hmm_derate": deadband["suggested_hysteresis_derate"],
        "combined_proposed_risk_multiplier": contradiction,
        "execution_affected": False,
    })

    return {
        "status": "STRESS_DRY_RUN_COMPLETE",
        "purpose": "fail-closed controller-boundary proof; not market validation",
        "hmm_execution_status": "SHADOW_ONLY",
        "events": events,
    }
