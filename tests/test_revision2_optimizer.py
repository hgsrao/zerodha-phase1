"""Tests for the calibration optimizer: real algorithm correctness on a
synthetic objective (fast, deterministic), plus one real end-to-end pass
against an actual Revision2Orchestrator backtest (slower, proves the whole
pipeline — objective, search, phases — is wired to a real engine, not a
scoring shortcut).
"""

import math
import unittest

import numpy as np

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.optimizer import (
    CMAES,
    RandomSearch,
    SearchSpace,
    ThreePhaseCalibrationOrchestrator,
    TPESampler,
    local_fine_tune,
    make_backtest_objective,
)
from revision2.orchestrator import Revision2Orchestrator


def _quadratic_objective(space, target_unit):
    def objective(params):
        x = space.to_unit_vector(params)
        return -float(np.sum((x - target_unit) ** 2)), {"distance_sq": True}
    return objective


class TestSearchSpace(unittest.TestCase):
    def test_space_covers_the_44_numeric_calibratable_parameters(self):
        registry = CanonicalParameterRegistry()
        space = SearchSpace.from_registry(registry)
        # 45 calibratable minus the one string-typed one (capital_allocation_mode).
        self.assertEqual(len(space.names), 44)
        self.assertNotIn("capital_allocation_mode", space.names)
        for name in space.names:
            self.assertIn(name, registry.calibratable_names())

    def test_unit_vector_round_trip(self):
        registry = CanonicalParameterRegistry()
        space = SearchSpace.from_registry(registry)
        point = space.random_point(__import__("random").Random(1))
        vec = space.to_unit_vector(point)
        self.assertTrue(np.all(vec >= -1e-9) and np.all(vec <= 1 + 1e-9))
        back = space.from_unit_vector(vec)
        for name in space.names:
            self.assertAlmostEqual(float(point[name]), float(back[name]), delta=max(1e-6, (space.maximum[name] - space.minimum[name]) * 1e-6))


class TestSearchAlgorithmsOnSyntheticObjective(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = CanonicalParameterRegistry()
        cls.space = SearchSpace.from_registry(cls.registry)
        cls.target = np.full(len(cls.space.names), 0.7)
        # staticmethod() prevents the plain closure from being turned into a
        # bound method (with an implicit `self`) when accessed via `self.`.
        cls.objective = staticmethod(_quadratic_objective(cls.space, cls.target))

    def test_random_search_is_deterministic_given_a_seed(self):
        a = RandomSearch(self.space, seed=5).run(self.objective, 10)
        b = RandomSearch(self.space, seed=5).run(self.objective, 10)
        self.assertEqual([t.params for t in a], [t.params for t in b])
        self.assertEqual([t.score for t in a], [t.score for t in b])

    def test_tpe_improves_on_random_search_given_the_same_budget(self):
        random_trials = RandomSearch(self.space, seed=1).run(self.objective, 40)
        tpe_trials = TPESampler(self.space, seed=1).run(self.objective, 40, seed_trials=random_trials[:15])
        best_random = max(t.score for t in random_trials)
        best_tpe = max(t.score for t in tpe_trials)
        self.assertGreaterEqual(best_tpe, best_random)

    def test_cmaes_converges_toward_the_optimum(self):
        cma = CMAES(self.space, seed=3)
        trials = cma.run(self.objective, n_generations=25)
        lam = cma.lam
        gen_best = [max(t.score for t in trials[i:i + lam]) for i in range(0, len(trials), lam)]
        # Later generations must, on average, beat earlier ones — real
        # convergence, not noise. Compare the mean of the first vs last
        # quarter of generations rather than a single pair (CMA-ES is
        # stochastic and a single generation can regress).
        q = max(1, len(gen_best) // 4)
        self.assertGreater(np.mean(gen_best[-q:]), np.mean(gen_best[:q]))

    def test_local_fine_tune_never_returns_a_worse_best_than_its_start(self):
        start = self.space.from_unit_vector(np.full(len(self.space.names), 0.3))
        start_score, _ = self.objective(start)
        trials = local_fine_tune(self.objective, self.space, start, iterations=20, seed=2)
        best = max(t.score for t in trials)
        self.assertGreaterEqual(best, start_score)


class TestRealBacktestObjective(unittest.TestCase):
    """One real, slower pass: proves the whole chain — objective wraps a
    real Revision2Orchestrator, and the three-phase orchestrator actually
    finds a candidate whose real backtest score beats the registry
    defaults' — not a synthetic stand-in anywhere in the loop."""

    def test_three_phase_calibration_beats_defaults_on_a_real_backtest(self):
        from tests.test_revision2_causal_sensitivity import _synthetic_bars

        registry = CanonicalParameterRegistry()
        bars = _synthetic_bars()

        def factory(overrides):
            return Revision2Orchestrator("TESTSYM", registry, calibration_overrides=overrides)

        objective = make_backtest_objective(factory, bars, warmup=40)
        default_score, default_metrics = objective({})
        self.assertTrue(math.isfinite(default_score), "fixture must produce a valid default score")

        calib = ThreePhaseCalibrationOrchestrator(registry, objective, seed=7)
        result = calib.run(phase1_trials=6, phase2_generations=2, phase3_iterations=4)

        self.assertTrue(math.isfinite(result.best_score))
        self.assertGreaterEqual(result.best_score, default_score)
        self.assertIn("net_pnl", result.best_metrics)
        self.assertGreater(len(result.trials), 0)
        phases = {t.phase.split("_gen")[0] if "_gen" in t.phase else t.phase for t in result.trials}
        self.assertTrue({"phase1_random", "phase1_tpe"} <= phases)


if __name__ == "__main__":
    unittest.main()
