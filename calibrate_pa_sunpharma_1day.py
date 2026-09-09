#!/usr/bin/env python3
"""
PA Calibration: SUNPHARMA × 1 Day (Intraday Data)
==================================================

Uses Revision2 CalibrationSupervisor (Phase 1: Random/TPE, Phase 2: CMA-ES, Phase 3: Fine-tune).
Target: Improve PA confidence from [6-44%] → [50%+] so ID approvals > 0.

Calibrates all 18 PA parameters on real SUNPHARMA 1-day intraday data.
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
    CalibrationSupervisor, CalibrationRunConfig, AcceptanceGates
)

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_DAY = 390  # 1 trading day = 390 minutes

def load_sunpharma_1day():
    """Load SUNPHARMA for exactly 1 day after warmup."""
    print(f"[LOAD] Loading {SYMBOL} intraday data (1 day)...")

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
    print(f"  ✓ Total bars available: {len(bars)}")

    # Take only warmup + 1 day
    if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
        raise RuntimeError(f"Insufficient bars: {len(bars)} < {WARMUP + BARS_FOR_ONE_DAY}")

    bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
    print(f"  ✓ Using {len(bars_subset)} bars ({WARMUP} warmup + {BARS_FOR_ONE_DAY} active)")
    print(f"  ✓ Date range: {bars_subset.iloc[WARMUP]['timestamp']} to {bars_subset.iloc[-1]['timestamp']}")

    return {SYMBOL: bars_subset}

def run():
    """Run SUNPHARMA 1-day calibration."""

    print()
    print("="*100)
    print(f"PA CALIBRATION: {SYMBOL} × 1 DAY (INTRADAY)")
    print("="*100)
    print()

    # Load data
    symbol_bars = load_sunpharma_1day()
    symbols = [SYMBOL]

    print()

    # Initialize registry and calibration config
    print("[INIT] Initializing Bayesian calibration...")
    registry = CanonicalParameterRegistry()

    # Smoke-test gates (loose) for 1-day calibration
    gates = AcceptanceGates(
        min_trades=1,           # Very loose: accept 1+ trades
        min_profit_factor=0.90, # Accept even marginal profitability
        max_drawdown_fraction=0.50,  # Very loose
        require_positive_net_pnl=False,
        max_safety_violations=10,
    )

    # Small run config for 1-day test
    run_config = CalibrationRunConfig(
        phase1_trials=8,      # 8 random trials (Random Search + TPE)
        phase2_generations=2, # 2 CMA-ES generations
        phase3_iterations=5,  # 5 fine-tuning iterations
        wall_clock_budget_seconds=300,  # 5 minute budget per candidate = 60 minutes total
        seed=42,
    )

    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256[:16]}...")
    print(f"  ✓ Symbol: {SYMBOL}")
    print(f"  ✓ Phase 1 trials: {run_config.phase1_trials} (Random + TPE)")
    print(f"  ✓ Phase 2 generations: {run_config.phase2_generations} (CMA-ES)")
    print(f"  ✓ Phase 3 iterations: {run_config.phase3_iterations} (Fine-tune)")
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
    print("[RUN] Starting Bayesian optimization (3 phases)...")
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
        print("\nTop 3 candidates by guidance score (may not have passed hard gates):")
        for i, c in enumerate(sorted(result.candidates, key=lambda x: -x.guidance_score)[:3], 1):
            print(f"\n  Candidate {i} (Phase {c.phase}):")
            print(f"    Guidance score: {c.guidance_score:.4f}")
            print(f"    Trades: {c.metrics.get('completed_trades', 0)}")
            print(f"    P&L: ₹{c.metrics.get('net_pnl', 0):,.2f}")
            print(f"    Sharpe: {c.metrics.get('sharpe', 0):.2f}")
            if c.reject_reasons:
                print(f"    Why rejected: {c.reject_reasons[0]}")
        print()
        print("This suggests 1 day may be too short for meaningful calibration.")
        print("Consider extending to 1 week or running multiple 1-day windows.")
        sys.exit(1)

    print(f"✅ WINNER FOUND")
    print(f"   Guidance score: {result.best_score:.4f}")
    print(f"   Trades: {result.best_report.get('completed_trades', 0)}")
    print(f"   P&L: ₹{result.best_report.get('net_pnl', 0):,.2f}")
    print(f"   Sharpe: {result.best_report.get('sharpe', 0):.2f}")
    print(f"   Max drawdown: {result.best_report.get('max_drawdown_fraction', 0):.2%}")
    print()

    # Display calibrated parameters (PA focus)
    print("="*100)
    print("CALIBRATED PA PARAMETERS (all 18)")
    print("="*100)
    print()

    pa_key_params = [
        'momentum_calculation_period',
        'vwap_calculation_period',
        'atr_calculation_period',
        'base_dp_dt_multiplier',
        'base_dv_dt_multiplier',
        'momentum_weight',
        'vwap_weight',
        'volatility_weight',
        'confirmation_2bar_weight',
        'green_threshold',
        'amber_threshold_lower',
        'red_threshold',
        'entry_signal_smoothing_window',
        'exit_signal_smoothing_window',
        'signal_persistence_requirement',
        'volatility_regime_multiplier',
        'low_vol_regime_multiplier',
        'high_vol_regime_multiplier',
    ]

    print("Signal Generation:")
    for param in pa_key_params[:5]:
        if param in result.best_params:
            print(f"  {param:40s} = {result.best_params[param]}")

    print("\nComponent Weighting:")
    for param in pa_key_params[5:9]:
        if param in result.best_params:
            print(f"  {param:40s} = {result.best_params[param]}")

    print("\nQuality Thresholds:")
    for param in pa_key_params[9:12]:
        if param in result.best_params:
            print(f"  {param:40s} = {result.best_params[param]}")

    print("\nSmoothing & Persistence:")
    for param in pa_key_params[12:15]:
        if param in result.best_params:
            print(f"  {param:40s} = {result.best_params[param]}")

    print("\nRegime Multipliers:")
    for param in pa_key_params[15:18]:
        if param in result.best_params:
            print(f"  {param:40s} = {result.best_params[param]}")

    print()

    # Save report
    output_path = Path("diagnostic_output/pa_calibration_sunpharma_1day.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": sum(c.elapsed_seconds for c in result.candidates),
        "symbol": SYMBOL,
        "bars_used": WARMUP + BARS_FOR_ONE_DAY,
        "data_range": "1 day (intraday 1-minute)",
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

    # Show next steps
    print("="*100)
    print("NEXT STEPS")
    print("="*100)
    print()

    if result.best_params:
        print("1. ✅ Calibrated parameters found")
        print("2. Apply to stage2 configuration:")
        print(f"   config_override_pa_calibrated.json")
        print("3. Rerun SUNPHARMA 1-day test with calibrated params")
        print("4. Verify PA confidence improved (target: ≥50%)")
        print("5. Check ID approvals > 0 (gate passes signals)")
        print("6. If successful, extend to full 48-symbol validation")

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
