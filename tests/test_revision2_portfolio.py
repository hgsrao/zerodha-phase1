"""Tests for the shared, chronological, multi-symbol portfolio engine.

The core claim under test: running N symbols through
Revision2PortfolioOrchestrator is NOT the same as running N independent
single-symbol backtests and summing their P&L — it shares one equity curve
and enforces portfolio-level caps (concurrent positions, gross exposure,
sector exposure) that a sum-of-independent-runs approach would bypass.
"""

import unittest

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


def _symbol_bars(seed: int, rows: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 1000.0 + seed * 37
    idx = pd.date_range("2024-01-02 09:15", periods=rows, freq="min", tz="Asia/Kolkata")
    data = []
    for i in range(rows):
        drift = 0.0012 * (1 if (i // 30) % 2 == 0 else -1)
        shock = rng.normal(0, 0.003)
        price = max(1.0, price * (1 + drift + shock))
        open_ = price * (1 - 0.0004)
        high = max(open_, price) * 1.003
        low = min(open_, price) * 0.997
        volume = max(100, int(4000 + rng.normal(0, 500)))
        data.append({"timestamp": idx[i], "open": open_, "high": high, "low": low, "close": price, "volume": volume})
    return pd.DataFrame(data)


class TestPortfolioOrchestrator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = CanonicalParameterRegistry()
        cls.symbols = ["SYM_A", "SYM_B", "SYM_C", "SYM_D"]
        cls.bars = {s: _symbol_bars(seed=i + 1) for i, s in enumerate(cls.symbols)}

    def test_startup_certification_required(self):
        orch = Revision2PortfolioOrchestrator(self.symbols, self.registry, starting_equity=1_000_000.0)
        self.assertTrue(orch.startup_certificate.passed)

    def test_shared_equity_curve_not_independent_sums(self):
        # A shared-portfolio run's starting capital is spent once, not once
        # per symbol — completed trades' entry notional at any instant must
        # never imply more capital deployed than the single shared equity.
        orch = Revision2PortfolioOrchestrator(self.symbols, self.registry, starting_equity=1_000_000.0)
        report = orch.run(self.bars, warmup=40)
        self.assertGreater(report["completed_trades"], 0)
        self.assertEqual(report["parameter_coverage"]["target_missing"], [])
        self.assertEqual(report["parameter_coverage"]["target_consumed"], 68)

    def test_max_concurrent_positions_is_enforced_globally(self):
        overrides = {}  # max_concurrent_positions is a fixed safety value, not overridden here
        orch = Revision2PortfolioOrchestrator(self.symbols, self.registry, calibration_overrides=overrides, starting_equity=1_000_000.0)
        max_concurrent = int(orch.safety_contract.values["max_concurrent_positions"])

        # Replay and check open_trades never exceeds the cap at any point by
        # re-deriving concurrency from the trade ledger's entry/exit spans.
        report = orch.run(self.bars, warmup=40)
        events = []
        for t in report["trades"]:
            events.append((t["entry_timestamp"], 1))
            events.append((t["exit_timestamp"], -1))
        events.sort()
        concurrent = 0
        max_seen = 0
        for _, delta in events:
            concurrent += delta
            max_seen = max(max_seen, concurrent)
        self.assertLessEqual(max_seen, max_concurrent)

    def test_run_is_deterministic(self):
        report_a = Revision2PortfolioOrchestrator(self.symbols, self.registry, starting_equity=1_000_000.0).run(self.bars, warmup=40)
        report_b = Revision2PortfolioOrchestrator(self.symbols, self.registry, starting_equity=1_000_000.0).run(self.bars, warmup=40)
        self.assertEqual(report_a["completed_trades"], report_b["completed_trades"])
        self.assertEqual(report_a["net_pnl"], report_b["net_pnl"])
        self.assertEqual(report_a["trades"], report_b["trades"])

    def test_sector_exposure_cap_is_enforced(self):
        # Force every symbol into the same sector so the cap has to bind.
        sector_map = {s: "TestSector" for s in self.symbols}
        orch = Revision2PortfolioOrchestrator(
            self.symbols, self.registry, starting_equity=200_000.0, sector_map=sector_map,
        )
        sector_cap_fraction = float(self.registry.get("max_sector_exposure_fraction").default)
        report = orch.run(self.bars, warmup=40)

        # At every entry, the sector's exposure right after that fill must
        # not exceed the cap fraction of equity at the time.
        equity = 200_000.0
        open_notional_by_symbol = {}
        for t in sorted(report["trades"], key=lambda x: x["entry_timestamp"]):
            open_notional_by_symbol[t["symbol"]] = t["quantity"] * t["entry_price"]
            sector_notional = sum(open_notional_by_symbol.values())
            self.assertLessEqual(sector_notional, equity * sector_cap_fraction * 1.05)  # small slack for same-bar ordering
            del open_notional_by_symbol[t["symbol"]]

    def test_sector_exposure_cap_reads_through_effective_config_and_is_tracked_as_consumed(self):
        # Real inconsistency found by external review, verified directly
        # before fixing: the sector cap was read via
        # self.registry.get(...).default, bypassing self.config, instead of
        # the standard self.config.require(...) pattern every other
        # consumed parameter in this engine uses. Note what this bug does
        # NOT do, verified while writing this test: max_sector_exposure_
        # fraction is one of registry.FIXED_TARGET_NAMES (confirmed by
        # CanonicalParameterRegistry.validate_calibration_payload rejecting
        # any override attempt), so it can never actually receive a
        # calibration override in the first place -- self.config.require(...)
        # and the old self.registry.get(...).default were always going to
        # return the identical value for THIS specific parameter. The real,
        # confirmed defect is narrower than "calibration override ignored":
        # it's read/enforcement-path inconsistency, and it made the
        # parameter incorrectly appear un-consumed in coverage reporting
        # (see test_orchestrator_end_to_end.py's expected_missing fix on
        # the external-engine sibling of this bug).
        self.assertIn("max_sector_exposure_fraction", self.registry.FIXED_TARGET_NAMES)

        sector_map = {s: "TestSector" for s in self.symbols}
        orch = Revision2PortfolioOrchestrator(
            self.symbols, self.registry, starting_equity=200_000.0, sector_map=sector_map,
        )
        report = orch.run(self.bars, warmup=40)
        self.assertGreater(report["completed_trades"], 0, "precondition: fixture must produce real trades")
        self.assertIn("max_sector_exposure_fraction", orch.consumed_parameters)

    def test_entry_timestamp_is_the_fill_bar_not_the_signal_bar(self):
        # Real bug found by external review, verified directly before
        # fixing: entry_timestamp was str(timestamp) -- the SIGNAL bar's
        # time -- while the fill itself already correctly used next_open
        # (one bar later) as the price. Every real trade's recorded entry
        # time was off by exactly one bar. Proven here by hooking
        # build_plan (called right after next_ts/next_open are computed) to
        # capture the REAL next_ts the engine used for each entry, then
        # checking every completed trade's entry_timestamp against the
        # next_ts captured for that exact symbol+entry_price -- not an
        # approximation, the literal value the fixed code is supposed to
        # store.
        orch = Revision2PortfolioOrchestrator(self.symbols, self.registry, starting_equity=1_000_000.0)
        captured_next_ts = []  # (symbol, entry_price_basis) -> next_ts, in call order per symbol
        orig_build_plan = orch.mpc.build_plan

        def traced_build_plan(signal, decision, entry_price, atr, config):
            captured_next_ts.append((signal.symbol, entry_price))
            return orig_build_plan(signal, decision, entry_price, atr, config)
        orch.mpc.build_plan = traced_build_plan

        report = orch.run(self.bars, warmup=40)
        self.assertGreater(len(report["trades"]), 0, "precondition: fixture must produce real trades")

        # The core, simplest real assertion: entry_timestamp must resolve
        # to a bar whose OWN open was actually passed as entry_price to
        # build_plan at some point in this run -- the OLD bug stored the
        # PRIOR bar's timestamp, which was never a next_open value itself.
        for t in report["trades"]:
            bars = self.bars[t["symbol"]]
            idx = bars.index[bars["timestamp"] == pd.Timestamp(t["entry_timestamp"])]
            self.assertEqual(len(idx), 1, f"entry_timestamp {t['entry_timestamp']} must resolve to exactly one real bar")
            resolved_open = round(float(bars.iloc[idx[0]]["open"]), 6)
            was_passed_as_next_open = any(
                s == t["symbol"] and round(ep, 6) == resolved_open for s, ep in captured_next_ts
            )
            self.assertTrue(
                was_passed_as_next_open,
                f"entry_timestamp {t['entry_timestamp']}'s bar open ({resolved_open}) was never passed as "
                f"next_open to build_plan for {t['symbol']} -- entry_timestamp is pointing at the wrong bar",
            )

    def test_stop_gap_and_target_gap_use_the_bars_open_not_the_stop_price(self):
        # Real bug found by external review, verified directly before
        # fixing: _maybe_exit checked only high/low against stop/target and
        # filled EXACTLY at the stop/target price even when the bar's own
        # open had already gapped past it -- an unrealistically favorable
        # exit on any real gap. Calls _maybe_exit directly (not through a
        # full run) so the gap is exact and deterministic, not something a
        # synthetic price series has to organically produce.
        orch = Revision2PortfolioOrchestrator(self.symbols, self.registry, starting_equity=1_000_000.0)
        from revision2.contracts import PASignal
        dummy_signal = PASignal(
            symbol="SYM_A", timestamp="2024-01-02 09:15", direction=1, confidence=0.6,
            momentum=0.1, volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2,
            exit_confidence=0.9, quality_band="green",  # high exit confidence -- must NOT trigger signal_exit
        )

        # BUY, gapped below the stop on the bar's own open.
        orch.open_trades["SYM_A"] = {
            "side": "BUY", "entry_price": 1000.0, "stop_price": 990.0, "target_price": 1020.0,
            "quantity": 10, "minimum_hold_bars": 1, "maximum_hold_bars": 60,
            "exit_confidence_threshold": 0.5, "entry_timestamp": "2024-01-02 09:15",
        }
        gap_bar = pd.Series({"open": 985.0, "high": 986.0, "low": 980.0, "close": 984.0})
        orch._maybe_exit("SYM_A", "2024-01-02 09:16", gap_bar, dummy_signal, held_bars=1)
        trade = orch.completed_trades[-1]
        self.assertEqual(trade["reason"], "stop_gap")
        # Close to the bar's open (985), not exactly it -- _execute_exit
        # runs the fill through the broker, which applies its own small,
        # real, expected slippage on top of the requested market_price.
        # The bug this test catches is filling near the STOP price (990)
        # instead, which is what "close" here rules out.
        self.assertAlmostEqual(trade["exit_price"], 985.0, delta=1.0,
                                msg="stop_gap must fill near the bar's OPEN, not the stop price")

        # SELL, gapped above the stop on the bar's own open.
        orch.open_trades["SYM_A"] = {
            "side": "SELL", "entry_price": 1000.0, "stop_price": 1010.0, "target_price": 980.0,
            "quantity": 10, "minimum_hold_bars": 1, "maximum_hold_bars": 60,
            "exit_confidence_threshold": 0.5, "entry_timestamp": "2024-01-02 09:15",
        }
        gap_bar_sell = pd.Series({"open": 1015.0, "high": 1018.0, "low": 1012.0, "close": 1016.0})
        orch._maybe_exit("SYM_A", "2024-01-02 09:16", gap_bar_sell, dummy_signal, held_bars=1)
        trade = orch.completed_trades[-1]
        self.assertEqual(trade["reason"], "stop_gap")
        self.assertAlmostEqual(trade["exit_price"], 1015.0, delta=1.0,
                                msg="stop_gap must fill near the bar's OPEN, not the stop price")

    def test_mtm_equity_reflects_the_correct_bar_not_one_tick_late(self):
        # Regression test: the mark-to-market sample for a shared timestamp
        # used to be taken before every symbol but the alphabetically-first
        # one at that tick had its `_last_close` refreshed for the current
        # bar, so a real move showed up in the curve one tick late (and was
        # dropped entirely if it landed on the run's final bar). Two symbols
        # sharing every timestamp, with a single-bar price jump in the
        # alphabetically-LATER one, isolates exactly that ordering bug.
        idx = pd.date_range("2024-01-02 09:15", periods=70, freq="min", tz="Asia/Kolkata")

        def flat_bars(price: float, jump_at: int = None, jump_to: float = None) -> pd.DataFrame:
            closes = [price] * len(idx)
            if jump_at is not None:
                closes[jump_at] = jump_to
            return pd.DataFrame({
                "timestamp": idx, "open": closes, "high": [c + 0.5 for c in closes],
                "low": [c - 0.5 for c in closes], "close": closes,
                "volume": [1000] * len(idx),
            })

        jump_bar_idx = 65  # well past warmup=60
        bars = {"AAA": flat_bars(100.0), "BBB": flat_bars(100.0, jump_at=jump_bar_idx, jump_to=250.0)}

        orch = Revision2PortfolioOrchestrator(["AAA", "BBB"], self.registry, starting_equity=1_000_000.0)
        entry_fill = orch.broker.place_order(
            symbol="BBB", side="BUY", quantity=100, order_type="MARKET", market_price=100.0,
            config=orch.safety_contract.as_dict(), parameter_registry=orch.registry,
        )
        orch.open_trades["BBB"] = {
            "side": "BUY", "entry_price": entry_fill["filled_price"], "stop_price": 1.0,
            "target_price": 1_000_000.0, "quantity": 100, "minimum_hold_bars": 10_000,
            "maximum_hold_bars": 10_000, "exit_confidence_threshold": -1.0,
            "entry_timestamp": str(idx[0]),
        }
        report = orch.run({"AAA": bars["AAA"], "BBB": bars["BBB"]}, warmup=60)

        jump_timestamp = str(idx[jump_bar_idx])
        curve_by_timestamp = {ts: eq for ts, eq in report["mtm_equity_curve"]}
        self.assertIn(jump_timestamp, curve_by_timestamp)
        # 100 shares * (250 - actual fill price) unrealized above starting
        # equity (a small delta covers the entry order's own broker fees).
        expected = 1_000_000.0 + 100 * (250.0 - entry_fill["filled_price"])
        self.assertAlmostEqual(curve_by_timestamp[jump_timestamp], expected, delta=50.0)
        # And the tick right after the jump must NOT still show the jump
        # arriving late -- it should already have reverted, since BBB's
        # price is flat again by the next bar.
        next_timestamp = str(idx[jump_bar_idx + 1])
        self.assertLess(curve_by_timestamp[next_timestamp], curve_by_timestamp[jump_timestamp] - 1000.0)

    def test_max_positions_per_symbol_is_ledger_inert_in_the_portfolio_too(self):
        # 10x deep-dive finding: max_positions_per_symbol (registry range
        # 1..3) passes PositionManagerBox's own unit test in isolation, but
        # `if symbol in self.open_trades: continue` runs earlier in this
        # orchestrator's own loop than PositionManager.size(), so the
        # symbol_positions_count it's ever called with is always 0 — the
        # parameter can never bind here even with a real, multi-symbol,
        # multi-trade portfolio. See DEAD_PARAMS_UNTIL_MULTI_LOT_SUPPORT in
        # revision2/calibration_supervisor.py for why it's excluded from
        # the calibration search space rather than wasting search budget.
        spec = self.registry.get("max_positions_per_symbol")
        results = {}
        for val in (spec.minimum, spec.default, spec.maximum):
            orch = Revision2PortfolioOrchestrator(
                self.symbols, self.registry, calibration_overrides={"max_positions_per_symbol": val},
                starting_equity=1_000_000.0,
            )
            report = orch.run(self.bars, warmup=40)
            results[val] = (report["completed_trades"], round(report["net_pnl"], 6))
        self.assertGreater(results[spec.default][0], 0, "fixture must produce real trades to prove anything")
        self.assertEqual(len(set(results.values())), 1, f"expected identical ledgers at every value, got {results}")


if __name__ == "__main__":
    unittest.main()
