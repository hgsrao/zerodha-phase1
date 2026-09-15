"""Tests for chart_studies_indicators.py. All synthetic data - no real
Kite/network calls anywhere, matching this project's established test
convention (see test_kite_request_governor.py's own module docstring)."""
from __future__ import annotations

import pandas as pd
import pytest

from chart_studies_indicators import (
    BULLISH, BEARISH, NEUTRAL, GREEN, AMBER, RED,
    ENTRY_THRESHOLD, EXIT_THRESHOLD, CONFIRMATION_BARS, HARD_STOP_PCT,
    ABSTAIN_SLIPPAGE_PCT, VOTING_STUDIES,
    anchored_vwap, bollinger_bands, classify_row, evaluate_fill, hard_stop_price, ichimoku,
    is_hard_stop_breached, project_state, session_vwap, stochastic_momentum_index, SymbolSignalState,
)


def _bars(n, start_price=100.0, step=1.0, start_ts="2026-08-20 09:15", freq="5min", volume=1000.0):
    ts = pd.date_range(start_ts, periods=n, freq=freq)
    closes = [start_price + i * step for i in range(n)]
    rows = []
    for i, (t, c) in enumerate(zip(ts, closes)):
        rows.append({
            "timestamp": t, "open": c - 0.2, "high": c + 0.5, "low": c - 0.5,
            "close": c, "volume": volume,
        })
    df = pd.DataFrame(rows)
    df["TradingDay"] = df["timestamp"].dt.normalize()
    return df


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------
def test_bollinger_basis_is_the_rolling_mean_of_close():
    df = _bars(25, start_price=100.0, step=0.0)  # flat series -> std 0
    out = bollinger_bands(df, period=20)
    last = out.iloc[-1]
    assert last["BB_Basis"] == pytest.approx(100.0)
    assert last["BB_Upper"] == pytest.approx(100.0)  # std=0 -> bands collapse to the basis
    assert last["BB_Lower"] == pytest.approx(100.0)


def test_bollinger_widens_with_real_dispersion():
    df = _bars(25, start_price=100.0, step=1.0)  # trending -> real std
    out = bollinger_bands(df, period=20)
    last = out.iloc[-1]
    assert last["BB_Upper"] > last["BB_Basis"] > last["BB_Lower"]


# ---------------------------------------------------------------------------
# SMI
# ---------------------------------------------------------------------------
def test_smi_is_positive_in_a_sustained_uptrend():
    df = _bars(40, start_price=100.0, step=1.0)
    out = stochastic_momentum_index(df)
    last = out.iloc[-1]
    assert last["SMI"] > 0  # close sits above the rolling high/low midpoint


def test_smi_is_negative_in_a_sustained_downtrend():
    df = _bars(40, start_price=200.0, step=-1.0)
    out = stochastic_momentum_index(df)
    last = out.iloc[-1]
    assert last["SMI"] < 0


def test_smi_flat_series_does_not_crash_on_zero_range():
    df = _bars(30, start_price=100.0, step=0.0)
    df["high"] = 100.0  # collapse high==low==close -> rng_ema2 == 0
    df["low"] = 100.0
    df["close"] = 100.0
    out = stochastic_momentum_index(df)
    assert out["SMI"].iloc[-1] is pd.NA or pd.isna(out["SMI"].iloc[-1])


# ---------------------------------------------------------------------------
# VWAP / Anchored VWAP
# ---------------------------------------------------------------------------
def test_session_vwap_resets_at_the_next_trading_day():
    day1 = _bars(3, start_price=100.0, step=0.0, start_ts="2026-08-20 09:15")
    day2 = _bars(3, start_price=200.0, step=0.0, start_ts="2026-08-21 09:15")
    df = pd.concat([day1, day2], ignore_index=True)
    out = session_vwap(df)
    # Day 2's VWAP must reflect ONLY day 2's prices (~200), not a blend
    # with day 1's ~100 prices - proves the groupby-by-day reset is real.
    assert out["VWAP"].iloc[-1] == pytest.approx(200.0, abs=1.0)


def test_anchored_vwap_does_not_reset_across_days():
    day1 = _bars(3, start_price=100.0, step=0.0, start_ts="2026-08-20 09:15")
    day2 = _bars(3, start_price=200.0, step=0.0, start_ts="2026-08-21 09:15")
    df = pd.concat([day1, day2], ignore_index=True)
    out = anchored_vwap(df, anchor_ts="2026-08-20 09:15")
    # Anchored VWAP blends BOTH days (unlike session VWAP above) - equal
    # volume each bar -> should land roughly at the simple average, ~150.
    assert out["AnchoredVWAP"].iloc[-1] == pytest.approx(150.0, abs=5.0)


def test_anchored_vwap_excludes_bars_before_the_anchor():
    df = _bars(6, start_price=100.0, step=0.0, start_ts="2026-08-20 09:15")
    # Anchor at the 4th bar onward with a step-change in price - bars
    # before the anchor must be fully excluded, not just down-weighted.
    df.loc[3:, "close"] = 500.0
    df.loc[3:, "high"] = 500.5
    df.loc[3:, "low"] = 499.5
    anchor_ts = df["timestamp"].iloc[3]
    out = anchored_vwap(df, anchor_ts=anchor_ts)
    assert out["AnchoredVWAP"].iloc[-1] == pytest.approx(500.0, abs=1.0)


def test_anchored_vwap_carries_forward_a_running_base():
    """The live monitor bootstraps running_pv/running_vol once at startup
    from a historical fetch, then only ever passes NEW bars in - this is
    what makes that possible without re-processing the whole history
    every poll cycle."""
    df = _bars(3, start_price=100.0, step=0.0, start_ts="2026-08-21 09:15")
    # A running base equivalent to "3 prior bars at price 300, volume 1000 each".
    out = anchored_vwap(df, anchor_ts="2026-08-01 00:00", running_pv=300.0 * 1000.0 * 3, running_vol=1000.0 * 3)
    # 6 bars total (3 carried-forward @300 + 3 new @100), equal volume -> simple average 200.
    assert out["AnchoredVWAP"].iloc[-1] == pytest.approx(200.0, abs=1.0)


# ---------------------------------------------------------------------------
# Ichimoku
# ---------------------------------------------------------------------------
def test_ichimoku_reads_bullish_in_a_strong_sustained_uptrend():
    df = _bars(60, start_price=100.0, step=2.0)
    out = ichimoku(df)
    last = out.iloc[-1]
    assert last["close"] > max(last["SenkouA"], last["SenkouB"])
    assert last["Tenkan"] > last["Kijun"]


def test_ichimoku_reads_bearish_in_a_strong_sustained_downtrend():
    df = _bars(60, start_price=500.0, step=-2.0)
    out = ichimoku(df)
    last = out.iloc[-1]
    assert last["close"] < min(last["SenkouA"], last["SenkouB"])
    assert last["Tenkan"] < last["Kijun"]


# ---------------------------------------------------------------------------
# classify_row / composite score
# ---------------------------------------------------------------------------
def test_classify_row_all_bullish_gives_score_plus_four_anchored_vwap_not_voted():
    """2026-08-24: anchored_vwap is still classified and returned in
    `reads` (BULLISH here, same as the other 4) but is excluded from
    VOTING_STUDIES - score is +4 (all 4 voting studies), not +5."""
    row = pd.Series({
        "close": 110.0,
        "SenkouA": 100.0, "SenkouB": 95.0, "Tenkan": 108.0, "Kijun": 102.0,
        "BB_Basis": 105.0,
        "SMI": 5.0, "SMI_Signal": 2.0,
        "VWAP": 108.0,
        "AnchoredVWAP": 100.0,
    })
    reads = classify_row(row)
    assert reads["score"] == 4
    assert all(v == BULLISH for k, v in reads.items() if k != "score")
    assert "anchored_vwap" not in VOTING_STUDIES


def test_classify_row_anchored_vwap_disagreement_does_not_move_the_score():
    """The whole point of excluding it from the vote: flipping ONLY
    anchored_vwap's read must not change the composite score at all."""
    bullish_row = pd.Series({
        "close": 110.0,
        "SenkouA": 100.0, "SenkouB": 95.0, "Tenkan": 108.0, "Kijun": 102.0,
        "BB_Basis": 105.0,
        "SMI": 5.0, "SMI_Signal": 2.0,
        "VWAP": 108.0,
        "AnchoredVWAP": 100.0,  # close (110) > this -> BULLISH
    })
    bearish_anchor_row = bullish_row.copy()
    bearish_anchor_row["AnchoredVWAP"] = 200.0  # close (110) < this -> BEARISH
    reads_a = classify_row(bullish_row)
    reads_b = classify_row(bearish_anchor_row)
    assert reads_a["anchored_vwap"] == BULLISH
    assert reads_b["anchored_vwap"] == BEARISH
    assert reads_a["score"] == reads_b["score"] == 4


def test_classify_row_missing_indicator_columns_are_neutral_not_a_crash():
    row = pd.Series({"close": 110.0})  # nothing else computed yet (warm-up period)
    reads = classify_row(row)
    assert reads["score"] == 0
    assert all(v == NEUTRAL for k, v in reads.items() if k != "score")


# ---------------------------------------------------------------------------
# SymbolSignalState — the confirmed-streak entry/exit state machine
# (2026-08-24: was single-bar edge-triggered before the "what can we make
# better" addendum - now requires CONFIRMATION_BARS consecutive closed bars)
# ---------------------------------------------------------------------------
def test_state_machine_requires_two_consecutive_bars_to_enter():
    assert CONFIRMATION_BARS == 2  # this test is written against that value
    st = SymbolSignalState()
    assert st.update("t1", ENTRY_THRESHOLD, {}) is None  # 1st confirming bar - not enough yet
    assert st.state == "FLAT"
    ev = st.update("t2", ENTRY_THRESHOLD, {})  # 2nd CONSECUTIVE confirming bar
    assert ev is not None and ev["direction"] == "ENTRY"
    assert st.state == "IN"


def test_state_machine_resets_the_streak_if_score_drops_before_confirmation():
    st = SymbolSignalState()
    assert st.update("t1", ENTRY_THRESHOLD, {}) is None  # 1st confirming bar
    assert st.update("t2", ENTRY_THRESHOLD - 1, {}) is None  # breaks the streak
    assert st.update("t3", ENTRY_THRESHOLD, {}) is None  # only 1 consecutive bar again
    ev = st.update("t4", ENTRY_THRESHOLD, {})  # now 2 consecutive
    assert ev is not None and ev["direction"] == "ENTRY"


def test_state_machine_staying_in_after_entry_does_not_refire():
    st = SymbolSignalState()
    st.update("t1", ENTRY_THRESHOLD, {})
    st.update("t2", ENTRY_THRESHOLD, {})  # ENTRY fires here
    assert st.state == "IN"
    assert st.update("t3", ENTRY_THRESHOLD + 1, {}) is None
    assert st.update("t4", ENTRY_THRESHOLD, {}) is None


def test_state_machine_requires_two_consecutive_bars_to_exit():
    st = SymbolSignalState()
    st.update("t1", ENTRY_THRESHOLD, {})
    st.update("t2", ENTRY_THRESHOLD, {})  # enters
    assert st.state == "IN"
    assert st.update("t3", EXIT_THRESHOLD, {}) is None  # 1st confirming bar
    assert st.state == "IN"
    ev = st.update("t4", EXIT_THRESHOLD, {})  # 2nd consecutive
    assert ev is not None and ev["direction"] == "EXIT"
    assert st.state == "FLAT"


def test_state_machine_never_fires_on_a_single_bar_even_from_flat_start():
    # 2026-08-24: unlike the old edge-triggered version, bar one alone
    # (even sitting at/above threshold) is never enough by itself now.
    st = SymbolSignalState()
    ev = st.update("t1", ENTRY_THRESHOLD, {})
    assert ev is None
    assert st.state == "FLAT"


def test_state_machine_records_every_event_in_order():
    st = SymbolSignalState()
    st.update("t1", 3, {}); st.update("t2", 3, {})   # ENTRY (2 confirming bars)
    st.update("t3", 0, {}); st.update("t4", 0, {})   # EXIT (2 confirming bars)
    st.update("t5", 3, {}); st.update("t6", 3, {})   # ENTRY again
    assert [e["direction"] for e in st.events] == ["ENTRY", "EXIT", "ENTRY"]


# ---------------------------------------------------------------------------
# Hard stop (2026-08-24 addendum) — pure, independent of the composite score
# ---------------------------------------------------------------------------
def test_hard_stop_price_is_below_entry_by_the_configured_pct():
    assert hard_stop_price(1000.0) == pytest.approx(1000.0 * (1 - HARD_STOP_PCT))


def test_hard_stop_not_breached_above_the_stop_price():
    stop = hard_stop_price(1000.0)
    assert not is_hard_stop_breached(1000.0, stop + 0.01)


def test_hard_stop_breached_at_or_below_the_stop_price():
    stop = hard_stop_price(1000.0)
    assert is_hard_stop_breached(1000.0, stop)
    assert is_hard_stop_breached(1000.0, stop - 0.01)


# ---------------------------------------------------------------------------
# project_state (GREEN/AMBER/RED, 2026-08-24 addendum)
# ---------------------------------------------------------------------------
def test_project_state_green_at_or_above_entry_threshold():
    assert project_state(ENTRY_THRESHOLD) == GREEN
    assert project_state(ENTRY_THRESHOLD + 1) == GREEN


def test_project_state_red_at_or_below_exit_threshold():
    assert project_state(EXIT_THRESHOLD) == RED
    assert project_state(EXIT_THRESHOLD - 3) == RED


def test_project_state_amber_strictly_between_the_two_thresholds():
    for score in range(EXIT_THRESHOLD + 1, ENTRY_THRESHOLD):
        assert project_state(score) == AMBER


# ---------------------------------------------------------------------------
# evaluate_fill (next-bar-open ABSTAIN slippage guard, 2026-08-24 addendum)
# ---------------------------------------------------------------------------
def test_evaluate_fill_fills_when_slippage_is_within_bounds():
    verdict, pct = evaluate_fill(decision_price=1000.0, fill_open_price=1002.0)  # 0.2%
    assert verdict == "FILL"
    assert pct == pytest.approx(0.002)


def test_evaluate_fill_abstains_when_slippage_exceeds_the_boundary():
    verdict, pct = evaluate_fill(decision_price=1000.0, fill_open_price=1005.0)  # 0.5% > 0.3%
    assert verdict == "ABSTAIN"
    assert pct == pytest.approx(0.005)


def test_evaluate_fill_boundary_is_inclusive_of_fill():
    fill_open = 1000.0 * (1.0 + ABSTAIN_SLIPPAGE_PCT)
    verdict, pct = evaluate_fill(decision_price=1000.0, fill_open_price=fill_open)
    assert verdict == "FILL"  # exactly AT the boundary still fills, only strictly beyond abstains


def test_evaluate_fill_is_symmetric_for_gap_down():
    verdict, pct = evaluate_fill(decision_price=1000.0, fill_open_price=990.0)  # -1.0%
    assert verdict == "ABSTAIN"
    assert pct == pytest.approx(0.01)
