from pathlib import Path
import hashlib
import json

import pandas as pd


ROOT = Path(__file__).resolve().parent

INPUT = ROOT / "daily_multitimescale_sensor_fusion_v1_20260825.csv"

OUTPUT = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.csv"
MANIFEST = ROOT / "daily_multitimescale_sensor_fusion_v1_MODEL_FROZEN_20260825.json"

EXPECTED_SYMBOLS = [
    "BAJFINANCE",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "RELIANCE",
    "SBIN",
    "SUNPHARMA",
    "TCS",
]

SPECIAL_SESSIONS = {
    pd.Timestamp("2023-11-12"),
    pd.Timestamp("2024-11-01"),
    pd.Timestamp("2025-10-21"),
}

TRUNCATED_SESSIONS = {
    pd.Timestamp(x)
    for x in [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-10",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
        "2026-08-14",
        "2026-08-17",
        "2026-08-18",
        "2026-08-19",
        "2026-08-20",
        "2026-08-21",
        "2026-08-24",
    ]
}

P2_READY_START = pd.Timestamp("2023-09-22")

TRAIN_START = pd.Timestamp("2023-09-22")
TRAIN_END = pd.Timestamp("2024-12-31")

VALID_START = pd.Timestamp("2025-01-01")
VALID_END = pd.Timestamp("2025-12-31")

HOLDOUT_START = pd.Timestamp("2026-01-01")

MAX_HORIZON = 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():

    print("=" * 100)
    print("FUSION V1 - CLEAN / PURGED / FROZEN MODELING DATASET")
    print("=" * 100)

    df = pd.read_csv(INPUT)
    df["date"] = pd.to_datetime(df["date"])

    print()
    print("INPUT")
    print("Rows :", len(df))
    print("Dates:", df["date"].nunique())
    print("Range:", df["date"].min().date(), "->", df["date"].max().date())

    # ------------------------------------------------------------------
    # Verify the abnormal-session audit before filtering.
    # ------------------------------------------------------------------

    actual_abnormal = set(
        df.loc[df["state_5m_bar_count"] != 75, "date"].unique()
    )

    expected_abnormal = SPECIAL_SESSIONS | TRUNCATED_SESSIONS

    if actual_abnormal != expected_abnormal:
        raise RuntimeError(
            "Abnormal-session set has changed.\n"
            f"Expected: {sorted(expected_abnormal)}\n"
            f"Actual  : {sorted(actual_abnormal)}"
        )

    print()
    print("Abnormal-session audit: PASS")
    print("Special sessions       :", len(SPECIAL_SESSIONS))
    print("Truncated sessions     :", len(TRUNCATED_SESSIONS))

    # ------------------------------------------------------------------
    # Common modeling population.
    # ------------------------------------------------------------------

    clean = df.loc[
        (df["date"] >= P2_READY_START)
        & (df["state_5m_bar_count"] == 75)
    ].copy()

    # P2 must now be fully ready.
    p2_fields = [
        "p2_reversion_zscore",
        "p2_displacement_atr",
        "p2_overshoot",
    ]

    for c in p2_fields:
        n = int(clean[c].isna().sum())
        if n:
            raise RuntimeError(
                f"{c} still has {n} missing rows after P2 warm-up cut"
            )

    # Exactly eight symbols per retained date.
    per_date = clean.groupby("date")["symbol"].nunique()

    if not (per_date == 8).all():
        raise RuntimeError(
            "Retained dates do not all contain exactly 8 symbols"
        )

    if sorted(clean["symbol"].unique()) != EXPECTED_SYMBOLS:
        raise RuntimeError("Unexpected symbol universe")

    # ------------------------------------------------------------------
    # Determine the last date with a COMPLETE 20-day target across
    # all 8 symbols. This is based only on target AVAILABILITY, not
    # target performance.
    # ------------------------------------------------------------------

    complete20 = (
        clean.groupby("date")["fwd_return_20d"]
        .apply(lambda s: len(s) == 8 and s.notna().all())
    )

    complete20_dates = complete20[complete20].index

    if len(complete20_dates) == 0:
        raise RuntimeError("No date has complete 20-day targets")

    HOLDOUT_END = complete20_dates.max()

    # Never allow the feature-period holdout to enter the truncated
    # August block.
    if HOLDOUT_END >= pd.Timestamp("2026-08-03"):
        raise RuntimeError(
            f"Holdout end unexpectedly enters truncated block: {HOLDOUT_END}"
        )

    print()
    print("Last complete 20D decision date:", HOLDOUT_END.date())

    # Only retain the intended experiment date range.
    model = clean.loc[
        (clean["date"] >= TRAIN_START)
        & (clean["date"] <= HOLDOUT_END)
    ].copy()

    # ------------------------------------------------------------------
    # Assign chronological periods.
    # ------------------------------------------------------------------

    model["split_role"] = "UNASSIGNED"

    model.loc[
        model["date"].between(TRAIN_START, TRAIN_END),
        "split_role"
    ] = "TRAIN"

    model.loc[
        model["date"].between(VALID_START, VALID_END),
        "split_role"
    ] = "VALIDATION"

    model.loc[
        model["date"].between(HOLDOUT_START, HOLDOUT_END),
        "split_role"
    ] = "HOLDOUT"

    if (model["split_role"] == "UNASSIGNED").any():
        bad = model.loc[
            model["split_role"] == "UNASSIGNED",
            "date"
        ].drop_duplicates()

        raise RuntimeError(
            "Unassigned dates found: "
            + ", ".join(str(x.date()) for x in bad)
        )

    # ------------------------------------------------------------------
    # PURGE 20 RETAINED DECISION DATES AT BOTH MODEL-SELECTION
    # BOUNDARIES.
    #
    # This prevents a 20-day forward label in the preceding segment
    # from extending into the following segment.
    # ------------------------------------------------------------------

    train_dates = sorted(
        model.loc[
            model["split_role"] == "TRAIN",
            "date"
        ].unique()
    )

    valid_dates = sorted(
        model.loc[
            model["split_role"] == "VALIDATION",
            "date"
        ].unique()
    )

    if len(train_dates) <= MAX_HORIZON:
        raise RuntimeError("Training period too short for purge")

    if len(valid_dates) <= MAX_HORIZON:
        raise RuntimeError("Validation period too short for purge")

    purge_train = set(train_dates[-MAX_HORIZON:])
    purge_valid = set(valid_dates[-MAX_HORIZON:])

    model.loc[
        model["date"].isin(purge_train),
        "split_role"
    ] = "PURGED_TRAIN_BOUNDARY"

    model.loc[
        model["date"].isin(purge_valid),
        "split_role"
    ] = "PURGED_VALIDATION_BOUNDARY"

    # ------------------------------------------------------------------
    # Final structural assertions.
    # ------------------------------------------------------------------

    if model.duplicated(["symbol", "date"]).any():
        raise RuntimeError("Duplicate symbol/date rows")

    per_day_final = model.groupby("date")["symbol"].nunique()

    if not (per_day_final == 8).all():
        raise RuntimeError("Final model data is not balanced 8-per-day")

    if (model["state_5m_bar_count"] != 75).any():
        raise RuntimeError("Non-standard session survived final dataset")

    if model[p2_fields].isna().any().any():
        raise RuntimeError("P2 warm-up missingness survived final dataset")

    model = model.sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Save.
    # ------------------------------------------------------------------

    model.to_csv(
        OUTPUT,
        index=False,
        date_format="%Y-%m-%d"
    )

    role_summary = {}

    for role, g in model.groupby("split_role"):

        role_summary[role] = {
            "rows": int(len(g)),
            "dates": int(g["date"].nunique()),
            "first_date": str(g["date"].min().date()),
            "last_date": str(g["date"].max().date()),
        }

    manifest = {
        "schema": "SELF_LEARNING_SENSOR_FUSION_V1_MODEL_FREEZE",

        "input_file": INPUT.name,
        "input_sha256": sha256_file(INPUT),

        "output_file": OUTPUT.name,

        "common_population_rule":
            "Standard 75x5-minute sessions only; P2 fully ready; "
            "balanced 8-symbol development universe.",

        "p2_ready_start": str(P2_READY_START.date()),

        "excluded_special_sessions": sorted(
            str(x.date()) for x in SPECIAL_SESSIONS
        ),

        "excluded_truncated_sessions": sorted(
            str(x.date()) for x in TRUNCATED_SESSIONS
        ),

        "train_calendar_window": {
            "start": str(TRAIN_START.date()),
            "end": str(TRAIN_END.date()),
        },

        "validation_calendar_window": {
            "start": str(VALID_START.date()),
            "end": str(VALID_END.date()),
        },

        "holdout_calendar_window": {
            "start": str(HOLDOUT_START.date()),
            "end": str(HOLDOUT_END.date()),
        },

        "purge_rule":
            "Last 20 retained decision dates of TRAIN and VALIDATION "
            "are excluded from model fitting/evaluation to protect the "
            "maximum 20-trading-day forward target boundary.",

        "max_target_horizon_days": MAX_HORIZON,

        "holdout_policy":
            "HOLDOUT is untouched during feature engineering, "
            "hyperparameter selection, threshold selection and "
            "model-family selection.",

        "role_summary": role_summary,
    }

    manifest["output_sha256"] = sha256_file(OUTPUT)

    MANIFEST.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8"
    )

    # ------------------------------------------------------------------
    # Report.
    # ------------------------------------------------------------------

    print()
    print("=" * 100)
    print("FINAL FROZEN DATASET")
    print("=" * 100)

    print("Rows        :", len(model))
    print("Dates       :", model["date"].nunique())
    print("Symbols     :", model["symbol"].nunique())
    print("First date  :", model["date"].min().date())
    print("Last date   :", model["date"].max().date())

    print()
    print("Split roles:")

    for role in [
        "TRAIN",
        "PURGED_TRAIN_BOUNDARY",
        "VALIDATION",
        "PURGED_VALIDATION_BOUNDARY",
        "HOLDOUT",
    ]:
        g = model[model["split_role"] == role]

        if len(g) == 0:
            print(f"{role:30s} EMPTY")
            continue

        print(
            f"{role:30s} "
            f"rows={len(g):5d} "
            f"dates={g['date'].nunique():4d} "
            f"{g['date'].min().date()} -> "
            f"{g['date'].max().date()}"
        )

    print()
    print("Target completeness by role:")

    for role in ["TRAIN", "VALIDATION", "HOLDOUT"]:

        g = model[model["split_role"] == role]

        print()
        print(role)

        for h in [1, 3, 5, 10, 20]:

            c = f"fwd_return_{h}d"

            print(
                f"  {c:18s} "
                f"missing={int(g[c].isna().sum()):4d}"
            )

    print()
    print("P2 missing after freeze:",
          int(model["p2_reversion_zscore"].isna().sum()))

    print("Non-75-bar rows:",
          int((model["state_5m_bar_count"] != 75).sum()))

    print("Duplicate symbol/date:",
          int(model.duplicated(["symbol", "date"]).sum()))

    print()
    print("OUTPUT  :", OUTPUT)
    print("MANIFEST:", MANIFEST)

    print()
    print("OUTPUT SHA256:", manifest["output_sha256"])

    print()
    print("=" * 100)
    print("MODEL POPULATION FROZEN - NO MODEL TRAINED")
    print("=" * 100)


if __name__ == "__main__":
    main()
