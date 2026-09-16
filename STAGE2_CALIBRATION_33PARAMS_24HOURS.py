#!/usr/bin/env python3
"""
================================================================================
STAGE 2 CALIBRATION - ALL 33 PARAMETERS × 24 HOURS (RANDOM SEARCH)
================================================================================

⚠️ RESEARCH ONLY: Random parameter search for exploratory analysis

- All 33 parameters (Tier 1 + Tier 2 + Tier 3) randomly sampled
- 48 NIFTY symbols, 3-year historical data validation
- 24 continuous hours of random sampling
- RANDOM GRID SEARCH (NOT Bayesian optimization)
- Single process (low resource usage)
- Backtest results for exploration only

NOTE: This is a random parameter sampling script, not a sophisticated
optimizer. It does not perform Bayesian adaptive sampling or intelligent
parameter selection. Each iteration randomly draws parameter values from
specified ranges and backtests against historical data.

For production parameter optimization, use dedicated Bayesian libraries.
See CRITICAL_AUDIT_RESPONSE_20260830.md for audit details.

Duration: ~24 hours (varies by system)
Output: Parameter samples + win rate logs (exploratory data only)

================================================================================
"""

import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Dict, List
import time

# Import the enhanced system with 33 parameters
try:
    from COMPLETE_TRADING_SYSTEM_WITH_33PARAMS import (
        CompleteIntegratedTradingSystemWith33Params,
        ParameterInjectionManager,
        validate_parameter_set
    )
    logger_init = logging.getLogger('IMPORT')
    logger_init.info("[OK] Imported 33-parameter system")
    SYSTEM_AVAILABLE = True
except ImportError as e:
    print("[FAIL] Cannot import system: {}".format(str(e)))
    SYSTEM_AVAILABLE = False

# Setup logging with FORCE FLUSH
class FlushingFileHandler(logging.FileHandler):
    """File handler that flushes after every write"""
    def emit(self, record):
        super().emit(record)
        self.flush()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [STAGE2_33P] - %(message)s',
    handlers=[
        FlushingFileHandler('STAGE2_calibration_33params.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('STAGE2_33PARAMS')


# ============================================================================
# 33-PARAMETER CALIBRATION ENGINE
# ============================================================================

class CalibrationEngine33Params:
    """Calibrate all 33 parameters over 24 hours"""

    def __init__(self, duration_hours: float = 24.0):
        self.logger = logger
        self.duration_hours = duration_hours
        self.market_data = {}
        self.symbols_list = []
        self.iteration_count = 0
        self.start_time = datetime.now()
        self.best_params = None
        self.best_win_rate = 0.5175  # Stage 1 baseline
        self.all_results = []

        if SYSTEM_AVAILABLE:
            logger.info("Initializing 33-parameter system...")
            self.system = CompleteIntegratedTradingSystemWith33Params()
            logger.info("[OK] System with 33 parameters ready")
        else:
            logger.error("[FAIL] System NOT available")
            self.system = None

    def load_market_data(self) -> bool:
        """Load 48 NIFTY symbols"""
        logger.info("")
        logger.info("Loading REAL market data...")

        data_dir = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

        if not data_dir.exists():
            logger.error("[FAIL] Data directory not found")
            return False

        csv_files = sorted(list(data_dir.glob("NSE_*_15minute_*.csv")))

        try:
            for csv_file in csv_files[:48]:
                symbol = csv_file.stem.split('_')[1]
                df = pd.read_csv(csv_file)
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
                df = df.sort_values('timestamp').reset_index(drop=True)

                self.market_data[symbol] = df
                self.symbols_list.append(symbol)

            logger.info("[OK] Loaded {} symbols".format(len(self.symbols_list)))

            # Initialize system with data
            if self.system:
                logger.info("Initializing system with data...")
                self.system.initialize_from_data(self.market_data, self.symbols_list)
                logger.info("[OK] System initialized with market data")

            return True

        except Exception as e:
            logger.error("[FAIL] Error: {}".format(str(e)))
            import traceback
            logger.error(traceback.format_exc())
            return False

    def generate_random_params(self) -> Dict:
        """Generate random 33-parameter set"""
        default = ParameterInjectionManager.get_default_params()

        return {
            # Tier 1: Random variations around defaults
            'base_dp_dt_multiplier': np.random.uniform(0.8, 1.2),
            'base_dv_dt_multiplier': np.random.uniform(0.8, 1.2),
            'sync_score_confidence_threshold': np.random.uniform(0.5, 2.0),
            'entry_confidence_threshold': np.random.uniform(0.3, 0.7),
            'exit_confidence_threshold': np.random.uniform(0.4, 0.8),
            'min_risk_reward_ratio': np.random.uniform(1.2, 2.5),
            'profit_target_margin_buffer': np.random.uniform(0.0, 0.3),

            # Chart weights - MUST sum to 1.0
            'vwap_weight': np.random.uniform(0.15, 0.35),
            'confirmation_2bar_weight': np.random.uniform(0.15, 0.35),
            'momentum_weight': np.random.uniform(0.15, 0.35),
            # Last weight balances to 1.0
            'volatility_weight': 0.0,  # Will calculate

            'green_threshold': np.random.uniform(0.6, 0.8),
            'amber_threshold_lower': np.random.uniform(0.3, 0.5),
            'red_threshold': np.random.uniform(0.1, 0.3),
            'slippage_guard_threshold': np.random.uniform(0.02, 0.1),

            'volatility_regime_multiplier': np.random.uniform(0.8, 1.2),
            'low_vol_regime_multiplier': np.random.uniform(0.9, 1.3),
            'medium_vol_regime_multiplier': np.random.uniform(0.8, 1.2),
            'high_vol_regime_multiplier': np.random.uniform(0.9, 1.3),

            # Tier 2
            'atr_calculation_period': int(np.random.uniform(15, 30)),
            'entry_signal_smoothing_window': int(np.random.uniform(1, 8)),
            'exit_signal_smoothing_window': int(np.random.uniform(1, 4)),
            'slippage_cost_multiplier': np.random.uniform(0.8, 1.5),
            'minimum_absolute_profit_rupees': np.random.uniform(0, 150),
            'momentum_calculation_period': int(np.random.uniform(10, 30)),
            'vwap_calculation_period': int(np.random.uniform(10, 30)),
            'signal_persistence_requirement': np.random.uniform(1.0, 2.0),

            # Tier 3
            'phase1_exploration_intensity': int(np.random.uniform(30, 70)),
            'phase2_optimization_intensity': int(np.random.uniform(150, 300)),
            'learning_rate_exploration_factor': np.random.uniform(0.01, 0.08),
            'lambda_risk_trigger_level': np.random.uniform(0.08, 0.18),
            'lambda_reduction_factor': np.random.uniform(0.6, 0.95),
            'recalibration_frequency_days': int(np.random.uniform(10, 40)),

            # Existing 6
            'profit_target_atr_mult': np.random.uniform(1.0, 2.0),
            'stop_loss_atr_mult': np.random.uniform(0.5, 1.0),
            'entry_pid_kp': np.random.uniform(0.08, 0.18),
            'exit_pid_kp': np.random.uniform(0.08, 0.18),
            'min_hold_bars': int(np.random.uniform(1, 4)),
            'max_hold_bars': int(np.random.uniform(30, 80)),
        }

    def normalize_params(self, params: Dict) -> Dict:
        """Normalize chart weights to sum to 1.0"""
        weights = (
            params['vwap_weight'] +
            params['confirmation_2bar_weight'] +
            params['momentum_weight']
        )

        # Balance the last weight
        params['volatility_weight'] = max(0.05, 1.0 - weights)

        # Re-normalize all to exactly 1.0
        total = (
            params['vwap_weight'] +
            params['confirmation_2bar_weight'] +
            params['momentum_weight'] +
            params['volatility_weight']
        )

        if total > 0:
            params['vwap_weight'] /= total
            params['confirmation_2bar_weight'] /= total
            params['momentum_weight'] /= total
            params['volatility_weight'] /= total

        return params

    def run_backtest_with_params(self, params: Dict) -> float:
        """Run backtest with 33 parameters"""
        if self.system is None:
            return 0.5175

        try:
            # Normalize parameters
            params = self.normalize_params(params)

            # Validate parameters
            is_valid, errors = validate_parameter_set(params)
            if not is_valid:
                logger.warning("Invalid params: {}".format(errors))
                return 0.5175

            # Inject parameters into system
            self.system.set_parameters(params)

            # Run backtest
            results = self.system.run_paper_trading(
                symbols_list=self.symbols_list,
                test_period_days=1000,
                use_optimized_params=True,
                injected_params=params
            )

            # Extract win rate
            if 'metrics' in results:
                metrics = results['metrics']
                win_rate = metrics.get('win_rate', 0.5175)
                total_trades = metrics.get('total_trades', 0)
                logger.info("    REAL: {} trades, {:.2%}".format(total_trades, win_rate))
                return win_rate
            return 0.5175

        except Exception as e:
            logger.error("[FAIL] Backtest error: {}".format(str(e)))
            return 0.5175

    def run_calibration(self):
        """Run 24-hour calibration loop"""
        logger.info("")
        logger.info("="*80)
        logger.info("STAGE 2 CALIBRATION: ALL 33 PARAMETERS × 24 HOURS")
        logger.info("="*80)
        logger.info("")
        logger.info("Parameters: 33 total (Tier 1: 20 + Tier 2: 8 + Tier 3: 5)")
        logger.info("Symbols: 48 NIFTY")
        logger.info("Data: 3 years historical")
        logger.info("Duration: 24 continuous hours")
        logger.info("Strategy: RANDOM PARAMETER SEARCH (not Bayesian optimization)")
        logger.info("")
        logger.info("Starting calibration...")
        logger.info("="*80)
        logger.info("")

        calibration_deadline = datetime.now() + pd.Timedelta(hours=self.duration_hours)

        while datetime.now() < calibration_deadline:
            self.iteration_count += 1
            elapsed_hours = (datetime.now() - self.start_time).total_seconds() / 3600

            # Generate parameters
            params = self.generate_random_params()
            params = self.normalize_params(params)

            # Log parameters
            logger.info("PARAMS: " + json.dumps(params))

            # Run backtest
            win_rate = self.run_backtest_with_params(params)

            # Store result
            result = {
                'iteration': self.iteration_count,
                'win_rate': win_rate,
                'params': params,
                'elapsed_hours': elapsed_hours
            }
            self.all_results.append(result)

            # Check for best
            if win_rate > self.best_win_rate:
                self.best_win_rate = win_rate
                self.best_params = params
                logger.info("[Iter {:4d}] {:.2%} [BEST!] [{:.2f}h]".format(
                    self.iteration_count, win_rate, elapsed_hours))
                logger.info("BEST_PARAMS: " + json.dumps(params))
            else:
                improvement = (win_rate - 0.5175) * 100
                logger.info("[Iter {:4d}] {:.2%} [+{:+.2f}%] [{:.2f}h]".format(
                    self.iteration_count, win_rate, improvement, elapsed_hours))

        # Final summary
        elapsed_hours = (datetime.now() - self.start_time).total_seconds() / 3600

        logger.info("")
        logger.info("="*80)
        logger.info("CALIBRATION COMPLETE - 24 HOURS DONE")
        logger.info("="*80)
        logger.info("Total Duration: {:.2f} hours".format(elapsed_hours))
        logger.info("Total Iterations: {}".format(self.iteration_count))
        logger.info("Best Win Rate: {:.2%}".format(self.best_win_rate))
        logger.info("Baseline (Stage 1): 51.75%")
        logger.info("Improvement: +{:.2f}%".format((self.best_win_rate - 0.5175) * 100))
        logger.info("="*80)
        logger.info("")

        # Save results
        results_file = "STAGE2_calibration_33params_results.json"
        with open(results_file, 'w') as f:
            json.dump({
                'calibration_type': '33_parameters_24_hours',
                'best_win_rate': float(self.best_win_rate),
                'best_parameters': self.best_params,
                'duration_hours': elapsed_hours,
                'total_iterations': self.iteration_count,
                'baseline_stage1': 0.5175,
                'improvement': (self.best_win_rate - 0.5175) * 100,
                'timestamp': datetime.now().isoformat(),
                'all_iterations': self.all_results[-10:] if len(self.all_results) > 10 else self.all_results
            }, f, indent=2)

        logger.info("Results saved: {}".format(results_file))


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("")
    logger.info("="*80)
    logger.info("STAGE 2 REAL CALIBRATION - ALL 33 PARAMETERS")
    logger.info("="*80)
    logger.info("")

    if not SYSTEM_AVAILABLE:
        logger.error("[FATAL] System not available")
        return

    # Initialize engine
    engine = CalibrationEngine33Params(duration_hours=24.0)

    # Load data
    if not engine.load_market_data():
        logger.error("[FATAL] Failed to load data")
        return

    # Run calibration
    engine.run_calibration()

    logger.info("")
    logger.info("STAGE 2 CALIBRATION FINISHED")
    logger.info("Check STAGE2_calibration_33params_results.json for results")
    logger.info("")


if __name__ == "__main__":
    main()
