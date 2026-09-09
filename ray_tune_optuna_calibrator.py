#!/usr/bin/env python3
"""
Ray Tune + Optuna Hyperparameter Calibrator for Revision 4 Engine
==================================================================

Massive parallel parameter calibration using:
- Ray Tune: Distributed computing framework (multi-core/multi-node execution)
- Optuna: TPE-based Bayesian optimization (intelligent search)
- Custom Orchestrator: Black-box objective function

Calibrates all 45 parameters simultaneously across unlimited parallel workers.

Usage:
    python ray_tune_optuna_calibrator.py \
        --num-samples 100 \
        --num-workers 16 \
        --data-path /path/to/symbol_bars.pkl \
        --output-dir ./calibration_results
"""

import os
import sys
import json
import pickle
import argparse
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

import numpy as np
import pandas as pd

# Ray + Optuna
from ray import tune
from ray.tune.stopper import Stopper
from ray.tune.schedulers import ASHAScheduler
from ray.tune.search.optuna import OptunaSearch
from optuna.samplers import TPESampler

# Our orchestrator
from canonical_parameter_registry import CanonicalParameterRegistry
from portfolio_orchestrator import PortfolioOrchestrator


# ============================================================================
# PARAMETER DEFINITIONS (All 45 calibratable parameters)
# ============================================================================

PARAMETER_RANGES = {
    # PA SIGNAL GENERATION (5)
    "base_dp_dt_multiplier": (0.5, 2.0),
    "base_dv_dt_multiplier": (0.5, 2.0),
    "momentum_calculation_period": (10, 30),
    "vwap_calculation_period": (10, 30),
    "atr_calculation_period": (10, 30),

    # PA WEIGHTING (4)
    "momentum_weight": (0.1, 0.4),
    "vwap_weight": (0.1, 0.4),
    "volatility_weight": (0.05, 0.4),
    "confirmation_2bar_weight": (0.1, 0.4),

    # PA QUALITY THRESHOLDS (3)
    "green_threshold": (0.6, 0.95),
    "amber_threshold_lower": (0.3, 0.7),
    "red_threshold": (0.1, 0.5),

    # PA REGIME (4)
    "volatility_regime_multiplier": (0.7, 1.5),
    "low_vol_regime_multiplier": (0.8, 1.5),
    "medium_vol_regime_multiplier": (0.7, 1.5),
    "high_vol_regime_multiplier": (0.8, 1.5),

    # PA SMOOTHING & PERSISTENCE (3)
    "entry_signal_smoothing_window": (1, 8),
    "exit_signal_smoothing_window": (1, 4),
    "signal_persistence_requirement": (1.0, 2.5),

    # ID / CONFIDENCE (3)
    "entry_confidence_threshold": (0.3, 0.8),
    "exit_confidence_threshold": (0.4, 0.9),
    "slippage_guard_threshold": (0.01, 0.15),

    # MPC / PROFIT TARGETS (3)
    "profit_target_atr_mult": (0.8, 2.5),
    "profit_target_margin_buffer": (0.0, 0.5),
    "minimum_absolute_profit_rupees": (0.0, 200.0),

    # MPC / STOP-LOSS (2)
    "stop_loss_atr_mult": (0.3, 1.2),
    "min_risk_reward_ratio": (1.0, 3.0),

    # MPC / HOLDING (2)
    "min_hold_bars": (1, 5),
    "max_hold_bars": (20, 120),

    # MPC / SLIPPAGE (1)
    "slippage_cost_multiplier": (0.8, 1.5),

    # MPC / PID TUNING (9)
    "pid_kp_entry": (0.05, 0.30),
    "pid_ki_entry": (0.01, 0.20),
    "pid_kd_entry": (0.01, 0.20),
    "pid_kp_exit": (0.05, 0.25),
    "pid_ki_exit": (0.01, 0.15),
    "pid_kd_exit": (0.01, 0.15),
    "pid_integral_window_bars": (5, 30),
    "pid_integral_max_clamp": (0.02, 0.25),
    "pid_derivative_smoothing": (1, 10),

    # POSITION MANAGER (6)
    "max_positions_live": (1, 12),
    "max_positions_per_symbol": (1, 3),
    "capital_per_trade_fraction": (0.005, 0.10),
    "min_capital_buffer_fraction": (0.05, 0.30),
    "max_sector_exposure_fraction": (0.10, 0.60),
    "max_symbol_concentration": (0.01, 0.15),

    # SAFETY GATES (6)
    "drawdown_normal_threshold": (0.05, 0.20),
    "drawdown_derated_threshold": (0.10, 0.25),
    "drawdown_halt_threshold": (0.15, 0.35),
    "max_loss_per_trade_rupees": (1000, 20000),
    "max_loss_per_day_rupees": (10000, 150000),
    "portfolio_lambda_risk_limit": (0.05, 0.30),

    # P01D / EXECUTION (5)
    "limit_order_offset_percent": (0.00, 0.05),
    "order_timeout_seconds": (5, 120),
    "max_retry_attempts": (0, 5),
    "retry_delay_seconds": (1, 20),
    "slippage_tolerance_percent": (0.02, 0.20),

    # OPTIMIZER META (3)
    "phase1_exploration_intensity": (30, 100),
    "phase2_optimization_intensity": (100, 500),
    "learning_rate_exploration_factor": (0.01, 0.10),

    # POSITION MANAGER OTHER (2)
    "rebalance_frequency_minutes": (15, 240),
}

PARAMETER_TYPES = {
    "momentum_calculation_period": "int",
    "vwap_calculation_period": "int",
    "atr_calculation_period": "int",
    "entry_signal_smoothing_window": "int",
    "exit_signal_smoothing_window": "int",
    "min_hold_bars": "int",
    "max_hold_bars": "int",
    "pid_integral_window_bars": "int",
    "pid_derivative_smoothing": "int",
    "max_positions_live": "int",
    "max_positions_per_symbol": "int",
    "order_timeout_seconds": "int",
    "max_retry_attempts": "int",
    "retry_delay_seconds": "int",
    "phase1_exploration_intensity": "int",
    "phase2_optimization_intensity": "int",
    "rebalance_frequency_minutes": "int",
}


# ============================================================================
# BLACK-BOX OBJECTIVE FUNCTION
# ============================================================================

def objective(config: Dict[str, Any], symbol_bars: Dict, warmup: int = 60) -> Dict[str, Any]:
    """
    Black-box objective function for Ray Tune.

    Treats PortfolioOrchestrator (wraps Revision2Orchestrator) as a pure black box:
    1. Accept parameter configuration
    2. Initialize orchestrator
    3. Run portfolio backtest on data
    4. Return objective metrics

    Args:
        config: Parameter configuration from Optuna
        symbol_bars: {symbol: DataFrame} of OHLCV data
        warmup: Warmup bars to skip

    Returns:
        Metrics dict with optimization objectives
    """
    try:
        # Initialize registry
        registry = CanonicalParameterRegistry()

        # Build calibration overrides from config
        calibration_overrides = {}
        for param_name, param_value in config.items():
            if param_name in PARAMETER_TYPES and PARAMETER_TYPES[param_name] == "int":
                calibration_overrides[param_name] = int(param_value)
            else:
                calibration_overrides[param_name] = float(param_value)

        # Initialize portfolio orchestrator with overridden parameters
        orchestrator = PortfolioOrchestrator(
            symbols=list(symbol_bars.keys()),
            registry=registry,
            starting_equity=100_000.0,
            calibration_overrides=calibration_overrides,
        )

        # Run portfolio backtest
        result = orchestrator.run(symbol_bars, warmup=warmup)

        # Extract portfolio-level metrics for optimization
        net_pnl = result.get("net_pnl", 0.0)
        completed_trades = result.get("completed_trades", 0)
        sharpe = result.get("sharpe_ratio", 0.0)
        max_dd = result.get("max_drawdown_fraction", 0.0)

        # Calculate profit factor from per-symbol trades
        per_symbol_results = result.get("per_symbol_results", {})
        gains = 0.0
        losses = 0.0
        for symbol, symbol_result in per_symbol_results.items():
            if isinstance(symbol_result, dict) and "trades" in symbol_result:
                for trade in symbol_result["trades"]:
                    pnl = trade.get("pnl", 0.0)
                    if pnl > 0:
                        gains += pnl
                    else:
                        losses += abs(pnl)

        profit_factor = gains / losses if losses > 0 else (10.0 if gains > 0 else 0.0)

        # Composite score: Sharpe + profit factor - drawdown penalty
        # (Favor Sharpe > 1.0, profit factor > 0.9, minimize drawdown)
        score = sharpe + 0.5 * min(profit_factor, 5.0) - 2.0 * max_dd

        return {
            "score": score,  # Primary optimization target
            "net_pnl": net_pnl,
            "sharpe": sharpe,
            "profit_factor": profit_factor,
            "max_drawdown": max_dd,
            "trades": completed_trades,
        }

    except Exception as e:
        # Return worst score on error
        print(f"❌ Evaluation failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            "score": float("-inf"),
            "net_pnl": 0.0,
            "sharpe": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 1.0,
            "trades": 0,
        }


# ============================================================================
# RAY TUNE CONFIGURATION
# ============================================================================

def build_search_space() -> Dict:
    """Build Ray Tune search space from parameter definitions."""
    search_space = {}

    for param_name, (min_val, max_val) in PARAMETER_RANGES.items():
        param_type = PARAMETER_TYPES.get(param_name, "float")

        if param_type == "int":
            search_space[param_name] = tune.randint(int(min_val), int(max_val))
        else:
            search_space[param_name] = tune.uniform(min_val, max_val)

    return search_space


# ============================================================================
# MAIN CALIBRATION RUNNER
# ============================================================================

def run_calibration(
    symbol_bars: Dict,
    num_samples: int = 100,
    num_workers: int = 16,
    output_dir: str = "./calibration_results",
    warmup: int = 60,
) -> Dict[str, Any]:
    """
    Run Ray Tune + Optuna calibration.

    Args:
        symbol_bars: {symbol: DataFrame} of OHLCV data
        num_samples: Total parameter combinations to test
        num_workers: Number of parallel workers (CPU cores)
        output_dir: Output directory for results
        warmup: Warmup bars to skip

    Returns:
        Best result and analysis
    """

    print()
    print("="*120)
    print("RAY TUNE + OPTUNA CALIBRATOR")
    print("="*120)
    print()

    print(f"Configuration:")
    print(f"  Parameters: {len(PARAMETER_RANGES)}")
    print(f"  Samples: {num_samples}")
    print(f"  Workers: {num_workers}")
    print(f"  Symbols: {len(symbol_bars)}")
    print()

    # Build search space
    search_space = build_search_space()

    # Configure Optuna search algorithm (TPE sampler wrapped by Ray)
    # Ray 2.58+ requires space parameter to be passed
    search_algo = OptunaSearch(
        space=search_space,
        metric="score",
        mode="max",
        sampler=TPESampler(seed=42),
    )

    # Configure scheduler (ASHA for early stopping)
    scheduler = ASHAScheduler(
        metric="score",
        mode="max",
        max_t=1,
        grace_period=1,
    )

    # Convert output_dir to absolute path (Ray requires it)
    output_dir_abs = str(Path(output_dir).resolve())
    Path(output_dir_abs).mkdir(parents=True, exist_ok=True)

    # Run Ray Tune
    print("[RUN] Starting parallel calibration...")
    print(f"      Output: {output_dir_abs}")
    print()

    results = tune.run(
        lambda config: objective(config, symbol_bars, warmup),
        name="revision4_calibration",
        search_alg=search_algo,
        scheduler=scheduler,
        num_samples=num_samples,
        resources_per_trial={"cpu": 1},
        log_to_file=True,
        storage_path=output_dir_abs,
        verbose=1,
    )

    # Get best result (Ray 2.58+ requires explicit metric/mode)
    best_trial = results.get_best_trial(metric="score", mode="max")
    best_result = best_trial.last_result if best_trial else {}
    best_config = best_trial.config if best_trial else {}

    print()
    print("="*120)
    print("CALIBRATION COMPLETE")
    print("="*120)
    print()

    print(f"Best score: {best_result['score']:.4f}")
    print(f"  Net P&L: ₹{best_result['net_pnl']:,.2f}")
    print(f"  Sharpe: {best_result['sharpe']:.2f}")
    print(f"  Profit Factor: {best_result['profit_factor']:.2f}")
    print(f"  Max Drawdown: {best_result['max_drawdown']:.2%}")
    print(f"  Trades: {best_result['trades']}")
    print()

    print("Best parameters (Top 10):")
    for param_name, param_value in sorted(best_config.items())[:10]:
        print(f"  {param_name}: {param_value}")

    # Save results
    output_path = Path(output_dir) / "calibration_winner.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    winner_report = {
        "timestamp": datetime.now().isoformat(),
        "best_score": best_result["score"],
        "best_config": best_config,
        "metrics": {
            "net_pnl": best_result["net_pnl"],
            "sharpe": best_result["sharpe"],
            "profit_factor": best_result["profit_factor"],
            "max_drawdown": best_result["max_drawdown"],
            "trades": best_result["trades"],
        },
        "calibration_params": {
            "num_samples": num_samples,
            "num_workers": num_workers,
            "total_parameters": len(PARAMETER_RANGES),
        },
    }

    output_path.write_text(json.dumps(winner_report, indent=2, default=str) + "\n")
    print(f"\n✅ Results saved: {output_path}")

    return winner_report


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ray Tune + Optuna Calibrator")
    parser.add_argument("--num-samples", type=int, default=100, help="Total samples to evaluate")
    parser.add_argument("--num-workers", type=int, default=16, help="Number of parallel workers")
    parser.add_argument("--data-path", type=str, required=True, help="Path to pickled symbol bars")
    parser.add_argument("--output-dir", type=str, default="./calibration_results", help="Output directory")
    parser.add_argument("--warmup", type=int, default=60, help="Warmup bars")

    args = parser.parse_args()

    # Load data
    print(f"[LOAD] Loading data from {args.data_path}...")
    with open(args.data_path, "rb") as f:
        symbol_bars = pickle.load(f)
    print(f"  ✓ Loaded {len(symbol_bars)} symbols")

    # Run calibration
    result = run_calibration(
        symbol_bars=symbol_bars,
        num_samples=args.num_samples,
        num_workers=args.num_workers,
        output_dir=args.output_dir,
        warmup=args.warmup,
    )
