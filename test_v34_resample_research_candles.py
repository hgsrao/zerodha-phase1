from datetime import datetime, timedelta

from brain_research_lab import Candle
from resample_research_candles import resample_hourly


def test_four_bars_form_one_hour_and_incomplete_group_is_dropped():
    start = datetime.fromisoformat("2025-01-02T09:15:00+05:30")
    candles = [Candle(start + timedelta(minutes=15 * i), 100 + i, 102 + i,
                      99 + i, 101 + i, 10 + i) for i in range(5)]
    result = resample_hourly(candles)
    assert len(result) == 1
    assert result[0].open == 100
    assert result[0].high == 105
    assert result[0].low == 99
    assert result[0].close == 104
    assert result[0].volume == 46


def test_missing_quarter_prevents_hour_construction():
    start = datetime.fromisoformat("2025-01-02T09:15:00+05:30")
    candles = [Candle(start + timedelta(minutes=15 * i), 100, 101, 99, 100, 10)
               for i in (0, 1, 3, 4)]
    assert resample_hourly(candles) == []
