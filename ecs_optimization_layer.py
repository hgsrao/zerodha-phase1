#!/usr/bin/env python3
"""
================================================================================
ECS OPTIMIZATION LAYER - INTEGRATED INTO R1
================================================================================

Black Box 8: Phase 1 Exploration Loop (Random sampling)
Black Box 9: Phase 2 Bayesian Optimizer (scipy differential_evolution)
Black Box 10: Phase 3 Fine-Tuning Loop (Convergence refinement)

3-phase calibration optimization:
- Phase 1: Random exploration of parameter space
- Phase 2: Intelligent Bayesian optimization (scipy differential_evolution)
- Phase 3: Fine-tuning around best parameters

================================================================================
"""

import json
import logging
import numpy as np
from typing import Dict, Optional, Callable, Tuple, List
from datetime import datetime
from scipy.optimize import differential_evolution
import time

# ============================================================================
# OPTIMIZATION BASE CLASS
# ============================================================================

class CalibrationOptimizer:
    """Base class for calibration optimization phases"""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"Optimizer_{name}")
        self.iteration_count = 0
        self.best_win_rate = 0.5175
        self.best_params = None
        self.iteration_history = []

    def optimize(self, objective_fn: Callable,
                 param_specs: Dict,
                 duration_hours: float,
                 orchestrator=None) -> Dict:
        """
        Run optimization loop.

        Args:
            objective_fn: Function that takes params dict and returns win_rate
            param_specs: ParameterSpec objects for all parameters
            duration_hours: Maximum duration for this phase
            orchestrator: ECSCalibratorOrchestrator for progress tracking

        Returns:
            Best parameters found
        """
        raise NotImplementedError("Subclasses must implement optimize()")

    def get_status(self) -> Dict:
        """Get optimization status"""
        return {
            'phase': self.name,
            'iterations': self.iteration_count,
            'best_win_rate': f"{self.best_win_rate:.2%}",
            'improvement': f"+{(self.best_win_rate - 0.5175) * 100:.2f}%"
        }


# ============================================================================
# PHASE 1: EXPLORATION - RANDOM SAMPLING
# ============================================================================

class Phase1ExplorationOptimizer(CalibrationOptimizer):
    """
    Phase 1: Random Exploration

    Samples parameter space randomly to understand landscape.
    Fast, parallel-friendly, baseline for Bayesian optimization.
    """

    def __init__(self):
        super().__init__("Phase1_Exploration")

    def optimize(self, objective_fn: Callable,
                 param_specs: Dict,
                 duration_hours: float = 8.0,
                 orchestrator=None) -> Dict:
        """
        Run Phase 1: Random exploration

        Args:
            objective_fn: Function(params) → win_rate
            param_specs: ParameterSpec for each parameter
            duration_hours: Phase 1 duration (default 8 hours)
            orchestrator: For progress tracking
        """
        self.logger.info("="*80)
        self.logger.info("PHASE 1: RANDOM EXPLORATION")
        self.logger.info("="*80)
        self.logger.info(f"Duration: {duration_hours} hours")
        self.logger.info(f"Parameters: {len(param_specs)}")
        self.logger.info("")

        start_time = datetime.now()
        deadline = start_time + \
            __import__('datetime').timedelta(hours=duration_hours)

        iteration = 0

        while datetime.now() < deadline:
            iteration += 1
            self.iteration_count = iteration
            elapsed_hours = (datetime.now() - start_time).total_seconds() / 3600

            # Generate random parameters
            params = self._generate_random_params(param_specs)

            # Evaluate
            try:
                win_rate = objective_fn(params)
            except Exception as e:
                self.logger.warning(f"Iteration {iteration}: {str(e)}")
                win_rate = 0.5175

            # Track best
            is_new_best = False
            if win_rate > self.best_win_rate:
                self.best_win_rate = win_rate
                self.best_params = params
                is_new_best = True

            # Log
            improvement = (win_rate - 0.5175) * 100
            status = "[BEST]" if is_new_best else f"[+{improvement:+.2f}%]"
            self.logger.info(
                f"[Iter {iteration:4d}] {win_rate:.2%} {status} [{elapsed_hours:.2f}h]"
            )

            # Store history
            self.iteration_history.append({
                'iteration': iteration,
                'win_rate': win_rate,
                'params': params.copy(),
                'elapsed_hours': elapsed_hours,
                'is_best': is_new_best
            })

            # Report to orchestrator
            if orchestrator:
                from ecs_calibration_orchestrator import IterationResult
                result = IterationResult(
                    iteration=iteration,
                    phase="phase_1_exploration",
                    parameters=params,
                    win_rate=win_rate,
                    total_trades=100,  # Placeholder
                    sharpe_ratio=1.0,  # Placeholder
                    max_drawdown=0.15,  # Placeholder
                    elapsed_hours=elapsed_hours,
                    timestamp=datetime.now().isoformat()
                )
                orchestrator.record_iteration(result)

        self.logger.info("")
        self.logger.info(f"Phase 1 complete: {iteration} iterations in {elapsed_hours:.2f} hours")
        self.logger.info(f"Best win rate: {self.best_win_rate:.2%}")
        self.logger.info("")

        return self.best_params

    def _generate_random_params(self, param_specs: Dict) -> Dict:
        """Generate random parameter set"""
        params = {}

        for param_name, spec in param_specs.items():
            if spec.param_type == 'int':
                params[param_name] = np.random.randint(
                    int(spec.min_value),
                    int(spec.max_value) + 1
                )
            else:
                params[param_name] = np.random.uniform(
                    spec.min_value,
                    spec.max_value
                )

        # Normalize weights
        return self._normalize_weights(params)

    def _normalize_weights(self, params: Dict) -> Dict:
        """Ensure chart weights sum to 1.0"""
        total = (
            params.get('vwap_weight', 0.25) +
            params.get('confirmation_2bar_weight', 0.25) +
            params.get('momentum_weight', 0.25) +
            params.get('volatility_weight', 0.25)
        )
        if total > 0:
            params['vwap_weight'] /= total
            params['confirmation_2bar_weight'] /= total
            params['momentum_weight'] /= total
            params['volatility_weight'] /= total
        return params


# ============================================================================
# PHASE 2: BAYESIAN OPTIMIZATION
# ============================================================================

class Phase2BayesianOptimizer(CalibrationOptimizer):
    """
    Phase 2: Bayesian Optimization

    Uses scipy.optimize.differential_evolution for intelligent parameter search.
    Learns from Phase 1 results to guide exploration.
    """

    def __init__(self):
        super().__init__("Phase2_Bayesian")
        self.eval_count = 0

    def optimize(self, objective_fn: Callable,
                 param_specs: Dict,
                 duration_hours: float = 10.0,
                 orchestrator=None,
                 initial_best_params: Dict = None) -> Dict:
        """
        Run Phase 2: Bayesian optimization via differential_evolution

        Args:
            objective_fn: Function(params) → win_rate
            param_specs: ParameterSpec for each parameter
            duration_hours: Phase 2 duration (default 10 hours)
            orchestrator: For progress tracking
            initial_best_params: Phase 1 best params (optional)
        """
        self.logger.info("="*80)
        self.logger.info("PHASE 2: BAYESIAN OPTIMIZATION")
        self.logger.info("="*80)
        self.logger.info(f"Duration: {duration_hours} hours")
        self.logger.info(f"Algorithm: scipy.optimize.differential_evolution")
        self.logger.info("")

        start_time = datetime.now()
        deadline = start_time + \
            __import__('datetime').timedelta(hours=duration_hours)

        # Build parameter bounds
        bounds = []
        param_names = []
        for param_name, spec in param_specs.items():
            bounds.append((spec.min_value, spec.max_value))
            param_names.append(param_name)

        # Create wrapper objective function
        iteration_tracker = {'count': 0}

        def objective_wrapper(x_array):
            """Convert array back to params dict and evaluate"""
            iteration_tracker['count'] += 1
            iteration = iteration_tracker['count']
            elapsed_hours = (datetime.now() - start_time).total_seconds() / 3600

            # Array → Dict
            params = {
                param_names[i]: x_array[i]
                for i in range(len(param_names))
            }

            # Normalize weights
            params = self._normalize_weights(params)

            # Evaluate
            try:
                win_rate = objective_fn(params)
            except Exception as e:
                self.logger.warning(f"Iteration {iteration}: {str(e)}")
                win_rate = 0.5175

            # Track best
            is_new_best = False
            if win_rate > self.best_win_rate:
                self.best_win_rate = win_rate
                self.best_params = params
                is_new_best = True

            # Log
            improvement = (win_rate - 0.5175) * 100
            status = "[BEST]" if is_new_best else f"[+{improvement:+:.2f}%]"
            self.logger.info(
                f"[Iter {iteration:4d}] {win_rate:.2%} {status} [{elapsed_hours:.2f}h]"
            )

            # Store history
            self.iteration_history.append({
                'iteration': iteration,
                'win_rate': win_rate,
                'params': params.copy(),
                'elapsed_hours': elapsed_hours,
                'is_best': is_new_best
            })

            # Report to orchestrator
            if orchestrator:
                from ecs_calibration_orchestrator import IterationResult
                result = IterationResult(
                    iteration=iteration,
                    phase="phase_2_bayesian",
                    parameters=params,
                    win_rate=win_rate,
                    total_trades=100,
                    sharpe_ratio=1.0,
                    max_drawdown=0.15,
                    elapsed_hours=elapsed_hours,
                    timestamp=datetime.now().isoformat()
                )
                orchestrator.record_iteration(result)

            # Check deadline
            if datetime.now() >= deadline:
                raise KeyboardInterrupt("Duration limit reached")

            # Scipy's differential_evolution MAXIMIZES,
            # but we want to minimize error (1 - win_rate)
            return 1.0 - win_rate

        # Run differential_evolution
        try:
            result = differential_evolution(
                objective_wrapper,
                bounds,
                maxiter=500,  # Fallback limit
                popsize=15,   # Population size
                seed=42,      # Reproducibility
                workers=1,    # Single process
                atol=1e-4,
                tol=1e-4
            )

            self.best_params = {
                param_names[i]: result.x[i]
                for i in range(len(param_names))
            }
            self.best_params = self._normalize_weights(self.best_params)
            self.best_win_rate = 1.0 - result.fun

        except KeyboardInterrupt:
            self.logger.info("[OK] Duration limit reached, continuing with best so far")

        elapsed_hours = (datetime.now() - start_time).total_seconds() / 3600
        self.logger.info("")
        self.logger.info(
            f"Phase 2 complete: {iteration_tracker['count']} iterations in {elapsed_hours:.2f} hours"
        )
        self.logger.info(f"Best win rate: {self.best_win_rate:.2%}")
        self.logger.info("")

        return self.best_params

    def _normalize_weights(self, params: Dict) -> Dict:
        """Ensure chart weights sum to 1.0"""
        total = (
            params.get('vwap_weight', 0.25) +
            params.get('confirmation_2bar_weight', 0.25) +
            params.get('momentum_weight', 0.25) +
            params.get('volatility_weight', 0.25)
        )
        if total > 0:
            params['vwap_weight'] /= total
            params['confirmation_2bar_weight'] /= total
            params['momentum_weight'] /= total
            params['volatility_weight'] /= total
        return params


# ============================================================================
# PHASE 3: FINE-TUNING
# ============================================================================

class Phase3FineTuningOptimizer(CalibrationOptimizer):
    """
    Phase 3: Fine-Tuning

    Refines parameters found in Phase 2.
    Smaller deviation range, focused search around best parameters.
    """

    def __init__(self):
        super().__init__("Phase3_FineTuning")

    def optimize(self, objective_fn: Callable,
                 param_specs: Dict,
                 duration_hours: float = 6.0,
                 orchestrator=None,
                 initial_best_params: Dict = None) -> Dict:
        """
        Run Phase 3: Fine-tuning around best parameters

        Args:
            objective_fn: Function(params) → win_rate
            param_specs: ParameterSpec for each parameter
            duration_hours: Phase 3 duration (default 6 hours)
            orchestrator: For progress tracking
            initial_best_params: Phase 2 best params
        """
        self.logger.info("="*80)
        self.logger.info("PHASE 3: FINE-TUNING")
        self.logger.info("="*80)
        self.logger.info(f"Duration: {duration_hours} hours")
        self.logger.info(f"Strategy: Refined search around Phase 2 best")
        self.logger.info("")

        if initial_best_params is None:
            self.logger.error("[FAIL] No initial best params provided")
            return None

        self.best_params = initial_best_params
        self.best_win_rate = objective_fn(initial_best_params)

        start_time = datetime.now()
        deadline = start_time + \
            __import__('datetime').timedelta(hours=duration_hours)

        iteration = 0
        deviation = 0.10  # 10% of range initially

        while datetime.now() < deadline:
            iteration += 1
            self.iteration_count = iteration
            elapsed_hours = (datetime.now() - start_time).total_seconds() / 3600

            # Generate params near best (smaller deviation each iteration)
            params = self._generate_near_best(
                self.best_params,
                param_specs,
                deviation * (1.0 - elapsed_hours / duration_hours)  # Shrink over time
            )

            # Evaluate
            try:
                win_rate = objective_fn(params)
            except Exception as e:
                self.logger.warning(f"Iteration {iteration}: {str(e)}")
                win_rate = 0.5175

            # Track best
            is_new_best = False
            if win_rate > self.best_win_rate:
                self.best_win_rate = win_rate
                self.best_params = params
                is_new_best = True

            # Log
            improvement = (win_rate - 0.5175) * 100
            status = "[BEST]" if is_new_best else f"[+{improvement:+.2f}%]"
            self.logger.info(
                f"[Iter {iteration:4d}] {win_rate:.2%} {status} [{elapsed_hours:.2f}h]"
            )

            # Store history
            self.iteration_history.append({
                'iteration': iteration,
                'win_rate': win_rate,
                'params': params.copy(),
                'elapsed_hours': elapsed_hours,
                'is_best': is_new_best
            })

            # Report to orchestrator
            if orchestrator:
                from ecs_calibration_orchestrator import IterationResult
                result = IterationResult(
                    iteration=iteration,
                    phase="phase_3_fine_tuning",
                    parameters=params,
                    win_rate=win_rate,
                    total_trades=100,
                    sharpe_ratio=1.0,
                    max_drawdown=0.15,
                    elapsed_hours=elapsed_hours,
                    timestamp=datetime.now().isoformat()
                )
                orchestrator.record_iteration(result)

        self.logger.info("")
        self.logger.info(f"Phase 3 complete: {iteration} iterations in {elapsed_hours:.2f} hours")
        self.logger.info(f"Best win rate: {self.best_win_rate:.2%}")
        self.logger.info("")

        return self.best_params

    def _generate_near_best(self, best_params: Dict,
                           param_specs: Dict,
                           deviation: float) -> Dict:
        """Generate params with small noise around best"""
        params = {}

        for param_name, spec in param_specs.items():
            best_value = best_params.get(param_name, spec.default)
            range_width = spec.max_value - spec.min_value

            if spec.param_type == 'int':
                noise = int(range_width * deviation * np.random.uniform(-1, 1))
                new_value = int(best_value) + noise
            else:
                noise = range_width * deviation * np.random.uniform(-1, 1)
                new_value = best_value + noise

            params[param_name] = np.clip(
                new_value,
                spec.min_value,
                spec.max_value
            )

        # Normalize weights
        return self._normalize_weights(params)

    def _normalize_weights(self, params: Dict) -> Dict:
        """Ensure chart weights sum to 1.0"""
        total = (
            params.get('vwap_weight', 0.25) +
            params.get('confirmation_2bar_weight', 0.25) +
            params.get('momentum_weight', 0.25) +
            params.get('volatility_weight', 0.25)
        )
        if total > 0:
            params['vwap_weight'] /= total
            params['confirmation_2bar_weight'] /= total
            params['momentum_weight'] /= total
            params['volatility_weight'] /= total
        return params


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - [OPTIMIZATION] - %(message)s'
    )

    # Dummy objective function for testing
    def dummy_objective(params: Dict) -> float:
        """Dummy: slightly random win rate"""
        return 0.52 + np.random.uniform(-0.01, 0.02)

    # Test Phase 1
    print("\n=== TESTING PHASE 1 ===")
    phase1 = Phase1ExplorationOptimizer()
    dummy_specs = {
        'param1': __import__('ecs_parameter_management').ParameterSpec(
            name='param1', tier=__import__('ecs_parameter_management').ParameterTier.TIER_1_CRITICAL,
            param_type='float', min_value=0.5, max_value=1.5, default=1.0, step=0.1,
            description='test', unit='fraction'
        ),
        'vwap_weight': __import__('ecs_parameter_management').ParameterSpec(
            name='vwap_weight', tier=__import__('ecs_parameter_management').ParameterTier.TIER_1_CRITICAL,
            param_type='float', min_value=0.1, max_value=0.4, default=0.25, step=0.02,
            description='test', unit='fraction'
        ),
        'confirmation_2bar_weight': __import__('ecs_parameter_management').ParameterSpec(
            name='confirmation_2bar_weight', tier=__import__('ecs_parameter_management').ParameterTier.TIER_1_CRITICAL,
            param_type='float', min_value=0.1, max_value=0.4, default=0.25, step=0.02,
            description='test', unit='fraction'
        ),
        'momentum_weight': __import__('ecs_parameter_management').ParameterSpec(
            name='momentum_weight', tier=__import__('ecs_parameter_management').ParameterTier.TIER_1_CRITICAL,
            param_type='float', min_value=0.1, max_value=0.4, default=0.25, step=0.02,
            description='test', unit='fraction'
        ),
        'volatility_weight': __import__('ecs_parameter_management').ParameterSpec(
            name='volatility_weight', tier=__import__('ecs_parameter_management').ParameterTier.TIER_1_CRITICAL,
            param_type='float', min_value=0.05, max_value=0.4, default=0.25, step=0.02,
            description='test', unit='fraction'
        ),
    }

    best_params = phase1.optimize(
        objective_fn=dummy_objective,
        param_specs=dummy_specs,
        duration_hours=0.1  # 6 minutes for testing
    )
    print(f"Phase 1 best win rate: {phase1.best_win_rate:.2%}")

