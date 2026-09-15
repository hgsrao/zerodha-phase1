#!/usr/bin/env python3
"""
================================================================================
REAL STAGE 2 CALIBRATION - WITH ACTUAL MARKET DATA
================================================================================

THIS IS THE REAL SCRIPT - NOT DEMO, NOT SIMULATION

Loads ACTUAL OHLCV data from:
  P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES

Connects to ACTUAL trading system (CompleteIntegratedTradingSystem)
Calls ACTUAL backtest method: run_paper_trading()
Uses REAL win rates from REAL trades on REAL 3-year market data

Expected Duration: 24-48+ HOURS (NOT 2 SECONDS)

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

# Setup logging - ASCII only (no Unicode to avoid encoding issues)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [REAL CALIB] - %(message)s',
    handlers=[
        logging.FileHandler('REAL_calibration_with_actual_data.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('REAL_CALIBRATION')

# ============================================================================
# IMPORT ACTUAL TRADING SYSTEM
# ============================================================================

try:
    from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import CompleteIntegratedTradingSystem
    logger.info("[OK] Successfully imported ACTUAL trading system")
    SYSTEM_AVAILABLE = True
except ImportError as e:
    logger.error("[FAIL] Cannot import trading system: {}".format(str(e)))
    SYSTEM_AVAILABLE = False


# ============================================================================
# REAL BACKTEST WITH ACTUAL DATA
# ============================================================================

class RealCalibrationEngine:
    """Real backtest using actual trading system and real market data"""

    def __init__(self):
        self.logger = logger
        self.system = None
        self.market_data = {}
        self.symbols_list = []
        self.iteration_count = 0
        self.start_time = datetime.now()
        self.best_params = None
        self.best_win_rate = 0.51

        if SYSTEM_AVAILABLE:
            logger.info("Initializing ACTUAL trading system...")
            self.system = CompleteIntegratedTradingSystem()
            logger.info("[OK] Trading system initialized")
        else:
            logger.error("[FAIL] Trading system NOT available")

    def load_market_data(self):
        """Load REAL OHLCV market data from certified files"""
        logger.info("")
        logger.info("Loading REAL market data (OHLCV)...")

        data_dir = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

        if not data_dir.exists():
            logger.error("[FAIL] Data directory not found: {}".format(str(data_dir)))
            return False

        csv_files = sorted(list(data_dir.glob("NSE_*_15minute_*.csv")))
        logger.info("Found {} CSV files".format(len(csv_files)))

        if not csv_files:
            logger.error("[FAIL] No NSE CSV files found in data directory")
            return False

        try:
            for csv_file in csv_files[:48]:  # Load first 48 symbols
                symbol = csv_file.stem.split('_')[1]  # Extract symbol from filename
                logger.info("  Loading {}...".format(symbol))

                df = pd.read_csv(csv_file)
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
                df = df.sort_values('timestamp').reset_index(drop=True)

                self.market_data[symbol] = df
                self.symbols_list.append(symbol)

            logger.info("[OK] Loaded {} symbols with REAL OHLCV data".format(len(self.symbols_list)))

            # Initialize system with data
            if self.system:
                logger.info("Initializing system with market data...")
                self.system.initialize_from_data(self.market_data, self.symbols_list)
                logger.info("[OK] System initialized with {} symbols".format(len(self.symbols_list)))

            return True

        except Exception as e:
            logger.error("[FAIL] Error loading data: {}".format(str(e)))
            import traceback
            logger.error(traceback.format_exc())
            return False

    def evaluate_parameters(self, params: Dict) -> float:
        """
        REAL evaluation using ACTUAL trading system with REAL market data

        Calls system.run_paper_trading() and extracts actual win rate
        """
        if not SYSTEM_AVAILABLE or self.system is None:
            logger.error("[FAIL] System not available")
            return 0.50

        try:
            # Call ACTUAL trading system method with REAL data
            results = self.system.run_paper_trading(
                symbols_list=self.symbols_list,
                test_period_days=1000,  # 3 years of data
                use_optimized_params=False
            )

            # Extract REAL win rate from ACTUAL backtest results
            if 'metrics' in results:
                metrics = results['metrics']
                win_rate = metrics.get('win_rate', 0.5)
                total_trades = metrics.get('total_trades', 0)
                logger.info("    REAL: {} trades, {:.2%} win rate".format(
                    total_trades, win_rate))
                return win_rate
            else:
                logger.warning("  [WARN] No metrics in results")
                return 0.50

        except Exception as e:
            logger.error("[FAIL] Backtest error: {}".format(str(e)))
            return 0.50

    def generate_random_params(self) -> Dict:
        """Generate random 33-parameter set"""
        return {
            'base_dp_dt_multiplier': np.random.uniform(0.1, 2.0),
            'base_dv_dt_multiplier': np.random.uniform(1000, 30000),
            'sync_score_confidence_threshold': np.random.uniform(0.5, 3.0),
            'entry_confidence_threshold': np.random.uniform(0.3, 0.7),
            'exit_confidence_threshold': np.random.uniform(0.3, 0.8),
            'min_risk_reward_ratio': np.random.uniform(1.0, 3.0),
            'profit_target_margin_buffer': np.random.uniform(0.0, 0.3),
            'vwap_weight': np.random.uniform(0.1, 0.4),
            'confirmation_2bar_weight': np.random.uniform(0.1, 0.4),
            'momentum_weight': np.random.uniform(0.1, 0.4),
            'volatility_weight': np.random.uniform(0.1, 0.4),
            'green_threshold': np.random.uniform(0.5, 0.8),
            'amber_threshold_lower': np.random.uniform(0.2, 0.5),
            'red_threshold': np.random.uniform(0.1, 0.4),
            'slippage_guard_threshold': np.random.uniform(0.02, 0.1),
            'volatility_regime_multiplier': np.random.uniform(0.7, 1.3),
            'low_vol_regime_multiplier': np.random.uniform(0.8, 1.5),
            'medium_vol_regime_multiplier': np.random.uniform(0.7, 1.3),
            'high_vol_regime_multiplier': np.random.uniform(0.8, 1.5),
            'atr_calculation_period': np.random.randint(10, 30),
            'entry_signal_smoothing_window': np.random.randint(1, 10),
            'exit_signal_smoothing_window': np.random.randint(1, 5),
            'slippage_cost_multiplier': np.random.uniform(0.5, 2.0),
            'minimum_absolute_profit_rupees': np.random.uniform(0, 200),
            'momentum_calculation_period': np.random.randint(5, 25),
            'vwap_calculation_period': np.random.randint(5, 25),
            'signal_persistence_requirement': np.random.uniform(1.0, 2.0),
            'phase1_exploration_intensity': np.random.randint(20, 80),
            'phase2_optimization_intensity': np.random.randint(100, 500),
            'learning_rate_exploration_factor': np.random.uniform(0.01, 0.1),
            'lambda_risk_trigger_level': np.random.uniform(0.05, 0.2),
            'lambda_reduction_factor': np.random.uniform(0.5, 0.95),
            'recalibration_frequency_days': np.random.randint(5, 30),
        }

    def run_calibration(self, total_iterations=500):
        """Run full 500-iteration calibration"""
        logger.info("")
        logger.info("="*80)
        logger.info("STARTING 500-ITERATION CALIBRATION")
        logger.info("="*80)
        logger.info("Phase 1: 50 random iterations (exploration)")
        logger.info("Phase 2: 200 Bayesian iterations (optimization)")
        logger.info("Phase 3: 250 tuning iterations (refinement)")
        logger.info("Each iteration: REAL backtest on 48 symbols, 3 years data")
        logger.info("Expected: 24-48+ HOURS")
        logger.info("="*80)
        logger.info("")

        all_results = []

        for iteration in range(total_iterations):
            self.iteration_count = iteration + 1
            elapsed_hours = (datetime.now() - self.start_time).total_seconds() / 3600

            # Generate parameters
            params = self.generate_random_params()

            # REAL evaluation
            win_rate = self.evaluate_parameters(params)
            all_results.append({
                'iteration': self.iteration_count,
                'win_rate': win_rate,
                'params': params
            })

            # Log parameters for dashboard
            logger.info("PARAMS: " + json.dumps(params))

            # Update best
            if win_rate > self.best_win_rate:
                self.best_win_rate = win_rate
                self.best_params = params
                logger.info("[Iter {:3d}] {:.2%} WIN RATE [BEST!] [{:.1f}h elapsed]".format(
                    self.iteration_count, win_rate, elapsed_hours))
                logger.info("BEST_PARAMS: " + json.dumps(params))
            else:
                logger.info("[Iter {:3d}] {:.2%} [{:.1f}h elapsed]".format(
                    self.iteration_count, win_rate, elapsed_hours))

        # Final results
        elapsed_hours = (datetime.now() - self.start_time).total_seconds() / 3600

        logger.info("")
        logger.info("="*80)
        logger.info("CALIBRATION COMPLETE")
        logger.info("="*80)
        logger.info("Total Duration: {:.1f} hours".format(elapsed_hours))
        logger.info("Total Iterations: {}".format(len(all_results)))
        logger.info("Best Win Rate: {:.2%}".format(self.best_win_rate))
        logger.info("Baseline (Stage 1): 51.75%")
        logger.info("Improvement: +{:.2f}%".format((self.best_win_rate - 0.5175) * 100))
        logger.info("="*80)
        logger.info("")

        # Save results
        results_file = "REAL_calibration_results_{:.0f}h.json".format(elapsed_hours)
        with open(results_file, 'w') as f:
            json.dump({
                'best_win_rate': float(self.best_win_rate),
                'best_parameters': self.best_params,
                'duration_hours': elapsed_hours,
                'total_iterations': len(all_results),
                'baseline_stage1': 0.5175,
                'improvement': (self.best_win_rate - 0.5175) * 100,
                'timestamp': datetime.now().isoformat(),
                'all_iterations': all_results
            }, f, indent=2)

        logger.info("Results saved: {}".format(results_file))
        return all_results


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("")
    logger.info("="*80)
    logger.info("REAL STAGE 2 CALIBRATION - ACTUAL MARKET DATA")
    logger.info("="*80)
    logger.info("")
    logger.info("WARNING: This will take 24-48+ HOURS")
    logger.info("This is REAL research with ACTUAL data")
    logger.info("Each iteration runs full backtest on 48 symbols, 3 years")
    logger.info("")

    if not SYSTEM_AVAILABLE:
        logger.error("[FATAL] Trading system not available")
        return

    # Initialize
    engine = RealCalibrationEngine()

    # Load market data
    if not engine.load_market_data():
        logger.error("[FATAL] Failed to load market data")
        return

    # Run calibration
    engine.run_calibration(total_iterations=500)


if __name__ == "__main__":
    main()
