from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteConnect

from kite_request_governor import (
    HISTORICAL as GOV_HISTORICAL,
    DEFAULT as GOV_DEFAULT,
    KiteRequestGovernor,
)


IST = ZoneInfo("Asia/Kolkata")

ROOT = Path(__file__).resolve().parent

START = date(2016, 1, 1)
END = date(2026, 8, 25)

OUT = (
    ROOT
    / "EXOGENOUS_CONTEXT_V1_RAW_20160101_20260825"
)

MANIFEST = OUT / "manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def require_environment():
    api_key = os.getenv(
        "KITE_API_KEY",
        "",
    ).strip()

    token = os.getenv(
        "KITE_ACCESS_TOKEN",
        "",
    ).strip()

    governor_dir = os.getenv(
        "KITE_RATE_GOVERNOR_DIR",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "KITE_API_KEY missing"
        )

    if not token:
        raise RuntimeError(
            "KITE_ACCESS_TOKEN missing"
        )

    if not governor_dir:
        raise RuntimeError(
            "KITE_RATE_GOVERNOR_DIR missing"
        )

    return (
        api_key,
        token,
        Path(governor_dir),
    )


def exact_match(rows, label):
    target = label.upper()

    return [
        r for r in rows
        if (
            str(
                r.get(
                    "tradingsymbol",
                    "",
                )
            ).upper()
            == target

            or

            str(
                r.get(
                    "name",
                    "",
                )
            ).upper()
            == target
        )
    ]


def chunk_ranges(
    start: date,
    end: date,
    max_days: int,
):
    cursor = start

    while cursor <= end:

        chunk_end = min(
            end,
            cursor
            + timedelta(
                days=max_days - 1
            ),
        )

        yield cursor, chunk_end

        cursor = (
            chunk_end
            + timedelta(days=1)
        )


def fetch_chunked(
    *,
    kite,
    governor,
    token,
    start,
    end,
    interval,
    max_days,
    continuous,
    oi,
    label,
):
    all_rows = []
    chunks = []

    ranges = list(
        chunk_ranges(
            start,
            end,
            max_days,
        )
    )

    print()
    print("=" * 105)
    print(label)
    print("=" * 105)

    for i, (a, b) in enumerate(
        ranges,
        start=1,
    ):
        governor.acquire(
            GOV_HISTORICAL
        )

        bars = kite.historical_data(
            token,
            a,
            b,
            interval,
            continuous=continuous,
            oi=oi,
        )

        print(
            f"[{i:02d}/{len(ranges):02d}] "
            f"{a} -> {b} "
            f"rows={len(bars)}"
        )

        chunks.append({
            "from": a.isoformat(),
            "to": b.isoformat(),
            "rows": len(bars),
        })

        all_rows.extend(bars)

    if not all_rows:
        raise RuntimeError(
            f"{label}: zero rows returned"
        )

    df = pd.DataFrame(
        all_rows
    )

    if "date" not in df.columns:
        raise RuntimeError(
            f"{label}: date column missing"
        )

    df["date"] = pd.to_datetime(
        df["date"],
        errors="raise",
    )

    df = df.sort_values(
        "date"
    ).reset_index(
        drop=True
    )

    duplicate_count = int(
        df["date"].duplicated().sum()
    )

    if duplicate_count:
        raise RuntimeError(
            f"{label}: "
            f"{duplicate_count} duplicate timestamps"
        )

    for col in [
        "open",
        "high",
        "low",
        "close",
    ]:
        if col not in df.columns:
            raise RuntimeError(
                f"{label}: missing {col}"
            )

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    bad_ohlc = int(
        (
            df[
                [
                    "open",
                    "high",
                    "low",
                    "close",
                ]
            ]
            .isna()
            .any(axis=1)
        ).sum()
    )

    bad_ohlc += int(
        (
            (
                df["high"]
                < df[
                    [
                        "open",
                        "close",
                        "low",
                    ]
                ].max(axis=1)
            )
            |
            (
                df["low"]
                > df[
                    [
                        "open",
                        "close",
                        "high",
                    ]
                ].min(axis=1)
            )
        ).sum()
    )

    if bad_ohlc:
        raise RuntimeError(
            f"{label}: "
            f"{bad_ohlc} invalid OHLC rows"
        )

    negative_volume = None

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce",
        )

        negative_volume = int(
            (df["volume"] < 0).sum()
        )

        if negative_volume:
            raise RuntimeError(
                f"{label}: negative volume found"
            )

    negative_oi = None
    oi_non_null = None
    oi_non_zero = None

    if "oi" in df.columns:

        df["oi"] = pd.to_numeric(
            df["oi"],
            errors="coerce",
        )

        negative_oi = int(
            (df["oi"] < 0).sum()
        )

        if negative_oi:
            raise RuntimeError(
                f"{label}: negative OI found"
            )

        oi_non_null = int(
            df["oi"].notna().sum()
        )

        oi_non_zero = int(
            (df["oi"].fillna(0) != 0)
            .sum()
        )

    audit = {
        "rows":
            int(len(df)),

        "first_timestamp":
            df["date"].iloc[0].isoformat(),

        "last_timestamp":
            df["date"].iloc[-1].isoformat(),

        "duplicate_timestamps":
            duplicate_count,

        "invalid_ohlc_rows":
            bad_ohlc,

        "negative_volume_rows":
            negative_volume,

        "negative_oi_rows":
            negative_oi,

        "oi_non_null_rows":
            oi_non_null,

        "oi_non_zero_rows":
            oi_non_zero,

        "columns":
            list(df.columns),

        "chunks":
            chunks,
    }

    return df, audit


def save_dataset(
    df,
    filename,
):
    path = OUT / filename

    df.to_csv(
        path,
        index=False,
        lineterminator="\n",
    )

    return (
        path,
        sha256_file(path),
    )


def main():
    print("=" * 105)
    print(
        "EXOGENOUS CONTEXT V1 "
        "- RAW HISTORICAL ACQUISITION"
    )
    print("=" * 105)

    print(
        "Broker operations : "
        "instruments + historical_data only"
    )

    print(
        "Order operations  : NONE"
    )

    if OUT.exists():
        existing = list(
            OUT.iterdir()
        )

        if existing:
            raise RuntimeError(
                "Output directory already contains "
                "files. Refusing overwrite."
            )
    else:
        OUT.mkdir(
            parents=True,
            exist_ok=False,
        )

    api_key, token, governor_dir = (
        require_environment()
    )

    governor_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    governor = KiteRequestGovernor(
        state_dir=governor_dir
    )

    kite = KiteConnect(
        api_key=api_key
    )

    kite.set_access_token(
        token
    )

    governor.acquire(
        GOV_DEFAULT
    )

    nse = kite.instruments(
        "NSE"
    )

    governor.acquire(
        GOV_DEFAULT
    )

    nfo = kite.instruments(
        "NFO"
    )

    print()
    print(
        "NSE instruments:",
        len(nse)
    )

    print(
        "NFO instruments:",
        len(nfo)
    )

    vix = exact_match(
        nse,
        "INDIA VIX",
    )

    bank = exact_match(
        nse,
        "NIFTY BANK",
    )

    if len(vix) != 1:
        raise RuntimeError(
            f"INDIA VIX exact matches={len(vix)}"
        )

    if len(bank) != 1:
        raise RuntimeError(
            f"NIFTY BANK exact matches={len(bank)}"
        )

    today = END

    nifty_futures = [
        r for r in nfo
        if (
            str(
                r.get(
                    "instrument_type",
                    "",
                )
            ).upper()
            == "FUT"

            and

            str(
                r.get(
                    "name",
                    "",
                )
            ).upper()
            == "NIFTY"

            and

            r.get(
                "expiry"
            ) is not None

            and

            r.get(
                "expiry"
            ) > today
        )
    ]

    nifty_futures.sort(
        key=lambda r: r["expiry"]
    )

    if not nifty_futures:

        nifty_futures = [
            r for r in nfo
            if (
                str(
                    r.get(
                        "instrument_type",
                        "",
                    )
                ).upper()
                == "FUT"

                and

                str(
                    r.get(
                        "name",
                        "",
                    )
                ).upper()
                == "NIFTY"

                and

                r.get(
                    "expiry"
                ) is not None

                and

                r.get(
                    "expiry"
                ) >= today
            )
        ]

        nifty_futures.sort(
            key=lambda r: r["expiry"]
        )

    if not nifty_futures:
        raise RuntimeError(
            "No usable NIFTY FUT anchor found"
        )

    fut_anchor = nifty_futures[0]

    print()
    print(
        "INDIA VIX token :",
        vix[0]["instrument_token"]
    )

    print(
        "NIFTY BANK token:",
        bank[0]["instrument_token"]
    )

    print(
        "Futures anchor   :",
        fut_anchor["tradingsymbol"],
        "expiry",
        fut_anchor["expiry"],
        "token",
        fut_anchor["instrument_token"],
    )

    # 15-minute historical API:
    # use conservative 180-calendar-day chunks.
    vix_df, vix_audit = fetch_chunked(
        kite=kite,
        governor=governor,
        token=vix[0][
            "instrument_token"
        ],
        start=START,
        end=END,
        interval="15minute",
        max_days=180,
        continuous=False,
        oi=False,
        label=(
            "INDIA VIX 15MIN "
            "2016-2026"
        ),
    )

    bank_df, bank_audit = fetch_chunked(
        kite=kite,
        governor=governor,
        token=bank[0][
            "instrument_token"
        ],
        start=START,
        end=END,
        interval="15minute",
        max_days=180,
        continuous=False,
        oi=False,
        label=(
            "NIFTY BANK 15MIN "
            "2016-2026"
        ),
    )

    # Daily continuous futures:
    # Kite limit is 2000 days,
    # so use conservative 1800-day chunks.
    fut_df, fut_audit = fetch_chunked(
        kite=kite,
        governor=governor,
        token=fut_anchor[
            "instrument_token"
        ],
        start=START,
        end=END,
        interval="day",
        max_days=1800,
        continuous=True,
        oi=True,
        label=(
            "NIFTY FUTURES CONTINUOUS "
            "DAILY + OI 2016-2026"
        ),
    )

    vix_path, vix_sha = save_dataset(
        vix_df,
        (
            "INDIA_VIX_15MIN_"
            "20160101_20260825.csv"
        ),
    )

    bank_path, bank_sha = save_dataset(
        bank_df,
        (
            "NIFTY_BANK_15MIN_"
            "20160101_20260825.csv"
        ),
    )

    fut_path, fut_sha = save_dataset(
        fut_df,
        (
            "NIFTY_FUTURES_CONTINUOUS_"
            "DAILY_OI_20160101_20260825.csv"
        ),
    )

    manifest = {
        "schema":
            "EXOGENOUS_CONTEXT_V1_RAW_MANIFEST",

        "created_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "status":
            (
                "ACQUIRED_BASIC_INTEGRITY_PASS_"
                "SESSION_COMPLETENESS_NOT_YET_CERTIFIED"
            ),

        "research_role":
            "RAW_EXTERNAL_CONTEXT_ONLY",

        "broker_operations": [
            "instruments",
            "historical_data",
        ],

        "broker_write_operations":
            [],

        "requested_range": {
            "start":
                START.isoformat(),

            "end":
                END.isoformat(),
        },

        "sources": {
            "INDIA_VIX_15MIN": {
                "instrument_token":
                    vix[0][
                        "instrument_token"
                    ],

                "tradingsymbol":
                    vix[0][
                        "tradingsymbol"
                    ],

                "file":
                    vix_path.name,

                "sha256":
                    vix_sha,

                "audit":
                    vix_audit,
            },

            "NIFTY_BANK_15MIN": {
                "instrument_token":
                    bank[0][
                        "instrument_token"
                    ],

                "tradingsymbol":
                    bank[0][
                        "tradingsymbol"
                    ],

                "file":
                    bank_path.name,

                "sha256":
                    bank_sha,

                "audit":
                    bank_audit,
            },

            "NIFTY_FUTURES_CONTINUOUS_DAILY_OI": {
                "anchor_instrument_token":
                    fut_anchor[
                        "instrument_token"
                    ],

                "anchor_tradingsymbol":
                    fut_anchor[
                        "tradingsymbol"
                    ],

                "anchor_expiry":
                    str(
                        fut_anchor[
                            "expiry"
                        ]
                    ),

                "continuous":
                    True,

                "oi":
                    True,

                "file":
                    fut_path.name,

                "sha256":
                    fut_sha,

                "audit":
                    fut_audit,
            },
        },

        "research_boundary": {
            "modeling_performed":
                False,

            "outcomes_joined":
                False,

            "feature_selection_performed":
                False,

            "production_authorized":
                False,

            "p01d":
                "UNCHANGED_AND_SOVEREIGN",
        },
    }

    MANIFEST.write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest_sha = sha256_file(
        MANIFEST
    )

    print()
    print("=" * 105)
    print("ACQUISITION COMPLETE")
    print("=" * 105)

    for label, path, sha, audit in [
        (
            "INDIA VIX 15MIN",
            vix_path,
            vix_sha,
            vix_audit,
        ),
        (
            "NIFTY BANK 15MIN",
            bank_path,
            bank_sha,
            bank_audit,
        ),
        (
            "NIFTY FUT CONT DAILY+OI",
            fut_path,
            fut_sha,
            fut_audit,
        ),
    ]:
        print()
        print(label)
        print(
            "  Rows :",
            audit["rows"]
        )
        print(
            "  First:",
            audit[
                "first_timestamp"
            ]
        )
        print(
            "  Last :",
            audit[
                "last_timestamp"
            ]
        )
        print(
            "  Duplicates:",
            audit[
                "duplicate_timestamps"
            ]
        )
        print(
            "  Bad OHLC  :",
            audit[
                "invalid_ohlc_rows"
            ]
        )

        if (
            audit[
                "oi_non_null_rows"
            ]
            is not None
        ):
            print(
                "  OI non-null:",
                audit[
                    "oi_non_null_rows"
                ]
            )
            print(
                "  OI non-zero:",
                audit[
                    "oi_non_zero_rows"
                ]
            )

        print(
            "  SHA256:",
            sha
        )

    print()
    print(
        "Manifest SHA256:",
        manifest_sha
    )

    print()
    print(
        "Session completeness:"
        " NOT YET CERTIFIED"
    )

    print(
        "Modeling performed   : NO"
    )

    print(
        "Broker writes        : NONE"
    )

    print("=" * 105)


if __name__ == "__main__":
    main()
