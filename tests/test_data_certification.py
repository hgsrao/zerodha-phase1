import pandas as pd
import pytest

from revision2.data_certification import certify_bars


def bars():
    return pd.DataFrame([
        {"timestamp": "2026-01-02 09:15", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
        {"timestamp": "2026-01-02 09:16", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 20},
    ])


def test_exact_duplicates_are_removed_and_timezone_is_canonical():
    frame = pd.concat([bars(), bars().iloc[[0]]], ignore_index=True)
    result, audit = certify_bars(frame)
    assert len(result) == 2
    assert audit == {"input_rows": 3, "output_rows": 2, "exact_duplicates_removed": 1}
    assert str(result["timestamp"].dt.tz) == "Asia/Kolkata"
    assert result["timestamp"].is_monotonic_increasing


def test_conflicting_duplicate_timestamp_fails_closed():
    frame = pd.concat([bars(), bars().iloc[[0]].assign(close=100.5)], ignore_index=True)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        certify_bars(frame)


@pytest.mark.parametrize("column,value", [
    ("open", -1), ("high", 98), ("low", 103), ("close", float("nan")), ("volume", -1),
])
def test_invalid_ohlcv_fails_closed(column, value):
    frame = bars()
    frame.loc[0, column] = value
    with pytest.raises(ValueError):
        certify_bars(frame)
