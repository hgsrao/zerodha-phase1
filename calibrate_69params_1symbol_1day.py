#!/usr/bin/env python3
"""
Full 69-Parameter Calibration: 1 Symbol × 1 Day
================================================

Calibrate ALL 69 calibratable parameters on SUNPHARMA 1-day intraday data.

Uses full 3-phase Bayesian optimization (Random + TPE → CMA-ES → Fine-tune).
Fast iteration for testing the full parameter space.
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
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_DAY = 390

def load_symbol_1day():
    """Load one symbol for 1 day."""
    print(f"[LOAD] Loading {SYMBOL}, 1 day...")

    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    if entry is None:
        raise RuntimeError(f"{SYMBOL} not in manifest")

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename

    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"Hash mismatch")

    bars = pd.read_csv(path)
    if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
        raise RuntimeError(f"Insufficient bars")

    bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
    print(f"  ✓ Loaded {len(bars_subset)} bars")
    print(f"  ✓ Date range: {bars_subset.iloc[WARMUP]['timestamp']} to {bars_subset.iloc[-1]['timestamp']}")

    return {SYMBOL: bars_subset}

def run():
    """Run full 69-parameter calibration on single symbol."""

    print()
    print("="*120)
    print("FULL 69-PARAMETER CALIBRATION: 1 SYMBOL × 1 DAY")
    print("="*120)
    print()

    # Load data
    symbol_bars = load_symbol_1day()
    symbols = [SYMBOL]

    print()

    # Initialize registry
    print("[INIT] Initializing full-parameter Bayesian calibration...")
    registry = CanonicalParameterRegistry()

    # Get full trading search space (all 69 calibratable params)
    search_space = trading_search_space(registry)
    print(f"  ✓ Calibratable parameters: {len(search_space.names)}")
    print(f"  ✓ Symbol: {SYMBOL}")
    print()

    # Loose acceptance gates for single symbol, 1 day
    gates = AcceptanceGates(
        min_trades=1,            # Any trade counts
        min_profit_factor=0.80,  # Accept near-breakeven
        max_drawdown_fraction=0.30,
        require_positive_net_pnl=False,
        max_safety_violations=10,
    )

    # Moderate 3-phase run for full parameter space
    run_config = CalibrationRunConfig(
        phase1_trials=12,       # Exploration
        phase2_generations=3,   # Refinement
        phase3_iterations=6,    # Polish
        wall_clock_budget_seconds=None,
        seed=42,
    )

    print(f"Search space: {len(search_space.names)} parameters")
    print()
    print(f"  Phase 1: {run_config.phase1_trials} trials (Random + TPE)")
    print(f"  Phase 2: {run_config.phase2_generations} generations (CMA-ES)")
    print(f"  Phase 3: {run_config.phase3_iterations} iterations (Fine-tune)")
    print()

    print(f"Acceptance gates:")
    print(f"  Min trades: {gates.min_trades}")
    print(f"  Min profit factor: {gates.min_profit_factor}")
    print(f"  Max drawdown: {gates.max_drawdown_fraction:.0%}")
    print()

    # Run calibration
    print("[RUN] Starting 3-phase Bayesian optimization on 69 parameters...")
    print()

    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=symbols,
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=100_000.0,
    )

    result = supervisor.run()

    # Report results
    print()
    print("="*120)
    print("CALIBRATION RESULTS (69 PARAMETERS)")
    print("="*120)
    print()

    print(f"Total candidates evaluated: {len(result.candidates)}")
    print(f"Candidates passed gates: {len([c for c in result.candidates if c.accepted])}")
    print(f"Stopped reason: {result.stopped_reason}")
    print()

    if result.best_params is None:
        print("⚠ NO CANDIDATE PASSED acceptance gates")
        print("\nTop 5 candidates by guidance score:")
        for i, c in enumerate(sorted(result.candidates, key=lambda x: -x.guidance_score)[:5], 1):
            print(f"\n  Candidate {i} (Phase {c.phase}):")
            print(f"    Guidance score: {c.guidance_score:.4f}")
            print(f"    Trades: {c.metrics.get('completed_trades', 0)}")
            print(f"    P&L: ₹{c.metrics.get('net_pnl', 0):,.2f}")
            print(f"    Sharpe: {c.metrics.get('sharpe', 0):.2f}")
            if c.reject_reasons:
                print(f"    Why rejected: {c.reject_reasons[0]}")
        sys.exit(1)

    print(f"✅ WINNER FOUND")
    winner_phase = [c for c in result.candidates if c.score == result.best_score][0].phase
    print(f"   Phase: {winner_phase}")
    print(f"   Score: {result.best_score:.4f}")
    print(f"   Trades: {result.best_report.get('completed_trades', 0)}")
    print(f"   P&L: ₹{result.best_report.get('net_pnl', 0):,.2f}")
    print(f"   Sharpe: {result.best_report.get('sharpe', 0):.2f}")
    print(f"   Max DD: {result.best_report.get('max_drawdown_fraction', 0):.2%}")
    print()

    # Show all 69 calibrated parameters
    print("="*120)
    print("ALL 69 CALIBRATED PARAMETERS")
    print("="*120)
    print()

    if result.best_params:
        print(f"Total parameters calibrated: {len(result.best_params)}\n")

        for i, (param, value) in enumerate(sorted(result.best_params.items()), 1):
            print(f"{i:2d}. {param:50s} = {value}")

    print()

    # Save detailed report
    output_path = Path("diagnostic_output/calibration_69params_1symbol_1day.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": sum(c.elapsed_seconds for c in result.candidates),
        "symbol": SYMBOL,
        "total_parameters_calibrated": len(result.best_params) if result.best_params else 0,
        "candidates_evaluated": len(result.candidates),
        "candidates_accepted": len([c for c in result.candidates if c.accepted]),
        "best_found": result.best_params is not None,
        "best_params": result.best_params,
        "best_score": result.best_score,
        "best_report": result.best_report,
        "stopped_reason": result.stopped_reason,
        "phase_breakdown": {
            "phase1": len([c for c in result.candidates if c.phase.startswith("phase1")]),
            "phase2": len([c for c in result.candidates if c.phase.startswith("phase2")]),
            "phase3": len([c for c in result.candidates if c.phase.startswith("phase3")]),
        }
    }

    output_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"✅ Report saved: {output_path}")
    print()

    return result

if __name__ == "__main__":
    try:
        result = run()
    except Exception as e:
        print(f"❌ Calibration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
