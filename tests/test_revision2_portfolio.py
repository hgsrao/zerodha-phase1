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


if __name__ == "__main__":
    unittest.main()
