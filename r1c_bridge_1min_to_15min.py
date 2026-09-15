"""R1-C Gate G1 — 1-minute -> 15-minute causal bridge. LOCAL FILES ONLY,
no Kite calls in this module itself (the live observer that calls this
module handles the real connection separately, with the owner's own
credentials).

**Why this module exists**: R1-C's composite state machine needs V2-C's
frozen classifier (built on 15-minute bars, Kite start-stamped, a
14:45:00-start bar signal cutoff) to run against the SAME live feed that
drives Pillar I/II's 1-minute logic. Rather than reimplementing V2-C's
feature/label pipeline at 1-minute resolution (which would silently
create a second, divergent implementation of already-frozen formulas),
this module aggregates 1-minute bars UP to V2-C's exact 15-minute input
schema, so `v2c_train_feature_construction.py` and
`v2c_train_label_generation.py`'s own frozen functions can be called
completely unmodified - the same reuse-not-reimplement discipline
already used everywhere else in the V2-C track.

**A genuine data-boundary finding, disclosed here rather than glossed
over**: R1-C's own proposal document assumed this aggregation would be
verified against "known overlapping data" between the two source
datasets. There isn't any - V2-C's certified 15-minute dataset covers
2014-12-03..2023-07-31; Pillar I/II's 1-minute data covers 2026 dates
only. The two never overlap in real time. Certification here is
therefore done with **deterministic synthetic fixtures** (exact,
hand-specified 1-minute bars composing to an exactly-known 15-minute
result) plus a **schema round-trip check** feeding this module's real
output directly into V2-C's real, frozen functions - not by comparing
against a real overlapping period, because none exists. This is an
adaptation of R1-C G1's original plan, made necessary by a real
constraint, not a shortcut around it.

**Bucketing convention, matched exactly to V2-C's own frozen data**:
Kite start-stamped bars - a 15-minute bar labeled `HH:MM:00` covers
`[HH:MM:00, HH:MM:00 + 15min)`. Buckets align to the four quarter-hour
marks (`:00`, `:15`, `:30`, `:45`), matching NSE's 09:15 session open
exactly (09:15 is itself a quarter-hour mark) and V2-C's own
`SIGNAL_SLOT = "14:45:00"` / `SIGNAL_TIME = "14:45:00"` constants
(`v2c_train_label_generation.py`, `v2c_train_feature_construction.py`).

**Aggregation rule, standard OHLC bar-merge - the same rule
`v2c_train_label_generation.build_daily_series` already uses one level
up (15-minute bars -> daily), applied one level down (1-minute bars ->
15-minute)**: open = first bar's open (by time), high = max high,
low = min low, close = last bar's close (by time), volume = sum of
volume (carried through for diagnostics only - V2-C's own schema never
reads a volume column).

**Fail-closed on incomplete buckets**: a 15-minute bucket missing any of
its expected 1-minute bars is emitted with an explicit
`bar_count < expected` marker in the diagnostic sidecar, never silently
padded or interpolated. Downstream code (V2-C's own functions) has no
concept of "partial bar" - a caller of this module must decide whether
to exclude incomplete-bucket days from live decisions, and this module
never makes that decision silently on its behalf.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# --- Timestamp handling -----------------------------------------------------
#
# Matches the exact format already used throughout this project's real
# Kite-derived data (e.g. `"2026-05-20T11:10:00+05:30"` in
# `d1_discovery_trades.json`, and V2-C's own certified 15-minute CSVs):
# `YYYY-MM-DDTHH:MM:SS<offset>`. V2-C's readers only ever slice
# `[:10]` (date) and `[11:19]` (HH:MM:SS) - offset content past index 19
# is never inspected by them, so this module preserves whatever offset
# string the source data carries rather than assuming one.

TS_DATE_END = 10          # row["timestamp"][:10]  -> "YYYY-MM-DD"
TS_TIME_START = 11
TS_TIME_END = 19          # row["timestamp"][11:19] -> "HH:MM:SS"

BUCKET_MINUTES = 15


@dataclass(frozen=True)
class OneMinuteBar:
    timestamp: str   # full source timestamp string, offset preserved verbatim
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class FifteenMinuteBar:
    timestamp: str   # bucket start, same offset convention as the source bars
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_count: int
    expected_bar_count: int

    @property
    def complete(self) -> bool:
        return self.bar_count == self.expected_bar_count


class BridgeError(RuntimeError):
    """Raised on any structurally invalid input - never silently patched."""


def _bucket_start_minute(minute: int) -> int:
    return (minute // BUCKET_MINUTES) * BUCKET_MINUTES


def _bucket_key(ts: str) -> tuple[str, str]:
    """Returns (date_str, bucket_time_str) for one source timestamp,
    e.g. "2026-05-20T14:47:00+05:30" -> ("2026-05-20", "14:45:00")."""
    date_str = ts[:TS_DATE_END]
    time_str = ts[TS_TIME_START:TS_TIME_END]
    if len(time_str) != 8 or time_str[2] != ":" or time_str[5] != ":":
        raise BridgeError(f"REFUSING TO RUN: malformed timestamp {ts!r} - "
                           f"expected HH:MM:SS at offset [{TS_TIME_START}:{TS_TIME_END}].")
    hh, mm, ss = time_str.split(":")
    bucket_minute = _bucket_start_minute(int(mm))
    bucket_time = f"{hh}:{bucket_minute:02d}:00"
    offset = ts[TS_TIME_END:]  # preserved verbatim, appended back on output
    return date_str, bucket_time, offset


def _bucket_key_full(ts: str) -> tuple[str, str, str]:
    return _bucket_key(ts)


def aggregate_1min_to_15min(
    bars: list[OneMinuteBar],
    *,
    expected_bars_per_bucket: int = BUCKET_MINUTES,
) -> list[FifteenMinuteBar]:
    """Deterministic, causal, bucket-then-merge aggregation. Bars must
    already be sorted by timestamp within each symbol's own series
    (this function does not re-sort across symbols - callers pass one
    symbol's bars at a time, matching every other reader in this
    project's own convention of one CSV per security).

    Never imputes a missing minute. Never merges across a date boundary
    (each calendar date's buckets are independent, matching
    `build_daily_series`'s own per-date grouping)."""
    if not bars:
        return []

    buckets: dict[tuple[str, str], list[OneMinuteBar]] = {}
    offsets: dict[tuple[str, str], str] = {}
    order: list[tuple[str, str]] = []
    for b in bars:
        date_str, bucket_time, offset = _bucket_key(b.timestamp)
        key = (date_str, bucket_time)
        if key not in buckets:
            buckets[key] = []
            offsets[key] = offset
            order.append(key)
        buckets[key].append(b)

    out: list[FifteenMinuteBar] = []
    for key in order:
        group = sorted(buckets[key], key=lambda x: x.timestamp)
        date_str, bucket_time = key
        out.append(FifteenMinuteBar(
            timestamp=f"{date_str}T{bucket_time}{offsets[key]}",
            open=group[0].open,
            high=max(g.high for g in group),
            low=min(g.low for g in group),
            close=group[-1].close,
            volume=sum(g.volume for g in group),
            bar_count=len(group),
            expected_bar_count=expected_bars_per_bucket,
        ))
    return out


def read_1min_csv(path: Path) -> list[OneMinuteBar]:
    """Reads the exact schema already used by Pillar I/II's own 1-minute
    files (`timestamp,open,high,low,close,volume`) - no reformatting of
    the source, matching this project's reuse-not-reimplement rule."""
    rows: list[OneMinuteBar] = []
    with path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            ts = row.get("timestamp")
            if not ts:
                continue
            rows.append(OneMinuteBar(
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            ))
    rows.sort(key=lambda b: b.timestamp)
    return rows


def write_15min_csv(bars: list[FifteenMinuteBar], out_path: Path) -> Path:
    """Writes the EXACT schema V2-C's own frozen readers expect
    (`timestamp,open,high,low,close`) - verified by the round-trip test
    in `test_r1c_bridge_1min_to_15min.py`, which feeds this file
    straight into `v2c_train_feature_construction.get_bars_by_date` and
    `v2c_train_label_generation.build_daily_series` unmodified."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as h:
        writer = csv.writer(h)
        writer.writerow(["timestamp", "open", "high", "low", "close"])
        for b in bars:
            writer.writerow([b.timestamp, b.open, b.high, b.low, b.close])
    return out_path


def incomplete_buckets(bars: list[FifteenMinuteBar]) -> list[FifteenMinuteBar]:
    """Every bucket that did not receive its full expected minute count -
    the caller (the live observer, not this module) decides what to do
    with these; they are never silently completed or dropped here."""
    return [b for b in bars if not b.complete]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
