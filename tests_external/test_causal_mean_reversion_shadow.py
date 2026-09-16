import numpy as np
import pandas as pd

from scripts.run_causal_mean_reversion_shadow import causal_features


def _bars(size: int) -> pd.DataFrame:
    timestamps = pd.date_range("2023-09-01 09:15", periods=size, freq="min", tz="Asia/Kolkata")
    close = np.linspace(100.0, 110.0, size)
    return pd.DataFrame({"timestamp": timestamps, "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": np.arange(1, size + 1) * 100})


def test_features_for_a_prefix_do_not_change_when_future_rows_are_appended() -> None:
    prefix = _bars(160)
    extended = pd.concat([prefix, _bars(40).assign(timestamp=lambda d: d["timestamp"] + pd.Timedelta("1 day"))], ignore_index=True)
    left, right = causal_features(prefix), causal_features(extended).iloc[:len(prefix)]
    columns = ["atr_14", "rsi_14", "rsi_percentile_100", "volume_flow_ratio", "session_vwap", "session_vwap_z"]
    for column in columns:
        assert np.allclose(left[column].fillna(-999), right[column].fillna(-999), equal_nan=True)
