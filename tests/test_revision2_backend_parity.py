"""Real in-house-vs-Backtrader parity tests.

Both revision2/in_house_backend.py and revision2/backtrader_backend.py run
a REAL Revision2Orchestrator / real Backtrader Strategy (the same box
objects, the same EffectiveConfig) against the same bars and emit
BackendEvent streams; first_divergence() finds the first place they
disagree. These tests lock in what real runs (this fixture, and separately
a real ADANIENT/5000-bar window -- see this file's own development
history) proved:

  - signal, gate_decision, order, and fill events (the entry side) match
    EXACTLY -- byte-for-byte, same order -- once the harness itself is
    wired correctly (timezone handling, warmup alignment, order-vs-fill
    price semantics, slippage/cost formula, and an entry_bar_idx off-by-one
    were all real bugs this comparison found and fixed in the harness).

  - "exit" events do NOT yet match: they agree on side/quantity/reason, but
    Backtrader's plain Market order for a stop/target/signal exit only
    fills at the NEXT bar's open, one bar later than
    revision2/orchestrator.py's _maybe_exit(), which fills a stop/target
    hit intra-bar at the trigger level itself (a common, deliberate
    backtesting simplification). Making Backtrader match would mean using
    Stop/Limit order types for exits instead of Market -- a real
    execution-model decision, not fixed here.

  - the "equity" event does NOT yet match after a trade with real costs:
    Revision2Orchestrator's per-bar equity is realized-P&L-only (it never
    reflects entry costs or unrealized P&L until the run's final aggregate
    _transaction_costs() call), while Backtrader's broker.getvalue() is a
    true real-time mark-to-market including costs as they're paid. This is
    the same class of gap already fixed for Revision2PortfolioOrchestrator
    earlier (its mtm_equity_curve) but not yet applied to the single-symbol
    orchestrator.

The exit-timing and equity gaps are real, open findings, not harness bugs,
and are NOT silently patched here since both touch core trading/accounting
semantics (safety-gate drawdown reads _equity_curve; exit fill timing
changes real P&L) rather than instrumentation.
"""

import unittest

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.backend_contract import first_divergence
from revision2.backtrader_backend import run_backtrader_events
from revision2.in_house_backend import run_in_house_events


def _synthetic_bars(rows: int = 600, seed: int = 11) -> pd.DataFrame:
    """Identical construction to tests/test_revision2_causal_sensitivity.py's
    fixture (three-regime: calm-then-trending, choppy, high-vol shock) --
    reliably produces dozens of real trades within a few hundred bars."""
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


class TestBackendParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = CanonicalParameterRegistry()
        cls.bars = _synthetic_bars()
        cls.in_house_events = run_in_house_events("TESTSYM", cls.bars, cls.registry, warmup=40)
        cls.backtrader_events = run_backtrader_events("TESTSYM", cls.bars, cls.registry, warmup=40)

    def test_fixture_produces_real_trades(self):
        # Otherwise this whole suite proves nothing.
        fills = [e for e in self.in_house_events if e.event_type == "fill"]
        self.assertGreater(len(fills), 0, "fixture must produce at least one real fill")

    def test_signal_and_gate_decision_events_match_before_the_first_exit(self):
        # Strictly before the first exit's own bar: in_house_backend.py's
        # source orchestrator goes flat and re-evaluates a fresh entry on
        # the SAME bar it just exited on (real behavior: _maybe_exit()
        # closing the position and the entry check both run within one
        # bar's processing). Backtrader can't do that at the same bar,
        # because -- the documented, open gap below -- its exit fill
        # hasn't landed yet at that point; it fires one bar later. So even
        # the exit's own bar carries one extra signal/gate_decision pair on
        # the in-house side that has no Backtrader counterpart yet, not a
        # new bug on top of the one already documented.
        first_exit_ts = next(e for e in self.in_house_events if e.event_type == "exit").event_timestamp
        signals_only = lambda events: [
            e for e in events if e.event_type in ("signal", "gate_decision") and e.event_timestamp < first_exit_ts
        ]
        diff = first_divergence(signals_only(self.in_house_events), signals_only(self.backtrader_events))
        self.assertIsNone(diff, f"signal/gate_decision divergence: {diff}")

    def test_order_and_entry_fill_events_match_exactly(self):
        # The entry side only: order, fill, and entry-leg cost, through the
        # first exit. Exit events are checked separately below since they
        # have a documented, open timing gap.
        ih, bt = self._events_through_first_exit()

        def entry_events(events):
            return [
                e for e in events
                if e.event_type in ("order", "fill")
                or (e.event_type == "cost" and e.reason == "entry leg cost")
            ]
        diff = first_divergence(entry_events(ih), entry_events(bt))
        self.assertIsNone(diff, f"order/fill divergence: {diff}")

    def test_first_exit_reason_and_size_match_but_timing_is_the_documented_one_bar_gap(self):
        # Guards against this gap silently changing shape: if a future fix
        # (e.g. switching Backtrader's exit orders to Stop/Limit types)
        # makes exit timing agree, this test starts failing on the
        # assertNotEqual below and should be merged into the entry-side test.
        ih_exit = next(e for e in self.in_house_events if e.event_type == "exit")
        bt_exit = next(e for e in self.backtrader_events if e.event_type == "exit")
        self.assertEqual(ih_exit.side, bt_exit.side)
        self.assertEqual(ih_exit.quantity, bt_exit.quantity)
        self.assertEqual(ih_exit.reason, bt_exit.reason)
        self.assertNotEqual(
            ih_exit.event_timestamp, bt_exit.event_timestamp,
            "exit timing now matches -- update this test's docstring and assertions",
        )

    def test_equity_divergence_is_the_documented_realized_vs_mtm_gap(self):
        # This happens well before the first exit (the very first bar with
        # a paid entry cost is enough), so the whole, un-truncated event
        # streams are compared here.
        # Guards against this gap silently changing shape: if a future fix
        # makes per-bar equity agree, this test starts failing on the
        # assertIsNotNone below and the exclusion should be removed (and
        # ideally replaced with a real mark-to-market equity curve on
        # Revision2Orchestrator, matching Revision2PortfolioOrchestrator's
        # mtm_equity_curve).
        diff = first_divergence(self.in_house_events, self.backtrader_events)
        self.assertIsNotNone(diff, "equity events now match -- update this test's docstring and assertions")
        self.assertEqual(diff["in_house"]["event_type"], "equity")
        self.assertEqual(diff["backtrader"]["event_type"], "equity")

    def _events_through_first_exit(self):
        """Both streams, truncated to end right after the FIRST exit event
        (inclusive) -- the honest scope of what stays aligned given the
        documented, open exit-timing gap."""
        def through_first_exit(events):
            for i, e in enumerate(events):
                if e.event_type == "exit":
                    return events[: i + 1]
            raise AssertionError("no exit event found")
        return through_first_exit(self.in_house_events), through_first_exit(self.backtrader_events)


if __name__ == "__main__":
    unittest.main()
