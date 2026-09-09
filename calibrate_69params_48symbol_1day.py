#!/usr/bin/env python3
"""
Full 69-Parameter Calibration: 48 Symbols × 1 Day
===================================================

Calibrate ALL 69 calibratable parameters (not just PA):
- PA parameters (18)
- MPC parameters (profit targets, stop-loss, risk/reward, etc.)
- Safety gate parameters
- Position sizing parameters
- Exit control parameters
- All other tunable parameters

On 48-symbol portfolio, 1 day each (~500+ trades expected).

Uses full 3-phase Bayesian optimization (Random + TPE → CMA-ES → Fine-tune).
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

def load_48symbol_1day():
    """Load 48 symbols for 1 day each."""
    print(f"[LOAD] Loading 48 symbols, 1 day each...")

    loader = ManifestLoader(MANIFEST)
    manifest = loader.manifest
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    symbols = manifest.get_symbols()[:48]

    symbol_bars = {}
    skipped = []

    for symbol in symbols:
        entry = manifest.get_file(symbol)
        if entry is None:
            skipped.append(symbol)
            continue

        path = data_dir / entry.filename
        actual = loader._compute_file_hash(path)
        if actual != entry.sha256:
            skipped.append(f"{symbol} (hash)")
            continue

        bars = pd.read_csv(path)
        if len(bars) < WARMUP + BARS_FOR_ONE_DAY:
            skipped.append(f"{symbol} (bars)")
            continue

        bars_subset = bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()
        symbol_bars[symbol] = bars_subset

    print(f"  ✓ Loaded {len(symbol_bars)} symbols")
    if skipped:
        print(f"  ⚠ Skipped {len(skipped)}")

    return symbol_bars, list(symbol_bars.keys())

def run():
    """Run full 69-parameter calibration on 48-symbol portfolio."""

    print()
    print("="*120)
    print("FULL 69-PARAMETER CALIBRATION: 48 SYMBOLS × 1 DAY")
    print("="*120)
    print()

    # Load data
    symbol_bars, symbols = load_48symbol_1day()
    if not symbols:
        print("❌ No symbols loaded")
        sys.exit(1)

    print()

    # Initialize registry
    print("[INIT] Initializing full-parameter Bayesian calibration...")
    registry = CanonicalParameterRegistry()

    # Get full trading search space (all 69 calibratable params)
    search_space = trading_search_space(registry)
    print(f"  ✓ Calibratable parameters: {len(search_space.names)}")
    print(f"  ✓ Symbols: {len(symbols)}")
    print()

    # Portfolio-realistic acceptance gates
    gates = AcceptanceGates(
        min_trades=100,          # Need meaningful volume
        min_profit_factor=0.85,  # Relax slightly from 0.90
        max_drawdown_fraction=0.25,
        require_positive_net_pnl=False,
        max_safety_violations=5,
        min_symbols_traded=10,   # At least 10 symbols
    )

    # Aggressive 3-phase run for full parameter space
    run_config = CalibrationRunConfig(
        phase1_trials=15,       # More exploration (larger space)
        phase2_generations=5,   # More refinement
        phase3_iterations=10,   # Aggressive fine-tuning
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
    print("[RUN] Starting 3-phase Bayesian optimization on 69 parameters...")
    print()

    supervisor = CalibrationSupervisor(
        registry=registry,
        symbols=symbols,
        symbol_bars=symbol_bars,
        run_config=run_config,
        gates=gates,
        warmup=WARMUP,
        starting_equity=1_000_000.0,  # ₹10L for portfolio
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
            print(f"    Symbols: {c.metrics.get('symbols_traded', 0)}")
            if c.reject_reasons:
                print(f"    Why rejected: {', '.join(c.reject_reasons[:2])}")
        sys.exit(1)

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

    # Show all 69 calibrated parameters
    print("="*120)
    print("ALL 69 CALIBRATED PARAMETERS")
    print("="*120)
    print()

    params_by_category = {
        'PA Signal Generation': [
            'momentum_calculation_period', 'vwap_calculation_period', 'atr_calculation_period',
            'base_dp_dt_multiplier', 'base_dv_dt_multiplier'
        ],
        'PA Weighting': [
            'momentum_weight', 'vwap_weight', 'volatility_weight', 'confirmation_2bar_weight'
        ],
        'PA Quality Thresholds': [
            'green_threshold', 'amber_threshold_lower', 'red_threshold'
        ],
        'PA Smoothing & Persistence': [
            'entry_signal_smoothing_window', 'exit_signal_smoothing_window', 'signal_persistence_requirement'
        ],
        'PA Regime Multipliers': [
            'volatility_regime_multiplier', 'low_vol_regime_multiplier', 'high_vol_regime_multiplier'
        ],
        'MPC Profit Targets': [
            'profit_target_atr_mult', 'profit_target_margin_buffer', 'minimum_absolute_profit_rupees'
        ],
        'MPC Stop-Loss': [
            'stop_loss_atr_mult', 'min_risk_reward_ratio'
        ],
        'MPC Holding Period': [
            'min_hold_bars', 'max_hold_bars'
        ],
        'MPC PID Tuning': [
            'pid_kp_entry', 'pid_ki_entry', 'pid_kd_entry',
            'pid_kp_exit', 'pid_ki_exit', 'pid_kd_exit',
            'pid_integral_window_bars', 'pid_integral_max_clamp', 'pid_derivative_smoothing'
        ],
        'Safety Gates & Sizing': [
            'max_positions_live', 'max_exposure_pct', 'position_size_atr_multiple',
            'safety_drawdown_halt_threshold', 'max_daily_loss_rupees'
        ],
        'Slippage & Costs': [
            'slippage_cost_multiplier', 'profit_target_margin_buffer'
        ],
        'Other': []
    }

    # Categorize and display
    displayed = set()
    for category, param_list in params_by_category.items():
        matching = [p for p in param_list if p in result.best_params]
        if not matching:
            continue

        print(f"\n{category}:")
        for param in matching:
            if param in result.best_params:
                value = result.best_params[param]
                print(f"  {param:45s} = {value}")
                displayed.add(param)

    # Show any remaining parameters not in categories
    remaining = set(result.best_params.keys()) - displayed
    if remaining:
        print(f"\nOther Parameters ({len(remaining)}):")
        for param in sorted(remaining):
            value = result.best_params[param]
            print(f"  {param:45s} = {value}")

    print()

    # Save detailed report
    output_path = Path("diagnostic_output/pa_calibration_69params_48symbol_1day.json")
    output_path.parent.mkdir(exist_ok=True)

    report = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": sum(c.elapsed_seconds for c in result.candidates),
        "symbols": symbols,
        "symbol_count": len(symbols),
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
