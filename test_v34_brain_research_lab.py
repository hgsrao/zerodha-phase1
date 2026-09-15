from datetime import datetime, timedelta

import pytest

from brain_research_lab import Candle, StrategyConfig, performance_metrics, run_backtest, validate_candles


def candles_with_signal(exit_bar="target"):
    start = datetime(2025, 1, 1, 9, 15)
    candles = []
    for i in range(10):
        price = 100 + i * 0.1
        candles.append(Candle(start + timedelta(minutes=5 * i), price, price + 0.2, price - 0.2, price, 100))
    candles.append(Candle(start + timedelta(minutes=50), 101, 103, 100.8, 102.5, 300))
    if exit_bar == "both":
        candles.append(Candle(start + timedelta(minutes=55), 102.6, 106, 99, 103, 100))
    elif exit_bar == "target":
        candles.append(Candle(start + timedelta(minutes=55), 102.6, 106, 102, 105, 100))
    else:
        candles.append(Candle(start + timedelta(minutes=55), 102.6, 103, 102, 102.4, 100))
    return candles


def config(**overrides):
    values = dict(slow_window=5, breakout_window=5, atr_window=3, volume_window=5,
                  volume_multiplier=1.2, atr_stop_multiple=1, reward_risk=1,
                  max_holding_bars=1, minimum_bars=10, cost_bps_per_side=0,
                  slippage_bps_per_side=0)
    values.update(overrides)
    return StrategyConfig(**values)


def test_signal_enters_only_at_next_bar_open():
    trades = run_backtest(candles_with_signal(), config())
    assert len(trades) == 1
    assert trades[0].signal_time < trades[0].entry_time
    assert trades[0].entry_price == pytest.approx(102.6)


def test_stop_wins_when_same_bar_touches_stop_and_target():
    trade = run_backtest(candles_with_signal("both"), config())[0]
    assert trade.exit_reason == "STOP"
    assert trade.net_pnl < 0


def test_costs_reduce_result():
    candles = candles_with_signal()
    free = run_backtest(candles, config())[0]
    costly = run_backtest(candles, config(cost_bps_per_side=10, slippage_bps_per_side=10))[0]
    assert costly.net_pnl < free.net_pnl


def test_time_exit_is_recorded():
    trade = run_backtest(candles_with_signal("time"), config())[0]
    assert trade.exit_reason == "END_OF_DATA"


def test_bad_ohlc_and_duplicate_time_are_rejected():
    candles = candles_with_signal()
    bad = list(candles)
    bad[0] = Candle(bad[0].timestamp, 100, 99, 98, 100, 100)
    with pytest.raises(ValueError, match="high"):
        validate_candles(bad)
    duplicate = list(candles)
    duplicate[1] = Candle(duplicate[0].timestamp, 100, 101, 99, 100, 100)
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_candles(duplicate)


def test_empty_metrics_are_explicit():
    metrics = performance_metrics([])
    assert metrics["trades"] == 0
    assert metrics["expectancy"] is None


def test_slope_filter_uses_only_prior_slow_means():
    start = datetime(2025, 1, 1, 9, 15)
    candles = []
    for i in range(10):
        price = 110 - i * 0.1
        candles.append(Candle(start + timedelta(minutes=5 * i), price,
                              price + 0.2, price - 0.2, price, 100))
    candles.append(Candle(start + timedelta(minutes=50), 109, 113, 108.8, 112, 300))
    candles.append(Candle(start + timedelta(minutes=55), 112, 116, 111, 115, 100))
    trades = run_backtest(candles, config(slow_slope_lookback=5))
    assert trades == []


def test_market_regime_requires_matching_supportive_completed_bar():
    candles = candles_with_signal()
    cfg = config(market_regime_window=3)
    with pytest.raises(ValueError, match="market candles"):
        run_backtest(candles, cfg)
    hostile_market = [Candle(c.timestamp, 100, 101, 98, 99, 100) for c in candles]
    assert run_backtest(candles, cfg, hostile_market) == []
