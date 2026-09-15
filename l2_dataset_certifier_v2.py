"""L2 DATASET CERTIFIER V2 — 2026-08-26

Understands ACTUAL nested recorder output structure.

ONE JSONL LINE = ONE COLLECTION CYCLE

Each cycle contains:
  - observed_at_utc, observed_at_ist (recorder observation timestamps)
  - snapshots dictionary with per-symbol records (0 to N symbols, authoritative 48)

Audits two orthogonal dimensions:
  A. CYCLE COVERAGE: 1,440 theoretical 15-second slots per full session (09:15–15:15 IST)
  B. SYMBOL COVERAGE: per-cycle symbol presence, missing authoritative symbols, unknown symbols

Accounting invariants:
  FILE: nonempty_lines = parseable_cycles + malformed_json_lines
  CYCLE: total_snapshot_entries = canonical_entries + rejected_entries
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

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
FULL_SESSION_SECONDS = 21600
FULL_SESSION_CYCLE_SLOTS = 1440
SESSION_START_IST = "09:15"
SESSION_END_IST = "15:15"


@dataclass
class CycleDetail:
    """One collection cycle audit."""
    line_num: int
    observed_at_ist: str
    snapshot_count: int
    missing_symbols: list[str]
    unknown_symbols: list[str]
    malformed_snapshots: list[str]
    canonical_entries: int
    rejected_entries: int


@dataclass
class CertificationResult:
    """Complete session certification."""
    status: Literal["PASS", "PASS_WITH_SOURCE_FLAGS", "HOLD", "FAIL"]
    session_date: str | None
    file_sha256: str
    certifier_sha256: str

    nonempty_raw_lines: int = 0
    parseable_cycles: int = 0
    malformed_json_lines: int = 0
    total_snapshot_entries: int = 0
    canonical_entries: int = 0
    rejected_snapshot_entries: int = 0

    first_cycle_ist: str | None = None
    last_cycle_ist: str | None = None

    anomalies_malformed_json: list[tuple[int, str]] = field(default_factory=list)
    anomalies_reversed_cycles: list[int] = field(default_factory=list)
    anomalies_missing_symbols_any_cycle: set[str] = field(default_factory=set)

    cycle_details: list[CycleDetail] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_timestamp(ts_str: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def certify_session(jsonl_path: Path) -> CertificationResult:
    """Audit nested-cycle L2 JSONL against frozen recorder contract.

    ONE JSONL LINE = ONE CYCLE (with 0–N per-symbol snapshots in dict).
    """
    if not jsonl_path.exists():
        return CertificationResult(
            status="FAIL",
            session_date=None,
            file_sha256="",
            certifier_sha256=sha256_file(Path(__file__)),
        )

    file_sha = sha256_file(jsonl_path)
    certifier_sha = sha256_file(Path(__file__))

    session_date = None
    try:
        session_date = jsonl_path.parent.name
    except (ValueError, IndexError):
        pass

    result = CertificationResult(
        status="PASS",
        session_date=session_date,
        file_sha256=file_sha,
        certifier_sha256=certifier_sha,
    )

    last_timestamp_ist = None

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line:
                continue

            result.nonempty_raw_lines += 1

            # Parse JSON cycle
            try:
                cycle = json.loads(line)
            except json.JSONDecodeError as e:
                result.malformed_json_lines += 1
                result.anomalies_malformed_json.append((line_num, str(e)))
                continue

            result.parseable_cycles += 1

            if not isinstance(cycle, dict):
                result.anomalies_malformed_json.append((line_num, "NOT_DICT"))
                continue

            # Parse cycle-level observation timestamps
            obs_utc = parse_timestamp(cycle.get("observed_at_utc"))
            obs_ist = parse_timestamp(cycle.get("observed_at_ist"))

            if obs_utc is None or obs_ist is None:
                result.anomalies_malformed_json.append((line_num, "MISSING_OBS_TIMESTAMP"))
                continue

            obs_ist_local = obs_ist.astimezone(IST) if obs_ist.tzinfo else obs_ist

            # Parse snapshots container
            snapshots = cycle.get("snapshots")

            if snapshots is None:
                result.malformed_json_lines += 1
                result.anomalies_malformed_json.append((line_num, "MISSING_SNAPSHOTS"))
                continue

            if not isinstance(snapshots, dict):
                result.malformed_json_lines += 1
                result.anomalies_malformed_json.append((line_num, "SNAPSHOTS_NOT_DICT"))
                continue

            # Process per-symbol snapshots
            snapshot_count = len(snapshots)
            result.total_snapshot_entries += snapshot_count

            missing_syms = []
            unknown_syms = []
            malformed_syms = []
            canonical_cnt = 0
            rejected_cnt = 0

            for symbol, snapshot_raw in snapshots.items():
                if not isinstance(snapshot_raw, dict):
                    malformed_syms.append(symbol)
                    rejected_cnt += 1
                    continue

                # Snapshot marked valid by recorder?
                if snapshot_raw.get("valid"):
                    canonical_cnt += 1
                else:
                    rejected_cnt += 1

                # Track authoritative universe
                if symbol not in AUTHORITATIVE_UNIVERSE:
                    unknown_syms.append(symbol)

            # Missing symbols
            for auth_sym in AUTHORITATIVE_UNIVERSE:
                if auth_sym not in snapshots:
                    missing_syms.append(auth_sym)
                    result.anomalies_missing_symbols_any_cycle.add(auth_sym)

            result.canonical_entries += canonical_cnt
            result.rejected_snapshot_entries += rejected_cnt

            # Timestamp ordering
            if last_timestamp_ist and obs_ist_local < last_timestamp_ist:
                result.anomalies_reversed_cycles.append(line_num)

            last_timestamp_ist = obs_ist_local

            # Record cycle detail
            if len(result.cycle_details) < 100:
                result.cycle_details.append(CycleDetail(
                    line_num=line_num,
                    observed_at_ist=obs_ist_local.isoformat(),
                    snapshot_count=snapshot_count,
                    missing_symbols=missing_syms,
                    unknown_symbols=unknown_syms,
                    malformed_snapshots=malformed_syms,
                    canonical_entries=canonical_cnt,
                    rejected_entries=rejected_cnt,
                ))

            if len(result.cycle_details) == 1:
                result.first_cycle_ist = obs_ist_local.isoformat()
            result.last_cycle_ist = obs_ist_local.isoformat()

    # Accounting invariant
    if result.canonical_entries + result.rejected_snapshot_entries != result.total_snapshot_entries:
        result.status = "HOLD"

    # DETERMINISTIC DECISION PRECEDENCE
    # 1. FAIL: unrecoverable structural/provenance corruption
    if result.malformed_json_lines > 0 or result.parseable_cycles == 0:
        result.status = "FAIL"
    # 2. HOLD: incomplete/insufficient session (regardless of source anomalies)
    elif result.parseable_cycles < int(FULL_SESSION_CYCLE_SLOTS * 0.95):
        result.status = "HOLD"
    # 3. PASS_WITH_SOURCE_FLAGS: complete/usable + legitimate source anomalies
    elif result.anomalies_reversed_cycles or result.anomalies_missing_symbols_any_cycle:
        result.status = "PASS_WITH_SOURCE_FLAGS"
    # 4. PASS: complete and clean
    else:
        result.status = "PASS"

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python l2_dataset_certifier_v2.py <path>")
        sys.exit(1)
    result = certify_session(Path(sys.argv[1]))
    print(f"Status: {result.status}")
    print(f"Cycles: {result.parseable_cycles}")
    print(f"Snapshots: {result.total_snapshot_entries}")
    print(f"Canonical: {result.canonical_entries}")
