"""P01D S1.0 — dataset acceptance-gate auditor.

Read-only. Audits a set of "research_ready" OHLCV CSV files for exactly
the defect classes S1.0's Dataset Contract must record before any
contestant touches this data: per symbol and date - expected session ->
expected bars -> observed bars -> duplicates -> missing bars -> malformed
timestamps -> unexpected bars outside the NSE session - plus each file's
own correction_audit.csv (OHLC-correction provenance) and a SHA-256 hash
per file (the "immutable dataset hash" half of the RAW -> preparation ->
RESEARCH_READY -> hash chain).

Session-*date* calendar derivation, and why it is NOT a hardcoded holiday
list: this repository has exactly one independently verified NSE holiday
year (institutional_engine_v34_p01d_candidate.py's NSE_HOLIDAYS_2026);
2023-2025 do not exist here, and no fully reliable, independently
verifiable holiday list for those years could be obtained (a live search
and a direct fetch of Zerodha's own holiday-calendar page both came back
2026-only) without risking a transcription error baked silently into
"expected session" ground truth the whole audit would then depend on.
Instead, the trading-*day* calendar is derived from cross-symbol
consensus within the dataset itself: a weekday on which most symbols
have data is treated as an expected session; a weekday almost entirely
absent across the universe is treated as a holiday/non-session, not a
defect. A weekday present for only a handful of symbols while absent for
the rest is exactly the shape of a genuine, symbol-specific data gap -
that is what this auditor exists to surface, not paper over.

Session *hours* (09:15-15:30 IST) are, by contrast, asserted directly -
that is stable NSE market structure, not something that changes year to
year the way holiday dates do, so there is no equivalent risk in stating
it outright.

No forward-filling, no synthesis, no "fixing" of missing data anywhere in
this module - a missing observation is reported, never manufactured.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

IST_SESSION_OPEN = time(9, 15)
IST_SESSION_CLOSE = time(15, 30)
IST_OFFSET = timedelta(hours=5, minutes=30)

# A weekday date counts as an "expected session" if at least this
# fraction of the symbol universe has at least one bar on it. Not a
# frozen threshold - the report below prints the raw distribution so
# S1.0 can pick the real number with actual data in hand, per this
# session's own "deliberately unresolved until the audit tells us"
# principle. 0.5 here is only the working cut used to separate "probably
# a holiday" from "probably a per-symbol gap" for reporting purposes.
CONSENSUS_THRESHOLD = 0.5


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_bar_times(interval_minutes: int) -> List[time]:
    """Canonical intraday bar-start times for a full NSE session at the
    given interval - e.g. 25 bars at 15 minutes (09:15..15:15), 7 bars
    at 60 minutes (09:15..15:15, the last covering the shortened
    15:15-15:30 close)."""
    times: List[time] = []
    cursor = datetime.combine(date(2000, 1, 1), IST_SESSION_OPEN)
    end = datetime.combine(date(2000, 1, 1), IST_SESSION_CLOSE)
    while cursor < end:
        times.append(cursor.time())
        cursor += timedelta(minutes=interval_minutes)
    return times


@dataclass
class FileAudit:
    symbol: str
    path: str
    sha256: str
    total_rows: int = 0
    malformed_timestamp_rows: int = 0
    duplicate_timestamp_rows: int = 0
    weekend_rows: int = 0
    off_session_time_rows: int = 0
    observed_dates: Set[date] = field(default_factory=set)
    bars_by_date: Dict[date, List[time]] = field(default_factory=dict)
    correction_count: int = 0


def _scan_file(path: Path, symbol: str) -> FileAudit:
    audit = FileAudit(symbol=symbol, path=str(path), sha256=_sha256_file(path))
    seen_timestamps: Set[str] = set()
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            audit.total_rows += 1
            raw_ts = (row.get("timestamp") or "").strip()
            try:
                ts = datetime.fromisoformat(raw_ts)
            except ValueError:
                audit.malformed_timestamp_rows += 1
                continue
            if ts.utcoffset() != IST_OFFSET:
                audit.malformed_timestamp_rows += 1
                continue

            key = ts.isoformat()
            if key in seen_timestamps:
                audit.duplicate_timestamp_rows += 1
            seen_timestamps.add(key)

            d = ts.date()
            t = ts.time()
            audit.observed_dates.add(d)
            audit.bars_by_date.setdefault(d, []).append(t)

            if d.weekday() >= 5:
                audit.weekend_rows += 1
            elif not (IST_SESSION_OPEN <= t < IST_SESSION_CLOSE):
                audit.off_session_time_rows += 1
    return audit


def _load_correction_counts(directory: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    audit_path = directory / "correction_audit.csv"
    if not audit_path.exists():
        return counts
    with audit_path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            filename = row.get("file", "")
            counts[filename] = counts.get(filename, 0) + 1
    return counts


@dataclass
class DatasetAuditReport:
    label: str
    interval_minutes: int
    files: List[FileAudit]
    consensus_dates: Set[date]
    per_symbol_missing: Dict[str, List[Tuple[date, int, int]]]  # symbol -> [(date, expected, observed)]
    date_consensus_distribution: Dict[date, int]  # date -> count of symbols with data
    universe_size: int


def audit_directory(directory: Path, *, interval_minutes: int, label: str) -> DatasetAuditReport:
    """Audits every `NSE_<SYMBOL>_*.csv` file in `directory` (skips
    correction_audit.csv and anything else). Pure read - never mutates a
    data file."""
    correction_counts = _load_correction_counts(directory)
    files: List[FileAudit] = []
    for path in sorted(directory.glob("NSE_*.csv")):
        # "NSE_RELIANCE_15minute_2023-08-14_2026-08-13.csv" -> "RELIANCE"
        # ("NSE_NIFTY 50_..." -> "NIFTY 50", handled the same way).
        stem_parts = path.stem.split("_")
        symbol = stem_parts[1] if len(stem_parts) > 1 else path.stem
        audit = _scan_file(path, symbol)
        audit.correction_count = correction_counts.get(path.name, 0)
        files.append(audit)

    universe_size = len(files)
    date_consensus_distribution: Dict[date, int] = {}
    for f in files:
        for d in f.observed_dates:
            date_consensus_distribution[d] = date_consensus_distribution.get(d, 0) + 1

    consensus_dates = {
        d for d, count in date_consensus_distribution.items()
        if d.weekday() < 5 and universe_size > 0 and (count / universe_size) >= CONSENSUS_THRESHOLD
    }

    expected_times = _expected_bar_times(interval_minutes)
    per_symbol_missing: Dict[str, List[Tuple[date, int, int]]] = {}
    for f in files:
        missing_for_symbol = []
        for d in sorted(consensus_dates):
            observed = f.bars_by_date.get(d, [])
            expected_count = len(expected_times)
            observed_count = len(observed)
            if observed_count != expected_count:
                missing_for_symbol.append((d, expected_count, observed_count))
        if missing_for_symbol:
            per_symbol_missing[f.symbol] = missing_for_symbol

    return DatasetAuditReport(
        label=label, interval_minutes=interval_minutes, files=files,
        consensus_dates=consensus_dates, per_symbol_missing=per_symbol_missing,
        date_consensus_distribution=date_consensus_distribution, universe_size=universe_size,
    )


def print_report(report: DatasetAuditReport) -> None:
    print(f"\n{'=' * 70}\n{report.label}  (interval={report.interval_minutes}min)\n{'=' * 70}")
    print(f"Universe size: {report.universe_size} symbols")
    print(f"Consensus session dates (>= {CONSENSUS_THRESHOLD:.0%} of universe): {len(report.consensus_dates)}")
    if report.consensus_dates:
        print(f"  Range: {min(report.consensus_dates)} .. {max(report.consensus_dates)}")

    print(f"\n{'Symbol':<12}{'Rows':>8}{'Dates':>8}{'Malformed':>11}{'Dupes':>8}{'Weekend':>9}{'OffSess':>9}{'Correct.':>10}{'SHA256(12)':>14}")
    for f in report.files:
        print(f"{f.symbol:<12}{f.total_rows:>8}{len(f.observed_dates):>8}{f.malformed_timestamp_rows:>11}"
              f"{f.duplicate_timestamp_rows:>8}{f.weekend_rows:>9}{f.off_session_time_rows:>9}"
              f"{f.correction_count:>10}{f.sha256[:12]:>14}")

    print(f"\nDate-consensus distribution (how many symbols have data, for weekday dates NOT meeting the {CONSENSUS_THRESHOLD:.0%} threshold):")
    below_threshold = {
        d: c for d, c in report.date_consensus_distribution.items()
        if d.weekday() < 5 and (c / report.universe_size) < CONSENSUS_THRESHOLD
    }
    if not below_threshold:
        print("  (none - every weekday date is either full-universe-present or full-universe-absent)")
    else:
        for d, c in sorted(below_threshold.items())[:30]:
            print(f"  {d}: {c}/{report.universe_size} symbols")
        if len(below_threshold) > 30:
            print(f"  ... and {len(below_threshold) - 30} more")

    print(f"\nPer-symbol missing/extra-bar days (observed bar count != {len(_expected_bar_times(report.interval_minutes))} expected) on consensus session dates:")
    if not report.per_symbol_missing:
        print("  (none)")
    else:
        for symbol, entries in report.per_symbol_missing.items():
            print(f"  {symbol}: {len(entries)} affected date(s)")
            for d, expected, observed in entries[:10]:
                print(f"    {d}: expected {expected}, observed {observed}")
            if len(entries) > 10:
                print(f"    ... and {len(entries) - 10} more")


def to_manifest_dict(report: DatasetAuditReport) -> Dict:
    """A compact, JSON-serializable summary suitable for embedding in
    S1.0's own manifest/provenance record."""
    return {
        "label": report.label,
        "interval_minutes": report.interval_minutes,
        "universe_size": report.universe_size,
        "consensus_session_count": len(report.consensus_dates),
        "consensus_range": [min(report.consensus_dates).isoformat(), max(report.consensus_dates).isoformat()] if report.consensus_dates else None,
        "files": [
            {
                "symbol": f.symbol, "path": f.path, "sha256": f.sha256,
                "total_rows": f.total_rows, "observed_dates": len(f.observed_dates),
                "malformed_timestamp_rows": f.malformed_timestamp_rows,
                "duplicate_timestamp_rows": f.duplicate_timestamp_rows,
                "weekend_rows": f.weekend_rows, "off_session_time_rows": f.off_session_time_rows,
                "correction_count": f.correction_count,
            }
            for f in report.files
        ],
        "symbols_with_missing_bars": {
            symbol: [{"date": d.isoformat(), "expected": e, "observed": o} for d, e, o in entries]
            for symbol, entries in report.per_symbol_missing.items()
        },
    }


if __name__ == "__main__":
    root = Path(__file__).parent
    reports = [
        audit_directory(root / "historical_data_research_ready", interval_minutes=15, label="PRIMARY 15-min (base 5)"),
        audit_directory(root / "historical_data_v5_additional_research_ready", interval_minutes=15, label="PRIMARY 15-min (v5-additional 15)"),
        audit_directory(root / "historical_data_market_research_ready", interval_minutes=15, label="PRIMARY 15-min NIFTY benchmark"),
        audit_directory(root / "historical_data_60minute_extended_ready", interval_minutes=60, label="SECONDARY 60-min extended (19, POLYCAB excluded)"),
        audit_directory(root / "historical_data_market_60minute_extended_ready", interval_minutes=60, label="SECONDARY 60-min NIFTY benchmark"),
    ]
    for r in reports:
        print_report(r)

    manifest = {r.label: to_manifest_dict(r) for r in reports}
    out_path = root / "p01d_dataset_audit_report.json"
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nFull manifest written to {out_path}")
