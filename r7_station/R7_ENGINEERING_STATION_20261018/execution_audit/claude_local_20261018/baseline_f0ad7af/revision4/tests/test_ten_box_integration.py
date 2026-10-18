from __future__ import annotations

import pandas as pd
from dataclasses import replace

from revision4.contracts import Bar, EffectiveConfig, ExitEvent, ExitReason, PortfolioSnapshot, Position
from revision4.ten_box_integration import TenBoxIntegration
from revision4.validate_48symbol_sealed import _determine_status


def _history() -> pd.DataFrame:
    closes = [100 + i for i in range(30)]
    return pd.DataFrame({
        "timestamp": [f"2024-08-01T09:{15 + i:02d}:00+00:00" for i in range(30)],
        "open": closes, "high": [x + 1 for x in closes],
        "low": [x - 1 for x in closes], "close": closes,
        "volume": [1_000 + i for i in range(30)],
    })


def _position() -> Position:
    return Position(
        symbol="ABC", direction=1, entry_price=100.0, entry_bar_index=0,
        entry_bar_timestamp="2024-08-01T09:15:00+00:00", quantity=10,
        stop_price=98.0, target_price=104.0, cost_paid=1.0, fill_id="fill-1",
    )


def _snapshot(position: Position) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp="2024-08-01T09:20:00+00:00", bar_index=5, cash=90_000,
        reserved_cash=0.0, positions={position.symbol: position}, pending_orders={},
        realized_pnl=0.0, unrealized_pnl=0.0, total_costs=1.0, daily_pnl=0.0,
        marked_equity=100_000.0, exposure=1_000.0, sector_exposure={},
    )


def test_data_chart_entry_and_grid_paths_are_real_and_audited():
    boxes = TenBoxIntegration(EffectiveConfig())
    history = _history()
    admitted, _ = boxes.admit_and_certify("ABC", history)
    assert admitted
    chart = boxes.chart_signal(history)
    approved, _ = boxes.validate_entry(1, "2024-08-01T10:00:00+00:00", chart)
    assert approved
    grid_ok, _ = boxes.grid_allows(1, {"ABC": Bar("2024-08-01T10:00:00+00:00", "ABC", 100, 102, 99, 101, 10_000)})
    assert grid_ok
    audit = boxes.audit.report()["calls"]
    assert audit["data_input"] == 1
    assert audit["chart_studies"] == 1
    assert audit["entry_validator"] == 1
    assert audit["grid_sync"] == 1


def test_data_and_entry_rejections_are_explicit():
    boxes = TenBoxIntegration(replace(EffectiveConfig(), symbols_to_trade=["ABC"]))
    admitted, reason = boxes.admit_and_certify("DENIED", _history())
    assert not admitted and "universe" in reason
    chart = boxes.chart_signal(_history())
    # 02:00 UTC is 07:30 IST, before the canonical NSE session open.
    approved, reason = boxes.validate_entry(1, "2024-08-01T02:00:00+00:00", chart)
    assert not approved and "window" in reason


def test_dedicated_risk_path_rejects_excess_loss():
    boxes = TenBoxIntegration(replace(EffectiveConfig(), max_loss_per_trade_rupees=10.0))
    approved, reason = boxes.approve_risk(100.0, 90.0, 2)
    assert not approved and "exceeds cap" in reason
    assert boxes.audit.report()["calls"]["risk_manager"] == 1


def test_exit_controller_and_performance_tracker_use_actual_exit_event():
    boxes = TenBoxIntegration(EffectiveConfig())
    position = _position()
    reason, price = boxes.decide_exit(
        _snapshot(position), position,
        Bar("2024-08-01T09:20:00+00:00", "ABC", 100, 105, 99, 104, 1_000),
        event_index=5, atr=1.0,
    )
    assert reason is ExitReason.TARGET_HIT and price == 104.0
    event = ExitEvent(
        exit_id="exit-1", symbol="ABC", timestamp_exit="2024-08-01T09:20:00+00:00",
        bar_index_exit=5, entry_price=100.0, exit_price=104.0, quantity=10,
        direction=1, bars_held=5, entry_cost_paid=1.0, exit_cost_paid=1.0,
        exit_reason=reason, pnl_realized=38.0, pnl_pct=3.8,
    )
    boxes.record_exit(event)
    assert boxes.performance_report() == {"closed_trades": 1, "daily_pnl": {"2024-08-01": 38.0}}
    assert boxes.audit.report()["calls"]["exit_controller"] == 1
    assert boxes.audit.report()["calls"]["performance_tracker"] == 1


def test_empty_reconciled_replay_is_no_execution_not_passed():
    assert _determine_status(exact=True, completed_trade_count=0, has_remediation=False) == "NO_EXECUTION"
    assert _determine_status(exact=True, completed_trade_count=1, has_remediation=False) == "PASSED"
