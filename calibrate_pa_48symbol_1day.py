#!/usr/bin/env python3
"""
PA Calibration: 48 Symbols × 1 Day
===================================

Uses Revision2 CalibrationSupervisor (Phase 1: Random/TPE, Phase 2: CMA-ES, Phase 3: Fine-tune).
Target: Improve PA confidence from [6-44%] → [50%+] so ID approvals > 0.

Calibrates all 14 core PA parameters on real 48-symbol 1-day data.
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
WARMUP = 60
BARS_FOR_ONE_DAY = 390  # 1 trading day = 390 minutes

def load_48symbol_1day():
    """Load 48 symbols for exactly 1 day after warmup."""
    print("[LOAD] Loading 48 symbols, 1 day each from manifest...")

    loader = ManifestLoader(MANIFEST)
    manifest = loader.manifest

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))

    symbols = manifest.get_symbols()[:48]  # Exactly 48 symbols (or fewer if unavailable)
    symbol_bars = {}

    for symbol in symbols:
        entry = manifest.get_file(symbol)
        if entry is None:
            print(f"  ⚠ {symbol} not in manifest, skipping")
            continue

        path = data_dir / entry.filename

        # Verify hash
        actual = loader._compute_file_hash(path)
        if actual != entry.sha256:
            print(f"  ❌ {symbol} hash mismatch, skipping")
            continue

        # Load all bars
        bars = pd.read_csv(path)

        # Take only warmup + 1 day
        if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
            print(f"  ⚠ {symbol} insufficient bars ({len(bars)} < {WARMUP + BARS_FOR_ONE_DAY}), skipping")
            continue

        bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
        symbol_bars[symbol] = bars_subset
        print(f"  ✓ {symbol}: {len(bars_subset)} bars")

    print(f"\nLoaded {len(symbol_bars)} symbols (target: 48)")
    return symbol_bars, list(symbol_bars.keys())

def run():
    """Run 48-symbol 1-day calibration."""

    print()
    print("="*100)
    print("PA CALIBRATION: 48 SYMBOLS × 1 DAY")
    print("="*100)
    print()

    # Load data
    symbol_bars, symbols = load_48symbol_1day()
    if not symbols:
        print("❌ No symbols loaded")
        sys.exit(1)

    print()

    # Initialize registry and calibration config
    print("[INIT] Initializing calibration...")
    registry = CanonicalParameterRegistry()

    # Smoke-test gates (loose) for 1-day, 1-symbol calibration
    gates = AcceptanceGates(
        min_trades=1,           # Very loose for 1 day
        min_profit_factor=0.90, # Accept even marginal
        max_drawdown_fraction=0.50,  # Very loose
        require_positive_net_pnl=False,
        max_safety_violations=10,
    )

    # Small run config for 1-day test
    run_config = CalibrationRunConfig(
        phase1_trials=8,      # Just 8 random trials to start
        phase2_generations=2, # Quick CMA-ES
        phase3_iterations=5,  # Minimal fine-tuning
        wall_clock_budget_seconds=600,  # 10 minute budget
        seed=42,
    )

    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256[:16]}...")
    print(f"  ✓ Symbols: {len(symbols)}")
    print(f"  ✓ Phase 1 trials: {run_config.phase1_trials}")
    print(f"  ✓ Phase 2 generations: {run_config.phase2_generations}")
    print(f"  ✓ Phase 3 iterations: {run_config.phase3_iterations}")
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
    print("[RUN] Starting Bayesian optimization...")
    print("  Phase 1: Random Search + TPE")
    print("  Phase 2: CMA-ES")
    print("  Phase 3: Local Fine-Tune")
    print()

    result = supervisor.run()

    # Report results
    print()
    print("="*100)
    print("CALIBRATION RESULTS")
    print("="*100)
    print()

    print(f"Candidates evaluated: {len(result.candidates)}")
    print(f"Candidates accepted: {len([c for c in result.candidates if c.accepted])}")
    print(f"Stopped reason: {result.stopped_reason}")
    print()

    if result.best_params is None:
        print("❌ NO CANDIDATE PASSED acceptance gates")
        print("\nTop 5 candidates by guidance score:")
        for i, c in enumerate(sorted(result.candidates, key=lambda x: -x.guidance_score)[:5], 1):
            print(f"  {i}. Phase {c.phase}: {c.guidance_score:.2f}")
            print(f"     Trades: {c.metrics.get('completed_trades', 0)}")
            print(f"     P&L: ₹{c.metrics.get('net_pnl', 0):.2f}")
            print(f"     Reasons: {', '.join(c.reject_reasons[:2])}")
        sys.exit(1)

    print(f"✅ WINNER FOUND (Phase: {result.best_params.get('phase', 'unknown')})")
    print(f"   Score: {result.best_score:.4f}")
    print(f"   P&L: ₹{result.best_report.get('net_pnl', 0):,.2f}")
    print(f"   Trades: {result.best_report.get('completed_trades', 0)}")
    print(f"   Sharpe: {result.best_report.get('sharpe', 0):.2f}")
    print()

    # Display calibrated parameters (PA focus)
    print("="*100)
    print("CALIBRATED PA PARAMETERS")
    print("="*100)
    print()

    pa_params = {
        k: v for k, v in result.best_params.items()
        if any(x in k.lower() for x in [
            'momentum', 'vwap', 'atr', 'dp_dt', 'dv_dt', 'weight', 'threshold',
            'smoothing', 'persistence', 'regime', 'confidence', 'band'
        ])
    }

    if pa_params:
        for param, value in sorted(pa_params.items()):
            print(f"  {param:40s} = {value}")
    else:
        print("  (No PA-specific parameters in best candidate)")

    print()

    # Save report
    output_path = Path("diagnostic_output/pa_calibration_48symbol_1day.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": sum(c.elapsed_seconds for c in result.candidates),
        "symbols": symbols,
        "bars_per_symbol": WARMUP + BARS_FOR_ONE_DAY,
        "data_range": "1 day",
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
        print(f"1. Review calibrated parameters above")
        print(f"2. Apply parameters to config:")
        print(f"   config_override_pa_calibrated.json")
        print(f"3. Rerun 48-symbol 1-day test with calibrated params")
        print(f"4. Check PA confidence improvement (target: ≥50%)")
        print(f"5. If successful, scale to full 3-year validation")
    else:
        print(f"⚠ Calibration found no passing candidates")
        print(f"  This suggests:")
        print(f"  - 1 day is too short for meaningful trades")
        print(f"  - Parameters need wider search range")
        print(f"  - Consider extending to 1 week or 1 month")

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
