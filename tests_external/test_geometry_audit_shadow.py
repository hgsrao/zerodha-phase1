import pandas as pd

from revision2_external.geometry_audit_shadow import evaluate_path, summarize_geometry


def _bars(values):
    return pd.DataFrame(values)


def test_target_and_stop_on_same_bar_is_excluded_as_intrabar_unknown():
    result = evaluate_path(
        entry_price=100.0, initial_risk=10.0, side="BUY", target_r=0.5, stop_r=1.0,
        future=_bars([{"open": 100.0, "high": 106.0, "low": 89.0, "close": 100.0}]),
    )
    assert result["outcome"] == "INTRABAR_ORDER_UNKNOWN"
    assert summarize_geometry([result])["usable"] == 0


def test_stop_gap_uses_adverse_open_and_timeout_uses_adverse_fill():
    gap = evaluate_path(
        entry_price=100.0, initial_risk=10.0, side="BUY", target_r=1.0, stop_r=1.0,
        future=_bars([{"open": 88.0, "high": 90.0, "low": 87.0, "close": 89.0}]),
    )
    assert gap["outcome"] == "STOP_FIRST"
    assert gap["exit_price"] < 88.0
    timeout = evaluate_path(
        entry_price=100.0, initial_risk=10.0, side="SELL", target_r=1.0, stop_r=1.0,
        future=_bars([{"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}]),
    )
    assert timeout["outcome"] == "TIMEOUT"
    assert timeout["net_pnl_per_share"] < 0.0
