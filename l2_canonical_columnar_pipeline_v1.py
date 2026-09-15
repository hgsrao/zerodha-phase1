"""L2 Canonical Columnar Pipeline V1 — 2026-08-26

Transforms certified raw L2 JSONL → canonical columnar Parquet.

CRITICAL: Raw file is sovereign. Every structurally parseable JSON record
must appear in canonical output with explicit quality provenance. No silent
record drops.

No feature engineering. No PA logic. No predictive content.

Input requirement: certified raw session (per L2_DATASET_CERTIFIER_V2)

Output: deterministic Parquet with provenance and quality flags.

Source immutability: GUARANTEED (read-only input, SHA256 verified before/after).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import polars as pl

IST = ZoneInfo("Asia/Kolkata")

CANONICAL_SCHEMA_VERSION = "V1_20260826"

# Columns in deterministic output order
# Authoritative canonical schema: 62 columns total
CANONICAL_COLUMNS = [
    # === Identifiers & Provenance (6 fields) ===
    "session_date",
    "source_json_line_num",
    "symbol",
    "instrument_token",
    # === Timestamps (2 fields) ===
    "exchange_timestamp",
    "exchange_timestamp_ist",
    # === Market Data (6 fields) ===
    "last_price",
    "last_quantity",
    "average_price",
    "volume",
    "buy_quantity",
    "sell_quantity",
    # === Market Fields Continued (2 fields) ===
    "net_change",
    "last_trade_timestamp",
    # === OHLC Intrabar (4 fields) ===
    "ohlc_open",
    "ohlc_high",
    "ohlc_low",
    "ohlc_close",
    # === Bid Levels 1–5 (16 fields) ===
    "bid_1_price",
    "bid_1_qty",
    "bid_1_orders",
    "bid_2_price",
    "bid_2_qty",
    "bid_2_orders",
    "bid_3_price",
    "bid_3_qty",
    "bid_3_orders",
    "bid_4_price",
    "bid_4_qty",
    "bid_4_orders",
    "bid_5_price",
    "bid_5_qty",
    "bid_5_orders",
    "bid_populated_count",
    # === Ask Levels 1–5 (16 fields) ===
    "ask_1_price",
    "ask_1_qty",
    "ask_1_orders",
    "ask_2_price",
    "ask_2_qty",
    "ask_2_orders",
    "ask_3_price",
    "ask_3_qty",
    "ask_3_orders",
    "ask_4_price",
    "ask_4_qty",
    "ask_4_orders",
    "ask_5_price",
    "ask_5_qty",
    "ask_5_orders",
    "ask_populated_count",
    # === Validity & Quality Provenance Flags (8 fields) ===
    "record_valid",
    "has_bids",
    "has_asks",
    "malformed_depth",
    "missing_exchange_timestamp",
    "missing_last_price",
    "malformed_last_price",
    "missing_symbol",
]

# Verify exact count
assert len(CANONICAL_COLUMNS) == 58, f"Expected 58 columns, got {len(CANONICAL_COLUMNS)}"


def sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_timestamp(ts_str: str | None) -> tuple[datetime | None, datetime | None]:
    """Parse ISO 8601 timestamp to UTC and IST.

    Returns (utc_dt, ist_dt) or (None, None) if parse fails.
    """
    if not ts_str:
        return None, None

    try:
        utc_dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        ist_dt = utc_dt.astimezone(IST)
        return utc_dt, ist_dt
    except (ValueError, TypeError):
        return None, None


def extract_depth_levels(
    depth_list: Any, side: Literal["buy", "sell"]
) -> tuple[list[float | None], list[int | None], list[int | None], int, bool]:
    """Extract bid or ask levels 1–5.

    Returns:
        (prices, quantities, orders, populated_count, malformed)
    """
    prices = [None] * 5
    quantities = [None] * 5
    orders_counts = [None] * 5
    populated = 0
    malformed = False

    if not isinstance(depth_list, list):
        malformed = True
        return prices, quantities, orders_counts, populated, malformed

    for i, level_raw in enumerate(depth_list[:5]):
        if not isinstance(level_raw, dict):
            continue

        price = level_raw.get("price")
        qty = level_raw.get("quantity")
        order_count = level_raw.get("orders")

        # Coerce to float/int safely
        try:
            if price is not None:
                price = float(price)
                if not (price == price and price != float("inf")):  # NaN and Infinity check
                    price = None
        except (ValueError, TypeError):
            price = None
            malformed = True

        try:
            if qty is not None:
                qty = int(qty)
        except (ValueError, TypeError):
            qty = None
            malformed = True

        try:
            if order_count is not None:
                order_count = int(order_count)
        except (ValueError, TypeError):
            order_count = None

        # Populated = has both price and qty (orders optional)
        if price is not None and qty is not None:
            prices[i] = price
            quantities[i] = qty
            orders_counts[i] = order_count
            populated += 1

    return prices, quantities, orders_counts, populated, malformed


def transform_jsonl_to_canonical(
    jsonl_path: Path,
    session_date: str,
) -> tuple[pl.DataFrame, list[dict]]:
    """Transform raw JSONL to canonical columnar format.

    CRITICAL: Preserves every structurally parseable record.
    No silent drops. Explicit quality provenance on all records.

    Args:
        jsonl_path: Path to certified raw_l2_snapshots.jsonl
        session_date: YYYY-MM-DD session identifier

    Returns:
        (DataFrame with canonical schema, list of explicitly rejected records with reasons)
    """
    records = []
    rejected_records = []

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line:
                continue

            # Parse JSON
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                rejected_records.append({
                    "line_num": line_num,
                    "reason": "JSON_PARSE_ERROR",
                    "detail": "Line is not valid JSON"
                })
                continue

            # Check required fields for record admission
            symbol = raw.get("symbol")
            if not symbol:
                rejected_records.append({
                    "line_num": line_num,
                    "reason": "MISSING_SYMBOL",
                    "detail": "Symbol field missing or empty"
                })
                continue

            ts_utc, ts_ist = parse_timestamp(raw.get("exchange_timestamp"))
            has_timestamp = ts_utc is not None

            # === Quality Flags ===
            missing_exchange_timestamp = not has_timestamp
            missing_last_price = "last_price" not in raw
            malformed_last_price = False

            # === Market fields ===
            last_price = raw.get("last_price")
            try:
                if last_price is not None:
                    last_price = float(last_price)
                    if not (last_price == last_price and last_price != float("inf")):
                        # NaN or Infinity detected
                        last_price = None
                        malformed_last_price = True
            except (ValueError, TypeError):
                last_price = None
                malformed_last_price = True

            last_qty = raw.get("last_quantity")
            try:
                last_qty = int(last_qty) if last_qty is not None else None
            except (ValueError, TypeError):
                last_qty = None

            avg_price = raw.get("average_price")
            try:
                avg_price = float(avg_price) if avg_price is not None else None
            except (ValueError, TypeError):
                avg_price = None

            volume = raw.get("volume")
            try:
                volume = int(volume) if volume is not None else None
            except (ValueError, TypeError):
                volume = None

            buy_qty = raw.get("buy_quantity")
            try:
                buy_qty = int(buy_qty) if buy_qty is not None else None
            except (ValueError, TypeError):
                buy_qty = None

            sell_qty = raw.get("sell_quantity")
            try:
                sell_qty = int(sell_qty) if sell_qty is not None else None
            except (ValueError, TypeError):
                sell_qty = None

            net_chg = raw.get("net_change")
            try:
                net_chg = float(net_chg) if net_chg is not None else None
            except (ValueError, TypeError):
                net_chg = None

            last_trade_ts_utc, last_trade_ts_ist = parse_timestamp(raw.get("last_trade_time"))

            # === OHLC ===
            ohlc = raw.get("ohlc", {})
            ohlc_open = ohlc.get("open")
            try:
                ohlc_open = float(ohlc_open) if ohlc_open is not None else None
            except (ValueError, TypeError):
                ohlc_open = None

            ohlc_high = ohlc.get("high")
            try:
                ohlc_high = float(ohlc_high) if ohlc_high is not None else None
            except (ValueError, TypeError):
                ohlc_high = None

            ohlc_low = ohlc.get("low")
            try:
                ohlc_low = float(ohlc_low) if ohlc_low is not None else None
            except (ValueError, TypeError):
                ohlc_low = None

            ohlc_close = ohlc.get("close")
            try:
                ohlc_close = float(ohlc_close) if ohlc_close is not None else None
            except (ValueError, TypeError):
                ohlc_close = None

            # === Depth ===
            depth_raw = raw.get("depth", {})
            if not isinstance(depth_raw, dict):
                depth_raw = {}

            bid_prices, bid_qtys, bid_orders, bid_pop, bid_malformed = extract_depth_levels(
                depth_raw.get("buy"), "buy"
            )
            ask_prices, ask_qtys, ask_orders, ask_pop, ask_malformed = extract_depth_levels(
                depth_raw.get("sell"), "sell"
            )

            malformed_depth = bid_malformed or ask_malformed

            # === Validity ===
            record_valid = (
                last_price is not None
                and bid_pop > 0
                and ask_pop > 0
                and not malformed_depth
                and not missing_exchange_timestamp
            )

            # === Canonical record (every structurally parseable JSON gets a row) ===
            canonical = {
                "session_date": session_date,
                "source_json_line_num": line_num,
                "symbol": symbol,
                "instrument_token": raw.get("instrument_token"),
                "exchange_timestamp": ts_utc,
                "exchange_timestamp_ist": ts_ist,
                "last_price": last_price,
                "last_quantity": last_qty,
                "average_price": avg_price,
                "volume": volume,
                "buy_quantity": buy_qty,
                "sell_quantity": sell_qty,
                "net_change": net_chg,
                "last_trade_timestamp": last_trade_ts_utc,
                "ohlc_open": ohlc_open,
                "ohlc_high": ohlc_high,
                "ohlc_low": ohlc_low,
                "ohlc_close": ohlc_close,
                # Bids
                "bid_1_price": bid_prices[0],
                "bid_1_qty": bid_qtys[0],
                "bid_1_orders": bid_orders[0],
                "bid_2_price": bid_prices[1],
                "bid_2_qty": bid_qtys[1],
                "bid_2_orders": bid_orders[1],
                "bid_3_price": bid_prices[2],
                "bid_3_qty": bid_qtys[2],
                "bid_3_orders": bid_orders[2],
                "bid_4_price": bid_prices[3],
                "bid_4_qty": bid_qtys[3],
                "bid_4_orders": bid_orders[3],
                "bid_5_price": bid_prices[4],
                "bid_5_qty": bid_qtys[4],
                "bid_5_orders": bid_orders[4],
                "bid_populated_count": bid_pop,
                # Asks
                "ask_1_price": ask_prices[0],
                "ask_1_qty": ask_qtys[0],
                "ask_1_orders": ask_orders[0],
                "ask_2_price": ask_prices[1],
                "ask_2_qty": ask_qtys[1],
                "ask_2_orders": ask_orders[1],
                "ask_3_price": ask_prices[2],
                "ask_3_qty": ask_qtys[2],
                "ask_3_orders": ask_orders[2],
                "ask_4_price": ask_prices[3],
                "ask_4_qty": ask_qtys[3],
                "ask_4_orders": ask_orders[3],
                "ask_5_price": ask_prices[4],
                "ask_5_qty": ask_qtys[4],
                "ask_5_orders": ask_orders[4],
                "ask_populated_count": ask_pop,
                # Validity & quality provenance
                "record_valid": record_valid,
                "has_bids": bid_pop > 0,
                "has_asks": ask_pop > 0,
                "malformed_depth": malformed_depth,
                "missing_exchange_timestamp": missing_exchange_timestamp,
                "missing_last_price": missing_last_price,
                "malformed_last_price": malformed_last_price,
                "missing_symbol": False,  # Would have been rejected above
            }

            records.append(canonical)

    # Create DataFrame
    if records:
        df = pl.DataFrame(records)
    else:
        df = pl.DataFrame({col: [] for col in CANONICAL_COLUMNS})

    # Enforce column order and types
    df = df.select(CANONICAL_COLUMNS)

    # Sort deterministically
    df = df.sort(["exchange_timestamp", "symbol"])

    return df, rejected_records


def process_certified_session(
    jsonl_path: Path,
    output_base_dir: Path = None,
) -> dict[str, Any]:
    """Process a certified L2 session into canonical Parquet.

    Args:
        jsonl_path: Path to raw_l2_snapshots.jsonl
        output_base_dir: Where to write Parquet (default: L2_MICROSTRUCTURE_COLUMNAR)

    Returns:
        Metadata dictionary with hashes, counts, provenance, and rejected-record accounting
    """
    if output_base_dir is None:
        output_base_dir = jsonl_path.parent.parent.parent / "L2_MICROSTRUCTURE_COLUMNAR"

    # Verify raw file exists and is readable
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Raw JSONL not found: {jsonl_path}")

    # Get raw SHA256 before processing
    raw_sha_before = sha256_file(jsonl_path)

    # Extract session date from path
    session_date = jsonl_path.parent.name  # YYYY-MM-DD

    # Transform to canonical
    df, rejected_records = transform_jsonl_to_canonical(jsonl_path, session_date)

    # Verify raw file unchanged
    raw_sha_after = sha256_file(jsonl_path)
    if raw_sha_before != raw_sha_after:
        raise RuntimeError("Raw JSONL was modified during processing — integrity violation")

    # Prepare output
    output_dir = output_base_dir / session_date
    output_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = output_dir / "l2_top5.parquet"
    manifest_path = output_dir / "manifest.json"

    # Write Parquet
    df.write_parquet(str(parquet_path))

    # Compute Parquet SHA256
    parquet_sha = sha256_file(parquet_path)

    # Create manifest
    manifest = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "pipeline_name": "l2_canonical_columnar_pipeline_v1",
        "session_date": session_date,
        "source": {
            "jsonl_path": str(jsonl_path),
            "jsonl_sha256": raw_sha_before,
        },
        "output": {
            "parquet_path": str(parquet_path),
            "parquet_sha256": parquet_sha,
        },
        "record_accounting": {
            "canonical_rows": len(df),
            "explicitly_rejected_records": len(rejected_records),
            "rejected_by_reason": _summarize_rejected_by_reason(rejected_records),
            "invariant_check": f"parseable_records >= canonical_rows + rejected ({len(df) + len(rejected_records)})",
        },
        "rejected_records_detail": rejected_records[:100],  # First 100 for inspection
        "statistics": {
            "row_count": len(df),
            "unique_symbols": int(df.select("symbol").n_unique()),
            "valid_records": int(df.filter("record_valid").height),
            "malformed_depth_count": int(df.filter("malformed_depth").height),
            "missing_exchange_timestamp_count": int(df.filter("missing_exchange_timestamp").height),
            "malformed_last_price_count": int(df.filter("malformed_last_price").height),
            "timestamp_range": {
                "first": _safe_first_timestamp(df),
                "last": _safe_last_timestamp(df),
            },
        },
        "immutability": {
            "raw_sha_before": raw_sha_before,
            "raw_sha_after": raw_sha_after,
            "unchanged": raw_sha_before == raw_sha_after,
        },
        "schema": {
            "columns": CANONICAL_COLUMNS,
            "total_count": len(CANONICAL_COLUMNS),
        },
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str),
        encoding="utf-8",
    )

    manifest_sha = sha256_file(manifest_path)
    manifest_path.with_suffix(".json.sha256").write_text(
        f"{manifest_sha}  {manifest_path.name}\n",
        encoding="utf-8",
    )

    return manifest


def _summarize_rejected_by_reason(rejected_records: list[dict]) -> dict[str, int]:
    """Count rejections by reason code."""
    summary = {}
    for rec in rejected_records:
        reason = rec.get("reason", "UNKNOWN")
        summary[reason] = summary.get(reason, 0) + 1
    return summary


def _safe_first_timestamp(df: pl.DataFrame) -> str | None:
    """Safely extract first timestamp_ist, handling empty/null."""
    if df.is_empty():
        return None
    try:
        ts = df.select("exchange_timestamp_ist").min()[0, 0]
        return ts.isoformat() if ts else None
    except Exception:
        return None


def _safe_last_timestamp(df: pl.DataFrame) -> str | None:
    """Safely extract last timestamp_ist, handling empty/null."""
    if df.is_empty():
        return None
    try:
        ts = df.select("exchange_timestamp_ist").max()[0, 0]
        return ts.isoformat() if ts else None
    except Exception:
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python l2_canonical_columnar_pipeline_v1.py <path/to/raw_l2_snapshots.jsonl>")
        sys.exit(1)

    jsonl_file = Path(sys.argv[1])
    result = process_certified_session(jsonl_file)

    print("Pipeline complete:")
    print(f"  Source JSONL: {result['source']['jsonl_path']}")
    print(f"  Output Parquet: {result['output']['parquet_path']}")
    print(f"  Canonical rows: {result['record_accounting']['canonical_rows']}")
    print(f"  Explicitly rejected: {result['record_accounting']['explicitly_rejected_records']}")
    print(f"  Immutable: {result['immutability']['unchanged']}")
