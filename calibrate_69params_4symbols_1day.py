#!/usr/bin/env python3
"""
Full 69-Parameter Calibration: 3-4 Symbols × 1 Day
====================================================

Calibrate ALL 42 calibratable parameters on 3-4 symbol portfolio (1 day each).
Expected trades: 36-48 (better signal than 1 symbol).

Uses full 3-phase Bayesian optimization.
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
SYMBOLS_TO_USE = ["SUNPHARMA", "INFY", "RELIANCE", "HDFC"]  # 4 symbols
WARMUP = 60
BARS_FOR_ONE_DAY = 390

def load_symbols_1day():
    """Load 3-4 symbols for 1 day each."""
    print(f"[LOAD] Loading {len(SYMBOLS_TO_USE)} symbols, 1 day each...")

    loader = ManifestLoader(MANIFEST)
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))

    symbol_bars = {}
    loaded = []

    for symbol in SYMBOLS_TO_USE:
        entry = loader.manifest.get_file(symbol)
        if entry is None:
            print(f"  ⚠ {symbol} not found, skipping")
            continue

        path = data_dir / entry.filename

        try:
            actual = loader._compute_file_hash(path)
            if actual != entry.sha256:
                print(f"  ⚠ {symbol} hash mismatch, skipping")
                continue

            bars = pd.read_csv(path)
            if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
                print(f"  ⚠ {symbol} insufficient bars, skipping")
                continue

            bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
            symbol_bars[symbol] = bars_subset
            loaded.append(symbol)
            print(f"  ✓ {symbol} loaded")
        except Exception as e:
            print(f"  ⚠ {symbol} error: {e}")
            continue

    if not loaded:
        raise RuntimeError("No symbols loaded")

    print(f"\n  Total loaded: {len(loaded)} symbols")
    return symbol_bars, loaded

def run():
    """Run full 69-parameter calibration on 3-4 symbol portfolio."""

    print()
    print("="*120)
    print("FULL 69-PARAMETER CALIBRATION: 3-4 SYMBOLS × 1 DAY")
    print("="*120)
    print()

    # Load data
    symbol_bars, symbols = load_symbols_1day()

    print()

    # Initialize registry
    print("[INIT] Initializing full-parameter Bayesian calibration...")
    registry = CanonicalParameterRegistry()

    # Get full trading search space
    search_space = trading_search_space(registry)
    print(f"  ✓ Calibratable parameters: {len(search_space.names)}")
    print(f"  ✓ Symbols: {len(symbols)}")
    print()

    # Realistic acceptance gates for 3-4 symbols
    gates = AcceptanceGates(
        min_trades=20,           # Need at least 20 trades
        min_profit_factor=0.85,  # Accepting near-breakeven
        max_drawdown_fraction=0.25,
        require_positive_net_pnl=False,
        max_safety_violations=5,
        min_symbols_traded=2,    # At least 2 of 4 symbols
    )

    # Full 3-phase run
    run_config = CalibrationRunConfig(
        phase1_trials=15,       # Good exploration
        phase2_generations=4,   # Refinement
        phase3_iterations=8,    # Polish
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
    print(f"  Min symbols: {gates.min_symbols_traded}")
    print()

    # Run calibration
    print("[RUN] Starting 3-phase Bayesian optimization on 42 parameters...")
    print()

    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=symbols,
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=500_000.0,  # ₹5L for 4-symbol portfolio
    )

    result = supervisor.run()

    # Report results
    print()
    print("="*120)
    print("CALIBRATION RESULTS (42 PARAMETERS)")
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
            print(f"    Symbols: {c.metrics.get('symbols_traded', 0)}")
            if c.reject_reasons:
                print(f"    Why rejected: {c.reject_reasons[0]}")

        # Still show best-by-guidance even without gate pass
        print("\n" + "="*120)
        print("BEST BY GUIDANCE SCORE (Even Without Gate Pass)")
        print("="*120)
        best = max(result.candidates, key=lambda c: c.guidance_score)
    else:
        best = result.best_params
        print(f"✅ WINNER FOUND")
        winner_phase = [c for c in result.candidates if c.score == result.best_score][0].phase
        print(f"   Phase: {winner_phase}")
        print(f"   Score: {result.best_score:.4f}")
        print(f"   Trades: {result.best_report.get('completed_trades', 0)}")
        print(f"   P&L: ₹{result.best_report.get('net_pnl', 0):,.2f}")
        print(f"   Sharpe: {result.best_report.get('sharpe', 0):.2f}")
        print(f"   Max DD: {result.best_report.get('max_drawdown_fraction', 0):.2%}")
        print(f"   Symbols: {result.best_report.get('symbols_traded', 0)}")
        print()

    if result.best_params is None:
        best = max(result.candidates, key=lambda c: c.guidance_score)
        best_params = best.params
        best_metrics = best.metrics
        print()
        print(f"Phase: {best.phase}")
        print(f"Trades: {best_metrics.get('completed_trades', 0)}")
        print(f"P&L: ₹{best_metrics.get('net_pnl', 0):,.2f}")
        print()
    else:
        best_params = result.best_params
        best_metrics = result.best_report

    # Show all 42 calibrated parameters
    print("="*120)
    print(f"ALL {len(best_params)} CALIBRATED PARAMETERS")
    print("="*120)
    print()

    for i, (param, value) in enumerate(sorted(best_params.items()), 1):
        print(f"{i:2d}. {param:50s} = {value}")

    print()

    # Save detailed report
    output_path = Path("diagnostic_output/calibration_42params_4symbols_1day.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "symbols": symbols,
        "symbol_count": len(symbols),
        "total_parameters_calibrated": len(best_params),
        "candidates_evaluated": len(result.candidates),
        "candidates_accepted": len([c for c in result.candidates if c.accepted]),
        "best_found": result.best_params is not None,
        "best_params": best_params,
        "best_score": result.best_score if result.best_params else float('-inf'),
        "best_report": best_metrics if result.best_params else best.metrics,
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
