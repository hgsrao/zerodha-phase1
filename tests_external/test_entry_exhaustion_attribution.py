import pandas as pd

from scripts.attribute_external_entry_exhaustion import build_attribution, entry_features


def _bars():
    closes = list(range(100, 131))
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01 09:15", periods=len(closes), freq="min", tz="Asia/Kolkata"),
        "open": closes, "high": [value + 1 for value in closes], "low": [value - 1 for value in closes],
        "close": closes, "volume": [100] * 30 + [500],
    })


def test_entry_features_are_direction_aware_and_causal():
    bars = _bars()
    at_decision = bars.iloc[-1]["timestamp"]
    buy = entry_features(bars, at_decision, "BUY")
    sell = entry_features(bars, at_decision, "SELL")
    assert buy["directional_roc_percent"] > 0.0
    assert sell["directional_roc_percent"] < 0.0
    assert buy["volume_ratio"] == 5.0


def test_attribution_requires_path_aware_trade_telemetry():
    bars = _bars()
    timestamp = str(bars.iloc[-1]["timestamp"])
    report = {
        "controller_telemetry": [{"event_type": "ENTRY_CONFIDENCE_THROTTLE", "candidate_id": "c1", "timestamp": timestamp}],
        "trades": [{"trade_id": "t1", "candidate_id": "c1", "side": "BUY", "net_pnl": -10.0,
                    "mfe_r": 0.0, "terminal_bar_excursion": "intrabar_order_unknown"}],
    }
    result = build_attribution([report], bars)
    assert result["all_trades"]["immediate_rejections"] == 1
    assert result["shadow_only"] is True
