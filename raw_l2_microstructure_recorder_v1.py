from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")

ARCHIVE = (
    Path(__file__).resolve().parent
    / "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825"
    / "DATA_1MIN_48_20230703_20260824"
)

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "L2_MICROSTRUCTURE_RAW"
)

SCHEMA = "RAW_L2_QUOTE_SNAPSHOT_V1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def finite_float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None

    return x if math.isfinite(x) else None


def finite_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def iso_value(value):
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass

    return str(value)


def load_authoritative_universe() -> tuple[str, ...]:

    if not ARCHIVE.exists():
        raise RuntimeError(
            f"Authoritative archive not found: {ARCHIVE}"
        )

    files = sorted(
        ARCHIVE.glob("NSE_*_minute_*.csv")
    )

    symbols = []

    for path in files:

        name = path.name

        if not name.startswith("NSE_"):
            continue

        if "_minute_" not in name:
            continue

        symbol = (
            name
            .split("NSE_", 1)[1]
            .split("_minute_", 1)[0]
        )

        symbols.append(symbol)

    symbols = tuple(sorted(set(symbols)))

    if len(symbols) != 48:
        raise RuntimeError(
            f"Expected exactly 48 authoritative symbols, "
            f"found {len(symbols)}"
        )

    return symbols


def source_authority_audit() -> None:
    """
    AST-level audit.

    We inspect broker method CALLS, rather than searching source text,
    so comments and documentation cannot create false positives.
    """

    source_path = Path(__file__).resolve()

    tree = ast.parse(
        source_path.read_text(
            encoding="utf-8"
        )
    )

    forbidden = {
        "place" + "_" + "order",
        "modify" + "_" + "order",
        "cancel" + "_" + "order",
    }

    found = []

    for node in ast.walk(tree):

        if not isinstance(node, ast.Call):
            continue

        func = node.func

        if isinstance(func, ast.Attribute):
            name = func.attr

        elif isinstance(func, ast.Name):
            name = func.id

        else:
            continue

        if name in forbidden:
            found.append(
                (name, getattr(node, "lineno", None))
            )

    if found:
        raise RuntimeError(
            f"FAIL_CLOSED: broker-write call found: {found}"
        )


def normalize_depth_level(
    raw: Any,
) -> dict[str, Any] | None:

    if not isinstance(raw, Mapping):
        return None

    price = finite_float(
        raw.get("price")
    )

    quantity = finite_int(
        raw.get("quantity")
    )

    orders = finite_int(
        raw.get("orders")
    )

    if (
        price is None
        or quantity is None
    ):
        return None

    return {
        "price": price,
        "quantity": quantity,
        "orders": orders,
    }


def normalize_depth_side(
    raw: Any,
) -> list[dict[str, Any]]:

    if not isinstance(raw, list):
        return []

    result = []

    for level in raw[:5]:

        clean = normalize_depth_level(
            level
        )

        if clean is not None:
            result.append(clean)

    return result


def normalize_quote(
    symbol: str,
    payload: Any,
) -> dict[str, Any]:

    if not isinstance(payload, Mapping):
        return {
            "symbol": symbol,
            "valid": False,
            "reason": "PAYLOAD_NOT_MAPPING",
        }

    depth = payload.get(
        "depth",
        {}
    )

    if not isinstance(depth, Mapping):
        depth = {}

    buy = normalize_depth_side(
        depth.get("buy")
    )

    sell = normalize_depth_side(
        depth.get("sell")
    )

    ohlc = payload.get(
        "ohlc",
        {}
    )

    if not isinstance(ohlc, Mapping):
        ohlc = {}

    last_price = finite_float(
        payload.get("last_price")
    )

    valid = bool(
        last_price is not None
        and buy
        and sell
    )

    return {
        "symbol": symbol,

        "valid": valid,

        "reason": (
            ""
            if valid
            else "MISSING_LTP_OR_DEPTH"
        ),

        "instrument_token":
            finite_int(
                payload.get(
                    "instrument_token"
                )
            ),

        "last_price":
            last_price,

        "last_quantity":
            finite_int(
                payload.get(
                    "last_quantity"
                )
            ),

        "average_price":
            finite_float(
                payload.get(
                    "average_price"
                )
            ),

        "volume":
            finite_int(
                payload.get(
                    "volume"
                )
            ),

        "buy_quantity":
            finite_int(
                payload.get(
                    "buy_quantity"
                )
            ),

        "sell_quantity":
            finite_int(
                payload.get(
                    "sell_quantity"
                )
            ),

        "net_change":
            finite_float(
                payload.get(
                    "net_change"
                )
            ),

        "ohlc": {
            "open":
                finite_float(
                    ohlc.get("open")
                ),

            "high":
                finite_float(
                    ohlc.get("high")
                ),

            "low":
                finite_float(
                    ohlc.get("low")
                ),

            "close":
                finite_float(
                    ohlc.get("close")
                ),
        },

        "exchange_timestamp":
            iso_value(
                payload.get(
                    "timestamp"
                )
            ),

        "last_trade_time":
            iso_value(
                payload.get(
                    "last_trade_time"
                )
            ),

        # RAW BOOK ONLY.
        #
        # No OBI, signal, score, threshold,
        # direction or trade decision is calculated.
        "depth": {
            "buy": buy,
            "sell": sell,
        },
    }


def append_jsonl(
    path: Path,
    record: Mapping[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    encoded = (
        json.dumps(
            record,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )

    with path.open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as handle:

        handle.write(encoded)
        handle.flush()
        os.fsync(
            handle.fileno()
        )


def ensure_manifest(
    session_dir: Path,
    *,
    symbols: tuple[str, ...],
    interval_seconds: float,
    code_sha256: str,
) -> Path:

    manifest = (
        session_dir
        / "manifest.json"
    )

    intended = {
        "schema":
            "RAW_L2_COLLECTION_MANIFEST_V1",

        "research_role":
            "OBSERVATION_ONLY",

        "broker_operation":
            "quote",

        "broker_write_capability":
            False,

        "derived_signal":
            None,

        "threshold":
            None,

        "trade_decision":
            None,

        "symbols":
            list(symbols),

        "symbol_count":
            len(symbols),

        "interval_seconds":
            interval_seconds,

        "recorder_code_sha256":
            code_sha256,

        "raw_schema":
            SCHEMA,
    }

    if manifest.exists():

        existing = json.loads(
            manifest.read_text(
                encoding="utf-8"
            )
        )

        locked_fields = [
            "broker_operation",
            "broker_write_capability",
            "derived_signal",
            "threshold",
            "trade_decision",
            "symbols",
            "interval_seconds",
            "recorder_code_sha256",
            "raw_schema",
        ]

        for key in locked_fields:

            if (
                existing.get(key)
                != intended.get(key)
            ):
                raise RuntimeError(
                    f"FAIL_CLOSED: manifest mismatch "
                    f"for {key}"
                )

        return manifest

    intended[
        "created_at_utc"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    manifest.write_text(
        json.dumps(
            intended,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return manifest


def parse_clock(text: str):
    hour, minute = (
        int(x)
        for x in text.split(":")
    )

    if not (
        0 <= hour <= 23
        and 0 <= minute <= 59
    ):
        raise ValueError(
            f"Invalid clock value: {text}"
        )

    return hour, minute


def within_session(
    now_ist: datetime,
    start: str,
    end: str,
) -> bool:

    sh, sm = parse_clock(start)
    eh, em = parse_clock(end)

    current = (
        now_ist.hour,
        now_ist.minute,
    )

    return (
        current >= (sh, sm)
        and current < (eh, em)
    )


def self_test() -> None:

    source_authority_audit()

    symbols = load_authoritative_universe()

    fake = {
        "instrument_token": 123,
        "last_price": 100.25,
        "last_quantity": 12,
        "average_price": 99.80,
        "volume": 123456,
        "buy_quantity": 5000,
        "sell_quantity": 4200,
        "net_change": 1.25,
        "ohlc": {
            "open": 99.0,
            "high": 101.0,
            "low": 98.5,
            "close": 99.0,
        },
        "timestamp":
            "2026-08-25T10:00:00+05:30",

        "last_trade_time":
            "2026-08-25T09:59:59+05:30",

        "depth": {
            "buy": [
                {
                    "price": 100.20,
                    "quantity": 100,
                    "orders": 2,
                },
                {
                    "price": 100.15,
                    "quantity": 200,
                    "orders": 3,
                },
            ],
            "sell": [
                {
                    "price": 100.30,
                    "quantity": 120,
                    "orders": 2,
                },
                {
                    "price": 100.35,
                    "quantity": 250,
                    "orders": 4,
                },
            ],
        },
    }

    normalized = normalize_quote(
        "TEST",
        fake,
    )

    assert normalized["valid"] is True
    assert normalized["last_price"] == 100.25
    assert len(
        normalized["depth"]["buy"]
    ) == 2
    assert len(
        normalized["depth"]["sell"]
    ) == 2

    # Prove this RAW schema does not calculate OBI.
    assert "obi" not in normalized
    assert "score" not in normalized
    assert "decision" not in normalized

    malformed = normalize_quote(
        "BAD",
        {},
    )

    assert malformed["valid"] is False

    print("=" * 100)
    print("RAW L2 MICROSTRUCTURE RECORDER - SELF TEST")
    print("=" * 100)

    print(
        "Authoritative universe :",
        len(symbols),
        "symbols",
    )

    print(
        "Source authority audit : PASS"
    )

    print(
        "Quote normalization    : PASS"
    )

    print(
        "Malformed quote handling:",
        "PASS"
    )

    print(
        "Raw 5-level depth      : PASS"
    )

    print(
        "OBI calculation        : ABSENT"
    )

    print(
        "Trading score          : ABSENT"
    )

    print(
        "Trade decision         : ABSENT"
    )

    print(
        "Broker calls in test   : ZERO"
    )

    print(
        "Broker-write authority : NONE"
    )

    print("=" * 100)
    print("SELF TEST PASS")
    print("=" * 100)


def live_main(args) -> int:

    source_authority_audit()

    symbols = load_authoritative_universe()

    api_key = os.getenv(
        "KITE_API_KEY"
    )

    access_token = os.getenv(
        "KITE_ACCESS_TOKEN"
    )

    governor_dir = os.getenv(
        "KITE_RATE_GOVERNOR_DIR"
    )

    if not api_key:
        print(
            "[BLOCK] KITE_API_KEY missing."
        )
        return 2

    if not access_token:
        print(
            "[BLOCK] KITE_ACCESS_TOKEN missing."
        )
        return 2

    if not governor_dir:
        print(
            "[BLOCK] KITE_RATE_GOVERNOR_DIR missing."
        )
        return 2

    now_ist = datetime.now(
        timezone.utc
    ).astimezone(
        IST
    )

    if (
        not args.allow_outside_session
        and not within_session(
            now_ist,
            args.session_start,
            args.session_end,
        )
    ):
        print(
            "[BLOCK] Outside configured "
            "continuous-session window:",
            args.session_start,
            "to",
            args.session_end,
            "IST"
        )
        return 3

    from kiteconnect import KiteConnect

    from kite_request_governor import (
        QUOTE as GOV_QUOTE,
        KiteRequestGovernor,
    )

    governor = KiteRequestGovernor(
        state_dir=Path(
            governor_dir
        )
    )

    kite = KiteConnect(
        api_key=api_key
    )

    kite.set_access_token(
        access_token
    )

    script_path = Path(
        __file__
    ).resolve()

    code_sha = sha256_file(
        script_path
    )

    session_date = (
        now_ist.date().isoformat()
    )

    session_dir = (
        Path(args.output_dir)
        / session_date
    )

    manifest = ensure_manifest(
        session_dir,
        symbols=symbols,
        interval_seconds=args.interval_seconds,
        code_sha256=code_sha,
    )

    output = (
        session_dir
        / "raw_l2_snapshots.jsonl"
    )

    instruments = [
        f"NSE:{symbol}"
        for symbol in symbols
    ]

    print("=" * 100)
    print("RAW L2 MICROSTRUCTURE RECORDER")
    print("=" * 100)

    print(
        "Mode              : READ-ONLY OBSERVATION"
    )

    print(
        "Broker operation  : quote only"
    )

    print(
        "Broker write      : unavailable"
    )

    print(
        "Symbols           :",
        len(symbols)
    )

    print(
        "Interval          :",
        args.interval_seconds,
        "seconds"
    )

    print(
        "Session window    :",
        args.session_start,
        "to",
        args.session_end,
        "IST"
    )

    print(
        "Output            :",
        output
    )

    print(
        "Manifest          :",
        manifest
    )

    print(
        "Code SHA256       :",
        code_sha
    )

    print(
        "Derived OBI       : NONE"
    )

    print(
        "Trading rule      : NONE"
    )

    print(
        "Press Ctrl+C to stop this recorder only."
    )

    cycle = 0

    try:

        while True:

            cycle_started = time.monotonic()

            now_utc = datetime.now(
                timezone.utc
            )

            now_ist = now_utc.astimezone(
                IST
            )

            if (
                not args.allow_outside_session
                and not within_session(
                    now_ist,
                    args.session_start,
                    args.session_end,
                )
            ):
                print(
                    "Configured session window ended."
                )
                return 0

            governor.acquire(
                GOV_QUOTE
            )

            raw = kite.quote(
                instruments
            )

            if not isinstance(
                raw,
                Mapping,
            ):
                raise RuntimeError(
                    "FAIL_CLOSED: quote response "
                    "is not a mapping"
                )

            snapshots = {}

            missing = []

            valid_count = 0

            for symbol in symbols:

                instrument = (
                    f"NSE:{symbol}"
                )

                payload = raw.get(
                    instrument
                )

                clean = normalize_quote(
                    symbol,
                    payload,
                )

                snapshots[
                    symbol
                ] = clean

                if clean["valid"]:
                    valid_count += 1
                else:
                    missing.append(
                        symbol
                    )

            cycle += 1

            elapsed_ms = (
                time.monotonic()
                - cycle_started
            ) * 1000.0

            record = {
                "schema":
                    SCHEMA,

                "cycle":
                    cycle,

                "observed_at_utc":
                    now_utc.isoformat(),

                "observed_at_ist":
                    now_ist.isoformat(),

                "source":
                    "KITE_QUOTE_READ_ONLY",

                "broker_write_capability":
                    False,

                "symbol_count":
                    len(symbols),

                "valid_symbol_count":
                    valid_count,

                "missing_symbols":
                    missing,

                "quote_cycle_duration_ms":
                    round(
                        elapsed_ms,
                        3
                    ),

                "snapshots":
                    snapshots,
            }

            append_jsonl(
                output,
                record,
            )

            print(
                f"{now_ist.isoformat()} "
                f"cycle={cycle} "
                f"valid={valid_count}/"
                f"{len(symbols)} "
                f"duration_ms="
                f"{elapsed_ms:.1f}"
            )

            if args.once:
                return 0

            sleep_for = max(
                0.0,
                args.interval_seconds
                - (
                    time.monotonic()
                    - cycle_started
                ),
            )

            time.sleep(
                sleep_for
            )

    except KeyboardInterrupt:

        print()
        print(
            "Recorder stopped by user."
        )

        return 0


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Append-only raw L2 quote/depth "
            "observation recorder"
        )
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--output-dir",
        default=str(
            DEFAULT_OUTPUT
        ),
    )

    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=15.0,
    )

    parser.add_argument(
        "--session-start",
        default="09:15",
    )

    parser.add_argument(
        "--session-end",
        default="15:15",
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    parser.add_argument(
        "--allow-outside-session",
        action="store_true",
    )

    args = parser.parse_args()

    if args.interval_seconds < 5.0:
        raise SystemExit(
            "FAIL_CLOSED: interval must be "
            "at least 5 seconds"
        )

    if args.self_test:
        self_test()
        return 0

    return live_main(args)


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
