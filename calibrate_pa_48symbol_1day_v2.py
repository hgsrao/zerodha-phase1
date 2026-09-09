#!/usr/bin/env python3
"""
PA Calibration: 48 Symbols × 1 Day
====================================

Uses Revision2 CalibrationSupervisor (Phase 1: Random/TPE, Phase 2: CMA-ES, Phase 3: Fine-tune).
Target: Calibrate all 18 PA parameters across diversified portfolio.

Data: 48 symbols, 1 trading day each (1-minute bars).
Expected trades: ~500+ across portfolio.
Phases: All 3 (enough volume for meaningful optimization).
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
    print(f"[LOAD] Loading 48 symbols, 1 day each from manifest...")

    loader = ManifestLoader(MANIFEST)
    manifest = loader.manifest

    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    symbols = manifest.get_symbols()[:48]  # Get all available symbols (up to 48)

    symbol_bars = {}
    skipped = []

    for symbol in symbols:
        entry = manifest.get_file(symbol)
        if entry is None:
            skipped.append(f"{symbol} (not in manifest)")
            continue

        path = data_dir / entry.filename

        # Verify hash
        actual = loader._compute_file_hash(path)
        if actual != entry.sha256:
            skipped.append(f"{symbol} (hash mismatch)")
            continue

        # Load all bars
        bars = pd.read_csv(path)

        # Take only warmup + 1 day
        if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
            skipped.append(f"{symbol} ({len(bars)} < {WARMUP + BARS_FOR_ONE_DAY} bars)")
            continue

        bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
        symbol_bars[symbol] = bars_subset

    print(f"  ✓ Loaded {len(symbol_bars)} symbols")
    if skipped:
        print(f"  ⚠ Skipped {len(skipped)}: {skipped[0]}")

    print(f"  ✓ Date range (first symbol): {symbol_bars[list(symbol_bars.keys())[0]].iloc[WARMUP]['timestamp']} to {symbol_bars[list(symbol_bars.keys())[0]].iloc[-1]['timestamp']}")

    return symbol_bars, list(symbol_bars.keys())

def run():
    """Run 48-symbol 1-day calibration."""

    print()
    print("="*100)
    print(f"PA CALIBRATION: 48 SYMBOLS × 1 DAY (PORTFOLIO)")
    print("="*100)
    print()

    # Load data
    symbol_bars, symbols = load_48symbol_1day()
    if not symbols:
        print("❌ No symbols loaded")
        sys.exit(1)

    print()

    # Initialize registry and calibration config
    print("[INIT] Initializing 3-phase Bayesian calibration...")
    registry = CanonicalParameterRegistry()

    # Realistic gates for portfolio (48 symbols, 1 day = ~500+ trades)
    gates = AcceptanceGates(
        min_trades=50,           # At least 50 trades across portfolio
        min_profit_factor=0.90,  # Accept if losses not too large
        max_drawdown_fraction=0.20,  # Reasonable portfolio drawdown
        require_positive_net_pnl=False,  # Accept breakeven if structure good
        max_safety_violations=10,
        min_symbols_traded=15,   # Trade at least 15 different symbols
    )

    # Full 3-phase run config for portfolio data
    run_config = CalibrationRunConfig(
        phase1_trials=10,      # 10 trials (Random + TPE)
        phase2_generations=3,  # 3 CMA-ES generations
        phase3_iterations=6,   # 6 fine-tuning iterations
        wall_clock_budget_seconds=None,  # No time limit, finish all phases
        seed=42,
    )

    print(f"  ✓ Registry hash: {registry.FROZEN_IDENTITY_SHA256[:16]}...")
    print(f"  ✓ Symbols: {len(symbols)}")
    print(f"  ✓ Data: 1 day per symbol (~500+ expected trades)")
    print()
    print(f"  Phase 1: {run_config.phase1_trials} trials (Random + TPE)")
    print(f"  Phase 2: {run_config.phase2_generations} generations (CMA-ES)")
    print(f"  Phase 3: {run_config.phase3_iterations} iterations (Fine-tune)")
    print()

    # Initialize supervisor
    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=symbols,
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=1_000_000.0,  # Larger equity for portfolio
    )

    # Run calibration
    print("[RUN] Starting 3-phase Bayesian optimization (portfolio mode)...")
    print("      Phase 1: Random + TPE sampling")
    print("      Phase 2: CMA-ES optimization")
    print("      Phase 3: Local fine-tuning")
    print()

    result = supervisor.run()

    # Report results
    print()
    print("="*100)
    print("CALIBRATION RESULTS (48-SYMBOL PORTFOLIO)")
    print("="*100)
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
                print(f"    Why rejected: {', '.join(c.reject_reasons[:2])}")
        sys.exit(1)

    print(f"✅ WINNER FOUND")
    print(f"   Phase: {[c for c in result.candidates if c.score == result.best_score][0].phase}")
    print(f"   Score: {result.best_score:.4f}")
    print(f"   Trades: {result.best_report.get('completed_trades', 0)}")
    print(f"   P&L: ₹{result.best_report.get('net_pnl', 0):,.2f}")
    print(f"   Sharpe: {result.best_report.get('sharpe', 0):.2f}")
    print(f"   Max drawdown: {result.best_report.get('max_drawdown_fraction', 0):.2%}")
    print(f"   Symbols traded: {result.best_report.get('symbols_traded', 0)}")
    print()

    # Display calibrated parameters
    print("="*100)
    print("CALIBRATED PA PARAMETERS (ALL 18)")
    print("="*100)
    print()

    pa_params = [
        ('momentum_calculation_period', 'Momentum calc period'),
        ('vwap_calculation_period', 'VWAP calc period'),
        ('atr_calculation_period', 'ATR calc period'),
        ('base_dp_dt_multiplier', 'Price momentum multiplier'),
        ('base_dv_dt_multiplier', 'Volume momentum multiplier'),
        ('momentum_weight', 'Momentum weight'),
        ('vwap_weight', 'VWAP weight'),
        ('volatility_weight', 'Volatility weight'),
        ('confirmation_2bar_weight', '2-bar confirmation weight'),
        ('green_threshold', 'Green (high confidence) threshold'),
        ('amber_threshold_lower', 'Amber (medium) threshold'),
        ('red_threshold', 'Red (low) threshold'),
        ('entry_signal_smoothing_window', 'Entry smoothing window'),
        ('exit_signal_smoothing_window', 'Exit smoothing window'),
        ('signal_persistence_requirement', 'Signal persistence bars'),
        ('volatility_regime_multiplier', 'Normal vol regime multiplier'),
        ('low_vol_regime_multiplier', 'Low vol regime multiplier'),
        ('high_vol_regime_multiplier', 'High vol regime multiplier'),
    ]

    for param, desc in pa_params:
        if param in result.best_params:
            value = result.best_params[param]
            print(f"  {param:40s} = {value:10}")

    print()

    # Save report
    output_path = Path("diagnostic_output/pa_calibration_48symbol_1day.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": sum(c.elapsed_seconds for c in result.candidates),
        "symbols": symbols,
        "symbol_count": len(symbols),
        "bars_per_symbol": WARMUP + BARS_FOR_ONE_DAY,
        "data_range": "1 day (1-minute intraday)",
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
            "min_trades": 50,
            "min_profit_factor": 0.90,
            "max_drawdown_fraction": 0.20,
            "min_symbols_traded": 15,
        }
    }

    output_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"✅ Report saved: {output_path}")
    print()

    # Next steps
    print("="*100)
    print("NEXT STEPS")
    print("="*100)
    print()
    print("1. Review calibrated PA parameters above")
    print("2. Create configuration override:")
    print("   config_override_pa_calibrated_48symbol.json")
    print("3. Apply to orchestrator")
    print("4. Rerun 48-symbol 1-day validation with calibrated params")
    print("5. Verify PA confidence improvement")
    print("6. Check execution funnel (signals → approvals → trades)")
    print("7. Review trade metrics (P&L, Sharpe, drawdown)")
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
