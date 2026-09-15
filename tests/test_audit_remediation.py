import math
from datetime import datetime

import pandas as pd
import pytest

from gates_framework import EntryDecisionEngine, EntrySignal, SystemState
from market_data_loader import MarketDataLoader
from oos_calibration_engine import OOSCalibrationEngine


def test_market_data_loader_rejects_synthetic_fallback_when_disabled(tmp_path):
    loader = MarketDataLoader(data_dir=str(tmp_path), synthetic_if_missing=False)

    with pytest.raises(ValueError, match="missing real market data|synthetic"):
        loader.load_universe()


def test_oos_engine_computes_metrics_from_real_price_data():
    timestamps = pd.date_range("2024-01-01", periods=10, freq="min")
    close = [100.0, 101.0, 100.5, 102.0, 102.5, 103.2, 104.1, 103.8, 105.0, 106.4]
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "open": close,
        "high": [x * 1.01 for x in close],
        "low": [x * 0.99 for x in close],
        "close": close,
        "volume": [1000 + i * 10 for i in range(len(close))],
    })
    engine = OOSCalibrationEngine(iterations=1, data_dir="/tmp", synthetic_if_missing=False)

    result = engine.evaluate_oos_performance(best_score=0.12, frames={"INFY": frame})

    assert result.passed is False
    assert result.metrics.total_return > 0.0
    assert result.metrics.total_return != 0.18
    assert result.metrics.sharpe > 0.0
    assert result.metrics.sharpe != 1.4


def test_gate_engine_rejects_nan_state_and_equals_boundaries():
    state = SystemState(
        portfolio_value=float("nan"),
        current_dd_percent=0.25,
        current_lambda=0.15,
        daily_realized_loss=50000.0,
        open_positions_count=5,
        market_data_age_seconds=30,
        broker_connected=True,
        kill_switch_active=False,
    )
    signal = EntrySignal(
        symbol="INFY",
        entry_price=100.0,
        stop_loss_price=95.0,
        profit_target_price=110.0,
        confidence=0.55,
        suggested_quantity=1,
        position_notional=1000.0,
        risk_reward_ratio=1.5,
    )

    engine = EntryDecisionEngine()
    report = engine.evaluate(
        state=state,
        signal=signal,
        current_time=datetime(2024, 1, 2, 9, 30),
        proposed_quantity=1,
        target_price=100.0,
        fill_price=100.0,
        expected_qty=1,
        actual_qty=1,
        symbol="INFY",
        seen_recent=True,
    )

    assert report["passed"] is False
    assert "nan" in str(report["reason"]).lower() or "invalid" in str(report["reason"]).lower()
