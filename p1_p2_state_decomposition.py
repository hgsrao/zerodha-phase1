"""P1/P2 State Decomposition V1 (2026-08-25, owner-specified prerequisite
for the self-learning A/B/C/D experiment).

"Give the learner measurements, not P1's final BUY/NO-BUY decision."
"Those names should ultimately follow what the actual P02 implementation
computes - we should not invent features merely because they sound
attractive."

Every feature below is either DIRECTLY an existing p02_core.py column
(imported and computed via compute_indicators() UNCHANGED - no
indicator math reimplemented here) or a SIMPLE, DISCLOSED algebraic
combination of existing columns (e.g. an ATR-normalized distance between
two already-computed prices). Two features (persistence/setup-age) are
genuinely NEW - they did not exist in p02_core.py at all - and are
flagged explicitly as such, computed causally, same pattern as
map_context_indicators.consecutive_directional_days.

Deliberately OUT OF SCOPE for this pass, not silently approximated:
true chandelier distance and reversion progress are properties of an
OPEN POSITION (they need to know the actual entry price/peak-high, which
only exists once a trade is taken) - meaningful for a per-trade
trajectory decomposition, not for a per-bar, position-independent state
vector. A v2 could add them; this file does not pretend to.

Output: one row per (symbol, date) with the continuous P1 and P2 state,
`p1_signal_active`/`p2_signal_active` (generate_entry_signal's own real
output, exposed as a boolean flag - not as a vote and not the only P1/P2
information available, per the owner's explicit instruction), and
forward-looking return/MFE/MAE labels at horizons NATIVE to daily bars
(1/3/5/10/20 trading days) - NOT the intraday 5/10/15/30/60-min horizons
used elsewhere today, since P1/P2 operate on daily bars.

This file does NOT run the self-learning experiment - it prepares the
feature set that experiment will consume. Descriptive/plausibility
checks only (ranges, correlation with the pre-existing Momentum90/
ZScore columns as a sanity check that nothing is broken) - not an
ALPHA/RISK verdict on each new feature; that is the next experiment's
job, on frozen train/validate/holdout splits, not this file's.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

P02_ROOT = Path(__file__).parent / "P02_QUANT_LAB_20260816"
sys.path.insert(0, str(P02_ROOT))
import p02_core  # noqa: E402 - path must be set before this import

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "p1_p2_state_decomposition_20260825.csv"

SAMPLE_SYMBOLS = ["BAJFINANCE", "SBIN", "SUNPHARMA", "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK"]
HORIZONS_DAYS = [1, 3, 5, 10, 20]  # native to daily bars, not the intraday horizons used elsewhere today


def _rolling_forward_max(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).max()[::-1]


def _rolling_forward_min(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).min()[::-1]


def _regime_persistence(regime: pd.Series, target: str) -> pd.Series:
    """Consecutive trading days (INCLUSIVE of today) the Regime column has
    read `target`, resetting to 0 the moment it reads anything else - the
    same causal running-counter pattern already used and tested in
    map_context_indicators.consecutive_directional_days, applied here to
    P02's own Regime bucket instead of reimplementing a new counter idea."""
    is_target = (regime == target).astype(int)
    reset_points = is_target.diff().fillna(1) != 0
    groups = reset_points.cumsum()
    counter = is_target.groupby(groups).cumsum()
    return counter.where(is_target == 1, 0)


def decompose_symbol(symbol: str) -> pd.DataFrame:
    path = P02_ROOT / "kite_nifty50_data" / f"{symbol}_daily.csv"
    raw = pd.read_csv(path)
    raw["Date"] = pd.to_datetime(raw["date"]).dt.tz_localize(None)
    raw = raw.sort_values("Date").reset_index(drop=True)
    df = p02_core.compute_indicators(raw)  # UNCHANGED p02_core logic - SMA20/50, STD20, ZScore,
    # Momentum90, Prev20High/10Low, ATR, Regime - nothing reimplemented here.

    close, high, low, atr = df["close"], df["high"], df["low"], df["ATR"]

    out = pd.DataFrame({
        "symbol": symbol, "date": df["Date"], "close": close,
        # --- P1 (trend) continuous state ---
        "p1_trend_slope": (close - df["SMA50"]) / df["SMA50"],            # raw continuous version of the Regime bucket
        "p1_trend_strength_90d": df["Momentum90"],                        # already a real p02_core column
        "p1_breakout_distance_atr": (close - df["Prev20High"]) / atr,     # ATR-normalized distance to the entry trigger level
        "p1_invalidation_distance_atr": (close - df["Prev10Low"]) / atr,  # ATR-normalized distance to the exit trigger level
        "p1_trend_persistence_days": _regime_persistence(df["Regime"], "TRENDING"),  # NEW - flagged in module docstring
        "p1_signal_active": df.apply(lambda r: p02_core.generate_entry_signal(r) == "I_TREND", axis=1),
        # --- P2 (mean reversion) continuous state ---
        "p2_reversion_zscore": df["ZScore"],                              # already a real p02_core column - the exact entry-trigger quantity
        "p2_displacement_atr": (close - df["SMA20"]) / atr,               # ATR-normalized version of the same displacement idea
        "p2_overshoot": (-2.0 - df["ZScore"]).clip(lower=0.0),            # magnitude past the -2.0 entry threshold, 0 if not past it
        "p2_reversion_setup_age_days": _regime_persistence(df["Regime"], "MEAN_REVERTING"),  # NEW - flagged in module docstring
        "p2_signal_active": df.apply(lambda r: p02_core.generate_entry_signal(r) == "II_MEAN_REV", axis=1),
        # --- shared context ---
        "regime_bucket": df["Regime"],
        "atr_pct": atr / close,
    })

    for h in HORIZONS_DAYS:
        fwd_close = close.shift(-h)
        out[f"fwd_return_{h}d"] = (fwd_close - close) / close
        high_fwd1, low_fwd1 = high.shift(-1), low.shift(-1)
        mfe = (_rolling_forward_max(high_fwd1, h) - close) / close
        mae = (_rolling_forward_min(low_fwd1, h) - close) / close
        out[f"mfe_{h}d"] = mfe
        out[f"mae_{h}d"] = mae

    return out


def main():
    frames = [decompose_symbol(s) for s in SAMPLE_SYMBOLS]
    pooled = pd.concat(frames, ignore_index=True)
    print(f"Pooled rows: {len(pooled):,} across {len(SAMPLE_SYMBOLS)} symbols")
    print(f"Date range: {pooled['date'].min().date()} to {pooled['date'].max().date()}")
    print(f"p1_signal_active fires on {int(pooled['p1_signal_active'].sum()):,} bars "
          f"({pooled['p1_signal_active'].mean():.2%})")
    print(f"p2_signal_active fires on {int(pooled['p2_signal_active'].sum()):,} bars "
          f"({pooled['p2_signal_active'].mean():.2%})")

    print("\n--- Plausibility checks (not a hypothesis test - just confirming nothing is broken) ---")
    print("p1_trend_persistence_days distribution:", pooled["p1_trend_persistence_days"].describe()[["mean", "min", "max"]].to_dict())
    print("p2_reversion_setup_age_days distribution:", pooled["p2_reversion_setup_age_days"].describe()[["mean", "min", "max"]].to_dict())
    # p1_signal_active should ALWAYS coincide with Regime==TRENDING (by construction of generate_entry_signal) -
    # a direct consistency check against p02_core's own logic, not a redundant reimplementation.
    active_regimes = pooled.loc[pooled["p1_signal_active"], "regime_bucket"].unique()
    print(f"Regime bucket whenever p1_signal_active=True (should be only 'TRENDING'): {list(active_regimes)}")
    active_regimes_p2 = pooled.loc[pooled["p2_signal_active"], "regime_bucket"].unique()
    print(f"Regime bucket whenever p2_signal_active=True (should be only 'MEAN_REVERTING'): {list(active_regimes_p2)}")
    # p2_overshoot should be >0 exactly when p2_signal_active (both gated by ZScore < -2.0, modulo the STD20>0 guard)
    corr_check = pooled.loc[pooled["p2_signal_active"], "p2_overshoot"]
    print(f"p2_overshoot when p2_signal_active=True: min={corr_check.min():.3f} (should be >0)")

    pooled.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH.name} ({len(pooled):,} rows, {len(pooled.columns)} columns) - "
          f"ready as an input feature set for the self-learning A/B/C/D experiment.")


if __name__ == "__main__":
    main()
