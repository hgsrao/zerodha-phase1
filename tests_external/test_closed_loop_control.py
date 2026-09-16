import pandas as pd

from revision2_external.closed_loop_control import ClosedLoopSupervisor, SymbolDynamicsProfiler, TradeReferencePath
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def test_one_symbol_one_signal_traverses_all_three_closed_loops():
    supervisor = ClosedLoopSupervisor()

    # One long SUNPHARMA signal: entry plan becomes a frozen trade path.
    snapshot = supervisor.entry_snapshot(
        symbol="SUNPHARMA", side="BUY", entry_price=100.0, stop_price=98.0,
        target_price=104.0, max_hold_bars=20,
    )
    assert snapshot["entry_quality"]["completed_outcomes_total"] == 0
    assert snapshot["reference_path"]["target_r"] == 2.0

    # Fast trade-path loop: a price below the reference lower bound is
    # explicitly identified as lagging, without changing a live stop.
    path = supervisor.observe_trade_path(snapshot, current_price=98.5, bars_held=10)
    assert path.behind_path
    assert path.error_r > 0.0
    assert -1.0 <= path.suggested_protection_r <= path.actual_r

    # Portfolio loop is one-way: nearing the hard limit never suggests a
    # multiplier above one and becomes a full freeze at the limit.
    portfolio = supervisor.observe_portfolio_risk(90_000.0, 100_000.0, 1.0)
    assert 0.0 <= portfolio["suggested_new_risk_derate"] <= 1.0
    assert portfolio["suggested_new_risk_derate"] < 1.0

    # Slow entry-quality feedback consumes only a completed trade. A later
    # snapshot can see this result; the original snapshot could not.
    profile = supervisor.record_outcome({
        "symbol": "SUNPHARMA", "side": "BUY", "net_pnl": -10.0,
        "trade_id": "trade-1", "reason": "stop",
    })
    assert profile["completed_outcomes_total"] == 1
    next_snapshot = supervisor.entry_snapshot(
        symbol="SUNPHARMA", side="BUY", entry_price=100.0, stop_price=98.0,
        target_price=104.0, max_hold_bars=20,
    )
    assert next_snapshot["entry_quality"]["completed_outcomes_total"] == 1
    assert 0.9 <= next_snapshot["entry_quality"]["suggested_entry_derate"] <= 1.0


def test_reference_path_is_frozen_and_does_not_use_future_outcome():
    supervisor = ClosedLoopSupervisor()
    snapshot = supervisor.entry_snapshot("INFY", "SELL", 100.0, 102.0, 96.0, 20)
    before = supervisor.observe_trade_path(snapshot, current_price=100.5, bars_held=5)
    supervisor.record_outcome({"symbol": "INFY", "side": "SELL", "net_pnl": 50.0})
    after = supervisor.observe_trade_path(snapshot, current_price=100.5, bars_held=5)
    assert before == after


def test_orchestrator_only_allows_explicit_closed_loop_modes():
    try:
        Revision2ExternalEngineOrchestrator(["SUNPHARMA"], closed_loop_mode="invalid")
    except ValueError as exc:
        assert "closed_loop_mode" in str(exc)
    else:
        raise AssertionError("invalid closed-loop mode was accepted")


def test_symbol_response_time_changes_path_speed_without_future_bars():
    fast = TradeReferencePath("FAST", "BUY", 100.0, 2.0, 1.5, 60, response_time_bars=5.0)
    slow = TradeReferencePath("SLOW", "BUY", 100.0, 2.0, 1.5, 60, response_time_bars=35.0)
    assert fast.expected_r(10) > slow.expected_r(10)

    closes = pd.DataFrame({"close": [100 + 0.1 * i + (-0.2 if i % 3 == 0 else 0.0) for i in range(80)]})
    profile = SymbolDynamicsProfiler().estimate(closes)
    assert profile["sample_bars"] == 60
    assert 5.0 <= profile["response_time_bars"] <= 45.0
    assert 0.0 <= profile["damping_ratio"] <= 1.0
    assert 0.5 <= profile["suggested_pid_gain_scale"] <= 1.5


def test_high_damping_slows_not_accelerates_response_time():
    directional = pd.DataFrame({"close": [100.0 + i for i in range(80)]})
    choppy = pd.DataFrame({"close": [100.0 + (1 if i % 2 else -1) + 0.02 * i for i in range(80)]})
    profiler = SymbolDynamicsProfiler()
    clean = profiler.estimate(directional)
    noisy = profiler.estimate(choppy)
    assert noisy["damping_ratio"] > clean["damping_ratio"]
    assert noisy["response_time_bars"] >= noisy["base_response_time_bars"]
