import pandas as pd

from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def _studies() -> dict:
    return {
        "direction": -1, "confidence": 0.25,
        "votes": {"ichimoku": -1, "bollinger": -1, "stochastic": 1, "session_vwap": -1},
        "weights": {}, "hit_rates": {},
    }


def test_shadow_candidate_fills_next_bar_and_resolves_on_later_bar():
    history = pd.DataFrame({"low": [100.0] * 9 + [99.0], "high": [101.0] * 10, "close": [100.5] * 9 + [100.9], "volume": [100.0] * 9 + [200.0]})
    ledger = StudyEntryShadowLedger(max_hold_bars=5)
    ledger.observe("TEST", 10, "t10", history.iloc[-1], history, _studies(), 1.0)
    # The fill bar has a target-looking high, but cannot resolve because OHLC
    # ordering inside the fill bar is unknown.
    ledger.advance("TEST", 11, "t11", pd.Series({"open": 101.0, "high": 110.0, "low": 90.0, "close": 101.0}))
    assert not ledger.resolved
    ledger.advance("TEST", 12, "t12", pd.Series({"open": 101.0, "high": 105.0, "low": 100.0, "close": 104.0}))
    assert len(ledger.resolved) == 1
    outcome = ledger.resolved[0]
    assert outcome["entry_timestamp"] == "t11"
    assert outcome["exit_reason"] == "target"
    assert outcome["target_before_stop"] is True
