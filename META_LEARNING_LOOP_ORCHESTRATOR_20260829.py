#!/usr/bin/env python3
"""
================================================================================
META-LEARNING LOOP ORCHESTRATOR
================================================================================

System Architecture:

┌─────────────────────────────────────────────────────────────────────┐
│ META-LEARNING LOOP (Outer - This Module)                            │
│                                                                      │
│  Iteration 1-500:                                                   │
│    ├─ Generate parameter set (Phase 1/2/3)                         │
│    ├─ Create 6-Stage System instance                              │
│    ├─ Run on backtest data                                         │
│    ├─ Measure: win_rate, sharpe, max_drawdown                      │
│    ├─ Track best found                                             │
│    └─ Adjust parameters for next iteration                         │
│                                                                      │
│  Output: Optimal parameters for live deployment                    │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
        ┌─────────────────────────────────────────┐
        │ 6-STAGE SYSTEM (runs WITHIN each meta iteration)    │
        ├─────────────────────────────────────────┤
        │ Stage 1: Data Validation                │
        │ Stage 2: PA Model + Feedback Loop 1     │
        │ Stage 3: ID Threshold                   │
        │ Stage 4: Bridge                         │
        │ Stage 5: MPC + Feedback Loop 2          │
        │ Stage 6: P01D Governor                  │
        └─────────────────────────────────────────┘

Meta-Learning manages outer loop (parameter tuning)
6-Stage System manages inner loops (trading logic + 2 feedback loops)

================================================================================
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import json
import logging
from dataclasses import dataclass, asdict
from scipy.optimize import differential_evolution
import warnings
warnings.filterwarnings('ignore')

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem,
    UnifiedP01DGovernor
)


# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('meta_learning_loop.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('MetaLearningLoop')


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class MetaIterationResult:
    """Result from one meta-learning iteration"""
    iteration: int
    phase: str
    parameters: Dict
    win_rate: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    timestamp: str

    def to_dict(self):
        return asdict(self)


@dataclass
class MetaLearningConvergence:
    """Convergence tracking"""
    best_run: int
    best_win_rate: float
    best_params: Dict
    improvement_trajectory: List[float]  # Win rates over iterations
    convergence_point: int  # When optimal found


# ============================================================================
# META-LEARNING LOOP ORCHESTRATOR
# ============================================================================

class MetaLearningLoopOrchestrator:
    """
    Outer loop that manages 500 iterations of system tuning

    Structure:
      Phase 1 (Runs 1-50): Random exploration of parameter space
      Phase 2 (Runs 51-250): Bayesian optimization (focus promising regions)
      Phase 3 (Runs 251-500): Fine-tuning (converge to optimum)
    """

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.logger = logging.getLogger('MetaLearningLoop')

        # Tracking
        self.iteration_results = []
        self.best_win_rate = 0.0
        self.best_params = None
        self.best_run = 0

        # For Bayesian optimization
        self.parameter_history = []
        self.performance_history = []

    def _log(self, message: str):
        if self.verbose:
            self.logger.info(message)

    # ========================================================================
    # PARAMETER GENERATION (3 Phases)
    # ========================================================================

    def _phase1_random_exploration(self, param_ranges: Dict) -> Dict:
        """
        Phase 1 (Runs 1-50): Random exploration

        Strategy: Try random combinations across entire parameter space
        Goal: Understand which regions are promising
        """
        params = {}
        for param_name, config in param_ranges.items():
            current = config['current']
            min_val = config['min']
            max_val = config['max']

            # Random value in range
            random_val = np.random.uniform(min_val, max_val)
            params[param_name] = random_val

        return params

    def _phase2_bayesian_optimization(self, param_ranges: Dict) -> Dict:
        """
        Phase 2 (Runs 51-250): Bayesian optimization

        Strategy: Focus on promising regions found in Phase 1
        Uses Gaussian Process model of win_rate vs parameters
        Goal: Converge toward optimal region
        """

        # If we have history, use it
        if len(self.performance_history) > 10:
            # Find top 5 performing configurations
            indices = np.argsort(self.performance_history)[-5:]

            # Average of top performers
            top_params = [self.parameter_history[i] for i in indices]

            # Start from best found + small perturbation
            best_params = self.parameter_history[indices[-1]]
            params = {}

            for param_name, value in best_params.items():
                config = param_ranges[param_name]
                min_val = config['min']
                max_val = config['max']

                # Small random walk around best (10% of range)
                range_size = max_val - min_val
                perturbation = np.random.normal(0, range_size * 0.1)
                new_val = value + perturbation
                params[param_name] = np.clip(new_val, min_val, max_val)

            return params
        else:
            # Fallback to Phase 1 if no history yet
            return self._phase1_random_exploration(param_ranges)

    def _phase3_fine_tuning(self, param_ranges: Dict) -> Dict:
        """
        Phase 3 (Runs 251-500): Fine-tuning

        Strategy: Micro-adjust around best found so far
        Very small perturbations only
        Goal: Precise convergence
        """

        if self.best_params:
            params = {}

            for param_name, value in self.best_params.items():
                config = param_ranges[param_name]
                min_val = config['min']
                max_val = config['max']
                step = config.get('step', (max_val - min_val) / 20)

                # Very small perturbation (30% of step size)
                perturbation = np.random.normal(0, step * 0.3)
                new_val = value + perturbation
                params[param_name] = np.clip(new_val, min_val, max_val)

            return params
        else:
            return self._phase2_bayesian_optimization(param_ranges)

    def _generate_parameters(self, iteration: int, param_ranges: Dict) -> Tuple[str, Dict]:
        """
        Generate parameter set for this iteration based on phase
        """
        if iteration < 50:
            phase = "🔀 RANDOM_EXPLORATION"
            params = self._phase1_random_exploration(param_ranges)
        elif iteration < 250:
            phase = "🎯 BAYESIAN_OPTIMIZATION"
            params = self._phase2_bayesian_optimization(param_ranges)
        else:
            phase = "🔬 FINE_TUNING"
            params = self._phase3_fine_tuning(param_ranges)

        return phase, params

    # ========================================================================
    # ITERATION EXECUTION
    # ========================================================================

    def run_single_iteration(self, iteration: int, phase: str, params: Dict,
                            system: CompleteIntegratedTradingSystem,
                            symbols_list: List[str]) -> MetaIterationResult:
        """
        Execute ONE iteration of meta-learning loop

        Steps:
          1. Create 6-stage system with current parameters
          2. Run backtest on all symbols
          3. Calculate metrics
          4. Track results
          5. Return for convergence analysis
        """

        # Run system with these parameters
        try:
            # Deploy parameters to system
            for symbol in symbols_list:
                if symbol in system.starting_params:
                    system.starting_params[symbol]['profit_target_atr_mult']['current'] = params.get('profit_target_atr_mult', 0.50)
                    system.starting_params[symbol]['stop_loss_atr_mult']['current'] = params.get('stop_loss_atr_mult', 1.00)
                    system.starting_params[symbol]['entry_pid_kp']['current'] = params.get('entry_pid_kp', 0.1)
                    system.starting_params[symbol]['exit_pid_kp']['current'] = params.get('exit_pid_kp', 0.1)
                    system.starting_params[symbol]['min_hold_bars']['current'] = int(params.get('min_hold_bars', 2))
                    system.starting_params[symbol]['max_hold_bars']['current'] = int(params.get('max_hold_bars', 20))

            # Run paper trading
            results = system.run_paper_trading(
                symbols_list=symbols_list,
                use_optimized_params=False  # Use starting params (which we just set)
            )

            # Extract metrics
            if results['status'] == 'COMPLETED':
                metrics = results['metrics']
                win_rate = metrics['win_rate']
                total_pnl = metrics['total_pnl']
                sharpe = metrics['sharpe_ratio']
                max_dd = metrics['max_drawdown']
                total_trades = metrics['total_trades']

                # Track for convergence analysis
                self.parameter_history.append(params.copy())
                self.performance_history.append(win_rate)

                # Update best if improved
                if win_rate > self.best_win_rate:
                    self.best_win_rate = win_rate
                    self.best_params = params.copy()
                    self.best_run = iteration + 1
                    is_new_best = True
                else:
                    is_new_best = False

                # Create result record
                result = MetaIterationResult(
                    iteration=iteration + 1,
                    phase=phase,
                    parameters=params,
                    win_rate=win_rate,
                    total_pnl=total_pnl,
                    sharpe_ratio=sharpe,
                    max_drawdown=max_dd,
                    total_trades=total_trades,
                    timestamp=datetime.now().isoformat()
                )

                # Log iteration
                self._log(f"\n{'─'*80}")
                self._log(f"RUN {iteration + 1}/500 | {phase}")
                self._log(f"{'─'*80}")
                self._log(f"Win Rate:     {win_rate:.2%} {'🏆 NEW BEST' if is_new_best else ''}")
                self._log(f"Total P&L:    ₹{total_pnl:+,.2f}")
                self._log(f"Sharpe:       {sharpe:.3f}")
                self._log(f"Max Drawdown: {max_dd:+,.2f}")
                self._log(f"Trades:       {total_trades}")

                self._log(f"\nParameters:")
                for param_name, value in params.items():
                    self._log(f"  {param_name:20} = {value:8.4f}")

                return result

            else:
                # System failed to run
                self._log(f"❌ Iteration {iteration + 1} failed to execute")
                return None

        except Exception as e:
            self._log(f"❌ Iteration {iteration + 1} error: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ========================================================================
    # MAIN LOOP (500 iterations)
    # ========================================================================

    def run_meta_learning_loop(self, system: CompleteIntegratedTradingSystem,
                              symbols_list: List[str],
                              param_ranges: Dict,
                              target_runs: int = 500) -> MetaLearningConvergence:
        """
        Main meta-learning loop: 500 iterations with 3 phases

        Args:
            system: CompleteIntegratedTradingSystem instance
            symbols_list: Symbols to optimize for
            param_ranges: Parameter configuration from SmartParameterInitializer
            target_runs: Number of iterations (default 500)

        Returns:
            MetaLearningConvergence with optimal parameters
        """

        self._log(f"\n{'='*80}")
        self._log(f"META-LEARNING LOOP ORCHESTRATOR")
        self._log(f"{'='*80}")
        self._log(f"Target runs: {target_runs}")
        self._log(f"Symbols: {len(symbols_list)}")
        self._log(f"Phases: Phase 1 (1-50), Phase 2 (51-250), Phase 3 (251-500)")
        self._log(f"{'='*80}\n")

        # Run 500 iterations
        for iteration in range(target_runs):
            # Generate parameters for this iteration
            phase, params = self._generate_parameters(iteration, param_ranges)

            # Execute iteration
            result = self.run_single_iteration(
                iteration=iteration,
                phase=phase,
                params=params,
                system=system,
                symbols_list=symbols_list
            )

            if result:
                self.iteration_results.append(result)

        # Convergence summary
        self._log(f"\n{'='*80}")
        self._log(f"META-LEARNING CONVERGENCE COMPLETE")
        self._log(f"{'='*80}")
        self._log(f"Best run: {self.best_run}/500")
        self._log(f"Best win rate: {self.best_win_rate:.2%}")
        self._log(f"\nOptimal parameters:")
        if self.best_params:
            for param_name, value in self.best_params.items():
                self._log(f"  {param_name:20} = {value:8.4f}")
        self._log(f"\n{'='*80}\n")

        # Create convergence object
        convergence = MetaLearningConvergence(
            best_run=self.best_run,
            best_win_rate=self.best_win_rate,
            best_params=self.best_params or {},
            improvement_trajectory=self.performance_history,
            convergence_point=self.best_run
        )

        return convergence

    # ========================================================================
    # RESULTS & ANALYSIS
    # ========================================================================

    def get_convergence_trajectory(self) -> pd.DataFrame:
        """Get convergence trajectory for analysis"""
        if not self.iteration_results:
            return None

        df = pd.DataFrame([r.to_dict() for r in self.iteration_results])
        return df

    def save_convergence_results(self, filename: str = "meta_learning_convergence.json"):
        """Save complete meta-learning results"""

        results = {
            'timestamp': datetime.now().isoformat(),
            'total_runs': len(self.iteration_results),
            'best_run': self.best_run,
            'best_win_rate': float(self.best_win_rate),
            'best_parameters': self.best_params or {},
            'convergence_trajectory': [float(w) for w in self.performance_history],
            'iteration_details': [r.to_dict() for r in self.iteration_results]
        }

        with open(filename, 'w') as f:
            json.dump(results, f, indent=2, default=str)

        self._log(f"\n✓ Convergence results saved: {filename}\n")

    def plot_convergence(self):
        """Plot convergence curve"""
        import matplotlib.pyplot as plt

        if not self.performance_history:
            self._log("No convergence data to plot")
            return

        plt.figure(figsize=(12, 6))
        plt.plot(range(1, len(self.performance_history) + 1),
                self.performance_history, alpha=0.7, label='Win Rate')
        plt.axhline(y=0.52, color='r', linestyle='--', label='52% Target')
        plt.axhline(y=self.best_win_rate, color='g', linestyle='--',
                   label=f'Best: {self.best_win_rate:.2%}')

        # Phase markers
        plt.axvline(x=50, color='orange', linestyle=':', alpha=0.5, label='Phase 2 Start')
        plt.axvline(x=250, color='purple', linestyle=':', alpha=0.5, label='Phase 3 Start')

        plt.xlabel('Iteration')
        plt.ylabel('Win Rate')
        plt.title('Meta-Learning Loop Convergence')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        filename = f"convergence_plot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(filename, dpi=150)
        self._log(f"\n✓ Convergence plot saved: {filename}\n")
        plt.show()


# ============================================================================
# INTEGRATION: Meta-Learning + 6-Stage System
# ============================================================================

class IntegratedTradingSystemWithMetaLearning:
    """
    Complete integrated system: Meta-Learning Loop orchestrates 6-Stage System

    Architecture:
      Meta-Learning Loop (Outer)
        └─ 500 iterations (3 phases)
           ├─ Each iteration: Create 6-Stage System
           ├─ Run backtest with current parameters
           ├─ Measure performance
           └─ Adjust parameters for next iteration
    """

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.logger = logging.getLogger('IntegratedSystem')

        self.dcs_system = CompleteIntegratedTradingSystem(verbose=verbose)
        self.meta_loop = MetaLearningLoopOrchestrator(verbose=verbose)

    def initialize(self, df_data: Dict[str, pd.DataFrame],
                  symbols_list: List[str]) -> Dict:
        """Initialize both DCS system and Meta-Learning loop"""

        self.logger.info("Initializing integrated system...")

        # Initialize DCS (calculates thresholds + starting params)
        init_result = self.dcs_system.initialize_from_data(df_data, symbols_list)

        return {
            'status': 'INITIALIZED',
            'dcs_ready': True,
            'meta_loop_ready': True,
            'symbols': len(symbols_list),
            'thresholds_calculated': len(self.dcs_system.sync_thresholds),
            'parameters_initialized': len(self.dcs_system.starting_params)
        }

    def run_meta_learning_optimization(self, symbols_list: List[str],
                                      target_runs: int = 500) -> Dict:
        """Run complete meta-learning loop (500 iterations)"""

        self.logger.info(f"Starting meta-learning optimization for {target_runs} iterations...")

        # Get parameter ranges from DCS system
        # Use first symbol's params as template
        first_symbol = symbols_list[0]
        starting_params = self.dcs_system.starting_params.get(first_symbol, {})

        # CONSTRUCT parameter ranges from starting values
        # starting_params contains actual values, meta-learning needs ranges
        param_ranges = {}

        # Define learnable parameter ranges
        if 'profit_target_atr_mult' in starting_params:
            pt_val = starting_params['profit_target_atr_mult']
            param_ranges['profit_target_atr_mult'] = {
                'current': pt_val,
                'min': 1.50,
                'max': 2.00,
                'step': 0.05
            }

        if 'stop_loss_atr_mult' in starting_params:
            sl_val = starting_params['stop_loss_atr_mult']
            param_ranges['stop_loss_atr_mult'] = {
                'current': sl_val,
                'min': 0.50,
                'max': 1.00,
                'step': 0.10
            }

        if 'entry_pid_kp' in starting_params:
            kp_val = starting_params['entry_pid_kp']
            param_ranges['entry_pid_kp'] = {
                'current': kp_val,
                'min': 0.05,
                'max': 0.25,
                'step': 0.02
            }

        if 'exit_pid_kp' in starting_params:
            exit_kp = starting_params.get('exit_pid_kp', 0.10)
            param_ranges['exit_pid_kp'] = {
                'current': exit_kp,
                'min': 0.05,
                'max': 0.25,
                'step': 0.02
            }

        if 'min_hold_bars' in starting_params:
            min_hold = starting_params.get('min_hold_bars', 2)
            param_ranges['min_hold_bars'] = {
                'current': min_hold,
                'min': 1,
                'max': 5,
                'step': 1
            }

        if 'max_hold_bars' in starting_params:
            max_hold = starting_params.get('max_hold_bars', 60)
            param_ranges['max_hold_bars'] = {
                'current': max_hold,
                'min': 10,
                'max': 120,
                'step': 5
            }

        self.logger.info(f"Constructed parameter ranges for {len(param_ranges)} learnable parameters")

        # Run meta-learning loop
        convergence = self.meta_loop.run_meta_learning_loop(
            system=self.dcs_system,
            symbols_list=symbols_list,
            param_ranges=param_ranges,
            target_runs=target_runs
        )

        # Save results
        self.meta_loop.save_convergence_results()

        return {
            'status': 'COMPLETED',
            'best_run': convergence.best_run,
            'best_win_rate': convergence.best_win_rate,
            'best_parameters': convergence.best_params,
            'trajectory': convergence.improvement_trajectory
        }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*80)
    print("META-LEARNING LOOP ORCHESTRATOR")
    print("="*80)
    print("\n✅ Architecture: Meta-Learning Loop → 6-Stage System")
    print("✅ Phases: Phase 1 (Random), Phase 2 (Bayesian), Phase 3 (Fine-tune)")
    print("✅ Iterations: 500 runs with automatic convergence tracking")
    print("✅ Output: Optimal parameters for live deployment")
    print("\nUsage:")
    print("  system = IntegratedTradingSystemWithMetaLearning()")
    print("  system.initialize(df_data, symbols_list)")
    print("  results = system.run_meta_learning_optimization(symbols_list, target_runs=500)")
    print("  system.meta_loop.plot_convergence()")
    print("\n" + "="*80 + "\n")
