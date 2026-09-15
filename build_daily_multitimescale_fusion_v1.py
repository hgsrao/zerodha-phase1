from __future__ import annotations

from pathlib import Path
from datetime import time
import hashlib
import json
import math

import numpy as np
import pandas as pd

import study_layer_v2_forward_information_study as fis
import study_layer_v2_state_vector as sv
import orb_historical_replay as orb
import map_context_historical_replay as mc


ROOT = Path(__file__).resolve().parent

P1P2_PATH = ROOT / "p1_p2_state_decomposition_20260825.csv"

OUT_CSV = ROOT / "daily_multitimescale_sensor_fusion_v1_20260825.csv"
OUT_META = ROOT / "daily_multitimescale_sensor_fusion_v1_20260825.json"


# ================================================================
# FROZEN V1 FEATURE CONTRACT
# ================================================================

P1P2_FEATURES = [
    "p1_trend_slope",
    "p1_trend_strength_90d",
    "p1_breakout_distance_atr",
    "p1_invalidation_distance_atr",
    "p1_trend_persistence_days",
    "p1_signal_active",

    "p2_reversion_zscore",
    "p2_displacement_atr",
    "p2_overshoot",
    "p2_reversion_setup_age_days",
    "p2_signal_active",

    "regime_bucket",
    "atr_pct",
]

STATE_FIELDS = [
    "structure",
    "location_vwap",
    "location_bb",
    "momentum",
    "momentum_delta",
    "volatility_pct",
]

TARGET_FIELDS = [
    "fwd_return_1d", "mfe_1d", "mae_1d",
    "fwd_return_3d", "mfe_3d", "mae_3d",
    "fwd_return_5d", "mfe_5d", "mae_5d",
    "fwd_return_10d", "mfe_10d", "mae_10d",
    "fwd_return_20d", "mfe_20d", "mae_20d",
]

KNOWN_BAD_DATES = set(fis.KNOWN_BAD_DATES)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def minutes_from_open(ts) -> float:
    ts = pd.Timestamp(ts)
    open_ts = ts.normalize() + pd.Timedelta(hours=9, minutes=15)
    return (ts - open_ts).total_seconds() / 60.0


# ================================================================
# 7D / STOCK STATE -> DAILY SUMMARY
# ================================================================

def build_state_daily(symbol: str, df1: pd.DataFrame) -> pd.DataFrame:

    bars5 = sv.aggregate_1min_to_5min_with_quality(df1)
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)

    # Remove the two already-certified contaminated dates.
    good = ~bars5["timestamp"].dt.date.isin(KNOWN_BAD_DATES)
    bars5 = bars5.loc[good].reset_index(drop=True)

    state = sv.compute_state_vector(bars5)
    state["date"] = state["timestamp"].dt.date

    rows = []

    for day, g in state.groupby("date", sort=True):

        g = g.sort_values("timestamp")

        row = {
            "symbol": symbol,
            "date": str(day),
            "state_5m_bar_count": int(len(g)),
        }

        # Minimal, frozen V1 daily summaries.
        for field in STATE_FIELDS:

            valid = g[field].dropna()

            row[f"{field}_ready_fraction"] = (
                float(g[f"{field}_ready"].mean())
                if f"{field}_ready" in g.columns
                else np.nan
            )

            if valid.empty:
                row[f"{field}_last"] = np.nan
                row[f"{field}_mean"] = np.nan
            else:
                row[f"{field}_last"] = float(valid.iloc[-1])
                row[f"{field}_mean"] = float(valid.mean())

        # A small number of path-shape summaries frozen before modeling.
        m = g["momentum"].dropna()
        row["momentum_max"] = float(m.max()) if len(m) else np.nan
        row["momentum_min"] = float(m.min()) if len(m) else np.nan

        v = g["volatility_pct"].dropna()
        row["volatility_pct_max"] = float(v.max()) if len(v) else np.nan

        # Preserve state identity.
        row["state_schema_version"] = (
            str(g["state_schema_version"].dropna().iloc[-1])
            if g["state_schema_version"].notna().any()
            else None
        )

        row["state_config_hash"] = (
            str(g["config_hash"].dropna().iloc[-1])
            if g["config_hash"].notna().any()
            else None
        )

        rows.append(row)

    return pd.DataFrame(rows)


# ================================================================
# ORB -> DAILY CAUSAL STATE
#
# IMPORTANT:
# We DO NOT use:
#   exit_price
#   exit_reason
#   gross_return_pct
#   net_return_pct
#   r_multiple_*
#
# Only same-day state known by EOD is retained.
# ================================================================

def summarize_orb_day(day_bars: pd.DataFrame) -> dict:

    day_bars = day_bars.sort_values("timestamp").reset_index(drop=True)

    result = {
        "orb_ready": False,
        "orb_active": False,
        "orb_high": np.nan,
        "orb_low": np.nan,
        "orb_range_pct": np.nan,
        "orb_breakout_minute": np.nan,
        "orb_first_breakout_strength_bps": np.nan,
    }

    if len(day_bars) < orb.ORB_MINUTES + 1:
        return result

    opening = day_bars.iloc[:orb.ORB_MINUTES]

    # Fail closed if the expected 09:15 -> 09:29 opening range is not intact.
    expected_times = [
        (pd.Timestamp("2000-01-01 09:15") + pd.Timedelta(minutes=i)).time()
        for i in range(orb.ORB_MINUTES)
    ]
    actual_times = [pd.Timestamp(x).time() for x in opening["timestamp"]]

    if actual_times != expected_times:
        return result

    orb_high = float(opening["high"].max())
    orb_low = float(opening["low"].min())

    if not (
        np.isfinite(orb_high)
        and np.isfinite(orb_low)
        and orb_low > 0
        and orb_high > orb_low
    ):
        return result

    result["orb_ready"] = True
    result["orb_high"] = orb_high
    result["orb_low"] = orb_low
    result["orb_range_pct"] = (orb_high - orb_low) / orb_low

    breakout_level = orb_high * (
        1.0 + orb.BREAKOUT_BUFFER_BPS / 10_000.0
    )

    rest = day_bars.iloc[orb.ORB_MINUTES:]

    hits = rest.loc[rest["high"] >= breakout_level]

    if hits.empty:
        return result

    first = hits.iloc[0]

    result["orb_active"] = True
    result["orb_breakout_minute"] = minutes_from_open(
        first["timestamp"]
    )
    result["orb_first_breakout_strength_bps"] = (
        float(first["high"]) / orb_high - 1.0
    ) * 10_000.0

    return result


def build_orb_daily(symbol: str, df1: pd.DataFrame) -> pd.DataFrame:

    x = df1.copy()
    x = x.sort_values("timestamp").reset_index(drop=True)

    x = x.loc[
        ~x["timestamp"].dt.date.isin(KNOWN_BAD_DATES)
    ].copy()

    x["date"] = x["timestamp"].dt.date

    rows = []

    for day, g in x.groupby("date", sort=True):

        r = summarize_orb_day(g)

        rows.append({
            "symbol": symbol,
            "date": str(day),
            **r,
        })

    return pd.DataFrame(rows)


# ================================================================
# MAP+CONTEXT -> DAILY ENTRY-SIDE STATE
#
# SAFE:
#   event occurrence
#   entry-side stop/target geometry
#
# DROP:
#   exit timestamp
#   exit price
#   exit reason
#   realized P&L
# ================================================================

def build_map_daily(
    symbol: str,
    index_daily: pd.DataFrame
) -> pd.DataFrame:

    trades = mc.replay_symbol(symbol, index_daily)

    raw = []

    for t in trades:

        entry_ts = pd.Timestamp(t.entry_ts)
        day = entry_ts.date()

        if day in KNOWN_BAD_DATES:
            continue

        stop_dist = (
            (t.entry_price - t.stop_price) / t.entry_price
            if t.entry_price and t.stop_price is not None
            else np.nan
        )

        target_dist = (
            (t.target_price - t.entry_price) / t.entry_price
            if (
                t.entry_price
                and t.target_price is not None
            )
            else np.nan
        )

        raw.append({
            "symbol": symbol,
            "date": str(day),
            "entry_ts": entry_ts,
            "entry_minute": minutes_from_open(entry_ts),
            "stop_distance_pct": stop_dist,
            "target_distance_pct": target_dist,
        })

    if not raw:
        return pd.DataFrame(columns=[
            "symbol",
            "date",
            "map_active",
            "map_entry_count",
            "map_first_entry_minute",
            "map_first_stop_distance_pct",
            "map_first_target_distance_pct",
            "map_mean_stop_distance_pct",
        ])

    df = pd.DataFrame(raw)

    rows = []

    for (sym, day), g in df.groupby(
        ["symbol", "date"],
        sort=True
    ):

        g = g.sort_values("entry_ts")

        rows.append({
            "symbol": sym,
            "date": day,
            "map_active": True,
            "map_entry_count": int(len(g)),
            "map_first_entry_minute":
                float(g["entry_minute"].iloc[0]),
            "map_first_stop_distance_pct":
                float(g["stop_distance_pct"].iloc[0]),
            "map_first_target_distance_pct":
                (
                    float(g["target_distance_pct"].iloc[0])
                    if pd.notna(g["target_distance_pct"].iloc[0])
                    else np.nan
                ),
            "map_mean_stop_distance_pct":
                float(g["stop_distance_pct"].mean()),
        })

    return pd.DataFrame(rows)


# ================================================================
# MAIN BUILD
# ================================================================

def main():

    print("=" * 100)
    print("DAILY MULTI-TIMESCALE SENSOR FUSION V1")
    print("=" * 100)

    p12 = pd.read_csv(P1P2_PATH)

    p12["date"] = pd.to_datetime(
        p12["date"]
    ).dt.date.astype(str)

    symbols = sorted(p12["symbol"].unique())

    print("P1/P2 rows   :", len(p12))
    print("Symbols      :", len(symbols))
    print("Universe     :", ", ".join(symbols))

    # Remove known contaminated sessions from master backbone.
    p12 = p12.loc[
        ~pd.to_datetime(p12["date"]).dt.date.isin(KNOWN_BAD_DATES)
    ].copy()

    index_daily = mc._load_index_daily()

    intraday_frames = []

    for i, symbol in enumerate(symbols, 1):

        print()
        print(
            f"[{i}/{len(symbols)}] Building {symbol}..."
        )

        df1 = fis.load_symbol_1min(symbol)
        df1 = df1.sort_values("timestamp").reset_index(drop=True)

        print("  1m rows :", len(df1))

        state_daily = build_state_daily(symbol, df1)
        orb_daily = build_orb_daily(symbol, df1)
        map_daily = build_map_daily(symbol, index_daily)

        print("  state days :", len(state_daily))
        print("  ORB days   :", len(orb_daily))
        print("  MAP days   :", len(map_daily))

        daily = state_daily.merge(
            orb_daily,
            on=["symbol", "date"],
            how="left",
            validate="one_to_one",
        )

        daily = daily.merge(
            map_daily,
            on=["symbol", "date"],
            how="left",
            validate="one_to_one",
        )

        # No Map event is itself a valid state: active=False.
        daily["map_active"] = daily["map_active"].fillna(False)
        daily["map_entry_count"] = (
            daily["map_entry_count"]
            .fillna(0)
            .astype(int)
        )

        intraday_frames.append(daily)

    intraday = pd.concat(
        intraday_frames,
        ignore_index=True
    )

    # Exactly one row per symbol/date.
    if intraday.duplicated(["symbol", "date"]).any():
        raise RuntimeError(
            "Duplicate symbol/date rows in intraday fusion table"
        )

    master = p12.merge(
        intraday,
        on=["symbol", "date"],
        how="inner",
        validate="one_to_one",
    )

    master = master.sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)

    # ============================================================
    # CAUSALITY / LEAKAGE ASSERTIONS
    # ============================================================

    forbidden_feature_fragments = [
        "exit_price",
        "exit_reason",
        "gross_return",
        "net_return",
        "r_multiple",
        "gross_pnl",
        "net_pnl",
    ]

    target_set = set(TARGET_FIELDS)

    feature_columns = [
        c for c in master.columns
        if c not in target_set
    ]

    leakage_candidates = [
        c for c in feature_columns
        if any(
            bad in c.lower()
            for bad in forbidden_feature_fragments
        )
    ]

    if leakage_candidates:
        raise RuntimeError(
            "Leakage-prone feature columns detected: "
            + ", ".join(leakage_candidates)
        )

    # Known contaminated sessions must be absent.
    master_dates = set(
        pd.to_datetime(master["date"]).dt.date
    )

    overlap_bad = sorted(
        master_dates.intersection(KNOWN_BAD_DATES)
    )

    if overlap_bad:
        raise RuntimeError(
            f"Known bad dates survived fusion: {overlap_bad}"
        )

    master.to_csv(
        OUT_CSV,
        index=False
    )

    # ============================================================
    # BUILD METADATA / FROZEN PROVENANCE
    # ============================================================

    meta = {
        "schema": "DAILY_MULTITIMESCALE_SENSOR_FUSION_V1",
        "created_for": "SELF_LEARNING_SENSOR_FUSION_V1",
        "symbols": symbols,
        "row_count": int(len(master)),
        "column_count": int(len(master.columns)),
        "first_date": str(master["date"].min()),
        "last_date": str(master["date"].max()),

        "intraday_source":
            "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/"
            "DATA_1MIN_48_20230703_20260824",

        "known_bad_dates": sorted(
            str(d) for d in KNOWN_BAD_DATES
        ),

        "causality_contract":
            "All intraday summaries use information available by end of "
            "decision day D. Forward targets begin D+1 onward. ORB and "
            "Map realized trade outcomes are excluded from feature space.",

        "stock_state_fields": STATE_FIELDS,

        "orb_safe_fields": [
            "orb_ready",
            "orb_active",
            "orb_high",
            "orb_low",
            "orb_range_pct",
            "orb_breakout_minute",
            "orb_first_breakout_strength_bps",
        ],

        "orb_explicitly_excluded": [
            "exit_price",
            "exit_reason",
            "gross_return_pct",
            "net_return_pct",
            "r_multiple_gross",
            "r_multiple_net",
        ],

        "map_safe_fields": [
            "map_active",
            "map_entry_count",
            "map_first_entry_minute",
            "map_first_stop_distance_pct",
            "map_first_target_distance_pct",
            "map_mean_stop_distance_pct",
        ],

        "map_explicitly_excluded": [
            "exit_ts",
            "exit_price",
            "exit_reason",
            "gross_pnl",
            "net_pnl",
        ],

        "targets": TARGET_FIELDS,

        "p1p2_source_sha256":
            sha256_file(P1P2_PATH),
    }

    meta["output_csv_sha256"] = sha256_file(OUT_CSV)

    OUT_META.write_text(
        json.dumps(
            meta,
            indent=2
        ),
        encoding="utf-8",
    )

    # ============================================================
    # REPORT
    # ============================================================

    print()
    print("=" * 100)
    print("MASTER FUSION TABLE BUILT")
    print("=" * 100)

    print("Rows        :", len(master))
    print("Columns     :", len(master.columns))
    print("First date  :", master["date"].min())
    print("Last date   :", master["date"].max())

    print()
    print("Rows per symbol:")
    print(
        master.groupby("symbol")
        .size()
        .to_string()
    )

    print()
    print("ORB activity:")
    print(
        master.groupby("symbol")["orb_active"]
        .agg(["count", "sum", "mean"])
        .to_string()
    )

    print()
    print("MAP activity:")
    print(
        master.groupby("symbol")["map_active"]
        .agg(["count", "sum", "mean"])
        .to_string()
    )

    print()
    print("Selected feature missingness:")

    audit_cols = [
        "p1_trend_strength_90d",
        "p2_reversion_zscore",
        "structure_last",
        "location_vwap_last",
        "location_bb_last",
        "momentum_last",
        "momentum_delta_last",
        "volatility_pct_last",
        "orb_ready",
        "map_active",
    ]

    for c in audit_cols:
        if c in master.columns:
            print(
                f"{c:35s} "
                f"{master[c].isna().mean():8.3%}"
            )

    print()
    print("Known bad dates present:",
          sorted(overlap_bad))

    print()
    print("Leakage-prone feature columns:",
          leakage_candidates)

    print()
    print("CSV :", OUT_CSV)
    print("META:", OUT_META)

    print()
    print("CSV SHA256:",
          meta["output_csv_sha256"])

    print()
    print("=" * 100)
    print("BUILD COMPLETE - MODELING NOT STARTED")
    print("=" * 100)


if __name__ == "__main__":
    main()
