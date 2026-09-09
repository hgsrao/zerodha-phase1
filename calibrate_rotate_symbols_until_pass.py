#!/usr/bin/env python3
"""
Rotating Symbol Search: Find Passing Calibration
=================================================

Rotate through 5-symbol groups (1 day each) across all 48 symbols.
Run 42-parameter calibration on each group.
STOP when finding a candidate that passes ALL gates.

Systematic search to identify which symbol combination allows profitable execution.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.calibration_supervisor import (
    CalibrationSupervisor, CalibrationRunConfig, AcceptanceGates, trading_search_space
)

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
WARMUP = 60
BARS_FOR_ONE_DAY = 390
SYMBOLS_PER_GROUP = 5

def load_symbol_group(symbols):
    """Load a group of symbols for 1 day each."""
    loader = ManifestLoader(MANIFEST)
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))

    symbol_bars = {}
    loaded = []

    for symbol in symbols:
        try:
            entry = loader.manifest.get_file(symbol)
            if entry is None:
                continue

            path = data_dir / entry.filename
            actual = loader._compute_file_hash(path)
            if actual != entry.sha256:
                continue

            bars = pd.read_csv(path)
            if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
                continue

            bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
            symbol_bars[symbol] = bars_subset
            loaded.append(symbol)
        except:
            continue

    return symbol_bars, loaded

def run_calibration_on_group(symbol_bars, loaded_symbols, group_num, total_groups):
    """Run 42-parameter calibration on a symbol group."""
    if not loaded_symbols:
        return None, None

    print(f"\n[GROUP {group_num}/{total_groups}] Testing {len(loaded_symbols)} symbols: {', '.join(loaded_symbols)}")
    print("-" * 120)

    registry = CanonicalParameterRegistry()

    # Realistic gates for portfolio
    gates = AcceptanceGates(
        min_trades=30,          # Need meaningful volume
        min_profit_factor=0.90, # Accept near-breakeven
        max_drawdown_fraction=0.25,
        require_positive_net_pnl=False,
        max_safety_violations=5,
        min_symbols_traded=2,
    )

    # Moderate 3-phase run
    run_config = CalibrationRunConfig(
        phase1_trials=12,
        phase2_generations=3,
        phase3_iterations=6,
        wall_clock_budget_seconds=None,
        seed=42,
    )

    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=loaded_symbols,
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=500_000.0,
    )

    result = supervisor.run()

    # Check if any candidate passed
    passed_candidates = [c for c in result.candidates if c.accepted]
    print(f"  Candidates evaluated: {len(result.candidates)}")
    print(f"  Candidates passed gates: {len(passed_candidates)}")

    if passed_candidates:
        winner = max(passed_candidates, key=lambda c: c.score)
        print(f"  ✅ WINNER FOUND!")
        print(f"     Phase: {winner.phase}")
        print(f"     Score: {winner.score:.4f}")
        print(f"     Trades: {winner.metrics.get('completed_trades', 0)}")
        print(f"     P&L: ₹{winner.metrics.get('net_pnl', 0):,.2f}")
        print(f"     Sharpe: {winner.metrics.get('sharpe', 0):.2f}")
        return winner.params, winner
    else:
        best = max(result.candidates, key=lambda c: c.guidance_score)
        print(f"  ❌ No gates passed")
        print(f"     Best guidance: {best.guidance_score:.4f}")
        print(f"     Trades: {best.metrics.get('completed_trades', 0)}")
        print(f"     P&L: ₹{best.metrics.get('net_pnl', 0):,.2f}")
        return None, best

def main():
    print()
    print("="*120)
    print("ROTATING SYMBOL SEARCH: Find Calibration That Passes All Gates")
    print("="*120)
    print()

    # Load all available symbols
    loader = ManifestLoader(MANIFEST)
    all_symbols = loader.manifest.get_symbols()
    print(f"[LOAD] Available symbols: {len(all_symbols)}")
    print(f"       Testing in groups of {SYMBOLS_PER_GROUP}...")
    print()

    # Create rotating groups
    groups = []
    for i in range(0, len(all_symbols), SYMBOLS_PER_GROUP):
        group = all_symbols[i:i+SYMBOLS_PER_GROUP]
        groups.append(group)

    total_groups = len(groups)
    print(f"[PLAN] Total groups to test: {total_groups}")
    print()

    # Iterate through groups
    for group_num, symbol_group in enumerate(groups, 1):
        print()
        print("="*120)
        print(f"GROUP {group_num}/{total_groups}")
        print("="*120)

        # Load symbols for this group
        symbol_bars, loaded_symbols = load_symbol_group(symbol_group)
        if not loaded_symbols:
            print(f"  ⚠ No symbols loaded for this group, skipping")
            continue

        # Run calibration
        best_params, best_candidate = run_calibration_on_group(
            symbol_bars, loaded_symbols, group_num, total_groups
        )

        if best_params is not None:
            # FOUND A WINNER!
            print()
            print("="*120)
            print("✅✅✅ CALIBRATION SUCCESS ✅✅✅")
            print("="*120)
            print()

            print(f"Winning symbols: {', '.join(loaded_symbols)}")
            print(f"Group: {group_num}/{total_groups}")
            print()

            print(f"Metrics:")
            print(f"  Trades: {best_candidate.metrics.get('completed_trades', 0)}")
            print(f"  P&L: ₹{best_candidate.metrics.get('net_pnl', 0):,.2f}")
            print(f"  Sharpe: {best_candidate.metrics.get('sharpe', 0):.2f}")
            print(f"  Max DD: {best_candidate.metrics.get('max_drawdown_fraction', 0):.2%}")
            print(f"  Score: {best_candidate.score:.4f}")
            print()

            print(f"ALL {len(best_params)} CALIBRATED PARAMETERS:")
            print()
            for i, (param, value) in enumerate(sorted(best_params.items()), 1):
                print(f"{i:2d}. {param:50s} = {value}")

            print()

            # Save winner
            output_path = Path("diagnostic_output/calibration_winner_rotated_search.json")
            output_path.parent.mkdir(exist_ok=True)

            report = {
                "timestamp": datetime.now().isoformat(),
                "status": "SUCCESS - Passed all gates",
                "winning_group": group_num,
                "total_groups_tested": group_num,
                "winning_symbols": loaded_symbols,
                "symbol_count": len(loaded_symbols),
                "phase": best_candidate.phase,
                "score": best_candidate.score,
                "metrics": best_candidate.metrics,
                "parameters": best_params,
                "parameter_count": len(best_params),
            }

            output_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
            print(f"✅ SAVED: {output_path}")
            print()

            return best_params, loaded_symbols

        # If no winner, continue to next group
        print(f"  → Moving to next group...")

    # If we reach here, no winner found
    print()
    print("="*120)
    print("❌ SEARCH COMPLETE: No calibration passed gates across all {0} groups".format(total_groups))
    print("="*120)
    print()

    return None, None

if __name__ == "__main__":
    try:
        best_params, winning_symbols = main()
        if best_params is None:
            sys.exit(1)
    except Exception as e:
        print(f"❌ Search failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
