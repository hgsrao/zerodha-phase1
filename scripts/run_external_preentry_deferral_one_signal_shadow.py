#!/usr/bin/env python3
"""Evaluate one causal pre-entry deferral decision for an existing trace.

The controller observes one completed provisional bar after an already
approved candidate.  It compares the candidate's hypothetical R-progress
with its frozen reference-path progress, then either cancels the candidate or
submits a paper order at the next bar's open.  This is a single-signal shadow
experiment, not a strategy promotion or a live-trading path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.contracts import IDDecision, PASignal
from revision2.dataset_manifest import DatasetManifest
from revision2_external.closed_loop_control import ClosedLoopSupervisor
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _row_at(frame: pd.DataFrame, timestamp: str) -> tuple[int, pd.Series]:
    matches = frame.index[frame["timestamp"].astype(str) == timestamp]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one bar at {timestamp}, found {len(matches)}")
    index = int(matches[0])
    return index, frame.iloc[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", default="diagnostic_output/no_pid_one_signal_trace_INFY_20230901.json")
    parser.add_argument("--output", default="diagnostic_output/preentry_deferral_shadow_INFY_20230901.json")
    args = parser.parse_args()

    trace_document = json.loads(Path(args.trace).read_text(encoding="utf-8"))
    trace = trace_document["first_completed_trade_trace"]
    original = trace["paper_execution_and_outcome"]
    signal = PASignal(**trace["pa_signal"])
    decision = IDDecision(**trace["id_decision"])
    mpc_inputs = trace["mpc_inputs"]
    original_plan = trace["mpc_plan"]
    symbol = signal.symbol

    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    frame = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)._load_symbol_csv(symbol)
    confirmation_i, confirmation_bar = _row_at(frame, original["entry_timestamp"])
    if confirmation_i + 1 >= len(frame):
        raise RuntimeError("no following bar is available for a deferred paper entry")
    deferred_entry_bar = frame.iloc[confirmation_i + 1]

    # The original plan is a frozen, hypothetical direct entry.  Its risk
    # and target define the provisional comparison; no later bar changes
    # either before the controller decides.
    supervisor = ClosedLoopSupervisor()
    reference = supervisor.entry_snapshot(
        symbol=symbol,
        side=original_plan["side"],
        entry_price=float(original_plan["entry_price"]),
        stop_price=float(original_plan["stop_price"]),
        target_price=float(original_plan["target_price"]),
        max_hold_bars=int(original_plan["maximum_hold_bars"]),
    )
    observation = supervisor.observe_trade_path(
        reference, float(confirmation_bar["close"]), bars_held=1)
    # This is intentionally stricter than the protective lower bound used
    # after entry: a deferred candidate must meet its expected first-bar
    # progress, not merely avoid a catastrophic excursion.
    confirmation_passed = observation.actual_r >= observation.expected_r
    confirmation = {
        "provisional_bar": confirmation_bar.to_dict(),
        "reference_path": reference["reference_path"],
        "actual_r": observation.actual_r,
        "expected_r": observation.expected_r,
        "progress_error_r": observation.expected_r - observation.actual_r,
        "decision": "ADMIT_NEXT_OPEN" if confirmation_passed else "CANCEL",
        "rule": "admit only when first completed provisional bar has actual_r >= expected_r",
    }

    result: dict = {
        "run_type": "one_signal_preentry_deferral_shadow",
        "research_boundary": "One-signal causal shadow experiment only; no live-trading inference.",
        "source_trace": str(args.trace),
        "symbol": symbol,
        "source_candidate_id": trace["candidate_id"],
        "source_signal": trace["pa_signal"],
        "source_id_decision": trace["id_decision"],
        "source_no_pid_plan": original_plan,
        "original_direct_entry_outcome": original,
        "preentry_feedback": confirmation,
    }

    if confirmation_passed:
        # Rebuild the same no-PID ATR/RR plan from the same frozen signal,
        # decision and ATR.  Only the later paper-entry open differs.
        registry = CanonicalParameterRegistry()
        config = trace_document["config_hash"]  # retained for audit below
        values = {name: spec.default for name, spec in registry.params.items()}
        from revision2.contracts import EffectiveConfig
        effective = EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)
        plan, pid_output, _ = SimplePIDModelPredictiveControlBox(pid_enabled=False).build_plan(
            signal, decision, float(deferred_entry_bar["open"]), float(mpc_inputs["atr"]), effective,
        )
        assert plan is not None
        quantity = int(original["quantity"])
        market_exit = None
        reason = None
        if plan.side == "BUY":
            if float(deferred_entry_bar["open"]) <= plan.stop_price:
                market_exit, reason = float(deferred_entry_bar["open"]), "stop_gap"
            elif float(deferred_entry_bar["open"]) >= plan.target_price:
                market_exit, reason = float(deferred_entry_bar["open"]), "target_gap"
            elif float(deferred_entry_bar["low"]) <= plan.stop_price:
                market_exit, reason = plan.stop_price, "stop"
            elif float(deferred_entry_bar["high"]) >= plan.target_price:
                market_exit, reason = plan.target_price, "target"
        else:
            raise RuntimeError("this single-signal demonstrator currently expects the traced BUY candidate")
        if market_exit is None:
            raise RuntimeError("deferred entry did not resolve in its entry bar; this one-bar demonstrator intentionally stops here")
        entry = float(plan.entry_price)
        exit_fill = Revision2ExternalEngineOrchestrator._paper_fill_price(
            float(market_exit), "SELL", 0.0005,
        )
        gross = (exit_fill - entry) * quantity
        costs = (
            Revision2ExternalEngineOrchestrator._leg_cost(entry, quantity, "BUY")
            + Revision2ExternalEngineOrchestrator._leg_cost(exit_fill, quantity, "SELL")
        )
        result["deferred_paper_execution"] = {
            "entry_timestamp": str(deferred_entry_bar["timestamp"]),
            "entry_bar": deferred_entry_bar.to_dict(),
            "frozen_inputs": {"atr": mpc_inputs["atr"], "signal_timestamp": signal.timestamp, "config_hash": config},
            "no_pid_plan_rebuilt_at_deferred_open": {
                "side": plan.side, "entry_price": plan.entry_price, "stop_price": plan.stop_price,
                "target_price": plan.target_price, "maximum_hold_bars": plan.maximum_hold_bars,
                "pid_output": pid_output,
            },
            "exit_on_entry_bar": {
                "market_exit_price": market_exit, "paper_exit_price": exit_fill,
                "reason": reason,
            },
            "quantity": quantity,
            "gross_pnl": gross,
            "costs": costs,
            "net_pnl": gross - costs,
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(output), "confirmation": confirmation["decision"],
                      "deferred_execution": result.get("deferred_paper_execution")}, indent=2, default=str))


if __name__ == "__main__":
    main()
