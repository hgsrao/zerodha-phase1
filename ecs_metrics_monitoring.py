#!/usr/bin/env python3
"""
================================================================================
ECS METRICS & MONITORING LAYER - INTEGRATED INTO R1
================================================================================

Black Box 14: Metrics Aggregator
Black Box 15: Iteration Manager
Black Box 16: Calibration Logger

Real-time metrics collection and progress tracking:
- Win rate, Sharpe ratio, drawdown aggregation
- Iteration counting + convergence detection
- Per-iteration logging + dashboard updates
- JSON export for analysis

================================================================================
"""

import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
import threading
from dataclasses import dataclass, asdict
import sqlite3

# ============================================================================
# METRICS AGGREGATOR
# ============================================================================

@dataclass
class AggregatedMetrics:
    """Aggregated metrics across all trades"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    total_profit: float
    max_drawdown: float
    sharpe_ratio: float
    return_percent: float
    best_trade: float
    worst_trade: float
    consecutive_wins: int
    consecutive_losses: int
    recovery_factor: float
    calmar_ratio: float

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class MetricsAggregator:
    """
    Aggregates backtest metrics across all iterations.
    Tracks win rate, Sharpe ratio, drawdown, and custom metrics.
    """

    def __init__(self):
        self.logger = logging.getLogger("MetricsAggregator")
        self.history = []  # List of aggregated metrics per iteration
        self._lock = threading.RLock()

    def aggregate_trades(self, trades: List) -> AggregatedMetrics:
        """
        Calculate comprehensive metrics from trade list.

        Args:
            trades: List of Trade objects from backtest

        Returns:
            AggregatedMetrics: Aggregated results
        """
        if not trades:
            return self._empty_metrics()

        # Extract P&L data
        pls = np.array([t.profit_loss for t in trades])
        pl_percents = np.array([t.profit_loss_percent for t in trades])

        # Win/loss stats
        winning_trades = [t for t in trades if t.profit_loss > 0]
        losing_trades = [t for t in trades if t.profit_loss <= 0]

        win_rate = len(winning_trades) / len(trades)
        avg_win = np.mean([t.profit_loss for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.profit_loss for t in losing_trades]) if losing_trades else 0

        total_wins = sum(t.profit_loss for t in winning_trades)
        total_losses = abs(sum(t.profit_loss for t in losing_trades))
        profit_factor = total_wins / total_losses if total_losses > 0 else 999

        # Drawdown & recovery
        cumulative = np.cumsum(pls)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - running_max)
        max_drawdown = np.min(drawdowns) if len(drawdowns) > 0 else 0

        # Sharpe ratio
        sharpe = np.mean(pl_percents) / (np.std(pl_percents) + 1e-6) if len(pl_percents) > 0 else 0

        # Return
        total_profit = np.sum(pls)
        return_percent = total_profit / 100000  # Assuming 100k starting capital

        # Consecutive wins/losses
        consecutive_wins = self._calc_consecutive(trades, 'wins')
        consecutive_losses = self._calc_consecutive(trades, 'losses')

        # Best/worst trade
        best_trade = np.max(pls) if len(pls) > 0 else 0
        worst_trade = np.min(pls) if len(pls) > 0 else 0

        # Recovery factor
        recovery_factor = total_profit / abs(max_drawdown) if max_drawdown != 0 else 999

        # Calmar ratio
        calmar = (return_percent * 252) / abs(max_drawdown) if max_drawdown != 0 else 999

        return AggregatedMetrics(
            total_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            total_profit=total_profit,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            return_percent=return_percent,
            best_trade=best_trade,
            worst_trade=worst_trade,
            consecutive_wins=consecutive_wins,
            consecutive_losses=consecutive_losses,
            recovery_factor=recovery_factor,
            calmar_ratio=calmar
        )

    def record_iteration_metrics(self, iteration: int, metrics: AggregatedMetrics):
        """Record metrics for one iteration"""
        with self._lock:
            self.history.append({
                'iteration': iteration,
                'timestamp': datetime.now().isoformat(),
                'metrics': metrics
            })

    def get_metrics_history(self) -> List[Dict]:
        """Get all recorded iteration metrics"""
        with self._lock:
            return self.history.copy()

    def get_latest_metrics(self) -> Optional[AggregatedMetrics]:
        """Get metrics from most recent iteration"""
        with self._lock:
            if not self.history:
                return None
            return self.history[-1]['metrics']

    def export_to_csv(self, filename: str):
        """Export metrics history to CSV"""
        if not self.history:
            self.logger.warning("No metrics history to export")
            return

        rows = []
        for entry in self.history:
            m = entry['metrics']
            rows.append({
                'iteration': entry['iteration'],
                'timestamp': entry['timestamp'],
                'total_trades': m.total_trades,
                'win_rate': f"{m.win_rate:.2%}",
                'avg_win': f"{m.avg_win:.2f}",
                'avg_loss': f"{m.avg_loss:.2f}",
                'profit_factor': f"{m.profit_factor:.2f}",
                'total_profit': f"{m.total_profit:.2f}",
                'sharpe_ratio': f"{m.sharpe_ratio:.2f}",
                'max_drawdown': f"{m.max_drawdown:.2f}",
                'return_percent': f"{m.return_percent:.2%}",
            })

        df = pd.DataFrame(rows)
        df.to_csv(filename, index=False)
        self.logger.info(f"[OK] Exported {len(rows)} iterations to {filename}")

    def _empty_metrics(self) -> AggregatedMetrics:
        """Return empty metrics"""
        return AggregatedMetrics(
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0.5, avg_win=0, avg_loss=0, profit_factor=0,
            total_profit=0, max_drawdown=0, sharpe_ratio=0,
            return_percent=0, best_trade=0, worst_trade=0,
            consecutive_wins=0, consecutive_losses=0,
            recovery_factor=0, calmar_ratio=0
        )

    def _calc_consecutive(self, trades: List, trade_type: str) -> int:
        """Calculate longest consecutive wins or losses"""
        if not trades:
            return 0

        is_win = [t.profit_loss > 0 for t in trades]
        current_streak = 0
        max_streak = 0

        for win in is_win:
            if (trade_type == 'wins' and win) or (trade_type == 'losses' and not win):
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak


# ============================================================================
# ITERATION MANAGER
# ============================================================================

class IterationManager:
    """
    Tracks iteration count, phase transitions, and convergence detection.
    Detects when algorithm has converged or hit diminishing returns.
    """

    def __init__(self, db_path: str = "iteration_tracking.db"):
        self.logger = logging.getLogger("IterationManager")
        self.db_path = db_path
        self._lock = threading.RLock()

        # Convergence parameters
        self.CONVERGENCE_WINDOW = 50  # Last N iterations to check
        self.CONVERGENCE_THRESHOLD = 0.01  # 1% improvement threshold
        self.STAGNATION_ITERATIONS = 100  # No improvement for N iterations

        self._init_database()

    def _init_database(self):
        """Initialize SQLite database for iteration tracking"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS iterations (
                iteration_id INTEGER PRIMARY KEY,
                phase TEXT,
                iteration INTEGER,
                win_rate REAL,
                sharpe_ratio REAL,
                total_trades INTEGER,
                is_best INTEGER,
                timestamp TEXT
            )
        """)
        conn.commit()
        conn.close()

    def record_iteration(self, phase: str, iteration: int,
                        win_rate: float, sharpe_ratio: float,
                        total_trades: int, is_best: bool):
        """Record one iteration"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO iterations
                (phase, iteration, win_rate, sharpe_ratio, total_trades, is_best, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                phase, iteration, win_rate, sharpe_ratio,
                total_trades, 1 if is_best else 0, datetime.now().isoformat()
            ))
            conn.commit()
            conn.close()

    def get_iteration_count(self) -> int:
        """Get total iterations completed"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM iterations")
            count = cursor.fetchone()[0]
            conn.close()
            return count

    def has_converged(self) -> Tuple[bool, str]:
        """
        Detect if optimization has converged.
        Returns: (has_converged, reason)
        """
        with self._lock:
            count = self.get_iteration_count()
            if count < self.CONVERGENCE_WINDOW:
                return False, "Too few iterations"

            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT win_rate FROM iterations
                ORDER BY iteration_id DESC
                LIMIT ?
            """, (self.CONVERGENCE_WINDOW,))
            recent_wins = [row[0] for row in cursor.fetchall()]
            conn.close()

            # Check if last N iterations show improvement
            if not recent_wins:
                return False, "No data"

            best_recent = max(recent_wins)
            baseline = recent_wins[0] if len(recent_wins) > 1 else recent_wins[0]
            improvement = (best_recent - baseline) / max(baseline, 0.001)

            if improvement < self.CONVERGENCE_THRESHOLD:
                return True, f"Improvement < {self.CONVERGENCE_THRESHOLD:.1%}"

            return False, "Still improving"

    def check_stagnation(self, best_win_rate: float) -> Tuple[bool, int]:
        """
        Check if optimization is stagnating (no improvement for N iterations).
        Returns: (is_stagnating, iterations_since_last_improvement)
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT iteration, win_rate FROM iterations
                ORDER BY iteration_id DESC
            """)
            rows = list(cursor.fetchall())
            conn.close()

            iterations_since_improvement = 0
            for iteration, win_rate in rows:
                if win_rate < best_win_rate:
                    iterations_since_improvement += 1
                else:
                    break

            is_stagnating = (
                iterations_since_improvement >= self.STAGNATION_ITERATIONS
            )

            return is_stagnating, iterations_since_improvement

    def get_phase_summary(self, phase: str) -> Dict:
        """Get summary for one phase"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    MAX(win_rate) as best_wr,
                    AVG(win_rate) as avg_wr,
                    MAX(sharpe_ratio) as best_sharpe
                FROM iterations
                WHERE phase = ?
            """, (phase,))

            row = cursor.fetchone()
            conn.close()

            if row[0] == 0:
                return None

            return {
                'phase': phase,
                'total_iterations': row[0],
                'best_win_rate': row[1],
                'avg_win_rate': row[2],
                'best_sharpe': row[3]
            }

    def get_convergence_curve(self, phase: str) -> List[float]:
        """Get win rate progression for plotting"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT win_rate FROM iterations
                WHERE phase = ?
                ORDER BY iteration_id
            """, (phase,))

            win_rates = [row[0] for row in cursor.fetchall()]
            conn.close()
            return win_rates


# ============================================================================
# CALIBRATION LOGGER
# ============================================================================

class CalibrationLogger:
    """
    Logs calibration progress with per-iteration details.
    Creates human-readable logs and machine-readable JSON.
    """

    def __init__(self, log_name: str = "calibration"):
        self.log_name = log_name
        self.json_file = f"{log_name}_progress.json"
        self.logger = logging.getLogger(f"CalibrationLogger_{log_name}")

        # Setup file handler
        handler = logging.FileHandler(f"{log_name}.log")
        formatter = logging.Formatter(
            '%(asctime)s - [%(name)s] - %(message)s'
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        self.iteration_log = []
        self._lock = threading.RLock()

    def log_phase_start(self, phase: str, description: str):
        """Log phase start"""
        self.logger.info("="*80)
        self.logger.info(f"PHASE: {phase}")
        self.logger.info(f"Description: {description}")
        self.logger.info("="*80)

    def log_iteration(self, iteration: int, phase: str,
                     win_rate: float, is_best: bool = False,
                     metrics: Dict = None):
        """Log single iteration"""
        status = "[BEST!]" if is_best else "[+]"
        improvement = (win_rate - 0.5175) * 100

        msg = f"[Iter {iteration:5d}] {win_rate:.2%} {status} [+{improvement:+.2f}%]"
        self.logger.info(msg)

        # Store in JSON log
        with self._lock:
            self.iteration_log.append({
                'iteration': iteration,
                'phase': phase,
                'timestamp': datetime.now().isoformat(),
                'win_rate': win_rate,
                'improvement': improvement,
                'is_best': is_best,
                'metrics': metrics or {}
            })

    def log_phase_complete(self, phase: str, total_iterations: int,
                          best_win_rate: float, duration_hours: float):
        """Log phase completion"""
        improvement = (best_win_rate - 0.5175) * 100

        self.logger.info("")
        self.logger.info(f"Phase {phase} complete:")
        self.logger.info(f"  Iterations: {total_iterations}")
        self.logger.info(f"  Best win rate: {best_win_rate:.2%}")
        self.logger.info(f"  Improvement: +{improvement:.2f}%")
        self.logger.info(f"  Duration: {duration_hours:.2f} hours")
        self.logger.info("")

    def log_calibration_complete(self, best_win_rate: float,
                                 total_iterations: int,
                                 total_duration_hours: float):
        """Log calibration completion"""
        improvement = (best_win_rate - 0.5175) * 100

        self.logger.info("="*80)
        self.logger.info("CALIBRATION COMPLETE")
        self.logger.info("="*80)
        self.logger.info(f"Best win rate: {best_win_rate:.2%}")
        self.logger.info(f"Improvement: +{improvement:.2f}%")
        self.logger.info(f"Total iterations: {total_iterations}")
        self.logger.info(f"Total duration: {total_duration_hours:.2f} hours")
        self.logger.info(f"Avg time per iteration: {(total_duration_hours * 3600 / total_iterations):.1f}s")
        self.logger.info("="*80)
        self.logger.info("")

    def export_json(self):
        """Export iteration log to JSON"""
        with self._lock:
            with open(self.json_file, 'w') as f:
                json.dump(self.iteration_log, f, indent=2)

        self.logger.info(f"[OK] Exported {len(self.iteration_log)} iterations to {self.json_file}")

    def get_progress_summary(self) -> Dict:
        """Get summary of logged progress"""
        with self._lock:
            if not self.iteration_log:
                return {}

            win_rates = [it['win_rate'] for it in self.iteration_log]
            return {
                'total_logged': len(self.iteration_log),
                'best_win_rate': max(win_rates),
                'avg_win_rate': np.mean(win_rates),
                'std_dev': np.std(win_rates)
            }


# ============================================================================
# MONITORING DASHBOARD (Console Output)
# ============================================================================

class MonitoringDashboard:
    """
    Real-time console dashboard for calibration progress.
    Updates iteration count, best parameters, convergence status.
    """

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.logger = logging.getLogger("MonitoringDashboard")

    def print_status(self):
        """Print current status to console"""
        status = self.orchestrator.get_status()

        print("\n" + "="*80)
        print("CALIBRATION STATUS")
        print("="*80)
        print(f"Phase: {status['phase']}")
        print(f"Mode: {status['mode']}")
        print(f"Iterations: {status['iterations']}")
        print(f"Best win rate: {status['best_win_rate']}")
        print(f"Elapsed time: {status['elapsed_hours']}")
        print(f"Running: {status['is_running']}")
        print("="*80 + "\n")

    def print_metrics(self, metrics: AggregatedMetrics):
        """Print metrics summary"""
        print("\n" + "-"*80)
        print("METRICS SUMMARY")
        print("-"*80)
        print(f"Total trades: {metrics.total_trades}")
        print(f"Win rate: {metrics.win_rate:.2%}")
        print(f"Avg win: ₹{metrics.avg_win:.2f}")
        print(f"Avg loss: ₹{metrics.avg_loss:.2f}")
        print(f"Profit factor: {metrics.profit_factor:.2f}")
        print(f"Total P&L: ₹{metrics.total_profit:.2f}")
        print(f"Max drawdown: {metrics.max_drawdown:.2f}")
        print(f"Sharpe ratio: {metrics.sharpe_ratio:.2f}")
        print(f"Return: {metrics.return_percent:.2%}")
        print("-"*80 + "\n")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test metrics aggregator
    print("\n=== TESTING METRICS AGGREGATOR ===")
    aggregator = MetricsAggregator()

    # Create dummy trades
    from ecs_backtest_infrastructure import Trade

    dummy_trades = [
        Trade(
            symbol="INFY",
            entry_timestamp="2026-01-01T09:00:00",
            entry_price=100,
            exit_timestamp="2026-01-01T10:00:00",
            exit_price=101,
            quantity=1,
            direction="LONG",
            profit_loss=50,
            profit_loss_percent=0.005,
            entry_cost=30,
            exit_cost=30,
            slippage=5,
            hold_bars=1
        ),
        Trade(
            symbol="TCS",
            entry_timestamp="2026-01-01T11:00:00",
            entry_price=100,
            exit_timestamp="2026-01-01T12:00:00",
            exit_price=99,
            quantity=1,
            direction="LONG",
            profit_loss=-50,
            profit_loss_percent=-0.005,
            entry_cost=30,
            exit_cost=30,
            slippage=5,
            hold_bars=1
        ),
    ]

    metrics = aggregator.aggregate_trades(dummy_trades)
    print(f"Win rate: {metrics.win_rate:.2%}")
    print(f"Total profit: {metrics.total_profit:.2f}")

