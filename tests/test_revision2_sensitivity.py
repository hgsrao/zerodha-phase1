"""Behavioral sensitivity tests for all 45 calibratable Revision 2 parameters.

For each calibratable parameter, the owning box is run at the parameter's
minimum, default, and maximum registry-declared value with everything else
held constant, across several representative inputs. The test fails if a
parameter's min/default/max outputs are all identical anywhere in the sweep
— that would mean the parameter is wired for tracing only and doesn't
actually influence behavior (exactly the wiring gap this suite exists to
catch).
"""

import unittest

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.boxes import (
    IntelligentDiscriminationBox,
    ModelPredictiveControlBox,
    PositionManagerBox,
    PredictiveAnalyticsBox,
    SafetyGatesTargetBox,
    UnifiedExecutionBox,
)
from revision2.contracts import EffectiveConfig, IDDecision, MarketSnapshot, PASignal, TradePlan


def _synthetic_bars(seed: int = 7) -> pd.DataFrame:
    """Three deliberate regimes so every PA branch actually gets exercised:
    a calm strong-trend segment (should read as low-vol, high confidence),
    a choppy segment, and a high-vol shock segment.
    """
    rng = np.random.default_rng(seed)
    price = 1000.0
    rows_data = []
    # (length, drift_mag, shock_start, shock_end) — shock ramps linearly
    # within a segment so the calibration window (the segment's first bars)
    # is never the calmest part of the whole series; later bars can register
    # as genuinely low-vol relative to that baseline.
    segments = [
        (50, 0.0035, 0.0028, 0.00008),  # first 10 bars (the calibration window)
                                        # are the highest-shock part; the rest
                                        # of the segment calms down well below
                                        # that baseline -> exercises low-vol
        (60, 0.0006, 0.0020, 0.0020),   # mild trend, moderate noise
        (60, 0.0004, 0.0090, 0.0090),   # weak trend, large shocks -> high-vol regime
    ]
    i = 0
    for length, drift_mag, shock_start, shock_end in segments:
        direction = 1 if (i // 40) % 2 == 0 else -1
        for step in range(length):
            shock_sigma = shock_start + (shock_end - shock_start) * (step / max(length - 1, 1))
            drift = drift_mag * direction
            shock = rng.normal(0, shock_sigma)
            price = max(1.0, price * (1 + drift + shock))
            open_ = price * (1 - 0.0004)
            high = max(open_, price) * (1 + shock_sigma)
            low = min(open_, price) * (1 - shock_sigma)
            volume = max(100, int(5000 + rng.normal(0, 800)))
            minute = 15 + (i % 300)
            hour = 9 + minute // 60
            minute = minute % 60
            rows_data.append({
                "timestamp": f"2024-01-0{1 + i // 300}T{hour:02d}:{minute:02d}:00+05:30",
                "open": open_, "high": high, "low": low, "close": price, "volume": volume,
            })
            i += 1
    return pd.DataFrame(rows_data)


class TestRevision2ParameterSensitivity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = CanonicalParameterRegistry()
        cls.bars = _synthetic_bars()

    def _config(self, overrides=None):
        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides or {})
        return EffectiveConfig.build(values, self.registry.FROZEN_IDENTITY_SHA256)

    def _assert_sensitive(self, name: str, outputs_by_value):
        default_out, min_out, max_out = outputs_by_value
        self.assertFalse(
            min_out == default_out == max_out,
            f"{name}: min/default/max produced identical output ({default_out}) across the whole sweep",
        )

    # ---- PA -----------------------------------------------------------
    def _pa_sweep(self, config: EffectiveConfig):
        pa = PredictiveAnalyticsBox()
        pa.calibrate("TEST", self.bars.iloc[:10])
        outputs = []
        for idx in range(15, len(self.bars) - 1, 3):
            snapshot = MarketSnapshot(symbol="TEST", timestamp=str(self.bars.iloc[idx]["timestamp"]), bars=self.bars.iloc[: idx + 1])
            signal, _ = pa.evaluate(snapshot, config)
            outputs.append((
                round(signal.confidence, 6), signal.direction, round(signal.momentum, 6),
                round(signal.vwap_deviation, 6), round(signal.exit_confidence, 6),
            ))
        return tuple(outputs)

    def test_pa_parameters_are_sensitive(self):
        pa_names = sorted(n for n, s in self.registry.params.items() if s.black_box == "PA" and s.calibratable)
        self.assertGreater(len(pa_names), 0)
        default_output = self._pa_sweep(self._config())
        for name in pa_names:
            spec = self.registry.get(name)
            with self.subTest(param=name):
                min_output = self._pa_sweep(self._config({name: spec.minimum}))
                max_output = self._pa_sweep(self._config({name: spec.maximum}))
                self._assert_sensitive(name, (default_output, min_output, max_output))

    # ---- ID -------------------------------------------------------------
    def _id_sweep(self, config: EffectiveConfig):
        idb = IntelligentDiscriminationBox()
        outputs = []
        for direction, confidence, volatility in [(1, 0.5, 0.01), (1, 0.65, 0.01), (-1, 0.55, 0.03), (0, 0.7, 0.005)]:
            signal = PASignal("TEST", "t", direction, confidence, 0.3, volatility, 0.1, 0.2)
            decision, _ = idb.evaluate(signal, config)
            outputs.append((
                decision.approved, round(decision.confidence, 6), round(decision.risk_reward_ratio, 6),
                decision.reason.split(" ")[0], round(decision.timing_quality, 6),
            ))
        return tuple(outputs)

    def test_id_parameters_are_sensitive(self):
        id_names = sorted(n for n, s in self.registry.params.items() if s.black_box == "ID" and s.calibratable)
        id_names += ["entry_confidence_threshold", "min_risk_reward_ratio"]  # read by ID even though registry-owned elsewhere
        default_output = self._id_sweep(self._config())
        for name in id_names:
            spec = self.registry.get(name)
            with self.subTest(param=name):
                min_output = self._id_sweep(self._config({name: spec.minimum}))
                max_output = self._id_sweep(self._config({name: spec.maximum}))
                self._assert_sensitive(name, (default_output, min_output, max_output))

    # ---- MPC --------------------------------------------------------------
    def _mpc_sweep(self, config: EffectiveConfig):
        mpc = ModelPredictiveControlBox()
        outputs = []
        scenarios = [
            (1, 0.6, 1000.0, 8.0), (-1, 0.65, 1500.0, 12.0), (1, 0.55, 800.0, 5.0),
            (1, 0.505, 1200.0, 6.0),  # confidence near the OLD entry-PID target (0.5),
                                      # kept as-is even though the target itself is no
                                      # longer fixed -- still a useful near-baseline case
        ]
        # Confidence now varies repeat to repeat within each scenario (not
        # held constant across all 12): the entry/exit PID setpoint is a
        # rolling mean of the symbol's own recent confidence (see
        # ModelPredictiveControlBox._confidence_baseline), so a constant
        # confidence would let that baseline converge to it and every PID
        # gain -- and the window/clamp/smoothing parameters below -- collapse
        # to the same zero-error, zero-adjustment output regardless of
        # value, proving nothing about any of them. A small oscillation
        # keeps a real, nonzero, evolving error flowing every repeat, which
        # is exactly what lets window size (not just the clamp) keep
        # mattering after many calls -- the original intent of this sweep.
        oscillation = [0.0, 0.02, -0.015, 0.01, -0.02, 0.015, 0.0, -0.01, 0.02, -0.015, 0.01, -0.02]
        for repeat_idx in range(12):
            for scenario_idx, (direction, confidence, entry_price, atr) in enumerate(scenarios):
                confidence = confidence + oscillation[repeat_idx]
                # A distinct symbol per scenario keeps each one's PID state
                # isolated — otherwise interleaving all four scenarios into
                # one shared PID mixes their errors and saturates the
                # integral clamp regardless of window size.
                signal = PASignal(f"TEST_{scenario_idx}", "t", direction, confidence, 0.3, 0.01, 0.1, 0.2)
                decision = IDDecision(True, "approved", confidence, 2.0, 0.5)
                plan, pid_info, _ = mpc.build_plan(signal, decision, entry_price, atr, config)
                if plan is None:
                    outputs.append(None)
                else:
                    outputs.append((
                        round(plan.entry_price, 4), round(plan.stop_price, 4), round(plan.target_price, 4),
                        plan.minimum_hold_bars, plan.maximum_hold_bars,
                        round(pid_info["entry_adjustment"], 6), round(pid_info["exit_adjustment"], 6),
                    ))
        return tuple(outputs)

    def test_mpc_parameters_are_sensitive(self):
        # 16, not 17: minimum_absolute_profit_rupees (black_box="MPC") was
        # replaced by minimum_profit_margin_over_cost (black_box=
        # "SafetyGates"), moving the profit-floor check post-sizing where
        # the real quantity and real round-trip cost are both known -- see
        # SafetyGatesTargetBox.evaluate_post_sizing()'s own comment and
        # test_revision2_causal_sensitivity.py's
        # test_minimum_profit_margin_over_cost_changes_the_real_ledger for
        # this parameter's own causal proof.
        mpc_names = sorted(n for n, s in self.registry.params.items() if s.black_box == "MPC" and s.calibratable)
        self.assertEqual(len(mpc_names), 16)
        default_output = self._mpc_sweep(self._config())
        for name in mpc_names:
            spec = self.registry.get(name)
            with self.subTest(param=name):
                min_output = self._mpc_sweep(self._config({name: spec.minimum}))
                max_output = self._mpc_sweep(self._config({name: spec.maximum}))
                self._assert_sensitive(name, (default_output, min_output, max_output))

    # ---- PositionManager -----------------------------------------------
    def _position_sweep(self, config: EffectiveConfig):
        pm = PositionManagerBox()
        outputs = []
        scenarios = [
            # (entry, stop, equity, size_mult, open_positions, symbol_positions)
            (1000.0, 990.0, 100000.0, 1.0, 0, 0),   # concentration cap dominant
            (100.0, 50.0, 100000.0, 1.0, 0, 0),     # wide stop -> capital_per_trade_fraction dominant
            (1500.0, 1470.0, 250000.0, 0.6, 3, 0),  # exercises max_positions_live
            (1500.0, 1470.0, 250000.0, 0.6, 0, 2),  # exercises max_positions_per_symbol
        ]
        for entry, stop, equity, size_mult, open_positions, symbol_positions in scenarios:
            plan = TradePlan("BUY", entry, stop, entry + (entry - stop) * 1.5, 2, 20)
            qty, _ = pm.size(plan, equity, size_mult, config, open_positions, symbol_positions)
            outputs.append(qty)
        return tuple(outputs)

    def test_position_manager_calibratable_parameters_are_sensitive(self):
        pm_names = sorted(
            n for n, s in self.registry.params.items()
            if s.black_box == "PositionManager" and s.calibratable and s.param_type in ("int", "float")
        )
        self.assertGreater(len(pm_names), 0)
        default_output = self._position_sweep(self._config())
        for name in pm_names:
            spec = self.registry.get(name)
            with self.subTest(param=name):
                min_output = self._position_sweep(self._config({name: spec.minimum}))
                max_output = self._position_sweep(self._config({name: spec.maximum}))
                self._assert_sensitive(name, (default_output, min_output, max_output))

    def test_capital_allocation_mode_changes_sizing(self):
        # capital_allocation_mode is a string parameter with placeholder
        # (0, 0) registry bounds, so it can't go through the generic
        # min/max sweep above — it's tested directly against its two
        # meaningful values instead.
        pm = PositionManagerBox()
        plan = TradePlan("BUY", 100.0, 50.0, 175.0, 2, 20)
        equal_qty, _ = pm.size(plan, 100000.0, 1.0, self._config({"capital_allocation_mode": "equal"}))
        aggressive_qty, _ = pm.size(plan, 100000.0, 1.0, self._config({"capital_allocation_mode": "aggressive"}))
        self.assertNotEqual(equal_qty, aggressive_qty)

    # ---- UnifiedExecution (only the one calibratable parameter) --------
    def test_unified_execution_learning_rate_is_sensitive(self):
        box = UnifiedExecutionBox()
        spec = self.registry.get("learning_rate_exploration_factor")

        def bias(value):
            _, exploration_bias, _ = box.check_window("2024-01-01T10:00:00+05:30", self._config({"learning_rate_exploration_factor": value}))
            return round(exploration_bias, 8)

        outputs = (bias(spec.default), bias(spec.minimum), bias(spec.maximum))
        self._assert_sensitive("learning_rate_exploration_factor", outputs)


class TestSafetyGatesPostSizingProfitMargin(unittest.TestCase):
    """SafetyGatesTargetBox.evaluate_post_sizing()'s minimum_profit_margin_
    over_cost check, direct and deterministic -- the standard synthetic
    fixture used elsewhere in this file (and in
    test_revision2_causal_sensitivity.py) masks this parameter, because its
    trades' profit always runs ~3x the required cost margin even at the
    parameter's maximum. This scenario is chosen so the real profit/cost
    ratio (~2.17x) sits between the minimum-margin requirement (1x cost)
    and the maximum-margin requirement (3x cost), so sweeping the
    parameter's full registry range genuinely flips the accept/reject
    outcome -- proving the check is real, not just documented as excluded
    from the standard fixture."""

    @classmethod
    def setUpClass(cls):
        cls.registry = CanonicalParameterRegistry()

    def _config(self, overrides=None):
        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides or {})
        return EffectiveConfig.build(values, self.registry.FROZEN_IDENTITY_SHA256)

    def test_margin_parameter_flips_accept_reject_on_a_real_scenario(self):
        box = SafetyGatesTargetBox()
        plan = TradePlan(side="BUY", entry_price=500.0, stop_price=490.0, target_price=501.0,
                          minimum_hold_bars=2, maximum_hold_bars=20)
        quantity = 50
        spec = self.registry.get("minimum_profit_margin_over_cost")

        passed_at_min, reason_min, _ = box.evaluate_post_sizing([1_000_000.0], plan, quantity, self._config({"minimum_profit_margin_over_cost": spec.minimum}))
        passed_at_max, reason_max, _ = box.evaluate_post_sizing([1_000_000.0], plan, quantity, self._config({"minimum_profit_margin_over_cost": spec.maximum}))

        self.assertTrue(passed_at_min, f"expected pass at minimum margin, got: {reason_min}")
        self.assertFalse(passed_at_max, f"expected reject at maximum margin, got: {reason_max}")
        self.assertIn("real round-trip cost", reason_max)

    def test_rejection_math_matches_the_real_leg_cost_formula(self):
        box = SafetyGatesTargetBox()
        plan = TradePlan(side="BUY", entry_price=500.0, stop_price=490.0, target_price=501.0,
                          minimum_hold_bars=2, maximum_hold_bars=20)
        quantity = 50
        expected_cost = box._leg_cost(500.0, 50, "BUY") + box._leg_cost(501.0, 50, "SELL")
        expected_profit = 1.0 * 50

        passed, reason, _ = box.evaluate_post_sizing([1_000_000.0], plan, quantity, self._config({"minimum_profit_margin_over_cost": 2.0}))
        self.assertFalse(passed)
        self.assertIn(f"Rs.{expected_profit:.2f}", reason)
        self.assertIn(f"Rs.{expected_cost:.2f}", reason)


if __name__ == "__main__":
    unittest.main()
