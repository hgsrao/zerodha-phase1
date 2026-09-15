"""V2-C TRAIN-only outcome-resolver label generation. LOCAL FILES ONLY -
no Kite calls, no credentials, no network.

Generates REVERTING / DETERIORATING / UNRESOLVED labels for every
qualifying V2-C event inside TRAIN ONLY (2015-02-02..2020-12-31), using
the FROZEN outcome-resolution rule in
P01D_V2C_OUTCOME_RESOLVER_D0_20260818.md - not the earlier sandbox
v2c_outcome_resolver.py (V2C_REAL_DATA_SANDBOX_20260817/ENGINE/), which
was written 2026-08-17 17:15, AFTER the correction note
(P01D_V2C_MASTER_RESEARCH_RECORD_CORRECTION_20260817.md, "Correction 2")
explicitly said the recovery-exit condition was still unfrozen, and
BEFORE the D0 spec (2026-08-18 06:08) properly froze it the next
morning with a DIFFERENT definition (close[t0-1] recovery / close[t0]
ATR stop, not the sandbox's SMA20 recovery / entry-price stop). The
sandbox code is treated as a stale, out-of-sequence artifact here - not
reused, not consulted for tie-breaking behavior.

Frozen resolver rule implemented (P01D_V2C_OUTCOME_RESOLVER_D0_20260818.md):
  - t0 = the event day (event_z20 < -2.0 fires at semantic 15:00,
    i.e. the close of the 14:45-start 15-minute bar).
  - Recovery level: close[t0-1] (prior COMPLETED day's daily close),
    captured once at t0, never recalculated forward.
  - Stop level: close[t0] - 2*ATR14[t0], ATR14 computed from daily
    bars ending at t0 (simple mean of the most recent 14 true ranges,
    requiring 15 completed daily closes) - fixed once, never
    recalculated forward.
  - REVERTING resolves at the first 15-min bar (within the horizon)
    whose CLOSE >= recovery level, provided the stop hasn't already
    resolved on an earlier bar.
  - DETERIORATING resolves at the first 15-min bar (within the
    horizon) whose LOW touches/breaches the stop level, provided
    REVERTING hasn't already resolved on an earlier bar.
  - Same-bar collision: if the SAME bar's close>=recovery AND
    low<=stop, DETERIORATING wins (stop-first), regardless of which
    level is numerically closer to the bar's open.
  - Gap treatment: if the bar's OPEN is already beyond the stop level
    or the recovery level, the open price determines resolution
    (using stop-first priority if the open is beyond both).
  - UNRESOLVED if neither resolves within 10 trading days forward
    from t0, including truncation at the end of available (TRAIN)
    data - a legitimate, recorded outcome, never dropped.

FROZEN D0 CLARIFICATION (added 2026-08-18, after an independent audit of
the first TRAIN label-generation run caught a boundary defect - see
"BUG FIX" comment below): observation begins on the first eligible
15-minute bar of trading day t0+1 and continues through t0+10; no bar
from event day t0 participates, ever. Reasoning: close[t0-1] was
already fully known before t0 started, but close[t0]/ATR[t0] (the stop
reference) are only fully determined once t0's session COMPLETES -
starting the observation walk at t0+1 avoids using t0's own partial,
still-forming session as both the stop's reference point and part of
the walk. This was stated as an "interpretation choice, flagged for
review" in the first run; the audit's finding is exactly why that
flagging mattered - it is now a frozen rule, not a soft default.

BUG FIX (2026-08-18, found by independent audit of the first run): the
first version of this script fell back to loading bars starting at t0
itself when a symbol's own certified file had no session after t0 at
all (ZEEL, t0=2020-09-24, the last date in ZEEL's file; JINDALSTEL,
t0=2015-03-26, likewise) - this let ZEEL's own 13:00 bar, BEFORE the
14:45 signal bar that defined the event, resolve REVERTING. A genuine
causality violation, not a data problem. Fixed: the fallback is
removed entirely. An empty forward horizon now resolves immediately as
UNRESOLVED (reason NO_FORWARD_DATA), never loads or walks any bar.
Two invariants are now asserted DURING generation (fail-closed, halts
the run) and re-verified AFTER generation as explicit, reported
integrity checks: observation_window_start must be strictly later than
event_t0 whenever it is populated; every resolution_timestamp must
fall on or after observation_window_start.

Scope, stated explicitly: this generates ONLY the resolver's three
labels (REVERTING/DETERIORATING/UNRESOLVED) plus event_z20 (the one
feature that defines qualification) and the resolver's own captured
reference values. It does NOT build the full 9-feature classifier
input vector from P01D_V2C_PREREGISTRATION_20260817.md (a separate,
later task) and does NOT fit any classifier. NIFTY 50 data is not
used - the D0 resolver rule (recovery/stop) only ever references the
stock's own OHLC series.

Fail-closed protections:
  - Refuses to run unless V2C_EPOCH_FREEZE_20260818.md's current
    SHA256 matches its frozen sidecar - protects against a later,
    accidental boundary edit silently changing the research
    population.
  - Refuses to run unless the certified calendar hash matches its
    frozen sidecar (reuses v2c_certify_15min_dataset.verify_calendar_hash).
  - Every candidate event is checked against
    v2c_frozen_data_validity_exclusions.check_event_eligibility()
    BEFORE any label is generated for it - an event whose lookback or
    outcome window overlaps a frozen exclusion (currently: YESBANK,
    2020-03-04..2020-03-18) is excluded, not silently labeled.
  - Zero/non-finite prior-20 SD, or fewer than 20 prior completed
    daily closes, or fewer than 15 prior completed daily bars for
    ATR14: the day is NOT a qualifying event (abstain), never a
    default/fabricated value.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev

import v2c_certify_15min_dataset as cert
from v2c_frozen_data_validity_exclusions import check_event_eligibility

BASE = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816")
REF = BASE / "V2C_REFERENCE"
CLEANED_DIR = BASE / "V2C_15MIN_DATA_CLEANED"
SUMMARY_CSV = BASE / "V2C_15MIN_DATA_ACQUIRED" / "V2C_15MIN_ACQUISITION_SUMMARY.csv"

EPOCH_FREEZE_MD = REF / "V2C_EPOCH_FREEZE_20260818.md"
EPOCH_FREEZE_SHA256 = REF / "V2C_EPOCH_FREEZE_20260818.sha256"

# Frozen TRAIN boundaries, per V2C_EPOCH_FREEZE_20260818.md (hash
# verified below before these are trusted) - not re-derived at runtime,
# the hash check is what protects against drift.
TRAIN_START = date(2015, 2, 2)
TRAIN_END = date(2020, 12, 31)

OBSERVATION_TRADING_DAYS = 10
EVENT_Z20_THRESHOLD = -2.0
SIGNAL_SLOT = "14:45:00"  # semantic 15:00 close (Kite start-stamped bar)

OUT_DIR = BASE / "V2C_LABELS_TRAIN_20260818"
AUDIT_CSV = OUT_DIR / "V2C_TRAIN_LABEL_AUDIT_20260818.csv"
INTEGRITY_REPORT = OUT_DIR / "V2C_TRAIN_LABEL_INTEGRITY_REPORT_20260818.json"


def verify_epoch_freeze_hash() -> str:
    if not EPOCH_FREEZE_SHA256.exists():
        raise SystemExit(f"REFUSING TO RUN: no SHA256 sidecar at {EPOCH_FREEZE_SHA256} - "
                          f"the epoch freeze was never hash-frozen.")
    expected = EPOCH_FREEZE_SHA256.read_text(encoding="utf-8").split()[0].strip()
    actual = hashlib.sha256(EPOCH_FREEZE_MD.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"REFUSING TO RUN: {EPOCH_FREEZE_MD} SHA256 mismatch.\n"
                          f"  expected (frozen): {expected}\n"
                          f"  actual (on disk):  {actual}\n"
                          f"The epoch-freeze document changed since it was frozen - "
                          f"this would silently change the TRAIN research population. Refusing.")
    print(f"Epoch freeze hash verified: {EPOCH_FREEZE_MD.name} matches frozen SHA256 {actual}.")
    return actual


@dataclass(frozen=True)
class DailyBar:
    session_date: date
    open: float
    high: float
    low: float
    close: float
    close_1500: float  # close of the 14:45-start bar (semantic 15:00), for event_z20


def build_daily_series(csv_path: Path, window_end: date) -> list[DailyBar]:
    """One DailyBar per real trading session found in the file, up to
    and including window_end. daily_close = the LAST bar of the day
    (whatever its timestamp - robust to documented-exception days);
    close_1500 = the 14:45-start bar's close specifically, required
    for event_z20 (day is skipped from event-scanning, not from the
    daily series, if that exact slot is missing - handled by the
    caller checking for None)."""
    per_date: dict[str, list[tuple[str, float, float, float, float]]] = {}
    with csv_path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            d = row["timestamp"][:10]
            if date.fromisoformat(d) > window_end:
                continue
            t = row["timestamp"][11:19]
            per_date.setdefault(d, []).append(
                (t, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
            )

    daily = []
    for d in sorted(per_date):
        bars = sorted(per_date[d], key=lambda x: x[0])
        day_open = bars[0][1]
        day_high = max(b[2] for b in bars)
        day_low = min(b[3] for b in bars)
        day_close = bars[-1][4]
        close_1500 = next((b[4] for b in bars if b[0] == SIGNAL_SLOT), None)
        daily.append(DailyBar(date.fromisoformat(d), day_open, day_high, day_low, day_close, close_1500))
    return daily


def compute_atr14(daily: list[DailyBar], t0_idx: int) -> float | None:
    """Simple mean of the most recent 14 true ranges, requiring 15
    completed daily bars ending at (including) t0_idx. Returns None
    (abstain) if insufficient history - never a default."""
    if t0_idx < 14:
        return None
    window = daily[t0_idx - 14: t0_idx + 1]  # 15 bars
    true_ranges = []
    for prev, cur in zip(window[:-1], window[1:]):
        tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        if tr <= 0 or tr != tr:  # non-finite/non-positive
            return None
        true_ranges.append(tr)
    if len(true_ranges) != 14:
        return None
    atr = mean(true_ranges)
    return atr if atr > 0 else None


def compute_event_z20(daily: list[DailyBar], t0_idx: int) -> float | None:
    """event_z20 using the prior 20 COMPLETED daily closes (strictly
    before t0) and today's semantic-15:00 close. Population SD
    (ddof=0). Returns None (abstain) on insufficient history, zero, or
    non-finite SD - never a default."""
    if t0_idx < 20:
        return None
    prior20 = [b.close for b in daily[t0_idx - 20: t0_idx]]
    if len(prior20) != 20:
        return None
    mu = mean(prior20)
    sd = pstdev(prior20)  # population SD, ddof=0
    if not sd or sd != sd or sd <= 0:
        return None
    c1500 = daily[t0_idx].close_1500
    if c1500 is None:
        return None
    return (c1500 - mu) / sd


def trading_days_forward(calendar_dates: list[date], t0: date, n: int) -> list[date]:
    """The next n trading days strictly after t0, per the frozen
    calendar - not n calendar days."""
    idx = calendar_dates.index(t0)
    return calendar_dates[idx + 1: idx + 1 + n]


def resolve_bar(bar_open: float, bar_high: float, bar_low: float, bar_close: float,
                 stop_level: float, recovery_level: float) -> tuple[str | None, float | None]:
    """One bar's resolution per D0 SS4-7. Returns (label, price) or
    (None, None) if this bar doesn't resolve anything."""
    gap_stop = bar_open <= stop_level
    gap_recovery = bar_open >= recovery_level
    if gap_stop and gap_recovery:
        return "DETERIORATING", bar_open  # stop-first, gap
    if gap_stop:
        return "DETERIORATING", bar_open
    if gap_recovery:
        return "REVERTING", bar_open
    stop_touched = bar_low <= stop_level
    recovery_touched = bar_close >= recovery_level
    if stop_touched and recovery_touched:
        return "DETERIORATING", stop_level  # same-bar collision, stop-first
    if stop_touched:
        return "DETERIORATING", stop_level
    if recovery_touched:
        return "REVERTING", bar_close
    return None, None


def load_intraday_bars(csv_path: Path, start_date: date, end_date: date) -> list[tuple[str, float, float, float, float]]:
    """Raw (timestamp, open, high, low, close) tuples in [start_date, end_date], sorted."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            d = date.fromisoformat(row["timestamp"][:10])
            if start_date <= d <= end_date:
                rows.append((row["timestamp"], float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"])))
    rows.sort(key=lambda x: x[0])
    return rows


def main() -> int:
    epoch_hash = verify_epoch_freeze_hash()
    calendar_hash = cert.verify_calendar_hash()
    calendar = cert.load_calendar()
    trading_dates = sorted(date.fromisoformat(d) for d in calendar if calendar[d])
    trading_dates_in_train = [d for d in trading_dates if TRAIN_START <= d <= TRAIN_END]

    summary_rows = list(csv.DictReader(SUMMARY_CSV.open(encoding="utf-8")))
    print(f"Scanning {len(summary_rows)} stock intervals for TRAIN-only qualifying events "
          f"({TRAIN_START}..{TRAIN_END})...")

    audit_rows = []
    excluded_by_frozen_window = []
    seen_event_keys = set()

    for row in summary_rows:
        sec_key = row["security_key"]
        candidates = list(CLEANED_DIR.glob(f"NSE_{sec_key}_15minute_{row['from']}_{row['through']}.csv"))
        if not candidates:
            continue
        csv_path = candidates[0]

        daily = build_daily_series(csv_path, TRAIN_END)
        if not daily:
            continue
        daily_dates = [b.session_date for b in daily]

        for t0_idx, bar in enumerate(daily):
            t0 = bar.session_date
            if not (TRAIN_START <= t0 <= TRAIN_END):
                continue

            event_z20 = compute_event_z20(daily, t0_idx)
            if event_z20 is None or not (event_z20 < EVENT_Z20_THRESHOLD):
                continue  # not a qualifying event (or abstain) - not an error

            # Reference levels, captured once.
            close_t0_minus_1 = daily[t0_idx - 1].close
            close_t0 = daily[t0_idx].close
            atr_t0 = compute_atr14(daily, t0_idx)
            if atr_t0 is None:
                continue  # insufficient ATR history - abstain, not a default

            recovery_level = close_t0_minus_1
            stop_level = close_t0 - 2.0 * atr_t0
            if stop_level <= 0:
                continue  # non-positive stop - abstain

            horizon_dates = trading_days_forward(daily_dates, t0, OBSERVATION_TRADING_DAYS)
            # Also need the calendar's own forward days in case this
            # symbol's own file has fewer sessions than the market
            # calendar around a gap - use the FULL market calendar for
            # the true 10-trading-day horizon end, then only walk bars
            # this symbol's file actually has.
            calendar_horizon = trading_days_forward(trading_dates, t0, OBSERVATION_TRADING_DAYS)
            if not calendar_horizon:
                continue  # t0 itself not resolvable in the calendar - shouldn't happen, abstain
            nominal_window_end = calendar_horizon[-1]
            truncated_at_train_end = nominal_window_end > TRAIN_END
            window_end = min(nominal_window_end, TRAIN_END)

            # Lookback start: earliest date touched by event_z20 (20
            # prior sessions) or ATR14 (15 prior sessions) - whichever
            # is earlier - for the frozen-exclusion crossing check.
            lookback_idx = min(t0_idx - 20, t0_idx - 15)
            lookback_start = daily_dates[max(lookback_idx, 0)]

            eligibility = check_event_eligibility(sec_key, lookback_start, window_end)
            if not eligibility.eligible:
                excluded_by_frozen_window.append({
                    "security_key": sec_key, "event_t0": t0.isoformat(),
                    "lookback_start": lookback_start.isoformat(), "window_end": window_end.isoformat(),
                    "reason": eligibility.reason,
                })
                continue

            event_key = (sec_key, t0.isoformat())
            if event_key in seen_event_keys:
                raise SystemExit(f"INTEGRITY VIOLATION: duplicate event {event_key} - refusing to continue")
            seen_event_keys.add(event_key)

            label = "UNRESOLVED"
            resolution_ts = None
            resolution_price = None

            if not horizon_dates:
                # BUG FIX (found by independent audit, 2026-08-18): this
                # symbol's own certified file has NO session after t0 at
                # all (t0 is the last date in the file - e.g. the symbol's
                # certified window ends exactly on t0). There is no
                # eligible post-t0 bar to observe, full stop - NEVER fall
                # back to loading bars starting at t0 itself, which would
                # use pre-event, same-day bars to "resolve" an outcome
                # that must be strictly forward-looking. This is a
                # legitimate, distinct UNRESOLVED case (no forward data
                # available), not the same as genuinely exhausting a
                # populated 10-day horizon.
                observation_window_start = None
                reason_code = "NO_FORWARD_DATA"
            else:
                observation_window_start = horizon_dates[0]
                # Frozen D0 clarification (this session, post-audit):
                # observation begins on the first eligible 15-minute bar
                # of trading day t0+1 and continues through t0+10; no bar
                # from event day t0 participates. Asserted, not assumed.
                if observation_window_start <= t0:
                    raise SystemExit(f"INTEGRITY VIOLATION: observation_window_start "
                                      f"{observation_window_start} is not strictly after event_t0 {t0} "
                                      f"for {sec_key} - refusing to continue")

                bars = load_intraday_bars(csv_path, observation_window_start, window_end)
                reason_code = "TRAIN_BOUNDARY" if truncated_at_train_end else "WINDOW_END"

                for ts, o, h, l, c in bars:
                    lbl, price = resolve_bar(o, h, l, c, stop_level, recovery_level)
                    if lbl is not None:
                        resolution_date = date.fromisoformat(ts[:10])
                        if resolution_date < observation_window_start:
                            raise SystemExit(f"INTEGRITY VIOLATION: resolution {ts} for {sec_key} "
                                              f"event {t0} precedes observation_window_start "
                                              f"{observation_window_start} - refusing to continue")
                        label = "DETERIORATING" if lbl == "DETERIORATING" else "REVERTING"
                        resolution_ts = ts
                        resolution_price = price
                        reason_code = "STOP_TOUCHED_OR_GAP" if label == "DETERIORATING" else "RECOVERY_CLOSE_OR_GAP"
                        break

            audit_rows.append({
                "security_key": sec_key,
                "symbol_at_interval_start": row["symbol_at_interval_start"],
                "event_t0": t0.isoformat(),
                "event_z20": round(event_z20, 6),
                "close_t0_minus_1": close_t0_minus_1,
                "close_t0": close_t0,
                "atr14_t0": round(atr_t0, 6),
                "recovery_level": recovery_level,
                "stop_level": round(stop_level, 6),
                "observation_window_start": observation_window_start.isoformat() if observation_window_start else "",
                "observation_window_nominal_end": nominal_window_end.isoformat(),
                "observation_window_effective_end": window_end.isoformat(),
                "truncated_at_train_boundary": truncated_at_train_end,
                "label": label,
                "resolution_timestamp": resolution_ts or "",
                "resolution_price": resolution_price if resolution_price is not None else "",
                "reason_code": reason_code,
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if audit_rows:
        with AUDIT_CSV.open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=list(audit_rows[0].keys()))
            w.writeheader()
            w.writerows(audit_rows)

    excl_path = OUT_DIR / "V2C_TRAIN_EVENTS_EXCLUDED_FROZEN_WINDOW_20260818.csv"
    if excluded_by_frozen_window:
        with excl_path.open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=list(excluded_by_frozen_window[0].keys()))
            w.writeheader()
            w.writerows(excluded_by_frozen_window)

    # --------------------------------------------------------------
    # Integrity checks - run and reported, not assumed.
    # --------------------------------------------------------------
    label_counts: dict[str, int] = {}
    for r in audit_rows:
        label_counts[r["label"]] = label_counts.get(r["label"], 0) + 1

    checks = {}

    out_of_train = [r for r in audit_rows if not (TRAIN_START.isoformat() <= r["event_t0"] <= TRAIN_END.isoformat())]
    checks["no_event_outside_train"] = {"pass": len(out_of_train) == 0, "violations": len(out_of_train)}

    event_keys = [(r["security_key"], r["event_t0"]) for r in audit_rows]
    checks["no_duplicate_events"] = {"pass": len(event_keys) == len(set(event_keys)),
                                      "violations": len(event_keys) - len(set(event_keys))}

    yesbank_events = [r for r in audit_rows if r["security_key"] == "YESBANK"]
    yesbank_crossing = []
    for r in yesbank_events:
        elig = check_event_eligibility("YESBANK", date.fromisoformat(r["event_t0"]),
                                        date.fromisoformat(r["observation_window_effective_end"]))
        if not elig.eligible:
            yesbank_crossing.append(r["event_t0"])
    checks["yesbank_exclusion_enforced"] = {"pass": len(yesbank_crossing) == 0,
                                             "violations": len(yesbank_crossing),
                                             "n_yesbank_events_retained": len(yesbank_events)}

    forbidden_crossing = []
    for r in audit_rows:
        elig = check_event_eligibility(r["security_key"], date.fromisoformat(r["event_t0"]),
                                        date.fromisoformat(r["observation_window_effective_end"]))
        if not elig.eligible:
            forbidden_crossing.append((r["security_key"], r["event_t0"]))
    checks["no_forbidden_window_crossing_in_retained_events"] = {
        "pass": len(forbidden_crossing) == 0, "violations": len(forbidden_crossing)}

    checks["events_correctly_excluded_for_frozen_window"] = {"count": len(excluded_by_frozen_window)}

    # Bug found by independent audit (2026-08-18): a fallback that loaded
    # bars starting at t0 itself when a symbol's file had no post-t0
    # session (ZEEL 2020-09-24, JINDALSTEL 2015-03-26) let a same-day,
    # pre-event bar resolve an outcome - a causality violation. Fixed by
    # removing the fallback entirely (empty horizon -> immediate
    # UNRESOLVED, reason NO_FORWARD_DATA). These two checks verify the
    # fix holds across the whole regenerated dataset, not just the two
    # originally-caught cases.
    window_start_not_after_t0 = [
        r for r in audit_rows
        if r["observation_window_start"] and date.fromisoformat(r["observation_window_start"]) <= date.fromisoformat(r["event_t0"])
    ]
    checks["observation_window_start_strictly_after_event_t0"] = {
        "pass": len(window_start_not_after_t0) == 0, "violations": len(window_start_not_after_t0)}

    resolution_before_window_start = [
        r for r in audit_rows
        if r["resolution_timestamp"] and r["observation_window_start"]
        and date.fromisoformat(r["resolution_timestamp"][:10]) < date.fromisoformat(r["observation_window_start"])
    ]
    checks["resolution_on_or_after_window_start"] = {
        "pass": len(resolution_before_window_start) == 0, "violations": len(resolution_before_window_start)}

    no_forward_data_events = [r for r in audit_rows if r["reason_code"] == "NO_FORWARD_DATA"]
    checks["no_forward_data_events_are_unresolved_with_empty_window"] = {
        "pass": all(r["label"] == "UNRESOLVED" and not r["observation_window_start"] and not r["resolution_timestamp"]
                    for r in no_forward_data_events),
        "count": len(no_forward_data_events),
        "events": [(r["security_key"], r["event_t0"]) for r in no_forward_data_events],
    }

    all_checks_pass = all(v.get("pass", True) for v in checks.values())

    report = {
        "epoch_freeze_sha256": epoch_hash,
        "calendar_sha256": calendar_hash,
        "train_start": TRAIN_START.isoformat(),
        "train_end": TRAIN_END.isoformat(),
        "n_symbols_scanned": len(summary_rows),
        "n_qualifying_events_labeled": len(audit_rows),
        "n_events_excluded_frozen_window": len(excluded_by_frozen_window),
        "label_counts": label_counts,
        "integrity_checks": checks,
        "all_checks_pass": all_checks_pass,
        "audit_file": str(AUDIT_CSV) if audit_rows else None,
        "excluded_file": str(excl_path) if excluded_by_frozen_window else None,
    }
    INTEGRITY_REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"\n{len(audit_rows)} qualifying TRAIN events labeled.")
    print("Label counts:", label_counts)
    print(f"{len(excluded_by_frozen_window)} candidate events excluded for crossing a frozen data-validity window.")
    print("\nIntegrity checks:")
    for name, result in checks.items():
        print(f"  {name}: {result}")
    print(f"\nALL CHECKS PASS: {all_checks_pass}")
    print(f"\nAudit file -> {AUDIT_CSV if audit_rows else '(none - no events)'}")
    print(f"Integrity report -> {INTEGRITY_REPORT}")
    print("\nNo classifier fit. No model. No P&L. VALIDATION and HOLDOUT untouched.")

    return 0 if all_checks_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
