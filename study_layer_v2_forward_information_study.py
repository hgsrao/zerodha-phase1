"""State-vector forward-information study (2026-08-25, owner-specified
next test). Answers ONE question, and only one: does x_t = [S, L_vwap,
L_bb, M, dM, V] contain measurable information about what happens next?
NOT "can we make it profitable" - no thresholds are optimized here, no
trading rule is proposed. Response surfaces and conditional means only.

Scope (explicit, not hidden): 8 liquid symbols (BAJFINANCE, SBIN,
SUNPHARMA, RELIANCE, INFY, TCS, HDFCBANK, ICICIBANK - the same sample
used in today's earlier Ichimoku empirical check), real V10-C 1-min
archive resampled to 5-min. Regime (R) is DELIBERATELY OMITTED from this
first pass - no continuous intraday NIFTY 5-min series has been
confirmed available yet; forcing a low-quality proxy in to complete the
7-dimension list would be worse than an honest omission, noted here
rather than silently worked around.

GAP-AWARE ELIGIBILITY (per today's earlier finding - 388 real gaps in
the archive, 96 of them a systematic 90-min hole on 2024-03-02 and
2024-05-18 across all 48 symbols): a bar is ELIGIBLE for this study only
if (a) every state dimension's own lookback window is CLEAN
(data_quality_status / vwap_session_contamination_status, using each
dimension's real lookback horizon - not one global flag), AND (b) the
entire forward labeling window is bar_complete AND stays within the SAME
trading session (no overnight/gap contamination of the label itself).
No repair anywhere - ineligible observations are excluded, never
patched.

STATISTICAL CAVEAT, stated explicitly: pooling 8 symbols' bars together
does NOT make the resulting N an effectively-independent sample count -
observations are autocorrelated within a symbol's own time series and
cross-correlated across symbols on the same trading day (market-wide
moves). N is reported as "bar-observations", not "independent trials" -
a real limitation of this first pass, not corrected for here.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from study_layer_v2_state_vector import (
    DEFAULT_LOOKBACK_BARS, aggregate_1min_to_5min_with_quality, average_true_range, compute_state_vector,
    data_quality_status, vwap_session_contamination_status,
)

ROOT = Path(__file__).parent
ARCHIVE_DIR = ROOT / "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825" / "DATA_1MIN_48_20230703_20260824"
SAMPLE_SYMBOLS = ["BAJFINANCE", "SBIN", "SUNPHARMA", "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK"]
HORIZONS = [1, 2, 3, 6, 12]  # 5,10,15,30,60 minutes on 5-min bars

# The two known-contaminated dates (2026-08-25 finding) - excluded outright,
# on top of (not instead of) the per-bar eligibility checks below.
KNOWN_BAD_DATES = {pd.Timestamp("2024-03-02").date(), pd.Timestamp("2024-05-18").date()}


def _rolling_forward_max(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).max()[::-1]


def _rolling_forward_min(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).min()[::-1]


def load_symbol_1min(symbol: str) -> pd.DataFrame:
    path = ARCHIVE_DIR / f"NSE_{symbol}_minute_2023-07-03_2026-08-24.csv"
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    return df.sort_values("timestamp").reset_index(drop=True)


def build_symbol_frame(symbol: str) -> pd.DataFrame:
    """One symbol's full pipeline: 1-min -> quality-aware 5-min bars ->
    state vector -> per-dimension quality -> forward labels -> combined
    eligibility. Returns one row per 5-min bar with everything needed
    for the bucket analyses below."""
    df_1min = load_symbol_1min(symbol)
    bars5 = aggregate_1min_to_5min_with_quality(df_1min)
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)

    state = compute_state_vector(bars5)
    atr = average_true_range(bars5)
    session_id = bars5["timestamp"].dt.normalize()

    bar_complete = bars5["bar_complete"]
    structure_q = data_quality_status(bar_complete, DEFAULT_LOOKBACK_BARS["structure"])
    bb_q = data_quality_status(bar_complete, DEFAULT_LOOKBACK_BARS["location_bb"])
    momentum_q = data_quality_status(bar_complete, DEFAULT_LOOKBACK_BARS["momentum"])
    vol_q = data_quality_status(bar_complete, DEFAULT_LOOKBACK_BARS["volatility_pct"])
    vwap_q = vwap_session_contamination_status(bar_complete, session_id)

    state_clean = (
        (structure_q == "CLEAN") & (bb_q == "CLEAN") & (momentum_q == "CLEAN")
        & (vol_q == "CLEAN") & (vwap_q == "CLEAN")
    )
    known_bad_date = session_id.dt.date.isin(KNOWN_BAD_DATES)

    out = pd.DataFrame({
        "symbol": symbol,
        "timestamp": bars5["timestamp"],
        "close": bars5["close"],
        "next_open": bars5["open"].shift(-1),
        "structure": state["structure"],
        "location_vwap": state["location_vwap"],
        "location_bb": state["location_bb"],
        "momentum": state["momentum"],
        "momentum_delta": state["momentum_delta"],
        "volatility_pct": state["volatility_pct"],
        "state_clean": state_clean & ~known_bad_date,
        "bar_complete": bar_complete,
        "session_id": session_id,
    })

    for h in HORIZONS:
        fwd_close = bars5["close"].shift(-h)
        out[f"fwd_return_{h}"] = (fwd_close - bars5["close"]) / bars5["close"]

        # ADDITIVE (2026-08-25, cost-overlay prerequisite) - a realistic
        # execution-model return: entry at the NEXT bar's OPEN (signal
        # observed at completed bar t, filled at t+1's open - matching
        # this project's established next-bar-open convention elsewhere,
        # e.g. run_chart_studies_live_monitor.py), exit at bar t+h's
        # close. Purely additive - does not alter fwd_return_{h} above,
        # so nothing already reported changes.
        exit_close_from_next_open = bars5["close"].shift(-h)
        out[f"fwd_return_open_{h}"] = (exit_close_from_next_open - out["next_open"]) / out["next_open"]

        high_fwd1, low_fwd1 = bars5["high"].shift(-1), bars5["low"].shift(-1)
        mfe_high = _rolling_forward_max(high_fwd1, h)
        mae_low = _rolling_forward_min(low_fwd1, h)
        out[f"mfe_{h}"] = (mfe_high - bars5["close"]) / bars5["close"]
        out[f"mae_{h}"] = (mae_low - bars5["close"]) / bars5["close"]

        # label eligibility: every bar in (t, t+h] must be bar_complete,
        # AND bar t+h must be in the SAME session as bar t (no overnight
        # contamination of the label itself).
        incomplete_ahead = pd.Series(False, index=bars5.index)
        for k in range(1, h + 1):
            incomplete_ahead = incomplete_ahead | (~bar_complete).shift(-k).fillna(True)
        same_session_h = (session_id.shift(-h) == session_id)
        out[f"label_eligible_{h}"] = (~incomplete_ahead) & same_session_h

    return out


def main():
    print("Building per-symbol frames (real 1-min V10-C archive, resampled + state vector + quality)...")
    frames = [build_symbol_frame(s) for s in SAMPLE_SYMBOLS]
    pooled = pd.concat(frames, ignore_index=True)
    print(f"Pooled bar-observations across {len(SAMPLE_SYMBOLS)} symbols: {len(pooled):,}")
    print(f"state_clean fraction: {pooled['state_clean'].mean():.1%}")

    # ------------------------------------------------------------------
    # 1. Structure buckets vs forward return / MFE / MAE
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("1. STRUCTURE (S) buckets vs forward outcomes")
    print("=" * 100)
    bucket_edges = [-1.01, -0.6, -0.2, 0.2, 0.6, 1.01]
    bucket_labels = ["[-1.0,-0.6)", "[-0.6,-0.2)", "[-0.2,+0.2)", "[+0.2,+0.6)", "[+0.6,+1.0]"]
    pooled["structure_bucket"] = pd.cut(pooled["structure"], bins=bucket_edges, labels=bucket_labels)

    for h in HORIZONS:
        eligible = pooled[pooled["state_clean"] & pooled[f"label_eligible_{h}"] & pooled["structure_bucket"].notna()]
        table = eligible.groupby("structure_bucket", observed=True).agg(
            mean_fwd_return=(f"fwd_return_{h}", "mean"),
            mean_mfe=(f"mfe_{h}", "mean"),
            mean_mae=(f"mae_{h}", "mean"),
            n=(f"fwd_return_{h}", "count"),
        )
        print(f"\n--- horizon +{h} bars (~{h*5} min) ---")
        print(table.to_string(float_format=lambda x: f"{x:+.4%}" if abs(x) < 1 else str(x)))

    # ------------------------------------------------------------------
    # 2. Momentum level vs momentum DELTA - the strengthening vs
    #    decelerating question that originally motivated the D-term idea
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("2. MOMENTUM (M) level x DELTA (dM) - strengthening vs decelerating, M>0 only")
    print("=" * 100)
    pos_m = pooled[(pooled["momentum"] > 0) & pooled["state_clean"]]
    for h in HORIZONS:
        eligible = pos_m[pos_m[f"label_eligible_{h}"]]
        accelerating = eligible[eligible["momentum_delta"] > 0]
        decelerating = eligible[eligible["momentum_delta"] <= 0]
        print(f"\n--- horizon +{h} bars ---")
        print(f"  M>0, dM>0 (accelerating): mean fwd_return={accelerating[f'fwd_return_{h}'].mean():+.4%}  "
              f"n={len(accelerating):,}")
        print(f"  M>0, dM<=0 (decelerating): mean fwd_return={decelerating[f'fwd_return_{h}'].mean():+.4%}  "
              f"n={len(decelerating):,}")

    # ------------------------------------------------------------------
    # 3. VWAP vs Bollinger conditional redundancy test
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("3. VWAP vs BOLLINGER - does L_bb add anything once L_vwap is known?")
    print("=" * 100)
    vwap_buckets = pd.cut(pooled["location_vwap"], bins=bucket_edges, labels=bucket_labels)
    pooled["vwap_bucket"] = vwap_buckets
    pooled["bb_sign"] = np.where(pooled["location_bb"] > 0, "L_bb>0", "L_bb<=0")

    h = 6  # 30-min horizon for this comparison - long enough to average out noise, short enough to stay intraday
    eligible = pooled[pooled["state_clean"] & pooled[f"label_eligible_{h}"] & pooled["vwap_bucket"].notna()]
    print(f"\n--- horizon +{h} bars (~30 min) ---")
    print("\nE[fwd_return | L_vwap bucket] only:")
    print(eligible.groupby("vwap_bucket", observed=True)[f"fwd_return_{h}"].agg(["mean", "count"])
          .rename(columns={"mean": "E[R|L_vwap]", "count": "n"}).to_string(float_format=lambda x: f"{x:+.4%}"))

    print("\nE[fwd_return | L_vwap bucket, L_bb sign]:")
    grouped = eligible.groupby(["vwap_bucket", "bb_sign"], observed=True)[f"fwd_return_{h}"].agg(["mean", "count"])
    print(grouped.rename(columns={"mean": "E[R|L_vwap,L_bb]", "count": "n"}).to_string(float_format=lambda x: f"{x:+.4%}"))


if __name__ == "__main__":
    main()
