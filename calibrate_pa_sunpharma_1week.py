#!/usr/bin/env python3
"""
PA Calibration: SUNPHARMA × 1 Week
====================================

Uses Revision2 CalibrationSupervisor (Phase 1: Random/TPE, Phase 2: CMA-ES, Phase 3: Fine-tune).
Target: Calibrate all 18 PA parameters to achieve profitable execution.

Data: 1 week of SUNPHARMA intraday 1-minute bars (~5 trading days = ~60-75 trades).
Phases: All 3 (enough data for meaningful optimization).
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.calibration_supervisor import (
    CalibrationSupervisor, CalibrationRunConfig, AcceptanceGates
)

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_WEEK = 1950  # ~5 trading days × 390 bars/day

def load_sunpharma_1week():
    """Load SUNPHARMA for exactly 1 week after warmup."""
    print(f"[LOAD] Loading {SYMBOL} intraday data (1 week)...")

    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    if entry is None:
        raise RuntimeError(f"{SYMBOL} not in manifest")

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename

    # Verify hash
    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"{SYMBOL} hash mismatch")

    # Load all bars
    bars = pd.read_csv(path)
    print(f"  ✓ File hash verified: {actual[:16]}...")
    print(f"  ✓ Total bars available: {len(bars):,}")

    # Take only warmup + 1 week
    if len(bars) < WARMUP + BARS_FOR_ONE_WEEK:
        raise RuntimeError(f"Insufficient bars: {len(bars)} < {WARMUP + BARS_FOR_ONE_WEEK}")

    bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_WEEK].copy()
    print(f"  ✓ Using {len(bars_subset):,} bars ({WARMUP} warmup + {BARS_FOR_ONE_WEEK} active)")
    print(f"  ✓ Date range: {bars_subset.iloc[WARMUP]['timestamp']} to {bars_subset.iloc[-1]['timestamp']}")

    return {SYMBOL: bars_subset}

def run():
    """Run SUNPHARMA 1-week calibration."""

    print()
    print("="*100)
    print(f"PA CALIBRATION: {SYMBOL} × 1 WEEK (INTRADAY)")
    print("="*100)
    print()

    # Load data
    symbol_bars = load_sunpharma_1week()
    symbols = [SYMBOL]

    print()

    # Initialize registry and calibration config
    print("[INIT] Initializing 3-phase Bayesian calibration...")
    registry = CanonicalParameterRegistry()

    # Smoke-test gates (loose) for proof-of-concept
    gates = AcceptanceGates(
        min_trades=10,           # At least 10 trades to qualify
        min_profit_factor=0.95,  # Close to breakeven acceptable (profit ≥ losses)
        max_drawdown_fraction=0.25,  # Reasonable drawdown limit
        require_positive_net_pnl=False,  # Loss acceptable if profit factor OK
        max_safety_violations=5,
    )

    # Full 3-phase run config for 1-week data
    run_config = CalibrationRunConfig(
        phase1_trials=12,      # 12 random trials (Random Search + TPE) - good exploration
        phase2_generations=4,  # 4 CMA-ES generations - meaningful refinement
        phase3_iterations=8,   # 8 fine-tuning iterations - polish
        wall_clock_budget_seconds=None,  # No wall-clock limit, just finish all phases
        seed=42,
    )

    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256[:16]}...")
    print(f"  ✓ Symbol: {SYMBOL}")
    print(f"  ✓ Data: 1 week (~5 trading days, ~60-75 expected trades)")
    print()
    print(f"  Phase 1: {run_config.phase1_trials} trials (Random + TPE) - explore")
    print(f"  Phase 2: {run_config.phase2_generations} generations (CMA-ES) - refine")
    print(f"  Phase 3: {run_config.phase3_iterations} iterations (Fine-tune) - polish")
    print()

    # Initialize supervisor
    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=symbols,
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=100_000.0,
    )

    # Run calibration
    print("[RUN] Starting 3-phase Bayesian optimization...")
    print("      Phase 1: Random + TPE sampling")
    print("      Phase 2: CMA-ES optimization")
    print("      Phase 3: Local fine-tuning")
    print()

    result = supervisor.run()

    # Report results
    print()
    print("="*100)
    print("CALIBRATION RESULTS")
    print("="*100)
    print()

    print(f"Total candidates evaluated: {len(result.candidates)}")
    print(f"Candidates passed gates: {len([c for c in result.candidates if c.accepted])}")
    print(f"Stopped reason: {result.stopped_reason}")
    print()

    if result.best_params is None:
        print("⚠ NO CANDIDATE PASSED acceptance gates")
        print("\nTop 5 candidates by guidance score (best attempts):")
        for i, c in enumerate(sorted(result.candidates, key=lambda x: -x.guidance_score)[:5], 1):
            print(f"\n  Candidate {i} (Phase {c.phase}):")
            print(f"    Guidance score: {c.guidance_score:.4f}")
            print(f"    Trades: {c.metrics.get('completed_trades', 0)}")
            print(f"    P&L: ₹{c.metrics.get('net_pnl', 0):,.2f}")
            print(f"    Sharpe: {c.metrics.get('sharpe', 0):.2f}")
            print(f"    Max DD: {c.metrics.get('max_drawdown_fraction', 0):.2%}")
            if c.reject_reasons:
                print(f"    Why rejected: {', '.join(c.reject_reasons[:2])}")
        sys.exit(1)

    print(f"✅ WINNER FOUND")
    print(f"   Phase: {[c for c in result.candidates if c.score == result.best_score][0].phase}")
    print(f"   Score: {result.best_score:.4f}")
    print(f"   Trades: {result.best_report.get('completed_trades', 0)}")
    print(f"   P&L: ₹{result.best_report.get('net_pnl', 0):,.2f}")
    print(f"   Sharpe: {result.best_report.get('sharpe', 0):.2f}")
    print(f"   Max drawdown: {result.best_report.get('max_drawdown_fraction', 0):.2%}")
    print()

    # Display all calibrated parameters
    print("="*100)
    print("CALIBRATED PA PARAMETERS (ALL 18)")
    print("="*100)
    print()

    pa_params = {
        'momentum_calculation_period': 'Period for momentum calc',
        'vwap_calculation_period': 'Period for VWAP calc',
        'atr_calculation_period': 'Period for ATR calc',
        'base_dp_dt_multiplier': 'Price momentum multiplier',
        'base_dv_dt_multiplier': 'Volume momentum multiplier',
        'momentum_weight': 'Weight of momentum component',
        'vwap_weight': 'Weight of VWAP component',
        'volatility_weight': 'Weight of volatility component',
        'confirmation_2bar_weight': 'Weight of 2-bar confirmation',
        'green_threshold': 'High confidence threshold',
        'amber_threshold_lower': 'Medium-low confidence threshold',
        'red_threshold': 'Low confidence threshold',
        'entry_signal_smoothing_window': 'Smoothing window for entry signals',
        'exit_signal_smoothing_window': 'Smoothing window for exit signals',
        'signal_persistence_requirement': 'Bars to confirm signal',
        'volatility_regime_multiplier': 'Normal volatility multiplier',
        'low_vol_regime_multiplier': 'Low volatility regime multiplier',
        'high_vol_regime_multiplier': 'High volatility regime multiplier',
    }

    for param, description in pa_params.items():
        if param in result.best_params:
            value = result.best_params[param]
            print(f"  {param:40s} = {value:10} ({description})")

    print()

    # Save detailed report
    output_path = Path("diagnostic_output/pa_calibration_sunpharma_1week.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": sum(c.elapsed_seconds for c in result.candidates),
        "symbol": SYMBOL,
        "bars_used": WARMUP + BARS_FOR_ONE_WEEK,
        "data_range": "1 week (5 trading days, 1-minute)",
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
        },
        "acceptance_gates": {
            "min_trades": 10,
            "min_profit_factor": 0.95,
            "max_drawdown_fraction": 0.25,
        }
    }

    output_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"✅ Report saved: {output_path}")
    print()

    # Show next steps
    print("="*100)
    print("NEXT STEPS")
    print("="*100)
    print()
    print("1. Review calibrated parameters above")
    print("2. Create config override file:")
    print("   config_override_pa_calibrated_1week.json")
    print("3. Apply parameters to orchestrator")
    print("4. Rerun SUNPHARMA 1-week validation with calibrated params")
    print("5. Verify improved PA confidence (target: ≥50%)")
    print("6. Check ID approval rate increase")
    print("7. If successful, extend to full 48-symbol or 1-month validation")
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
