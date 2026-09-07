#!/usr/bin/env python3
"""Replay a real backtest and record every actual Box-6 PID invocation.

This wraps the controller used by Revision2ExternalEngineOrchestrator. It
never simulates confidence or uses hard-coded trades. Trades which close before
Box 6 receives a bar retain an empty ``pid_cycles`` list: that is evidence the
PID did not receive a cycle, not a fabricated zero-valued cycle.

Usage: python3 scripts/pid_controller_detailed_tracer.py [max_bars]
Without an argument it replays INFY's first six calendar months.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import determinism_guard  # noqa: F401 -- before pandas/numpy
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

SYMBOL = "INFY"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output_external_engine"
JSON_PATH = OUT_DIR / "infy_pid_controller_detailed_trace.json"
TEXT_PATH = OUT_DIR / "infy_pid_controller_detailed_trace.txt"


def _baseline(store, symbol, value):
    """Exact pre-update rolling mean rule used by _baseline_from()."""
    history = store.get(symbol)
    return sum(history) / len(history) if history else value


def main() -> None:
    max_bars = int(sys.argv[1]) if len(sys.argv) > 1 else None
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv(SYMBOL)
    start = frame["timestamp"].iloc[0]
    frame = frame[frame["timestamp"] < start + pd.DateOffset(months=6)].reset_index(drop=True)
    if max_bars is not None:
        frame = frame.iloc[:max_bars].reset_index(drop=True)

    orch = Revision2ExternalEngineOrchestrator([SYMBOL], CanonicalParameterRegistry(), starting_equity=1_000_000.0)
    cycles_by_state, state_metadata, current_context = defaultdict(list), {}, {}
    next_trace_id = 0
    original_open = orch.exit_controller.open_position
    original_maybe_exit = orch._maybe_exit
    original_update = orch.exit_controller.update

    def traced_open(side, entry_price, stop_price, target_price, max_hold_bars):
        nonlocal next_trace_id
        state = original_open(side, entry_price, stop_price, target_price, max_hold_bars)
        # A state object is constructed exactly once per real position.
        # This is the identity used below to bind every update to its ledger
        # trade, including positions which receive no update at all.
        # ``id(state)`` can be reused by CPython after a short-lived position
        # exits. Give each position a monotonically increasing identity instead.
        state._pid_trace_id = next_trace_id
        state_metadata[next_trace_id] = {"side": side, "entry_price": entry_price,
                                     "initial_stop_price": stop_price,
                                     "initial_target_price": target_price}
        next_trace_id += 1
        return state

    def traced_maybe_exit(symbol, timestamp, bar, signal, held_bars, session_last_bar, chart_studies_confidence):
        current_context.clear()
        current_context.update({"timestamp": str(timestamp), "bar_open": float(bar["open"]),
                                "bar_high": float(bar["high"]), "bar_low": float(bar["low"]),
                                "bar_close": float(bar["close"]), "held_bars_argument": held_bars})
        return original_maybe_exit(symbol, timestamp, bar, signal, held_bars, session_last_bar, chart_studies_confidence)

    def traced_update(symbol, state, pa_confidence, studies_confidence, close, atr):
        # Values before original_update() mutates its histories/PIDs.
        pa_baseline = _baseline(orch.exit_controller._confidence_history, symbol, pa_confidence)
        studies_baseline = _baseline(orch.exit_controller._studies_history, symbol, studies_confidence)
        stop_before, extreme_before, bars_before = state.current_stop_price, state.favorable_extreme, state.bars_held
        updated = original_update(symbol, state, pa_confidence, studies_confidence, close, atr)
        pa_p, pa_i, pa_d = orch.exit_controller._pids[symbol].components
        st_p, st_i, st_d = orch.exit_controller._studies_pids[symbol].components
        pa_output, studies_output = updated.adjustment_history[-1], updated.studies_adjustment_history[-1]
        cycles_by_state[state._pid_trace_id].append({
            **current_context,
            "inputs": {"pa_exit_confidence": pa_confidence, "chart_studies_confidence": studies_confidence,
                       "current_close": close, "current_atr": atr},
            "setpoints_before_update": {"pa_rolling_baseline": pa_baseline,
                                          "studies_rolling_baseline": studies_baseline},
            "pa_pid": {"p": pa_p, "i": pa_i, "d": pa_d, "output": pa_output,
                       "at_positive_clamp": pa_output >= orch.exit_controller.clamp - 1e-9},
            "studies_pid": {"p": st_p, "i": st_i, "d": st_d, "output": studies_output,
                            "at_positive_clamp": studies_output >= orch.exit_controller.clamp - 1e-9},
            "state": {"bars_held_before": bars_before, "bars_held_after": updated.bars_held,
                      "favorable_extreme_before": extreme_before, "favorable_extreme_after": updated.favorable_extreme,
                      "stop_before": stop_before, "stop_after": updated.current_stop_price,
                      "pa_saturation_streak": updated.consecutive_bars_at_low_confidence_extreme,
                      "studies_saturation_streak": updated.consecutive_bars_at_low_studies_extreme,
                      "saturation_exit_reason": orch.exit_controller.saturation_exit_reason(updated)},
        })
        return updated

    orch.exit_controller.open_position = traced_open
    orch._maybe_exit = traced_maybe_exit
    orch.exit_controller.update = traced_update
    t0 = time.time()
    report = orch.run({SYMBOL: frame}, warmup=60)
    elapsed = time.time() - t0

    # State construction is one-to-one with a position. Match it to the real
    # ledger by the exact fill price and side, consuming each state once. This
    # deliberately keeps zero-cycle (same-bar) positions in their true slot.
    unused_states = list(state_metadata)
    trace_trades = []
    for trade in report["trades"]:
        state_id = next((sid for sid in unused_states
                         if state_metadata[sid]["side"] == trade["side"]
                         and abs(state_metadata[sid]["entry_price"] - trade["entry_price"]) < 1e-8), None)
        if state_id is None:
            candidates = [state_metadata[sid] for sid in unused_states[:5]]
            raise RuntimeError(f"could not bind controller state to real trade {trade}; next states={candidates}")
        unused_states.remove(state_id)
        rows = cycles_by_state[state_id]
        trace_trades.append({"trade": trade, "pid_cycles_recorded": len(rows), "pid_cycles": rows})

    OUT_DIR.mkdir(exist_ok=True)
    payload = {
        "source": "real Revision2ExternalEngineOrchestrator replay; no simulated PID inputs",
        "symbol": SYMBOL, "window": f"{frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]}",
        "bars": len(frame), "elapsed_seconds": elapsed,
        "report_summary": {k: report[k] for k in ("completed_trades", "net_pnl", "gross_pnl", "fills", "id_approvals")},
        "trades": trace_trades,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2, default=str))
    lines = ["REAL BOX-6 PID REPLAY", f"bars={len(frame)} trades={len(trace_trades)} elapsed={elapsed:.1f}s",
             f"net_pnl={report['net_pnl']:.2f}", ""]
    for n, item in enumerate(trace_trades, 1):
        trade, rows = item["trade"], item["pid_cycles"]
        lines.append(f"Trade {n}: {trade['side']} entry={trade['entry_timestamp']} @ {trade['entry_price']:.4f}; "
                     f"exit={trade['exit_timestamp']} @ {trade['exit_price']:.4f}; reason={trade['reason']}; PID cycles={len(rows)}")
        for row in rows:
            lines.append("  {timestamp}: PA={inputs[pa_exit_confidence]:.6f}->{pa_pid[output]:+.6f}; "
                         "studies={inputs[chart_studies_confidence]:.6f}->{studies_pid[output]:+.6f}; "
                         "stop={state[stop_before]:.4f}->{state[stop_after]:.4f}; "
                         "streaks=({state[pa_saturation_streak]},{state[studies_saturation_streak]})".format(**row))
    TEXT_PATH.write_text("\n".join(lines) + "\n")
    print(f"Real replay complete: {len(trace_trades)} trades, {sum(len(x['pid_cycles']) for x in trace_trades)} actual PID cycles")
    print(f"JSON ledger: {JSON_PATH}\nReadable ledger: {TEXT_PATH}")


if __name__ == "__main__":
    main()
