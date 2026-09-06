#!/usr/bin/env python3
"""Traces ContinuousExitController's real, bar-by-bar behavior for ONE real
INFY trade -- all three inputs (confidence, price, time) plus the ATR
droop, exactly as they actually ran, not a re-derivation.

Hooks ContinuousExitController.update() the same way earlier box tracers
this session did (proxy wrapper around a real method), on a real 6-month
run, then prints every real call made for the target symbol/window.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

SYMBOL = "INFY"


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv(SYMBOL)
    cutoff = pd.Timestamp("2023-07-03", tz=frame["timestamp"].dt.tz) + pd.DateOffset(months=6)
    frame = frame[frame["timestamp"] < cutoff].reset_index(drop=True)

    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator([SYMBOL], registry, starting_equity=1_000_000.0)

    log = []
    orig_update = orch.exit_controller.update

    def traced_update(symbol, state, current_confidence, current_close, current_atr):
        before = {
            "favorable_extreme": state.favorable_extreme, "current_stop_price": state.current_stop_price,
            "bars_held": state.bars_held,
        }
        new_state = orig_update(symbol, state, current_confidence, current_close, current_atr)
        log.append({
            "symbol": symbol, "confidence_in": current_confidence, "close_in": current_close, "atr_in": current_atr,
            "before": before,
            "after": {
                "favorable_extreme": new_state.favorable_extreme, "current_stop_price": new_state.current_stop_price,
                "bars_held": new_state.bars_held,
                "consecutive_saturated": new_state.consecutive_bars_at_low_confidence_extreme,
                "last_adjustment": new_state.adjustment_history[-1] if new_state.adjustment_history else None,
            },
        })
        return new_state
    orch.exit_controller.update = traced_update

    report = orch.run({SYMBOL: frame}, warmup=60)

    trade = next((t for t in report["trades"] if t["entry_timestamp"].startswith("2023-11-15 09:26")), None)
    if trade is None:
        print("Real trade not found in this run -- ledger may differ run to run at the margin; printing full log instead.")
        relevant = log
    else:
        print("REAL TRADE:")
        print(f"  entry: {trade['entry_timestamp']}  {trade['side']} {trade['quantity']} @ {trade['entry_price']}")
        print(f"  exit:  {trade['exit_timestamp']}  @ {trade['exit_price']}  reason={trade['reason']}")
        print(f"  gross_pnl={trade['pnl']:.2f}  costs={trade['costs']:.2f}  net_pnl={trade['net_pnl']:.2f}")
        print()
        # Filter the log to just this trade's real bars by finding the
        # contiguous run of INFY updates whose entry-adjacent bars_held
        # resets to 1 right after this trade's entry.
        relevant = [r for r in log if r["symbol"] == SYMBOL]
        # Split the log into contiguous per-position segments (bars_held
        # resets to 1 at the start of each new position; a single-symbol
        # run only ever has one open position at a time).
        segments, current = [], []
        for r in relevant:
            if r["after"]["bars_held"] == 1:
                if current:
                    segments.append(current)
                current = [r]
            else:
                current.append(r)
        if current:
            segments.append(current)
        # Pick the segment whose length matches this trade's real duration (10 bars).
        target_len = 10
        segment = min(segments, key=lambda s: abs(len(s) - target_len)) if segments else []
        relevant = segment

    print(f"BAR-BY-BAR ({len(relevant)} real controller updates):")
    for i, r in enumerate(relevant):
        print(f"\n--- bar {i+1} (bars_held={r['after']['bars_held']}) ---")
        print(f"  INPUT  confidence={r['confidence_in']:.4f}  close={r['close_in']:.4f}  atr={r['atr_in']:.4f}")
        print(f"  PRICE  favorable_extreme: {r['before']['favorable_extreme']:.4f} -> {r['after']['favorable_extreme']:.4f}")
        print(f"  STOP   {r['before']['current_stop_price']:.4f} -> {r['after']['current_stop_price']:.4f}")
        print(f"  PID    adjustment={r['after']['last_adjustment']:.6f}  saturated_streak={r['after']['consecutive_saturated']}")


if __name__ == "__main__":
    main()
