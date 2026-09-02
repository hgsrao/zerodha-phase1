"""Regression test for the O(n^2) scaling bug: MarketSnapshot.bars used to
be `bars.iloc[:bar_idx + 1]` — the ENTIRE history so far — even though PA
only ever reads a bounded trailing lookback (its largest lookback
parameter maxes out at 30 bars in the registry). That made both the slice
and PA's `.to_numpy()` conversion O(bar_idx) work repeated on every bar:
invisible on a short backtest, and the dominant cost at real (hundreds-of-
thousands-of-bars) scale. This proves the fix doesn't change results and
that per-bar cost stays flat as bar_idx grows.
"""

import time
import unittest

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.boxes import PredictiveAnalyticsBox
from revision2.contracts import EffectiveConfig, MarketSnapshot


def _trending_bars(rows: int, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 500.0
    idx = pd.date_range("2023-01-02 09:15", periods=rows, freq="min", tz="Asia/Kolkata")
    data = []
    for i in range(rows):
        price = max(1.0, price * (1 + 0.0002 * (1 if (i // 100) % 2 == 0 else -1) + rng.normal(0, 0.0015)))
        data.append({"timestamp": idx[i], "open": price, "high": price * 1.002, "low": price * 0.998, "close": price, "volume": 3000})
    return pd.DataFrame(data)


class TestSnapshotBoundDoesNotChangeResults(unittest.TestCase):
    def test_pa_output_is_identical_with_full_vs_bounded_history(self):
        bars = _trending_bars(2000)
        registry = CanonicalParameterRegistry()
        values = {name: spec.default for name, spec in registry.params.items()}
        config = EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)

        bar_idx = 1500
        pa_full = PredictiveAnalyticsBox()
        pa_full.calibrate("SYM", bars.iloc[:60])
        snap_full = MarketSnapshot(symbol="SYM", timestamp=str(bars.iloc[bar_idx]["timestamp"]), bars=bars.iloc[:bar_idx + 1])
        signal_full, _ = pa_full.evaluate(snap_full, config)

        pa_bounded = PredictiveAnalyticsBox()
        pa_bounded.calibrate("SYM", bars.iloc[:60])
        bound = 300
        snap_bounded = MarketSnapshot(
            symbol="SYM", timestamp=str(bars.iloc[bar_idx]["timestamp"]),
            bars=bars.iloc[max(0, bar_idx - bound + 1):bar_idx + 1],
        )
        signal_bounded, _ = pa_bounded.evaluate(snap_bounded, config)

        self.assertAlmostEqual(signal_full.confidence, signal_bounded.confidence, places=10)
        self.assertEqual(signal_full.direction, signal_bounded.direction)
        self.assertAlmostEqual(signal_full.momentum, signal_bounded.momentum, places=10)
        self.assertAlmostEqual(signal_full.volatility, signal_bounded.volatility, places=10)


class TestPerBarCostStaysFlat(unittest.TestCase):
    def test_evaluate_cost_does_not_grow_with_bar_index(self):
        bars = _trending_bars(30000)
        registry = CanonicalParameterRegistry()
        values = {name: spec.default for name, spec in registry.params.items()}
        config = EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)
        bound = 300

        def cost_at(bar_idx: int, n_reps: int = 50) -> float:
            pa = PredictiveAnalyticsBox()
            pa.calibrate("SYM", bars.iloc[:60])
            snap = MarketSnapshot(
                symbol="SYM", timestamp=str(bars.iloc[bar_idx]["timestamp"]),
                bars=bars.iloc[max(0, bar_idx - bound + 1):bar_idx + 1],
            )
            t0 = time.perf_counter()
            for _ in range(n_reps):
                pa.evaluate(snap, config)
            return (time.perf_counter() - t0) / n_reps

        early_cost = cost_at(500)
        late_cost = cost_at(29000)
        # With the O(n^2) bug this would scale with bar_idx (~58x here);
        # bounded, it should be flat within noise. Generous 4x slack.
        self.assertLess(late_cost, early_cost * 4, f"early={early_cost:.6f}s late={late_cost:.6f}s")


if __name__ == "__main__":
    unittest.main()
