"""Tests for the one authoritative Revision 2 calibration entry point.

Covers: optimizer-control parameters are excluded from the trading search
space, hard acceptance gates reject bad candidates before ranking (never
just a soft score penalty), checkpointing persists real progress, and a
small end-to-end run actually calls Revision2PortfolioOrchestrator (not a
scoring shortcut) and returns a usable result.
"""

import json
import math
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.calibration_supervisor import (
    CALIBRATION_CONTROL_PARAMS,
    AcceptanceGates,
    CalibrationRunConfig,
    CalibrationSupervisor,
    score_candidate,
    trading_search_space,
)


def _symbol_bars(seed: int, rows: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 1000.0 + seed * 41
    idx = pd.date_range("2024-01-02 09:15", periods=rows, freq="min", tz="Asia/Kolkata")
    data = []
    for i in range(rows):
        drift = 0.0014 * (1 if (i // 25) % 2 == 0 else -1)
        shock = rng.normal(0, 0.0032)
        price = max(1.0, price * (1 + drift + shock))
        open_ = price * (1 - 0.0004)
        high = max(open_, price) * 1.003
        low = min(open_, price) * 0.997
        volume = max(100, int(4000 + rng.normal(0, 500)))
        data.append({"timestamp": idx[i], "open": open_, "high": high, "low": low, "close": price, "volume": volume})
    return pd.DataFrame(data)


class TestCalibrationControlSeparation(unittest.TestCase):
    def test_learning_rate_excluded_from_trading_search_space(self):
        registry = CanonicalParameterRegistry()
        space = trading_search_space(registry)
        for name in CALIBRATION_CONTROL_PARAMS:
            self.assertNotIn(name, space.names)
        # It's excluded FROM the trading search, not deleted from the
        # registry — the canonical 45-count and frozen identity hash are
        # untouched.
        self.assertIn("learning_rate_exploration_factor", registry.calibratable_names())
        self.assertEqual(len(registry.calibratable_names()), 45)


class TestAcceptanceGates(unittest.TestCase):
    def test_too_few_trades_is_rejected(self):
        gates = AcceptanceGates(min_trades=10)
        report = {"trades": [{"pnl": 10.0, "symbol": "X"}] * 3, "ending_equity": 100030.0, "net_pnl": 30.0}
        result = gates.evaluate(report)
        self.assertFalse(result.passed)
        self.assertTrue(any("trades" in r for r in result.reasons))

    def test_low_profit_factor_is_rejected(self):
        gates = AcceptanceGates(min_trades=2, min_profit_factor=1.2)
        trades = [{"pnl": 5.0, "symbol": "X"}] * 5 + [{"pnl": -20.0, "symbol": "X"}] * 5
        report = {"trades": trades, "ending_equity": 100000 + sum(t["pnl"] for t in trades), "net_pnl": sum(t["pnl"] for t in trades)}
        result = gates.evaluate(report)
        self.assertFalse(result.passed)
        self.assertTrue(any("profit factor" in r for r in result.reasons))

    def test_healthy_candidate_passes(self):
        gates = AcceptanceGates(min_trades=2, min_profit_factor=1.0, max_drawdown_fraction=0.5)
        trades = [{"pnl": 20.0, "symbol": "X"}] * 8 + [{"pnl": -10.0, "symbol": "X"}] * 2
        report = {"trades": trades, "ending_equity": 100000 + sum(t["pnl"] for t in trades), "net_pnl": sum(t["pnl"] for t in trades)}
        result = gates.evaluate(report)
        self.assertTrue(result.passed, result.reasons)

    def test_score_never_ranks_by_ending_balance(self):
        # Two reports with identical trade P&L distribution but different
        # ending_equity (i.e. different starting capital) must score
        # identically — the old ProductionOptimizer defect this replaces.
        trades = [{"pnl": p, "symbol": "X"} for p in [30, -10, 25, -5, 40, -15]]
        report_small = {"trades": trades, "net_pnl": sum(t["pnl"] for t in trades), "ending_equity": 100_000 + sum(t["pnl"] for t in trades)}
        report_large = {"trades": trades, "net_pnl": sum(t["pnl"] for t in trades), "ending_equity": 50_000_000 + sum(t["pnl"] for t in trades)}
        self.assertAlmostEqual(score_candidate(report_small), score_candidate(report_large), places=6)


class TestCalibrationSupervisorSmoke(unittest.TestCase):
    """Small, fast end-to-end pass — real Revision2PortfolioOrchestrator
    calls, real gates, real checkpointing, on a synthetic multi-symbol
    fixture sized for test speed rather than statistical significance."""

    def test_end_to_end_run_produces_checkpointed_candidates(self):
        registry = CanonicalParameterRegistry()
        symbols = ["SYM_A", "SYM_B"]
        bars = {s: _symbol_bars(seed=i + 1) for i, s in enumerate(symbols)}

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = os.path.join(tmpdir, "checkpoint.json")
            cfg = CalibrationRunConfig(
                phase1_trials=4, phase2_generations=0, phase3_iterations=0,
                seed=11, checkpoint_path=checkpoint_path,
            )
            gates = AcceptanceGates(min_trades=1, min_profit_factor=0.0, min_symbols_traded=1)
            sup = CalibrationSupervisor(registry, symbols, bars, run_config=cfg, gates=gates, warmup=30, starting_equity=500_000.0)
            result = sup.run()

            self.assertGreater(len(result.candidates), 0)
            self.assertTrue(os.path.exists(checkpoint_path))
            saved = json.loads(open(checkpoint_path).read())
            self.assertEqual(len(saved["candidates"]), len(result.candidates))

    def test_wall_clock_budget_stops_the_run_early(self):
        registry = CanonicalParameterRegistry()
        symbols = ["SYM_A"]
        bars = {"SYM_A": _symbol_bars(seed=9)}
        cfg = CalibrationRunConfig(phase1_trials=50, phase2_generations=5, phase3_iterations=20, seed=1, wall_clock_budget_seconds=0.01)
        sup = CalibrationSupervisor(registry, symbols, bars, run_config=cfg, warmup=30, starting_equity=500_000.0)
        result = sup.run()
        self.assertEqual(result.stopped_reason, "wall_clock_budget_exhausted")
        # Every candidate after the budget trips should be the -inf skip
        # marker, not a real (and expensive) backtest.
        skipped = [c for c in sup._candidates if c.metrics.get("skipped") == "wall_clock_budget_exhausted"]
        self.assertGreater(len(skipped), 0)

    def test_candidate_exception_does_not_crash_the_run(self):
        registry = CanonicalParameterRegistry()
        symbols = ["SYM_A"]
        # An empty bars dict for the declared symbol forces
        # Revision2PortfolioOrchestrator.run() to raise (no data) —
        # proving one bad candidate can't take down the whole calibration.
        cfg = CalibrationRunConfig(phase1_trials=2, phase2_generations=0, phase3_iterations=0, seed=1)
        sup = CalibrationSupervisor(registry, symbols, {"SYM_A": pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])}, run_config=cfg, warmup=30)
        result = sup.run()
        self.assertEqual(result.best_score, float("-inf"))
        self.assertTrue(all(not c.accepted for c in sup._candidates))


if __name__ == "__main__":
    unittest.main()
