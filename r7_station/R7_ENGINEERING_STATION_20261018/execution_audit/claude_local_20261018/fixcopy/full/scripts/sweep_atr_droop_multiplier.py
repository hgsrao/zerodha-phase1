#!/usr/bin/env python3
"""Sweep trailing_stop_atr_mult [3.0, 4.0, 4.5, 5.0] on real INFY 6-month
to measure P&L, win rate, and (critically) how many times saturation_exit_pa
and saturation_exit_studies actually fire. The goal: loosen the ATR droop so
the PID tracks have time to accumulate error before the mechanical stop kills
the trade.

Real question being answered: does loosening the droop from 3.0 to 4.5-5.0
allow saturation_exit to fire at all on real 1-minute data, or is the race
condition still insurmountable?
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard  # Must be first import before numpy/pandas
import pandas as pd
from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from collections import Counter

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"
OUTPUT_DIR.mkdir(exist_ok=True)

def run_backtest(atr_mult: float):
    """Run a full 6-month INFY backtest with the given trailing_stop_atr_mult."""
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("INFY")
    start = frame["timestamp"].iloc[0]
    cutoff = start + pd.DateOffset(months=6)
    frame = frame[frame["timestamp"] < cutoff].reset_index(drop=True)

    registry = CanonicalParameterRegistry()
    # Override the single parameter being swept.
    registry.params["trailing_stop_atr_mult"].default = atr_mult

    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
    report = orch.run({"INFY": frame}, warmup=60)

    trades = report["trades"]

    # Count saturation exits.
    reason_counts = Counter(t["reason"] for t in trades)
    sat_pa = reason_counts.get("saturation_exit_pa", 0)
    sat_studies = reason_counts.get("saturation_exit_studies", 0)

    # Compute win rate.
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    total = len(trades)
    win_rate = (100 * wins / total) if total > 0 else 0.0

    return {
        "trailing_stop_atr_mult": atr_mult,
        "completed_trades": report["completed_trades"],
        "net_pnl": report["net_pnl"],
        "gross_pnl": report["gross_pnl"],
        "win_rate_pct": win_rate,
        "id_approvals": report["id_approvals"],
        "saturation_exit_pa_count": sat_pa,
        "saturation_exit_studies_count": sat_studies,
        "total_saturation_exits": sat_pa + sat_studies,
        "reason_breakdown": dict(reason_counts),
    }

def main():
    multipliers = [3.0, 4.0, 4.5, 5.0]
    results = []

    print("=" * 100)
    print("SWEEP: trailing_stop_atr_mult on real INFY 6-month (2023-07-03 to 2024-01-02)")
    print("=" * 100)
    print()

    for mult in multipliers:
        print(f"Running atr_mult={mult}...")
        result = run_backtest(mult)
        results.append(result)

        print(f"  Completed trades:     {result['completed_trades']}")
        print(f"  Net P&L:              ₹{result['net_pnl']:,.2f}")
        print(f"  Win rate:             {result['win_rate_pct']:.2f}%")
        print(f"  ID approvals:         {result['id_approvals']}")
        print(f"  Saturation exits:     {result['total_saturation_exits']} "
              f"(PA: {result['saturation_exit_pa_count']}, Studies: {result['saturation_exit_studies_count']})")
        print(f"  Reason breakdown:     {result['reason_breakdown']}")
        print()

    # Summary table.
    print("=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    print(f"{'ATR Mult':<12} {'Trades':<12} {'Net P&L':<18} {'Win %':<10} {'Sat Exits':<12} {'PA':<6} {'Studies':<8}")
    print("-" * 100)
    for r in results:
        print(
            f"{r['trailing_stop_atr_mult']:<12.1f} "
            f"{r['completed_trades']:<12} "
            f"₹{r['net_pnl']:>15,.2f} "
            f"{r['win_rate_pct']:>8.2f}% "
            f"{r['total_saturation_exits']:<12} "
            f"{r['saturation_exit_pa_count']:<6} "
            f"{r['saturation_exit_studies_count']:<8}"
        )
    print()

    # Highlight: did loosening the droop actually let saturation exits fire?
    baseline_sat = results[0]["total_saturation_exits"]
    max_sat = max(r["total_saturation_exits"] for r in results)
    if max_sat > baseline_sat:
        idx = next(i for i, r in enumerate(results) if r["total_saturation_exits"] == max_sat)
        print(f"✓ BREAKTHROUGH: Saturation exits increased from {baseline_sat} (at 3.0x) "
              f"to {max_sat} (at {results[idx]['trailing_stop_atr_mult']}x)")
    elif max_sat == 0:
        print(f"✗ STILL BLOCKED: Saturation exits remain 0 across all multipliers. "
              f"The ATR race condition persists or PID tuning needs adjustment.")
    else:
        print(f"⚠ PARTIAL: Saturation exits increased to {max_sat}, but still below expectations.")
    print()

    # Save full results.
    out_file = OUTPUT_DIR / "atr_droop_sweep_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Full results saved to {out_file}")

if __name__ == "__main__":
    main()
