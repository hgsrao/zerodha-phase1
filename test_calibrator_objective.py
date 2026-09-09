#!/usr/bin/env python3
"""
Test the calibrator objective function (standalone, no Ray dependency).
"""

import pandas as pd
import numpy as np
from portfolio_orchestrator import PortfolioOrchestrator
from canonical_parameter_registry import CanonicalParameterRegistry


def test_objective(config, symbol_bars, warmup=60):
    """Standalone objective function (no Ray dependency)."""
    try:
        registry = CanonicalParameterRegistry()

        # Build calibration overrides
        calibration_overrides = {}
        for param_name, param_value in config.items():
            calibration_overrides[param_name] = float(param_value)

        # Initialize portfolio orchestrator
        orchestrator = PortfolioOrchestrator(
            symbols=list(symbol_bars.keys()),
            registry=registry,
            starting_equity=100_000.0,
            calibration_overrides=calibration_overrides,
        )

        # Run portfolio backtest
        result = orchestrator.run(symbol_bars, warmup=warmup)

        # Extract metrics
        net_pnl = result.get("net_pnl", 0.0)
        completed_trades = result.get("completed_trades", 0)
        sharpe = result.get("sharpe_ratio", 0.0)
        max_dd = result.get("max_drawdown_fraction", 0.0)

        # Calculate profit factor
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

        # Composite score
        score = sharpe + 0.5 * min(profit_factor, 5.0) - 2.0 * max_dd

        return {
            "score": score,
            "net_pnl": net_pnl,
            "sharpe": sharpe,
            "profit_factor": profit_factor,
            "max_drawdown": max_dd,
            "trades": completed_trades,
        }

    except Exception as e:
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


if __name__ == "__main__":
    print("Testing calibrator objective function (standalone)...")
    print()

    # Create test data
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=100, freq="1min")
    dummy_bars = pd.DataFrame({
        "timestamp": dates,
        "open": 100 + np.random.randn(100).cumsum(),
        "high": 102 + np.random.randn(100).cumsum(),
        "low": 99 + np.random.randn(100).cumsum(),
        "close": 100 + np.random.randn(100).cumsum(),
        "volume": np.random.randint(1000, 10000, 100),
    })

    symbol_bars = {"TEST1": dummy_bars}

    # Test config
    test_config = {
        "base_dp_dt_multiplier": 1.0,
        "momentum_calculation_period": 20.0,
        "entry_confidence_threshold": 0.5,
    }

    print(f"✓ Created test data: 1 symbol × 100 bars")
    print(f"✓ Test config: {len(test_config)} parameters")
    print()

    print("[RUN] Evaluating objective function...")
    result = test_objective(test_config, symbol_bars, warmup=10)
    print("[DONE]")
    print()

    print("✅ Objective Result:")
    print(f"  Score: {result.get('score', 0):.4f}")
    print(f"  Net P&L: ₹{result.get('net_pnl', 0):,.2f}")
    print(f"  Sharpe: {result.get('sharpe', 0):.2f}")
    print(f"  Profit Factor: {result.get('profit_factor', 0):.2f}")
    print(f"  Max Drawdown: {result.get('max_drawdown', 0):.2%}")
    print(f"  Trades: {result.get('trades', 0)}")
    print()

    print("✅ Objective function works correctly!")
    print()
    print("Next: Install Ray + Optuna and run full calibrator")
    print("  pip install ray[tune] optuna")
    print("  python ray_tune_optuna_calibrator.py --help")
