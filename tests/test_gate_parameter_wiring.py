from datetime import datetime, timedelta

from gates_framework import (
    EntryDecisionEngine,
    EntrySignal,
    Gate07StaleData,
    Gate10DrawdownDerating,
    Gate14OrderTimeout,
    Gate15OrderReconciliation,
    SafetyGateConfig,
    SystemState,
)


def test_gate_07_uses_configured_market_data_age():
    gate = Gate07StaleData(SafetyGateConfig(max_market_data_age_seconds=7))
    assert gate.evaluate(SystemState(market_data_age_seconds=7)).passed
    assert not gate.evaluate(SystemState(market_data_age_seconds=8)).passed


def test_gate_10_uses_its_own_threshold_and_multiplier():
    gate = Gate10DrawdownDerating(SafetyGateConfig(
        drawdown_derate_threshold=0.10, drawdown_derate_multiplier=0.50,
        lambda_derate_multiplier=0.90,
    ))
    decision, size = gate.evaluate(SystemState(current_dd_percent=0.11), 10)
    assert decision.passed
    assert size == 5


def test_timeout_and_reconciliation_use_canonical_tolerances():
    timeout = Gate14OrderTimeout(SafetyGateConfig(order_timeout_seconds=3))
    assert timeout.evaluate(3).passed
    assert not timeout.evaluate(3.01).passed
    reconciliation = Gate15OrderReconciliation(SafetyGateConfig(max_reconciliation_qty_diff=2))
    assert reconciliation.evaluate(10, 8).passed
    assert not reconciliation.evaluate(10, 7).passed


def test_duplicate_order_is_detected_from_event_time():
    cfg = SafetyGateConfig(order_dedup_window_seconds=5, min_signal_confidence=0.5)
    engine = EntryDecisionEngine(cfg)
    state = SystemState(portfolio_value=1_000_000)
    signal = EntrySignal("INFY", 100.0, 99.0, 102.0, 0.8, 2, 200.0, 2.0)
    now = datetime(2026, 1, 2, 10, 0)

    kwargs = dict(
        state=state, signal=signal, proposed_quantity=2, target_price=100.0,
        fill_price=100.0, expected_qty=2, actual_qty=2, symbol="INFY",
        proposed_notional=200.0,
    )
    assert engine.evaluate(current_time=now, **kwargs)["passed"]
    duplicate = engine.evaluate(current_time=now + timedelta(seconds=4), **kwargs)
    assert not duplicate["passed"]
    assert duplicate["gate"] == "Gate13OrderDuplication"
    assert engine.evaluate(current_time=now + timedelta(seconds=6), **kwargs)["passed"]


def test_entry_engine_receives_real_elapsed_time():
    engine = EntryDecisionEngine(SafetyGateConfig(order_timeout_seconds=2))
    state = SystemState(portfolio_value=1_000_000)
    signal = EntrySignal("INFY", 100.0, 99.0, 102.0, 0.8, 2, 200.0, 2.0)
    result = engine.evaluate(
        state, signal=signal, current_time=datetime(2026, 1, 2, 10, 0),
        proposed_quantity=2, target_price=100.0, fill_price=100.0,
        expected_qty=2, actual_qty=2, symbol="INFY", proposed_notional=200.0,
        order_elapsed_seconds=3,
    )
    assert not result["passed"]
    assert result["gate"] == "Gate14OrderTimeout"


def test_reconciliation_and_slippage_are_post_fill_checks():
    engine = EntryDecisionEngine(SafetyGateConfig(
        max_reconciliation_qty_diff=0, slippage_tolerance_percent=0.001,
    ))
    assert engine.evaluate_post_fill(10, 10, 100.0, 100.1)["passed"]
    assert engine.evaluate_post_fill(10, 9, 100.0, 100.0)["gate"] == "Gate15OrderReconciliation"
    assert engine.evaluate_post_fill(10, 10, 100.0, 100.11)["gate"] == "Gate16Slippage"
