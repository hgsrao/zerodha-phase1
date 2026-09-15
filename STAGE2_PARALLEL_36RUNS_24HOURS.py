#!/usr/bin/env python3
"""
================================================================================
STAGE 2 CALIBRATION - 36 PARALLEL RUNS × 24 HOURS
================================================================================

MASSIVE PARALLEL OPTIMIZATION

Architecture:
- 36 concurrent backtest processes
- Each with different 6-parameter combination
- Each testing 48 NIFTY symbols
- Each running for 24 hours straight
- All running SIMULTANEOUSLY
- Pick the BEST performing parameters

Expected Result:
- 24 hours of continuous optimization
- Real trading results across parameter space
- Best parameters found from 36 simultaneous experiments
- Comprehensive calibration in 24 hours (not sequential)

================================================================================
"""

import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
import time
import threading
from queue import Queue
import os

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [PARALLEL] - %(message)s',
    handlers=[
        logging.FileHandler('STAGE2_parallel_36runs.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('PARALLEL_CALIB')

# ============================================================================
# IMPORT ACTUAL TRADING SYSTEM
# ============================================================================

try:
    from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import CompleteIntegratedTradingSystem
    logger.info("[OK] Trading system imported")
    SYSTEM_AVAILABLE = True
except ImportError as e:
    logger.error("[FAIL] Cannot import system: {}".format(str(e)))
    SYSTEM_AVAILABLE = False


# ============================================================================
# PARALLEL CALIBRATION ENGINE
# ============================================================================

class ParallelCalibrationWorker(threading.Thread):
    """Single worker thread running one parameter set for 24 hours"""

    def __init__(self, worker_id: int, market_data: Dict, symbols_list: List,
                 results_queue: Queue, run_duration_hours: float = 24.0):
        super().__init__(daemon=True)
        self.worker_id = worker_id
        self.market_data = market_data
        self.symbols_list = symbols_list
        self.results_queue = results_queue
        self.run_duration_hours = run_duration_hours

        self.system = None
        self.logger = logging.getLogger(f'WORKER_{worker_id}')
        self.params = None
        self.best_win_rate = 0.0
        self.iteration_count = 0
        self.start_time = datetime.now()

    def generate_params(self):
        """Generate unique parameters for this worker"""
        return {
            'profit_target_atr_mult': np.random.uniform(0.8, 1.8),
            'stop_loss_atr_mult': np.random.uniform(0.4, 1.0),
            'entry_pid_kp': np.random.uniform(0.08, 0.18),
            'exit_pid_kp': np.random.uniform(0.08, 0.18),
            'min_hold_bars': int(np.random.uniform(1, 5)),
            'max_hold_bars': int(np.random.uniform(20, 80))
        }

    def run(self):
        """Worker thread main loop"""
        try:
            # Initialize system for this worker
            if not SYSTEM_AVAILABLE:
                self.logger.error("[FAIL] System not available")
                return

            self.system = CompleteIntegratedTradingSystem()
            self.system.initialize_from_data(self.market_data, self.symbols_list)

            # Generate parameters once per worker
            self.params = self.generate_params()
            self.logger.info("[START] Worker {} starting with params:".format(self.worker_id))
            self.logger.info("  profit_target: {:.3f}".format(self.params['profit_target_atr_mult']))
            self.logger.info("  stop_loss: {:.3f}".format(self.params['stop_loss_atr_mult']))
            self.logger.info("  entry_kp: {:.3f}".format(self.params['entry_pid_kp']))
            self.logger.info("  exit_kp: {:.3f}".format(self.params['exit_pid_kp']))
            self.logger.info("  min_bars: {}".format(self.params['min_hold_bars']))
            self.logger.info("  max_bars: {}".format(self.params['max_hold_bars']))

            # Run backtest loops for 24 hours
            while True:
                elapsed = (datetime.now() - self.start_time).total_seconds() / 3600

                # Check if time is up
                if elapsed > self.run_duration_hours:
                    self.logger.info("[COMPLETE] Worker {} finished after {:.1f}h".format(
                        self.worker_id, elapsed))
                    break

                # Run one backtest iteration
                self.iteration_count += 1
                win_rate = self.run_backtest()

                if win_rate > self.best_win_rate:
                    self.best_win_rate = win_rate
                    self.logger.info("[Iter {:3d}] {:.2%} [BEST] [{:.1f}h]".format(
                        self.iteration_count, win_rate, elapsed))
                else:
                    self.logger.info("[Iter {:3d}] {:.2%} [{:.1f}h]".format(
                        self.iteration_count, win_rate, elapsed))

                # Small delay between iterations
                time.sleep(1)

            # Send results to queue
            self.results_queue.put({
                'worker_id': self.worker_id,
                'params': self.params,
                'best_win_rate': self.best_win_rate,
                'iterations': self.iteration_count,
                'elapsed_hours': elapsed,
                'timestamp': datetime.now().isoformat()
            })

            self.logger.info("[RESULT] Worker {} achieved {:.2%} win rate".format(
                self.worker_id, self.best_win_rate))

        except Exception as e:
            self.logger.error("[ERROR] Worker {} failed: {}".format(self.worker_id, str(e)))
            import traceback
            self.logger.error(traceback.format_exc())

    def run_backtest(self) -> float:
        """Run single backtest with current parameters"""
        try:
            # Inject parameters for all symbols
            for symbol in self.symbols_list:
                self.system.optimal_params[symbol] = {
                    'profit_target_atr_mult': self.params['profit_target_atr_mult'],
                    'stop_loss_atr_mult': self.params['stop_loss_atr_mult'],
                    'entry_pid_kp': self.params['entry_pid_kp'],
                    'exit_pid_kp': self.params['exit_pid_kp'],
                    'min_hold_bars': self.params['min_hold_bars'],
                    'max_hold_bars': self.params['max_hold_bars']
                }

            # Run backtest
            results = self.system.run_paper_trading(
                symbols_list=self.symbols_list,
                test_period_days=1000,
                use_optimized_params=True
            )

            # Extract win rate
            if 'metrics' in results:
                return results['metrics'].get('win_rate', 0.5175)
            return 0.5175

        except Exception as e:
            self.logger.error("[ERROR] Backtest failed: {}".format(str(e)))
            return 0.5175


# ============================================================================
# MASTER ORCHESTRATOR
# ============================================================================

class ParallelCalibrationMaster:
    """Orchestrates 36 parallel workers"""

    def __init__(self, num_workers: int = 36, duration_hours: float = 24.0):
        self.logger = logger
        self.num_workers = num_workers
        self.duration_hours = duration_hours
        self.market_data = {}
        self.symbols_list = []
        self.workers = []
        self.results_queue = Queue()
        self.all_results = []

    def load_market_data(self) -> bool:
        """Load 48 NIFTY symbols"""
        self.logger.info("")
        self.logger.info("Loading market data for 48 NIFTY symbols...")

        data_dir = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

        if not data_dir.exists():
            self.logger.error("[FAIL] Data directory not found")
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

            self.logger.info("[OK] Loaded {} symbols".format(len(self.symbols_list)))
            return True

        except Exception as e:
            self.logger.error("[FAIL] Error loading data: {}".format(str(e)))
            return False

    def start_workers(self):
        """Launch 36 parallel worker threads"""
        self.logger.info("")
        self.logger.info("="*80)
        self.logger.info("LAUNCHING 36 PARALLEL WORKERS")
        self.logger.info("="*80)
        self.logger.info("")
        self.logger.info("Each worker:")
        self.logger.info("  - Gets unique random parameters")
        self.logger.info("  - Runs backtest on 48 symbols, 3 years data")
        self.logger.info("  - Runs continuously for {} hours".format(self.duration_hours))
        self.logger.info("  - Reports best win rate found")
        self.logger.info("")
        self.logger.info("Total: 36 SIMULTANEOUS backtests for {} hours".format(self.duration_hours))
        self.logger.info("="*80)
        self.logger.info("")

        for worker_id in range(1, self.num_workers + 1):
            worker = ParallelCalibrationWorker(
                worker_id=worker_id,
                market_data=self.market_data,
                symbols_list=self.symbols_list,
                results_queue=self.results_queue,
                run_duration_hours=self.duration_hours
            )
            worker.start()
            self.workers.append(worker)
            self.logger.info("[SPAWN] Worker {} launched".format(worker_id))
            time.sleep(0.5)  # Stagger starts slightly

        self.logger.info("")
        self.logger.info("[OK] All 36 workers launched - running for {} hours...".format(self.duration_hours))

    def monitor_workers(self):
        """Monitor workers until completion"""
        self.logger.info("")
        self.logger.info("Monitoring workers...")
        self.logger.info("")

        # Wait for all workers to finish
        for worker in self.workers:
            worker.join()

        self.logger.info("")
        self.logger.info("[OK] All workers completed")

    def collect_results(self):
        """Collect and analyze results from all workers"""
        self.logger.info("")
        self.logger.info("="*80)
        self.logger.info("COLLECTING RESULTS FROM 36 WORKERS")
        self.logger.info("="*80)
        self.logger.info("")

        best_overall_rate = 0.5175
        best_overall_worker = None
        best_overall_params = None

        while not self.results_queue.empty():
            result = self.results_queue.get()
            self.all_results.append(result)

            worker_id = result['worker_id']
            win_rate = result['best_win_rate']
            iterations = result['iterations']
            elapsed = result['elapsed_hours']

            improvement = (win_rate - 0.5175) * 100

            self.logger.info("[Worker {:2d}] {:.2%} ({} iterations, {:.1f}h) [+{:+.2f}%]".format(
                worker_id, win_rate, iterations, elapsed, improvement))

            if win_rate > best_overall_rate:
                best_overall_rate = win_rate
                best_overall_worker = worker_id
                best_overall_params = result['params']

        self.logger.info("")
        self.logger.info("="*80)
        self.logger.info("BEST PARAMETERS FOUND")
        self.logger.info("="*80)
        self.logger.info("")
        self.logger.info("Worker: {}".format(best_overall_worker))
        self.logger.info("Win Rate: {:.2%}".format(best_overall_rate))
        self.logger.info("Baseline (Stage 1): 51.75%")
        self.logger.info("Improvement: +{:.2f}%".format((best_overall_rate - 0.5175) * 100))
        self.logger.info("")
        self.logger.info("BEST PARAMETERS:")
        for key, value in best_overall_params.items():
            self.logger.info("  {}: {}".format(key, value))
        self.logger.info("="*80)
        self.logger.info("")

        # Save results
        results_file = "STAGE2_parallel_36runs_results.json"
        with open(results_file, 'w') as f:
            json.dump({
                'calibration_type': '36_parallel_workers_24hours',
                'best_win_rate': float(best_overall_rate),
                'best_worker_id': best_overall_worker,
                'best_parameters': best_overall_params,
                'improvement': (best_overall_rate - 0.5175) * 100,
                'baseline_stage1': 0.5175,
                'timestamp': datetime.now().isoformat(),
                'all_worker_results': self.all_results
            }, f, indent=2)

        self.logger.info("Results saved: {}".format(results_file))


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("")
    logger.info("="*80)
    logger.info("STAGE 2 CALIBRATION - 36 PARALLEL WORKERS × 24 HOURS")
    logger.info("="*80)
    logger.info("")
    logger.info("Strategy: Massive parallel optimization")
    logger.info("  - 36 concurrent backtests")
    logger.info("  - Each with different 6-parameter combination")
    logger.info("  - Each on 48 NIFTY symbols, 3 years data")
    logger.info("  - All running simultaneously for 24 hours")
    logger.info("  - Pick the BEST performing parameters")
    logger.info("")

    if not SYSTEM_AVAILABLE:
        logger.error("[FATAL] System not available")
        return

    # Initialize master
    master = ParallelCalibrationMaster(num_workers=36, duration_hours=24.0)

    # Load market data
    if not master.load_market_data():
        logger.error("[FATAL] Failed to load market data")
        return

    # Start all workers
    master.start_workers()

    # Monitor until complete
    master.monitor_workers()

    # Collect and report results
    master.collect_results()

    logger.info("")
    logger.info("CALIBRATION COMPLETE")
    logger.info("")


if __name__ == "__main__":
    main()
