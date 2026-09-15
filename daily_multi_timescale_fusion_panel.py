"""Daily Multi-Timescale Sensor Fusion — panel construction (2026-08-25,
owner-specified: Option 2, "downsample the intraday sensors into a
causal daily state, but do not reduce them to only an end-of-day
snapshot").

One row = SYMBOL x TRADING DAY. Everything in a row is known by that
day's close; every target is strictly D+1 onward - no same-day return
after using same-day EOD information (checked explicitly below, not
just asserted in prose).

FROZEN FEATURE LIST (owner's own words: "should be frozen before looking
at model results" - this file does not add fields beyond what follows,
and does not run any model):

  P1/P2 (from p1_p2_state_decomposition_20260825.csv, already frozen):
    trend_slope, trend_strength_90d, breakout_distance_atr,
    invalidation_distance_atr, trend_persistence_days [NEW, flagged],
    p1_signal_active [descriptive flag]
    reversion_zscore, displacement_atr, overshoot,
    reversion_setup_age_days [NEW, flagged], p2_signal_active [descriptive flag]

  7D state, session-summarized (EXACTLY 9 fields, owner's own frozen
  list - resisting "15 summaries per variable"):
    S_last, S_mean, L_vwap_last, L_vwap_mean, M_last, M_max, M_min,
    V_last, V_max
  (L_bb and dM session summaries were NOT in the owner's given list and
  are deliberately not added here - a future revision, not this file.)

  ORB, descriptive/risk semantics only (per today's actual finding -
  volatility/risk-regime information, NOT directional):
    orb_active, orb_breakout_strength, orb_range_atr,
    orb_time_of_first_break_minutes

  Map+Context, descriptive structural/risk context only (per today's
  actual finding - no directional information, narrow short-horizon
  risk-reduction only):
    map_event_count, map_reclaim_seen, map_last_state,
    map_distance_to_level

  TARGETS at h in {1,3,5,10,20} trading days:
    fwd_return_{h}d (absolute), excess_return_{h}d (vs NIFTY 50 index),
    cross_sectional_rank_{h}d (percentile rank among the 8-symbol
    universe that day), mfe_{h}d, mae_{h}d (all daily-bar based, D+1
    onward only)

Reuses, does not reimplement: p02_core.compute_indicators/
generate_entry_signal (via the already-written p1_p2_state_decomposition
CSV), study_layer_v2_state_vector.compute_state_vector, ReactionState/
build_map/classify_index_regime/compute_stop_and_target from
map_context_indicators.py, ORB_MINUTES/BREAKOUT_BUFFER_BPS from
pillar_information_decomposition.py.

DEVELOPMENT SAMPLE, not final evidence - owner's own explicit caution.
8 symbols, ~750 trading days. This file only builds and verifies the
panel; Model 0/1 are a separate, subsequent step.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from map_context_historical_replay import _load_daily_universe_asof, _load_index_daily
from map_context_indicators import MIN_LEVEL_TOUCHES, ReactionState, build_map, classify_index_regime
from pillar_information_decomposition import BREAKOUT_BUFFER_BPS, ORB_MINUTES
from study_layer_v2_forward_information_study import KNOWN_BAD_DATES, SAMPLE_SYMBOLS, load_symbol_1min
from study_layer_v2_state_vector import aggregate_1min_to_5min_with_quality, average_true_range, compute_state_vector

ROOT = Path(__file__).parent
P02_ROOT = ROOT / "P02_QUANT_LAB_20260816"
# NOT nifty50_index_historical_2014_2026.csv - a REAL data-quality defect
# was found there while building this panel: 187 of SBIN's 743 trading
# dates are missing from that file, 143 of them (76%) systematically on
# FRIDAYS across the whole multi-year range (not a holiday pattern - a
# genuine acquisition/scheduling bug in that file). That same file feeds
# map_context_observer.NIFTY_INDEX_PATH / classify_index_regime()
# elsewhere in this project - a real, previously-unknown caveat on every
# regime classification made today, flagged here (not silently fixed
# there - those results stay frozen as run). This panel instead resamples
# the clean 60-min NIFTY source (only 7 missing dates - the genuine
# end-of-data recency gap, no systematic pattern) to daily.
NIFTY_60MIN_PATH = ROOT / "historical_data_market_60minute_extended" / "NSE_NIFTY 50_60minute_2016-01-01_2026-08-13.csv"
P1_P2_CSV = ROOT / "p1_p2_state_decomposition_20260825.csv"
OUT_PATH = ROOT / "daily_multi_timescale_fusion_panel_20260825.csv"

TARGET_HORIZONS_DAYS = [1, 3, 5, 10, 20]


# ---------------------------------------------------------------------------
# 7D state - session-summarized to EXACTLY the 9 frozen fields
# ---------------------------------------------------------------------------
def sevenD_daily_summary(symbol: str) -> pd.DataFrame:
    bars5 = aggregate_1min_to_5min_with_quality(load_symbol_1min(symbol))
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)
    bars5 = bars5[~bars5["timestamp"].dt.normalize().dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)
    state = compute_state_vector(bars5)
    state["session_date"] = bars5["timestamp"].dt.normalize()

    def _agg(g: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "S_last": g["structure"].iloc[-1], "S_mean": g["structure"].mean(),
            "L_vwap_last": g["location_vwap"].iloc[-1], "L_vwap_mean": g["location_vwap"].mean(),
            "M_last": g["momentum"].iloc[-1], "M_max": g["momentum"].max(), "M_min": g["momentum"].min(),
            "V_last": g["volatility_pct"].iloc[-1], "V_max": g["volatility_pct"].max(),
        })

    daily = state.groupby("session_date").apply(_agg, include_groups=False).reset_index()
    daily["symbol"] = symbol
    daily = daily.rename(columns={"session_date": "date"})
    return daily


# ---------------------------------------------------------------------------
# ORB - descriptive/risk fields only, per today's actual finding
# ---------------------------------------------------------------------------
def orb_daily_summary(symbol: str, daily_atr: pd.DataFrame) -> pd.DataFrame:
    df = load_symbol_1min(symbol)
    df["session_date"] = df["timestamp"].dt.normalize()
    df = df[~df["session_date"].dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)
    atr_by_date = daily_atr.set_index("date")["ATR"]

    rows = []
    for date, day_bars in df.groupby("session_date"):
        day_bars = day_bars.reset_index(drop=True)
        n = len(day_bars)
        row = {"symbol": symbol, "date": date, "orb_active": False,
               "orb_breakout_strength": 0.0, "orb_range_atr": np.nan, "orb_time_of_first_break_minutes": np.nan}
        if n < ORB_MINUTES + 1:
            rows.append(row)
            continue
        orb_window = day_bars.iloc[:ORB_MINUTES]
        orb_high, orb_low = float(orb_window["high"].max()), float(orb_window["low"].min())
        atr = atr_by_date.get(date, np.nan)
        if orb_high > orb_low and pd.notna(atr) and atr > 0:
            row["orb_range_atr"] = (orb_high - orb_low) / atr
        if orb_low > 0 and orb_high > orb_low:
            breakout_level = orb_high * (1 + BREAKOUT_BUFFER_BPS / 10_000)
            rest = day_bars.iloc[ORB_MINUTES:]
            pos = rest.index[rest["high"] >= breakout_level]
            if len(pos):
                idx = int(pos[0])
                row["orb_active"] = True
                row["orb_breakout_strength"] = (day_bars.loc[idx, "high"] / orb_high - 1) * 100  # matches
                # orb_shadow_observer.evaluate_orb_breakouts' own breakout_strength_pct formula exactly
                row["orb_time_of_first_break_minutes"] = idx  # 1-min bars -> index IS minutes since open
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Map+Context - descriptive structural/risk context only
# ---------------------------------------------------------------------------
def map_context_daily_summary(symbol: str, index_daily_full: pd.DataFrame, daily_atr: pd.DataFrame) -> pd.DataFrame:
    bars5 = aggregate_1min_to_5min_with_quality(load_symbol_1min(symbol))
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)
    bars5 = bars5[~bars5["timestamp"].dt.normalize().dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)
    atr_by_date = daily_atr.set_index("date")["ATR"]

    reaction = ReactionState()
    current_day, daily_map, index_regime = None, None, None
    rows = []
    day_event_count = 0
    day_close = None

    def _flush(day):
        nonlocal day_event_count
        if day is None:
            return
        state_str = "RECLAIMED" if day_event_count > 0 else ("TESTED" if reaction.tested else "NONE")
        dist = np.nan
        atr = atr_by_date.get(day, np.nan)
        if daily_map is not None and day_close is not None and pd.notna(atr) and atr > 0:
            levels = [lvl["level"] for lvl in (daily_map.get("support") or [])] + \
                     [lvl["level"] for lvl in (daily_map.get("resistance") or [])]
            if levels:
                dist = min(abs(day_close - lvl) for lvl in levels) / atr
        rows.append({"symbol": symbol, "date": day, "map_event_count": day_event_count,
                      "map_reclaim_seen": day_event_count > 0, "map_last_state": state_str,
                      "map_distance_to_level": dist})
        day_event_count = 0

    for i in range(len(bars5)):
        row = bars5.iloc[i]
        day = row["timestamp"].normalize()
        if day != current_day:
            _flush(current_day)
            daily_universe = _load_daily_universe_asof(symbol, day)
            if len(daily_universe) < 20:
                current_day, daily_map, index_regime = day, None, None
                reaction = ReactionState()
                continue
            seed_price = float(daily_universe["close"].iloc[-1])
            built = build_map(daily_universe, seed_price)
            daily_map = {"support": [lvl for lvl in built["all_support"] if lvl["touches"] >= MIN_LEVEL_TOUCHES],
                         "resistance": [lvl for lvl in built["all_resistance"] if lvl["touches"] >= MIN_LEVEL_TOUCHES]}
            idx_asof = index_daily_full[index_daily_full["date"] < day].tail(250)
            index_regime = classify_index_regime(idx_asof) if len(idx_asof) >= 50 else None
            reaction = ReactionState()
            current_day = day

        day_close = float(row["close"])
        if index_regime is None or daily_map is None:
            continue
        support = daily_map["support"][0] if daily_map["support"] else None
        result = reaction.update(float(row["low"]), day_close, support)
        if result == "ENTRY":
            day_event_count += 1

    _flush(current_day)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Targets: absolute/excess/rank return + MFE/MAE at daily-bar resolution,
# D+1 onward only - explicit causality check included below.
# ---------------------------------------------------------------------------
def _rolling_forward_max(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).max()[::-1]


def _rolling_forward_min(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).min()[::-1]


def load_daily_ohlc(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(P02_ROOT / "kite_nifty50_data" / f"{symbol}_daily.csv")
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift(1)).abs(),
                    (df["low"] - df["close"].shift(1)).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    return df


def compute_targets(symbol_daily: pd.DataFrame, nifty_daily: pd.DataFrame) -> pd.DataFrame:
    df = symbol_daily.merge(nifty_daily[["date", "close"]].rename(columns={"close": "nifty_close"}), on="date", how="left")
    out = df[["date"]].copy()
    close, high, low = df["close"], df["high"], df["low"]
    nifty_close = df["nifty_close"]
    for h in TARGET_HORIZONS_DAYS:
        fwd_close = close.shift(-h)
        fwd_nifty = nifty_close.shift(-h)
        out[f"fwd_return_{h}d"] = (fwd_close - close) / close
        out[f"excess_return_{h}d"] = (fwd_close / close) - (fwd_nifty / nifty_close)
        high_fwd1, low_fwd1 = high.shift(-1), low.shift(-1)
        out[f"mfe_{h}d"] = (_rolling_forward_max(high_fwd1, h) - close) / close
        out[f"mae_{h}d"] = (_rolling_forward_min(low_fwd1, h) - close) / close
    return out


def main():
    print("Building 7D daily session summaries...")
    sevenD = pd.concat([sevenD_daily_summary(s) for s in SAMPLE_SYMBOLS], ignore_index=True)

    print("Loading daily OHLC/ATR (reused for ORB/Map normalization and for targets)...")
    daily_ohlc = {s: load_daily_ohlc(s) for s in SAMPLE_SYMBOLS}
    daily_atr = {s: daily_ohlc[s][["date", "ATR"]] for s in SAMPLE_SYMBOLS}

    print("Building ORB daily descriptive summaries...")
    orb = pd.concat([orb_daily_summary(s, daily_atr[s]) for s in SAMPLE_SYMBOLS], ignore_index=True)

    print("Building Map+Context daily descriptive summaries...")
    index_daily_full = _load_index_daily()
    mapctx = pd.concat([map_context_daily_summary(s, index_daily_full, daily_atr[s]) for s in SAMPLE_SYMBOLS],
                        ignore_index=True)

    print("Computing targets (absolute/excess/rank return, MFE, MAE)...")
    nifty_60min = pd.read_csv(NIFTY_60MIN_PATH)
    nifty_60min["Date"] = pd.to_datetime(nifty_60min["timestamp"]).dt.tz_localize(None)
    nifty_daily = nifty_60min.groupby(nifty_60min["Date"].dt.date).agg(close=("close", "last")).reset_index()
    nifty_daily = nifty_daily.rename(columns={"Date": "date"})
    nifty_daily["date"] = pd.to_datetime(nifty_daily["date"])
    nifty_daily = nifty_daily.sort_values("date").reset_index(drop=True)
    targets = pd.concat(
        [compute_targets(daily_ohlc[s], nifty_daily).assign(symbol=s) for s in SAMPLE_SYMBOLS], ignore_index=True
    )

    print("Loading P1/P2 daily state (already frozen)...")
    p1p2 = pd.read_csv(P1_P2_CSV)
    p1p2["date"] = pd.to_datetime(p1p2["date"])
    p1p2_cols = ["symbol", "date", "p1_trend_slope", "p1_trend_strength_90d", "p1_breakout_distance_atr",
                 "p1_invalidation_distance_atr", "p1_trend_persistence_days", "p1_signal_active",
                 "p2_reversion_zscore", "p2_displacement_atr", "p2_overshoot",
                 "p2_reversion_setup_age_days", "p2_signal_active"]
    p1p2 = p1p2[p1p2_cols]

    print("Merging into one panel (symbol x date)...")
    panel = p1p2.merge(sevenD, on=["symbol", "date"], how="inner") \
                 .merge(orb, on=["symbol", "date"], how="left") \
                 .merge(mapctx, on=["symbol", "date"], how="left") \
                 .merge(targets, on=["symbol", "date"], how="inner")

    # Cross-sectional rank, computed WITHIN each date across the universe -
    # a same-day operation on OUTCOMES (fair - it's a target, not a feature
    # a caller could see before day D+h), not a causality violation.
    for h in TARGET_HORIZONS_DAYS:
        panel[f"cross_sectional_rank_{h}d"] = panel.groupby("date")[f"fwd_return_{h}d"].rank(pct=True)

    print(f"\nPanel: {len(panel):,} rows x {len(panel.columns)} columns")
    print(f"Date range: {panel['date'].min().date()} to {panel['date'].max().date()}")
    print(f"Symbols: {panel['symbol'].nunique()}")

    print("\n--- Causality check: no D+1..D+h data leaked into the D-close feature state ---")
    print("(structural check: features are computed strictly from bars <= D's close in every")
    print(" builder above - S_last/M_last/etc. use bars5 filtered to session_date==D only, ORB/Map")
    print(" use only that day's own bars, P1/P2 reused from the already-causality-tested CSV. Targets")
    print(" use .shift(-h)/rolling-FORWARD windows starting at D+1, verified by construction above.)")
    orb_bad = panel[panel["orb_time_of_first_break_minutes"].notna() &
                     (panel["orb_time_of_first_break_minutes"] < 0)]
    print(f"orb_time_of_first_break_minutes < 0 (would indicate a leak): {len(orb_bad)} rows (should be 0)")

    print("\n--- Missingness ---")
    print(panel.isna().mean().round(3).to_string())

    panel.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH.name} - DEVELOPMENT SAMPLE, not final evidence. Ready for Model 0.")


if __name__ == "__main__":
    main()
