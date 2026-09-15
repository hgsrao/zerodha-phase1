"""Tests for the P&L tracking added to run_chart_studies_live_monitor.py:
PaperTrade math, mark-at-close for still-open positions, and the EOD
statement generator. No real Kite/network calls - everything here is
synthetic, matching this project's established test convention."""
from __future__ import annotations

from datetime import datetime

import pytest

import pandas as pd

import run_chart_studies_live_monitor as mon
from run_chart_studies_live_monitor import (
    NOTIONAL_PER_POSITION, PaperTrade, generate_eod_pnl_statement,
    _net_of_costs, _trade_with_costs,
)
from zerodha_delivery_costs import buy_cost, sell_cost


def _watcher(**overrides):
    """Bare SymbolWatcher for testing _try_execute_pending_fill() - only
    the fields that method touches need to exist, no Kite client needed."""
    w = mon.SymbolWatcher.__new__(mon.SymbolWatcher)
    w.symbol = "X"
    w.pending_signal = None
    w.open_trade = None
    w.trades = []
    w.state = mon.ind.SymbolSignalState()
    for k, v in overrides.items():
        setattr(w, k, v)
    return w


def _bar(ts, open_price):
    return {"timestamp": pd.Timestamp(ts), "open": open_price, "high": open_price, "low": open_price, "close": open_price, "volume": 100.0}


class _FakeWatcher:
    """Minimal duck-typed stand-in for SymbolWatcher - the EOD statement
    generator only ever calls .mark_open_trade_at_close() and reads
    .trades, so a real Kite-connected watcher isn't needed to test it."""
    def __init__(self, trades, latest_close=None):
        self.trades = trades
        self.open_trade = trades[-1] if trades and trades[-1].status == "OPEN" else None
        self.latest_close = latest_close

    def mark_open_trade_at_close(self, ts):
        if self.open_trade is not None and self.open_trade.status == "OPEN" and self.latest_close is not None:
            self.open_trade.close(ts, self.latest_close, status="MARKED_AT_CLOSE")


# ---------------------------------------------------------------------------
# PaperTrade
# ---------------------------------------------------------------------------
def test_paper_trade_starts_open_with_no_return():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    assert t.status == "OPEN"
    assert t.return_pct is None
    assert t.notional_pnl is None


def test_paper_trade_close_computes_correct_return_pct():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    t.close("t1", 110.0)
    assert t.status == "CLOSED"
    assert t.return_pct == pytest.approx(10.0)


def test_paper_trade_notional_pnl_matches_the_shared_notional_convention():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    t.close("t1", 110.0)
    expected_shares = NOTIONAL_PER_POSITION / 100.0
    assert t.notional_pnl == pytest.approx(expected_shares * 10.0)


def test_paper_trade_loss_gives_negative_return_and_pnl():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    t.close("t1", 90.0)
    assert t.return_pct == pytest.approx(-10.0)
    assert t.notional_pnl < 0


# ---------------------------------------------------------------------------
# net_notional_pnl / _net_of_costs (2026-08-24 addendum)
# ---------------------------------------------------------------------------
def test_net_notional_pnl_is_none_while_open():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    assert t.net_notional_pnl is None


def test_net_of_costs_is_strictly_less_than_gross_on_a_winner():
    """Real costs are never negative, so net must never exceed gross."""
    gross, net, buy_fees, sell_fees = _net_of_costs(100.0, 110.0, NOTIONAL_PER_POSITION)
    assert buy_fees > 0
    assert sell_fees > 0
    assert net == pytest.approx(gross - buy_fees - sell_fees)
    assert net < gross


def test_net_of_costs_matches_zerodha_delivery_costs_directly():
    """Reuses buy_cost/sell_cost unchanged - cross-check against calling
    them directly the same way entry_gate_dry_run.py does."""
    entry_price, exit_price = 100.0, 105.0
    shares = NOTIONAL_PER_POSITION / entry_price
    expected_buy = buy_cost(shares * entry_price).total
    expected_sell = sell_cost(shares * exit_price).total
    _, net, buy_fees, sell_fees = _net_of_costs(entry_price, exit_price, NOTIONAL_PER_POSITION)
    assert buy_fees == pytest.approx(expected_buy)
    assert sell_fees == pytest.approx(expected_sell)


def test_paper_trade_net_notional_pnl_is_less_than_gross_when_closed():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    t.close("t1", 110.0)
    assert t.net_notional_pnl < t.notional_pnl


# ---------------------------------------------------------------------------
# _trade_with_costs (snapshot serialization)
# ---------------------------------------------------------------------------
def test_trade_with_costs_open_trade_uses_mark_price():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    d = _trade_with_costs(t, mark_price=105.0)
    assert d["status"] == "OPEN"
    assert d["cost_mark_price"] == 105.0
    assert d["net_notional_pnl"] is not None
    assert d["net_notional_pnl"] < d["gross_notional_pnl"]


def test_trade_with_costs_open_trade_with_no_mark_price_yet_is_all_none():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    d = _trade_with_costs(t, mark_price=None)
    assert d["net_notional_pnl"] is None
    assert d["gross_notional_pnl"] is None


def test_trade_with_costs_closed_trade_uses_exit_price_not_mark_price():
    t = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    t.close("t1", 110.0)
    d = _trade_with_costs(t, mark_price=999.0)  # must be ignored once closed
    assert d["cost_mark_price"] == 110.0


# ---------------------------------------------------------------------------
# _try_execute_pending_fill (next-bar-open execution, 2026-08-24 addendum)
# ---------------------------------------------------------------------------
def test_pending_fill_waits_when_no_bar_exists_after_the_decision_bar():
    signal = {"timestamp": "2026-08-24 10:00:00", "direction": "ENTRY", "score": 3, "reads": {}, "price": 100.0}
    w = _watcher(pending_signal=signal)
    closed = pd.DataFrame([_bar("2026-08-24 10:00:00", 100.0)])  # only the decision bar itself
    assert w._try_execute_pending_fill(closed) is None
    assert w.pending_signal is signal  # still waiting, not cleared


def test_pending_fill_entry_executes_at_the_next_bar_open_not_the_decision_price():
    signal = {"timestamp": "2026-08-24 10:00:00", "direction": "ENTRY", "score": 3, "reads": {}, "price": 100.0}
    w = _watcher(pending_signal=signal)
    closed = pd.DataFrame([_bar("2026-08-24 10:00:00", 100.0), _bar("2026-08-24 10:05:00", 100.2)])  # 0.2% - within ABSTAIN_SLIPPAGE_PCT
    event = w._try_execute_pending_fill(closed)
    assert event["direction"] == "ENTRY"
    assert event["stage"] == "FILL"
    assert event["price"] == pytest.approx(100.2)  # next bar's OPEN, not the decision price
    assert w.pending_signal is None
    assert w.open_trade is not None
    assert w.open_trade.entry_price == pytest.approx(100.2)
    assert w.trades == [w.open_trade]


def test_pending_fill_exit_closes_the_open_trade_at_next_bar_open():
    open_trade = PaperTrade(symbol="X", entry_ts="t0", entry_price=90.0)
    signal = {"timestamp": "2026-08-24 10:00:00", "direction": "EXIT", "score": 0, "reads": {}, "price": 100.0}
    w = _watcher(pending_signal=signal, open_trade=open_trade, trades=[open_trade])
    closed = pd.DataFrame([_bar("2026-08-24 10:00:00", 100.0), _bar("2026-08-24 10:05:00", 99.8)])  # 0.2% - within bound
    event = w._try_execute_pending_fill(closed)
    assert event["direction"] == "EXIT"
    assert event["price"] == pytest.approx(99.8)
    assert w.open_trade is None
    assert open_trade.status == "CLOSED"
    assert open_trade.exit_price == pytest.approx(99.8)


def test_pending_fill_abstains_when_slippage_exceeds_the_boundary():
    signal = {"timestamp": "2026-08-24 10:00:00", "direction": "ENTRY", "score": 3, "reads": {}, "price": 100.0}
    w = _watcher(pending_signal=signal)
    closed = pd.DataFrame([_bar("2026-08-24 10:00:00", 100.0), _bar("2026-08-24 10:05:00", 105.0)])  # 5% gap
    event = w._try_execute_pending_fill(closed)
    assert event["direction"] == "ABSTAIN"
    assert event["abstained_direction"] == "ENTRY"
    assert w.pending_signal is None  # cleared - does not keep retrying
    assert w.open_trade is None  # no trade opened
    assert w.trades == []


def test_pending_fill_and_abstain_are_both_recorded_in_state_events():
    """The card/ledger must show what actually happened (FILL or ABSTAIN),
    not just the original SIGNAL - both get appended to state.events."""
    signal = {"timestamp": "2026-08-24 10:00:00", "direction": "ENTRY", "score": 3, "reads": {}, "price": 100.0}
    w = _watcher(pending_signal=signal)
    closed = pd.DataFrame([_bar("2026-08-24 10:00:00", 100.0), _bar("2026-08-24 10:05:00", 100.2)])
    w._try_execute_pending_fill(closed)
    assert len(w.state.events) == 1
    assert w.state.events[0]["stage"] == "FILL"

    signal2 = {"timestamp": "2026-08-24 10:05:00", "direction": "ENTRY", "score": 3, "reads": {}, "price": 100.2}
    w2 = _watcher(pending_signal=signal2)
    closed2 = pd.DataFrame([_bar("2026-08-24 10:05:00", 100.2), _bar("2026-08-24 10:10:00", 105.0)])  # big gap -> ABSTAIN
    w2._try_execute_pending_fill(closed2)
    assert len(w2.state.events) == 1
    assert w2.state.events[0]["stage"] == "ABSTAIN"


def test_pending_fill_picks_the_first_bar_after_decision_not_the_last():
    """If several bars have accumulated since the decision (e.g. the
    monitor was polling slowly), the fill must use the FIRST one after
    the decision bar, not whichever is most recent - otherwise it isn't
    really "the next bar's open" any more."""
    signal = {"timestamp": "2026-08-24 10:00:00", "direction": "ENTRY", "score": 3, "reads": {}, "price": 100.0}
    w = _watcher(pending_signal=signal)
    closed = pd.DataFrame([
        _bar("2026-08-24 10:00:00", 100.0),
        _bar("2026-08-24 10:05:00", 100.2),  # the correct fill bar
        _bar("2026-08-24 10:10:00", 100.9),
    ])
    event = w._try_execute_pending_fill(closed)
    assert event["price"] == pytest.approx(100.2)


# ---------------------------------------------------------------------------
# mark_open_trade_at_close (via the real SymbolWatcher, no Kite calls needed)
# ---------------------------------------------------------------------------
def test_mark_open_trade_at_close_uses_the_last_seen_price():
    w = mon.SymbolWatcher.__new__(mon.SymbolWatcher)  # bypass __init__ - no client needed for this method
    trade = PaperTrade(symbol="X", entry_ts="t0", entry_price=100.0)
    w.open_trade = trade
    w.latest_close = 105.0
    w.mark_open_trade_at_close("t_close")
    assert trade.status == "MARKED_AT_CLOSE"
    assert trade.exit_price == 105.0
    assert trade.exit_ts == "t_close"


def test_mark_open_trade_at_close_is_a_no_op_when_nothing_is_open():
    w = mon.SymbolWatcher.__new__(mon.SymbolWatcher)
    w.open_trade = None
    w.latest_close = 105.0
    w.mark_open_trade_at_close("t_close")  # must not raise


# ---------------------------------------------------------------------------
# generate_eod_pnl_statement
# ---------------------------------------------------------------------------
def test_eod_statement_reports_no_trades_cleanly():
    watchers = {"X": _FakeWatcher(trades=[])}
    text = generate_eod_pnl_statement(watchers, datetime(2026, 8, 24, 15, 30))
    assert "No ENTRY signals fired today" in text


def test_eod_statement_totals_a_mix_of_closed_and_marked_trades():
    closed_winner = PaperTrade(symbol="A", entry_ts="t0", entry_price=100.0)
    closed_winner.close("t1", 110.0)  # +10% -> positive notional pnl

    closed_loser = PaperTrade(symbol="B", entry_ts="t0", entry_price=200.0)
    closed_loser.close("t1", 190.0)  # -5% -> negative notional pnl

    still_open = PaperTrade(symbol="C", entry_ts="t0", entry_price=50.0)

    watchers = {
        "A": _FakeWatcher(trades=[closed_winner]),
        "B": _FakeWatcher(trades=[closed_loser]),
        "C": _FakeWatcher(trades=[still_open], latest_close=55.0),
    }
    text = generate_eod_pnl_statement(watchers, datetime(2026, 8, 24, 15, 30))

    assert "Total trades: 3" in text
    assert "still open at close: 1" in text
    assert still_open.status == "MARKED_AT_CLOSE"  # confirms the generator actually called mark_open_trade_at_close
    expected_total = closed_winner.notional_pnl + closed_loser.notional_pnl + still_open.notional_pnl
    assert f"Rs.{expected_total:,.2f}" in text


def test_eod_statement_win_rate_only_counts_closed_trades_not_marked_ones():
    winner = PaperTrade(symbol="A", entry_ts="t0", entry_price=100.0)
    winner.close("t1", 120.0)
    loser = PaperTrade(symbol="B", entry_ts="t0", entry_price=100.0)
    loser.close("t1", 90.0)
    still_open_winner = PaperTrade(symbol="C", entry_ts="t0", entry_price=100.0)  # would also be a "winner" if marked

    watchers = {
        "A": _FakeWatcher(trades=[winner]),
        "B": _FakeWatcher(trades=[loser]),
        "C": _FakeWatcher(trades=[still_open_winner], latest_close=150.0),
    }
    text = generate_eod_pnl_statement(watchers, datetime(2026, 8, 24, 15, 30))
    # 1 of 2 CLOSED trades won -> 50.0%, NOT 2 of 3 (66.7%) even though the
    # marked-at-close trade would also have been profitable. Gross moves
    # here (+20%/-10%) are large enough that real costs don't flip either
    # trade's sign, so net win rate is also 50.0% - both lines present.
    assert "Win rate (closed trades only, gross): 50.0%" in text
    assert "Win rate (closed trades only, net of costs): 50.0%" in text
