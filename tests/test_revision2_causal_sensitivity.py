"""End-to-end causal perturbation tests: for every calibratable parameter,
run the REAL orchestrator (not an isolated box) at min/default/max and
compare the final trade ledger and net P&L.

This exists because box-level sensitivity (test_revision2_sensitivity.py)
only proves a box's own output changes — it does not prove that output
ever reaches an actual trade. That gap was real: the MPC PID gains
(pid_kp_entry, pid_ki_entry, pid_kd_entry, pid_kp_exit, pid_ki_exit,
pid_kd_exit, pid_integral_window_bars, pid_integral_max_clamp,
pid_derivative_smoothing) were computed and returned as `pid_info` but the
orchestrator discarded that dict entirely — sensitivity tests passed while
the parameters had zero effect on any trade. This suite is the fix's proof.
"""

import unittest

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.orchestrator import Revision2Orchestrator


def _synthetic_bars(rows: int = 600, seed: int = 11) -> pd.DataFrame:
    """Same three-regime construction as the box-level sensitivity tests
    (calm-then-trending, choppy, high-vol shock) sized to reliably produce
    dozens of real trades under default parameters within a few hundred
    bars, so 45 x 3 full orchestrator runs stay fast."""
    rng = np.random.default_rng(seed)
    price = 1000.0
    rows_data = []
    thirds = [rows // 3, rows // 3, rows - 2 * (rows // 3)]
    segments = [
        (thirds[0], 0.0035, 0.0022, 0.00008),
        (thirds[1], 0.0006, 0.0020, 0.0020),
        (thirds[2], 0.0004, 0.0090, 0.0090),
    ]
    i = 0
    for length, drift_mag, shock_start, shock_end in segments:
        direction = 1 if (i // 80) % 2 == 0 else -1
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


class TestCausalEndToEndSensitivity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = CanonicalParameterRegistry()
        cls.bars = _synthetic_bars()
        # Sanity: the default run must actually produce trades, or this
        # whole suite would prove nothing.
        cls.default_report = Revision2Orchestrator("TESTSYM", cls.registry).run(cls.bars, warmup=40)
        assert cls.default_report["completed_trades"] > 5, "fixture must produce multiple trades"

    def _run(self, overrides):
        report = Revision2Orchestrator("TESTSYM", self.registry, calibration_overrides=overrides).run(self.bars, warmup=40)
        # (trade count, net P&L, and the full ledger) — comparing the ledger
        # itself, not just aggregate P&L, catches a parameter that changes
        # *which* trades happen without moving the total by coincidence.
        return (report["completed_trades"], round(report["net_pnl"], 6), tuple(
            (t["side"], t["entry_bar_idx"], t["exit_bar_idx"], round(t["pnl"], 6), t["reason"]) for t in report["trades"]
        ))

    # Parameters that do NOT move this fixture's real trade ledger, each for
    # a documented, verified structural reason rather than an unexamined
    # gap. Every one of these was actually run against multiple fixtures
    # (this file's history has the numbers) before being excluded here —
    # none is excluded because it was merely inconvenient to prove.
    KNOWN_LEDGER_INERT = {
        # Single-symbol structural limitation: the orchestrator only ever
        # evaluates a new entry while flat, so open_positions_count and
        # symbol_positions_count are always 0 at PositionManager.size() —
        # these two parameters are proven live at the box level (with an
        # explicit nonzero position count) in test_revision2_sensitivity.py.
        # max_positions_live is genuinely end-to-end live once the 48-symbol
        # shared portfolio tracks concurrent positions across DIFFERENT
        # symbols (Revision2PortfolioOrchestrator passes a real, varying
        # open_positions_count). max_positions_per_symbol is NOT just
        # waiting on that: Revision2PortfolioOrchestrator's own
        # `if symbol in self.open_trades: continue` guard runs earlier in
        # the same loop than PositionManager.size(), so
        # `symbol_positions_count=1 if symbol in self.open_trades else 0`
        # is always evaluated with that condition already False — it is
        # permanently ledger-inert in BOTH orchestrators as currently
        # architected, not merely unverified. See
        # tests/test_revision2_portfolio.py::
        # test_max_positions_per_symbol_is_ledger_inert_in_the_portfolio_too
        # for the portfolio-level proof, and
        # revision2/calibration_supervisor.py's
        # DEAD_PARAMS_UNTIL_MULTI_LOT_SUPPORT for why it's excluded from the
        # calibration search space rather than silently wasting budget on
        # it. Enabling real multi-position-per-symbol trading (pyramiding)
        # would need open_trades to hold a list per symbol plus reworked
        # exit/exposure accounting — a product decision on strategy
        # behavior, not a bug fix.
        "max_positions_live": "single-symbol run never has concurrent positions to cap",
        "max_positions_per_symbol": "permanently masked by the single-position-per-symbol guard in both orchestrators, not just this fixture",
        # Optimizer/meta-learning control, not a per-bar trading parameter:
        # it governs how an optimizer explores the search space, not any
        # single backtest's decisions. Its causal effect belongs in a test
        # of the optimizer loop, not a single orchestrator run.
        "learning_rate_exploration_factor": "optimizer-level meta-parameter, not a per-bar trading input",
        # Verified (see this file's own investigation): with the fixed,
        # non-calibratable max_symbol_concentration at its default (0.05),
        # the concentration cap is the binding sizing constraint across the
        # full realistic equity/price range tried (Rs.100k-1.2M against
        # ~Rs.1000-2500 SUNPHARMA-like prices) — capital_per_trade_fraction
        # and capital_allocation_mode both size correctly, but to the same
        # concentration-capped quantity regardless of their own value. This
        # is a property of the current fixed-parameter defaults, not a code
        # defect — it cannot be fixed without changing a fixed parameter,
        # which calibration is explicitly not allowed to do.
        "capital_per_trade_fraction": "masked by the fixed max_symbol_concentration cap at its default",
        # Verified: this fixture's trades all resolve via stop/target/
        # signal-exit within 2-5 bars, never approaching even max_hold_bars'
        # minimum bound (20) — and a calmer fixture tried specifically to
        # let positions run long enough produced zero entries at all. Proven
        # live at the box level (test_revision2_sensitivity.py); needs a
        # fixture this suite doesn't yet have to prove end-to-end.
        "max_hold_bars": "no trade in this fixture is held long enough for it to bind",
        "red_threshold": "no trade in this fixture reaches the red-quality-band exit path",
        # Verified directly: sampled this fixture's real
        # SafetyGatesTargetBox.evaluate_post_sizing() calls at
        # minimum_profit_margin_over_cost=2.0 (its maximum) -- projected
        # profit (~Rs 32) still ran ~3x the required threshold (~Rs 10),
        # so every real trade here clears the margin requirement at every
        # value in its calibratable range. Same masking pattern as
        # capital_per_trade_fraction above: a property of this fixture's
        # ATR/price scale, not evidence the parameter is dead -- it
        # genuinely gates trades on real, higher-cost-relative-to-profit
        # data (see the INFY/MARUTI 3-year external-engine runs, where an
        # earlier, differently-shaped version of this same check rejected
        # 100% of INFY's candidate trades and 0% of MARUTI's).
        "minimum_profit_margin_over_cost": "projected profit is ~3x the required cost margin even at the maximum (2.0) on this fixture",
        # Not a fixture artifact -- genuinely unconsumed by either in-house
        # orchestrator this test file exercises. trailing_stop_atr_mult is
        # ContinuousExitController's own ATR trail multiplier, wired into
        # revision2_external's orchestrator only so far (see
        # tests/test_revision2_portfolio.py and test_revision2_pipeline.py
        # for the matching, honestly-documented "missing" parameter-
        # coverage entries on the in-house side).
        "trailing_stop_atr_mult": "ContinuousExitController is not wired into either in-house orchestrator yet",
    }

    def test_every_calibratable_parameter_changes_the_real_trade_ledger(self):
        calibratable = sorted(self.registry.calibratable_names())
        self.assertEqual(len(calibratable), 46)
        # String-typed parameters carry placeholder (0, 0) registry bounds,
        # so a numeric min/max sweep is meaningless for them — they get a
        # dedicated test below instead.
        numeric = [
            n for n in calibratable
            if self.registry.get(n).param_type in ("int", "float") and n not in self.KNOWN_LEDGER_INERT
        ]
        default_result = self._run({})
        failures = []
        for name in numeric:
            spec = self.registry.get(name)
            with self.subTest(param=name):
                min_result = self._run({name: spec.minimum})
                max_result = self._run({name: spec.maximum})
                if min_result == default_result == max_result:
                    failures.append(name)
        self.assertEqual(failures, [], f"parameters with zero effect on the real trade ledger: {failures}")

    def test_capital_allocation_mode_is_currently_masked_too(self):
        # Same root cause as capital_per_trade_fraction above (verified: the
        # fixed max_symbol_concentration cap dominates at its default) — the
        # box-level test in test_revision2_sensitivity.py proves the box
        # itself does react to "equal" vs "aggressive"; this documents that
        # it doesn't yet reach this fixture's real trade ledger.
        equal_result = self._run({"capital_allocation_mode": "equal"})
        aggressive_result = self._run({"capital_allocation_mode": "aggressive"})
        self.assertEqual(equal_result, aggressive_result)

    def test_documented_exclusions_still_show_zero_effect(self):
        # Guards against the exclusion list going stale: if a future fix
        # makes one of these causal after all, this test starts failing and
        # the parameter should move up into the main sweep.
        default_result = self._run({})
        for name, reason in self.KNOWN_LEDGER_INERT.items():
            spec = self.registry.get(name)
            if spec.param_type not in ("int", "float"):
                continue
            with self.subTest(param=name):
                min_result = self._run({name: spec.minimum})
                max_result = self._run({name: spec.maximum})
                self.assertTrue(
                    min_result == default_result == max_result,
                    f"{name} is no longer ledger-inert ({reason}) — move it into the main sweep",
                )

    def test_pid_gains_specifically_move_the_trade_ledger(self):
        # The exact gap flagged in review: prove each PID-related parameter
        # changes real trades, not just the diagnostic pid_info dict.
        pid_params = [
            "pid_kp_entry", "pid_ki_entry", "pid_kd_entry",
            "pid_kp_exit", "pid_ki_exit", "pid_kd_exit",
            "pid_integral_window_bars", "pid_integral_max_clamp", "pid_derivative_smoothing",
        ]
        default_result = self._run({})
        for name in pid_params:
            spec = self.registry.get(name)
            with self.subTest(param=name):
                min_result = self._run({name: spec.minimum})
                max_result = self._run({name: spec.maximum})
                self.assertFalse(
                    min_result == default_result == max_result,
                    f"{name}: identical trade ledger at min/default/max — PID output isn't reaching real trades",
                )


if __name__ == "__main__":
    unittest.main()
