"""L2 DATASET CERTIFICATION TOOL V1 — 2026-08-25

Frozen raw L2 JSONL audit engine.

This certifier is READ-ONLY. It opens raw L2 snapshots and validates them
against the frozen collection contract without modifying source files.

Core scope:
  - File identity and provenance
  - JSONL structural validity
  - Session time bounds and chronology
  - Symbol universe alignment
  - Depth schema validation (without fabricating missing levels)
  - Market field consistency
  - Anomaly detection and reporting

Out of scope:
  - OBI calculation
  - Predictive signal inference
  - Trading decision logic
  - Position sizing
  - Expected-return conversion
  - PA model training

Raw source anomalies (crossed books, unusual spreads, sparse depth) are
preserved and reported, not "corrected."
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Authoritative frozen universe
AUTHORITATIVE_UNIVERSE = frozenset({
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO"
})

EXPECTED_SYMBOL_COUNT = 48
FROZEN_INTERVAL_SECONDS = 15.0
SESSION_START_IST = "09:15"
SESSION_END_IST = "15:15"  # exclusive


@dataclass
class DepthLevel:
    """A single market depth level (bid or ask)."""
    price: float | None
    quantity: int | None
    orders: int | None
    valid: bool = False
    reason: str = ""

    def is_populated(self) -> bool:
        """True if price and quantity are both valid."""
        return self.valid and self.price is not None and self.quantity is not None


@dataclass
class Snapshot:
    """A single raw L2 snapshot record."""
    symbol: str
    timestamp_utc: datetime | None
    timestamp_ist: datetime | None
    last_price: float | None
    volume: int | None
    buy_quantity: int | None
    sell_quantity: int | None
    depth_buy: list[DepthLevel]
    depth_sell: list[DepthLevel]
    raw: dict[str, Any]

    valid: bool = False
    reason: str = ""

    def best_bid(self) -> float | None:
        """Best bid price from populated levels."""
        for level in self.depth_buy:
            if level.is_populated():
                return level.price
        return None

    def best_ask(self) -> float | None:
        """Best ask price from populated levels."""
        for level in self.depth_sell:
            if level.is_populated():
                return level.price
        return None

    def is_crossed(self) -> bool:
        """True if best bid >= best ask."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return False
        return bid >= ask

    def is_locked(self) -> bool:
        """True if best bid == best ask."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return False
        return bid == ask


def sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_snapshot(symbol: str, raw: Any) -> Snapshot:
    """Parse and validate a raw L2 snapshot record.

    Does NOT modify the raw record. Preserves source anomalies.
    """
    if not isinstance(raw, dict):
        return Snapshot(
            symbol=symbol,
            timestamp_utc=None,
            timestamp_ist=None,
            last_price=None,
            volume=None,
            buy_quantity=None,
            sell_quantity=None,
            depth_buy=[],
            depth_sell=[],
            raw=raw if isinstance(raw, dict) else {},
            valid=False,
            reason="NOT_MAPPING",
        )

    # Parse timestamps
    ts_utc = None
    ts_ist = None
    ts_reason = ""

    try:
        ts_raw = raw.get("exchange_timestamp")
        if ts_raw:
            ts_utc = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            ts_ist = ts_utc.astimezone(IST)
    except (ValueError, TypeError, AttributeError) as e:
        ts_reason = f"TIMESTAMP_PARSE_FAILED: {type(e).__name__}"

    # Parse numeric fields (preserve source values exactly)
    def finite_float(v):
        try:
            x = float(v)
            return x if math.isfinite(x) else None
        except (TypeError, ValueError):
            return None

    def finite_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    last_price = finite_float(raw.get("last_price"))
    volume = finite_int(raw.get("volume"))
    buy_quantity = finite_int(raw.get("buy_quantity"))
    sell_quantity = finite_int(raw.get("sell_quantity"))

    # Parse depth (up to 5 levels per side)
    def parse_depth_side(side_raw):
        levels = []
        if not isinstance(side_raw, list):
            return levels
        for level_raw in side_raw[:5]:
            if not isinstance(level_raw, dict):
                levels.append(
                    DepthLevel(
                        price=None,
                        quantity=None,
                        orders=None,
                        valid=False,
                        reason="NOT_MAPPING",
                    )
                )
                continue
            price = finite_float(level_raw.get("price"))
            quantity = finite_int(level_raw.get("quantity"))
            orders = finite_int(level_raw.get("orders"))
            valid = price is not None and quantity is not None
            levels.append(
                DepthLevel(
                    price=price,
                    quantity=quantity,
                    orders=orders,
                    valid=valid,
                    reason="" if valid else "MISSING_PRICE_OR_QUANTITY",
                )
            )
        return levels

    depth_raw = raw.get("depth", {})
    if not isinstance(depth_raw, dict):
        depth_raw = {}

    depth_buy = parse_depth_side(depth_raw.get("buy"))
    depth_sell = parse_depth_side(depth_raw.get("sell"))

    # Overall validity: must have timestamp and at least one populated level per side
    has_valid_buy = any(level.is_populated() for level in depth_buy)
    has_valid_sell = any(level.is_populated() for level in depth_sell)
    overall_valid = ts_utc is not None and has_valid_buy and has_valid_sell
    overall_reason = (
        ""
        if overall_valid
        else f"{'NO_TIMESTAMP' if ts_utc is None else ('NO_BID_DEPTH' if not has_valid_buy else 'NO_ASK_DEPTH')}"
    )

    return Snapshot(
        symbol=symbol,
        timestamp_utc=ts_utc,
        timestamp_ist=ts_ist,
        last_price=last_price,
        volume=volume,
        buy_quantity=buy_quantity,
        sell_quantity=sell_quantity,
        depth_buy=depth_buy,
        depth_sell=depth_sell,
        raw=raw,
        valid=overall_valid,
        reason=overall_reason,
    )


@dataclass
class SessionAnomalies:
    """Detected anomalies in a session."""
    malformed_json: list[tuple[int, str]] = field(default_factory=list)
    out_of_session: list[tuple[int, str]] = field(default_factory=list)
    reversed_timestamps: list[tuple[int, int, str, str]] = field(
        default_factory=list
    )
    duplicate_timestamps: list[tuple[int, int, str]] = field(default_factory=list)
    missing_symbols: set[str] = field(default_factory=set)
    unknown_symbols: set[str] = field(default_factory=set)
    crossed_books: list[tuple[int, str]] = field(default_factory=list)
    locked_books: list[tuple[int, str]] = field(default_factory=list)
    invalid_depth_structure: list[tuple[int, str, str]] = field(
        default_factory=list
    )


@dataclass
class CertificationResult:
    """Result of certifying a raw L2 JSONL file."""
    file_path: Path
    file_sha256: str
    status: Literal["PASS", "PASS_WITH_SOURCE_FLAGS", "HOLD", "FAIL"]
    session_date: str | None
    snapshot_count: int
    valid_snapshots: int
    anomalies: SessionAnomalies
    symbol_counts: dict[str, int] = field(default_factory=dict)
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    expected_snapshots_in_session: int = 0
    observed_snapshots_in_session: int = 0
    missing_symbols: list[str] = field(default_factory=list)
    unknown_symbols: list[str] = field(default_factory=list)
    depth_statistics: dict[str, Any] = field(default_factory=dict)
    certifier_code_sha256: str = ""
    frozen_contract_sha256: str = ""
    certification_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def certify_session(
    jsonl_path: Path,
    *,
    frozen_contract_sha256: str = "",
) -> CertificationResult:
    """Audit a raw L2 JSONL file without modifying it.

    Opens read-only, parses every line, validates against frozen contract,
    and reports findings in structured form.
    """
    if not jsonl_path.exists():
        return CertificationResult(
            file_path=jsonl_path,
            file_sha256="",
            status="FAIL",
            session_date=None,
            snapshot_count=0,
            valid_snapshots=0,
            anomalies=SessionAnomalies(),
            certifier_code_sha256=sha256_file(Path(__file__)),
            frozen_contract_sha256=frozen_contract_sha256,
        )

    # Compute file hash
    file_sha256 = sha256_file(jsonl_path)

    # Extract session date from path (L2_MICROSTRUCTURE_RAW/YYYY-MM-DD/raw_l2_snapshots.jsonl)
    session_date = None
    try:
        # Expect parent directory to be YYYY-MM-DD
        session_date = jsonl_path.parent.name
        datetime.strptime(session_date, "%Y-%m-%d")
    except (ValueError, AttributeError):
        session_date = None

    result = CertificationResult(
        file_path=jsonl_path,
        file_sha256=file_sha256,
        status="PASS",
        session_date=session_date,
        snapshot_count=0,
        valid_snapshots=0,
        anomalies=SessionAnomalies(),
        certifier_code_sha256=sha256_file(Path(__file__)),
        frozen_contract_sha256=frozen_contract_sha256,
    )

    snapshots: list[Snapshot] = []
    last_timestamp: datetime | None = None

    # Read and parse JSONL
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line:
                continue

            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                result.anomalies.malformed_json.append(
                    (line_num, str(e))
                )
                result.status = "FAIL"
                continue

            # Parse snapshot
            symbol = raw.get("symbol", "UNKNOWN")
            snapshot = parse_snapshot(symbol, raw)
            snapshots.append(snapshot)
            result.snapshot_count += 1

            if snapshot.valid:
                result.valid_snapshots += 1

            # Check session bounds (IST)
            if snapshot.timestamp_ist:
                ist_time = snapshot.timestamp_ist.time()
                session_start = datetime.strptime(SESSION_START_IST, "%H:%M").time()
                session_end = datetime.strptime(SESSION_END_IST, "%H:%M").time()

                if not (session_start <= ist_time < session_end):
                    result.anomalies.out_of_session.append(
                        (line_num, snapshot.timestamp_ist.isoformat())
                    )

                # Check chronology
                if last_timestamp:
                    if snapshot.timestamp_ist < last_timestamp:
                        result.anomalies.reversed_timestamps.append(
                            (line_num, line_num - 1,
                             snapshot.timestamp_ist.isoformat(),
                             last_timestamp.isoformat())
                        )
                    elif snapshot.timestamp_ist == last_timestamp:
                        result.anomalies.duplicate_timestamps.append(
                            (line_num, line_num - 1,
                             snapshot.timestamp_ist.isoformat())
                        )

                if result.first_timestamp is None:
                    result.first_timestamp = snapshot.timestamp_ist
                result.last_timestamp = snapshot.timestamp_ist
                last_timestamp = snapshot.timestamp_ist

            # Track symbols
            if symbol not in AUTHORITATIVE_UNIVERSE:
                result.anomalies.unknown_symbols.add(symbol)
            result.symbol_counts[symbol] = result.symbol_counts.get(symbol, 0) + 1

            # Check depth schema (only for valid snapshots)
            if snapshot.valid:
                has_bid = any(level.is_populated() for level in snapshot.depth_buy)
                has_ask = any(level.is_populated() for level in snapshot.depth_sell)
                if not has_bid:
                    result.anomalies.invalid_depth_structure.append(
                        (line_num, symbol, "NO_POPULATED_BID")
                    )
                if not has_ask:
                    result.anomalies.invalid_depth_structure.append(
                        (line_num, symbol, "NO_POPULATED_ASK")
                    )

            # Check book anomalies
            if snapshot.is_crossed():
                result.anomalies.crossed_books.append(
                    (line_num, symbol)
                )
            if snapshot.is_locked():
                result.anomalies.locked_books.append(
                    (line_num, symbol)
                )

    # Determine missing symbols
    result.missing_symbols = sorted(AUTHORITATIVE_UNIVERSE - set(result.symbol_counts.keys()))
    result.unknown_symbols = sorted(result.anomalies.unknown_symbols)

    if result.missing_symbols:
        result.anomalies.missing_symbols = set(result.missing_symbols)

    # Compute expected snapshots in session
    if result.first_timestamp and result.last_timestamp:
        duration_seconds = (
            result.last_timestamp - result.first_timestamp
        ).total_seconds()
        # Plus one to include both endpoints
        result.expected_snapshots_in_session = (
            max(1, int(duration_seconds / FROZEN_INTERVAL_SECONDS) + 1) *
            EXPECTED_SYMBOL_COUNT
        )
        result.observed_snapshots_in_session = result.valid_snapshots

    # Depth statistics
    populated_buy = sum(
        1 for s in snapshots for level in s.depth_buy if level.is_populated()
    )
    populated_sell = sum(
        1 for s in snapshots for level in s.depth_sell if level.is_populated()
    )
    result.depth_statistics = {
        "populated_buy_levels": populated_buy,
        "populated_sell_levels": populated_sell,
        "avg_buy_levels_per_snapshot": (
            populated_buy / result.valid_snapshots if result.valid_snapshots else 0
        ),
        "avg_sell_levels_per_snapshot": (
            populated_sell / result.valid_snapshots if result.valid_snapshots else 0
        ),
    }

    # Determine final status
    if result.status == "FAIL":
        pass  # Already set due to malformed JSON
    elif result.anomalies.malformed_json or result.anomalies.unknown_symbols:
        result.status = "FAIL"
    elif (result.missing_symbols or
          result.anomalies.out_of_session or
          result.anomalies.reversed_timestamps or
          len(result.anomalies.invalid_depth_structure) > 0.05 * result.snapshot_count):
        result.status = "HOLD"
    elif (result.anomalies.crossed_books or
          result.anomalies.locked_books or
          result.anomalies.duplicate_timestamps):
        result.status = "PASS_WITH_SOURCE_FLAGS"

    return result


def to_dict(obj: Any) -> Any:
    """Convert dataclass to dict recursively."""
    if hasattr(obj, "__dataclass_fields__"):
        return {
            k: to_dict(getattr(obj, k))
            for k in obj.__dataclass_fields__
        }
    elif isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    elif isinstance(obj, (set, frozenset)):
        return sorted(list(obj))
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, Path):
        return str(obj)
    else:
        return obj


def write_certification(
    result: CertificationResult,
    output_dir: Path,
) -> None:
    """Write certification results to separate artifact files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON report
    json_path = output_dir / "certification.json"
    json_path.write_text(
        json.dumps(to_dict(result), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # Markdown summary
    md_path = output_dir / "certification.md"
    md_lines = [
        f"# L2 Dataset Certification — {result.session_date or 'Unknown'}",
        "",
        f"**Status:** `{result.status}`",
        f"**File:** `{result.file_path.name}`",
        f"**SHA256:** `{result.file_sha256[:16]}...`",
        f"**Snapshots:** {result.valid_snapshots}/{result.snapshot_count}",
        "",
        "## Findings",
        "",
        f"- Malformed JSON: {len(result.anomalies.malformed_json)}",
        f"- Out-of-session: {len(result.anomalies.out_of_session)}",
        f"- Reversed timestamps: {len(result.anomalies.reversed_timestamps)}",
        f"- Duplicate timestamps: {len(result.anomalies.duplicate_timestamps)}",
        f"- Missing symbols: {len(result.missing_symbols)}",
        f"- Unknown symbols: {len(result.unknown_symbols)}",
        f"- Crossed books: {len(result.anomalies.crossed_books)}",
        f"- Locked books: {len(result.anomalies.locked_books)}",
        "",
        "## Symbols",
        "",
        f"- Expected: {EXPECTED_SYMBOL_COUNT}",
        f"- Observed: {len(result.symbol_counts)}",
        f"- Missing: {', '.join(result.missing_symbols) if result.missing_symbols else 'None'}",
        "",
        "## Raw Source Flags",
        "",
        "This certification preserves source anomalies (crossed books, sparse",
        "depth, unusual spreads) as part of the raw audit trail.",
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    # SHA256 of outputs
    json_hash = sha256_file(json_path)
    json_path.with_suffix(".json.sha256").write_text(
        f"{json_hash}  {json_path.name}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: python l2_dataset_certifier_v1.py <path/to/raw_l2_snapshots.jsonl>"
        )
        sys.exit(1)

    jsonl_file = Path(sys.argv[1])
    frozen_contract_hash = (
        sys.argv[2] if len(sys.argv) > 2
        else "daf7d9aa9e8c5cababf618b3f4c14e39fa211eab16c1cb9d81e9af5a1004a303"
    )

    result = certify_session(jsonl_file, frozen_contract_sha256=frozen_contract_hash)

    # Write outputs to a separate certification directory
    output_base = jsonl_file.parent.parent / "L2_MICROSTRUCTURE_CERTIFICATION"
    output_dir = output_base / (jsonl_file.parent.name)
    write_certification(result, output_dir)

    print(f"Certification: {result.status}")
    print(f"Report: {output_dir / 'certification.json'}")
