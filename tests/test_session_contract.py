import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import PASignal
from revision2.orchestrator import Revision2Orchestrator
from tests.test_revision2_causal_sensitivity import _synthetic_bars


def test_no_trade_crosses_a_trading_date_and_fill_timestamp_is_next_bar():
    report = Revision2Orchestrator("TEST").run(_synthetic_bars(), warmup=40)
    assert report["completed_trades"] > 0
    for trade in report["trades"]:
        assert pd.Timestamp(trade["entry_timestamp"]).date() == pd.Timestamp(trade["exit_timestamp"]).date()


def _open_long(orch, entry=100.0, stop=95.0, target=105.0):
    contract = orch.safety_contract.as_dict()
    fill = orch.broker.place_order("TEST", "BUY", 1, "MARKET", entry, contract, orch.registry, event_time="2026-01-02 10:00+05:30")
    orch._open_trade = {
        "side": "BUY", "entry_price": fill["filled_price"], "stop_price": stop,
        "target_price": target, "quantity": 1, "entry_bar_idx": 0,
        "entry_timestamp": "2026-01-02 10:00+05:30",
        "minimum_hold_bars": 2, "maximum_hold_bars": 60,
        "exit_confidence_threshold": 0.6,
    }


def _signal():
    return PASignal("TEST", "2026-01-02 10:01+05:30", 1, 0.8, 0.2, 0.01, 0.1, 0.1, 0.8, "green")


def test_stop_wins_when_stop_and_target_touch_same_bar():
    orch = Revision2Orchestrator("TEST")
    _open_long(orch)
    bar = pd.Series({"timestamp": "2026-01-02 10:01+05:30", "open": 100, "high": 106, "low": 94, "close": 101})
    orch._maybe_exit(0, pd.DataFrame([bar]), _signal(), {"exit_orders_submitted": 0, "fills": 0}, False)
    assert orch.completed_trades[-1]["reason"] == "stop"


def test_gap_through_stop_fills_at_open_not_stale_stop_price():
    orch = Revision2Orchestrator("TEST")
    _open_long(orch)
    bar = pd.Series({"timestamp": "2026-01-02 10:01+05:30", "open": 90, "high": 92, "low": 89, "close": 91})
    orch._maybe_exit(0, pd.DataFrame([bar]), _signal(), {"exit_orders_submitted": 0, "fills": 0}, False)
    trade = orch.completed_trades[-1]
    assert trade["reason"] == "stop_gap"
    assert trade["exit_price"] < 95.0


def test_last_available_session_bar_forces_mis_close():
    orch = Revision2Orchestrator("TEST")
    _open_long(orch)
    bar = pd.Series({"timestamp": "2026-01-02 15:29+05:30", "open": 100, "high": 101, "low": 99, "close": 100})
    orch._maybe_exit(0, pd.DataFrame([bar]), _signal(), {"exit_orders_submitted": 0, "fills": 0}, True)
    assert orch.completed_trades[-1]["reason"] == "mis_session_close"
