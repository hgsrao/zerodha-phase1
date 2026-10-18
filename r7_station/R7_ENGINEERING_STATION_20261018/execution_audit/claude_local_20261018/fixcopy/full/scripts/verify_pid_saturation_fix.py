#!/usr/bin/env python3
"""Verifies the entry-PID saturation fix (revision2_external/pid_controller.py
_entry_setpoint) against a real, non-simulated INFY run -- not just the unit
tests -- and checks the exit PID the same way, since it shares the same
design smell (a fixed absolute confidence target) even though it wasn't
proven to structurally guarantee saturation the way the entry PID was.
Hooks SimplePIDModelPredictiveControlBox._get_pid the same way the earlier
Box 6 tracer did this session (proxy wrapper, since reassigning
pid.__call__ directly is silently never invoked -- __call__ is looked up on
the class) to record every real adjustment the run actually produces, then
reports what fraction sit pinned at the clamp for each role.

Before the entry-PID fix, the two real calls traced by hand this session
(2023-07-13 15:15 and 15:16) gave entry_adjustment = -0.09971 and -0.100 --
both at/within 0.0003 of -pid_integral_max_clamp. The same two calls gave
exit_adjustment = -0.0643 and -0.0752 -- leaning the same direction but not
pinned. This script checks the full real 6-month window for both roles.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)
OUT_PATH = OUTPUT_DIR / "pid_saturation_fix_verification.json"


def _summarize(calls, clamp):
    if not calls:
        return {"total_calls": 0}
    pinned = [a for a in calls if abs(abs(a) - clamp) < 1e-6]
    return {
        "total_calls": len(calls),
        "min": min(calls), "max": max(calls),
        "mean": sum(calls) / len(calls),
        "count_pinned_at_clamp": len(pinned),
        "fraction_pinned_at_clamp": len(pinned) / len(calls),
        "distinct_values_rounded_4dp": len(set(round(a, 4) for a in calls)),
    }


def main():
    symbol = "INFY"
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv(symbol)
    start = frame["timestamp"].iloc[0]
    cutoff = start + pd.DateOffset(months=6)
    frame = frame[frame["timestamp"] < cutoff].reset_index(drop=True)

    registry = CanonicalParameterRegistry()
    clamp = float(registry.params["pid_integral_max_clamp"].default)
    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)

    calls_by_role = {"entry": [], "exit": []}

    class _TracedPID:
        def __init__(self, pid, role):
            object.__setattr__(self, "_pid", pid)
            object.__setattr__(self, "_role", role)

        def __call__(self, input_, dt=None):
            output = self._pid(input_, dt=dt)
            calls_by_role[self._role].append(output)
            return output

        def __getattr__(self, name):
            return getattr(self._pid, name)

        def __setattr__(self, name, value):
            setattr(self._pid, name, value)

    wrapped_cache = {}
    orig_get_pid = orch.mpc._get_pid

    def get_pid_traced(store, symbol_, kp, ki, kd, target, clamp):
        pid = orig_get_pid(store, symbol_, kp, ki, kd, target, clamp)
        role = "entry" if store is orch.mpc._entry_pids else "exit"
        key = id(store), symbol_
        if key not in wrapped_cache:
            wrapped_cache[key] = _TracedPID(pid, role)
        return wrapped_cache[key]
    orch.mpc._get_pid = get_pid_traced

    t0 = time.time()
    report = orch.run({symbol: frame}, warmup=60)
    elapsed = time.time() - t0

    result = {
        "symbol": symbol, "window_months": 6, "elapsed_seconds": elapsed,
        "entry_pid": _summarize(calls_by_role["entry"], clamp),
        "exit_pid": _summarize(calls_by_role["exit"], clamp),
        "completed_trades": report["completed_trades"], "net_pnl": report["net_pnl"],
    }
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
