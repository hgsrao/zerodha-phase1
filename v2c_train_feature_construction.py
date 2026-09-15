"""V2-C TRAIN-only feature construction. LOCAL FILES ONLY - no Kite
calls, no credentials, no network.

Builds the remaining 8 of the 9 frozen exhaustion/deterioration
features (`RESULTS/P01D_V2C_PREREGISTRATION_20260817.md`, feature spec
SHA256 `DF009214EDE4FD732E6C71625E53597A60EC9B3D3B05CEE55C40133D4B803D92`)
for exactly the 5467 already-frozen TRAIN events
(`V2C_LABELS_TRAIN_20260818/V2C_TRAIN_LABEL_AUDIT_20260818.csv`) — the
ninth, `event_z20`, was already computed by the label generator and is
carried forward here, RECOMPUTED and cross-checked against the frozen
value as an integrity control, not silently trusted.

**Does NOT fit any classifier.** Per explicit instruction, feature
construction and classifier fitting are separate, sequential steps —
this script stops after writing the feature table and its integrity
report. VALIDATION and HOLDOUT are never touched.

Frozen feature formulas implemented exactly as specified (causal
timing per the preregistration's "Causal timing" section):

  downside_return         = stock_1500_close / previous_completed_stock_close - 1
  drawdown_from_day_high  = stock_1500_close / stock_high_through_1500 - 1
  recovery_from_day_low   = stock_1500_close / stock_low_through_1500 - 1
  close_location           = (stock_1500_close - stock_low_through_1500)
                              / (stock_high_through_1500 - stock_low_through_1500)
  atr14_pct                = prior_completed_session_ATR14 / previous_completed_stock_close
  intraday_range_pct       = (stock_high_through_1500 - stock_low_through_1500)
                              / previous_completed_stock_close
  nifty_return              = nifty_1500_close / previous_completed_nifty_close - 1
  relative_return           = downside_return - nifty_return

**A causal-timing subtlety, deliberate and checked, not accidental**:
`atr14_pct`'s ATR14 is the "prior completed session" ATR — computed
from daily bars ending at `t0-1`
(`compute_atr14(daily, t0_idx - 1)`) — NOT the resolver's own
`atr14_t0` (`compute_atr14(daily, t0_idx)`, ending AT `t0`, used for the
D0 stop level). These are two different quantities computed from two
different index positions for two different, legitimate reasons: the
resolver's stop level is only finalized once `t0`'s session completes
(D0-A1's own reasoning), whereas this feature must be knowable AT the
causal 15:00 signal time on `t0`, when `t0`'s own session is still only
partially observed. Conflating the two would be a real look-ahead
defect; this script computes and reports both, and the integrity
report records how often they actually differ, as evidence the
distinction is real and implemented, not just described.

`stock_high_through_1500` / `stock_low_through_1500`: max/min of
high/low across every 15-minute bar of session `t0` from market open
through the 14:45-start bar INCLUSIVE (never later bars) - loaded
directly from the certified intraday data, not derived from the daily
series (which spans the whole session).

No feature is imputed. Any missing/non-finite/non-numeric mandatory
input ABSTAINS the entire event's feature vector (never a partial
vector) - per the preregistration's explicit no-imputation rule.

Run from the project root (matches v2c_train_label_generation.py's own
convention - both use relative paths rooted there, and this script
imports that one directly).
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import v2c_certify_15min_dataset as cert
import v2c_train_label_generation as labelgen
from v2c_frozen_data_validity_exclusions import check_event_eligibility

BASE = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816")
LABELS_DIR = BASE / "V2C_LABELS_TRAIN_20260818"
AUDIT_CSV = LABELS_DIR / "V2C_TRAIN_LABEL_AUDIT_20260818.csv"
MANIFEST_JSON = LABELS_DIR / "V2C_TRAIN_LABELS_HASH_MANIFEST_20260818.json"
MANIFEST_SHA = LABELS_DIR / "V2C_TRAIN_LABELS_HASH_MANIFEST_20260818.sha256"
CLEANED_DIR = BASE / "V2C_15MIN_DATA_CLEANED"
SUMMARY_CSV = BASE / "V2C_15MIN_DATA_ACQUIRED" / "V2C_15MIN_ACQUISITION_SUMMARY.csv"
NIFTY_CSV = CLEANED_DIR / "NSE_NIFTY 50_15minute_2014-12-03_2023-07-31.csv"

DATASET_MANIFEST_JSON = CLEANED_DIR / "V2C_15MIN_DATASET_HASH_MANIFEST_20260818.json"
DATASET_MANIFEST_SHA = CLEANED_DIR / "V2C_15MIN_DATASET_HASH_MANIFEST_20260818.json.sha256"

OUT_DIR = BASE / "V2C_FEATURES_TRAIN_20260818"
OUT_DIR.mkdir(exist_ok=True)
FEATURES_CSV = OUT_DIR / "V2C_TRAIN_FEATURES_20260818.csv"
INTEGRITY_JSON = OUT_DIR / "V2C_TRAIN_FEATURE_INTEGRITY_REPORT_20260818.json"

TRAIN_START = labelgen.TRAIN_START
TRAIN_END = labelgen.TRAIN_END
SIGNAL_TIME = "14:45:00"

FROZEN_FEATURE_ORDER = [
    "event_z20", "downside_return", "drawdown_from_day_high", "recovery_from_day_low",
    "close_location", "atr14_pct", "intraday_range_pct", "nifty_return", "relative_return",
]
FEATURE_SPEC_SHA256 = "DF009214EDE4FD732E6C71625E53597A60EC9B3D3B05CEE55C40133D4B803D92"

_EPS_MATCH = 1e-6


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inputs() -> None:
    if not MANIFEST_SHA.exists():
        raise SystemExit(f"REFUSING TO RUN: no sidecar hash for {MANIFEST_JSON.name}.")
    recorded = MANIFEST_SHA.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = _sha256(MANIFEST_JSON).lower()
    if recorded != actual:
        raise SystemExit(f"REFUSING TO RUN: {MANIFEST_JSON.name} hash mismatch "
                          f"(recorded {recorded}, actual {actual}).")
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        candidate = Path(entry["file"].replace("\\", "/"))
        if not candidate.exists():
            raise SystemExit(f"REFUSING TO RUN: manifest-listed file {candidate} does not exist.")
        h = _sha256(candidate).lower()
        if h != entry["sha256"].lower():
            raise SystemExit(f"REFUSING TO RUN: {candidate} hash mismatch against frozen manifest.")
    print(f"Label hash manifest verified: all {len(manifest['files'])} files match "
          f"{MANIFEST_JSON.name} (SHA256 {actual}).")
    cal_hash = cert.verify_calendar_hash()
    print(f"Calendar hash verified: {cal_hash}")
    verify_dataset_manifest_hash()


def verify_dataset_manifest_hash() -> None:
    """Fail-closed check against V2C_15MIN_DATASET_HASH_MANIFEST_20260818.json
    (all 70 certified stock/NIFTY files) - added per the independent
    feature-integrity re-audit's non-blocking hardening item: this
    generator previously verified the label manifest and calendar but
    not the underlying certified dataset itself. Refuses to run on any
    manifest or per-file hash mismatch, exactly like the other checks
    in verify_inputs()."""
    if not DATASET_MANIFEST_SHA.exists():
        raise SystemExit(f"REFUSING TO RUN: no sidecar hash for {DATASET_MANIFEST_JSON.name}.")
    recorded = DATASET_MANIFEST_SHA.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = _sha256(DATASET_MANIFEST_JSON).lower()
    if recorded != actual:
        raise SystemExit(f"REFUSING TO RUN: {DATASET_MANIFEST_JSON.name} hash mismatch "
                          f"(recorded {recorded}, actual {actual}).")
    manifest = json.loads(DATASET_MANIFEST_JSON.read_text(encoding="utf-8"))
    data_dir = Path(manifest["data_dir"].replace("\\", "/"))
    mismatches = []
    for entry in manifest["files"]:
        candidate = data_dir / entry["file"]
        if not candidate.exists():
            mismatches.append(f"{candidate} does not exist")
            continue
        h = _sha256(candidate).lower()
        if h != entry["sha256"].lower():
            mismatches.append(f"{candidate} hash mismatch (recorded {entry['sha256']}, actual {h})")
    if mismatches:
        raise SystemExit("REFUSING TO RUN: certified dataset hash mismatch(es):\n  "
                          + "\n  ".join(mismatches))
    print(f"Dataset manifest verified: all {manifest['n_files']} certified files "
          f"(stocks + NIFTY) match {DATASET_MANIFEST_JSON.name} (SHA256 {actual}).")


# --- Per-file caches (loaded once, reused across every event on that file) ---

_DAILY_CACHE: dict[tuple, list] = {}
_BARS_BY_DATE_CACHE: dict[Path, dict[str, list[tuple[str, float, float, float, float]]]] = {}


def get_daily_series(csv_path: Path, window_end: date) -> list:
    key = (csv_path, window_end)
    cached = _DAILY_CACHE.get(key)
    if cached is not None:
        return cached
    daily = labelgen.build_daily_series(csv_path, window_end)
    _DAILY_CACHE[key] = daily
    return daily


def get_bars_by_date(csv_path: Path) -> dict[str, list[tuple[str, float, float, float, float]]]:
    cached = _BARS_BY_DATE_CACHE.get(csv_path)
    if cached is not None:
        return cached
    by_date: dict[str, list[tuple[str, float, float, float, float]]] = {}
    with csv_path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            d = row["timestamp"][:10]
            t = row["timestamp"][11:19]
            by_date.setdefault(d, []).append(
                (t, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
            )
    for d in by_date:
        by_date[d].sort(key=lambda x: x[0])
    _BARS_BY_DATE_CACHE[csv_path] = by_date
    return by_date


def load_security_intervals() -> dict[str, list[tuple[date, date, str, str]]]:
    out: dict[str, list[tuple[date, date, str, str]]] = {}
    with SUMMARY_CSV.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            out.setdefault(row["security_key"], []).append((
                date.fromisoformat(row["from"]), date.fromisoformat(row["through"]),
                row["from"], row["through"],
            ))
    return out


def find_interval_file(security_key: str, t0: date, intervals: dict) -> Path | None:
    for from_d, through_d, from_s, through_s in intervals.get(security_key, []):
        if from_d <= t0 <= through_d:
            matches = list(CLEANED_DIR.glob(f"NSE_{security_key}_15minute_{from_s}_{through_s}.csv"))
            if matches:
                return matches[0]
    return None


def through_1500_hilo(csv_path: Path, session_date: date) -> tuple[float, float, float] | None:
    """(high, low, close) across bars from open through the 14:45-start
    bar inclusive, for one session. None if no such bars exist."""
    bars = get_bars_by_date(csv_path).get(session_date.isoformat(), [])
    through = [b for b in bars if b[0] <= SIGNAL_TIME]
    if not through:
        return None
    high = max(b[2] for b in through)
    low = min(b[3] for b in through)
    close = through[-1][4]  # last bar at/before 14:45, sorted
    return high, low, close


@dataclass(frozen=True)
class FeatureRow:
    security_key: str
    event_t0: str
    abstain: bool
    abstain_reason: str
    event_z20: float | None = None
    downside_return: float | None = None
    drawdown_from_day_high: float | None = None
    recovery_from_day_low: float | None = None
    close_location: float | None = None
    atr14_pct: float | None = None
    intraday_range_pct: float | None = None
    nifty_return: float | None = None
    relative_return: float | None = None
    # Diagnostic-only raw inputs, appended after the frozen 9 - never part
    # of the frozen feature order itself.
    stock_1500_close: float | None = None
    stock_high_through_1500: float | None = None
    stock_low_through_1500: float | None = None
    previous_completed_stock_close: float | None = None
    prior_completed_session_atr14: float | None = None
    resolver_atr14_t0: float | None = None
    nifty_1500_close: float | None = None
    previous_completed_nifty_close: float | None = None


def _finite(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


def main() -> int:
    verify_inputs()
    intervals = load_security_intervals()

    with AUDIT_CSV.open(newline="", encoding="utf-8") as h:
        events = list(csv.DictReader(h))
    print(f"{len(events)} frozen TRAIN events loaded.")

    nifty_daily = get_daily_series(NIFTY_CSV, TRAIN_END)
    nifty_dates = [b.session_date for b in nifty_daily]
    nifty_index = {d: i for i, d in enumerate(nifty_dates)}

    out_rows: list[FeatureRow] = []
    seen_keys: set[tuple[str, str]] = set()
    abstain_reasons = Counter()
    event_z20_mismatches = 0
    close1500_mismatches = 0
    frozen_window_violations = 0
    atr14_pairs_differ = 0
    atr14_pairs_both_present = 0

    for ev in events:
        sec_key = ev["security_key"]
        t0 = date.fromisoformat(ev["event_t0"])
        event_key = (sec_key, ev["event_t0"])
        if event_key in seen_keys:
            raise SystemExit(f"INTEGRITY VIOLATION: duplicate event key {event_key} in audit CSV.")
        seen_keys.add(event_key)

        csv_path = find_interval_file(sec_key, t0, intervals)
        if csv_path is None:
            raise SystemExit(f"INTEGRITY VIOLATION: no certified interval file for {sec_key} event {t0}.")

        daily = get_daily_series(csv_path, TRAIN_END)
        daily_dates = [b.session_date for b in daily]
        try:
            t0_idx = daily_dates.index(t0)
        except ValueError:
            raise SystemExit(f"INTEGRITY VIOLATION: {sec_key} event {t0} not found in its own "
                              f"daily series - join defect against the frozen label set.")

        # --- Causal-timing cross-check: recompute event_z20 exactly as the
        # label generator did, from the identical daily series, and require
        # an exact match against the frozen value. ---
        recomputed_z20 = labelgen.compute_event_z20(daily, t0_idx)
        frozen_z20 = float(ev["event_z20"])
        if recomputed_z20 is None or abs(recomputed_z20 - frozen_z20) > _EPS_MATCH:
            event_z20_mismatches += 1
            raise SystemExit(f"INTEGRITY VIOLATION: event_z20 recomputation mismatch for {sec_key} "
                              f"{t0}: frozen={frozen_z20}, recomputed={recomputed_z20}.")

        # --- Frozen-window re-check on the (narrower, backward-only) window
        # this feature construction actually reads. ---
        lookback_idx = min(t0_idx - 20, t0_idx - 15)
        lookback_start = daily_dates[max(lookback_idx, 0)]
        eligibility = check_event_eligibility(sec_key, lookback_start, t0)
        if not eligibility.eligible:
            frozen_window_violations += 1
            raise SystemExit(f"INTEGRITY VIOLATION: {sec_key} event {t0} feature lookback "
                              f"[{lookback_start}..{t0}] crosses a frozen exclusion window "
                              f"({eligibility.reason}) - this event should never have reached "
                              f"feature construction.")

        abstain_reason = ""
        previous_completed_stock_close = daily[t0_idx - 1].close
        stock_1500_close = daily[t0_idx].close_1500

        hilo = through_1500_hilo(csv_path, t0)
        resolver_atr14_t0 = labelgen.compute_atr14(daily, t0_idx)  # for reporting/contrast only
        prior_completed_atr14 = labelgen.compute_atr14(daily, t0_idx - 1) if t0_idx - 1 >= 0 else None

        if resolver_atr14_t0 is not None and prior_completed_atr14 is not None:
            atr14_pairs_both_present += 1
            if abs(resolver_atr14_t0 - prior_completed_atr14) > _EPS_MATCH:
                atr14_pairs_differ += 1

        if hilo is None:
            abstain_reason = "NO_THROUGH_1500_BARS"
        elif prior_completed_atr14 is None:
            abstain_reason = "ATR14_INSUFFICIENT_HISTORY_PRIOR_SESSION"
        elif not previous_completed_stock_close or previous_completed_stock_close <= 0:
            abstain_reason = "PREVIOUS_CLOSE_NON_POSITIVE"
        elif stock_1500_close is None:
            abstain_reason = "STOCK_1500_CLOSE_MISSING"

        if not abstain_reason:
            stock_high_through_1500, stock_low_through_1500, hilo_close = hilo
            if abs(hilo_close - stock_1500_close) > _EPS_MATCH:
                close1500_mismatches += 1
                raise SystemExit(f"INTEGRITY VIOLATION: {sec_key} {t0} through-1500 last bar close "
                                  f"{hilo_close} != daily series close_1500 {stock_1500_close}.")
            day_range = stock_high_through_1500 - stock_low_through_1500
            if not _finite(day_range) or day_range <= 0:
                abstain_reason = "ZERO_OR_INVALID_INTRADAY_RANGE"

        if not abstain_reason:
            nifty_idx = nifty_index.get(t0)
            if nifty_idx is None:
                abstain_reason = "NIFTY_DATE_NOT_FOUND"
            elif nifty_idx < 1:
                abstain_reason = "NIFTY_INSUFFICIENT_HISTORY"
            else:
                nifty_1500_close = nifty_daily[nifty_idx].close_1500
                previous_completed_nifty_close = nifty_daily[nifty_idx - 1].close
                if nifty_1500_close is None:
                    abstain_reason = "NIFTY_1500_CLOSE_MISSING"
                elif not previous_completed_nifty_close or previous_completed_nifty_close <= 0:
                    abstain_reason = "NIFTY_PREVIOUS_CLOSE_NON_POSITIVE"

        if abstain_reason:
            abstain_reasons[abstain_reason] += 1
            out_rows.append(FeatureRow(security_key=sec_key, event_t0=ev["event_t0"],
                                        abstain=True, abstain_reason=abstain_reason))
            continue

        downside_return = stock_1500_close / previous_completed_stock_close - 1
        drawdown_from_day_high = stock_1500_close / stock_high_through_1500 - 1
        recovery_from_day_low = stock_1500_close / stock_low_through_1500 - 1
        close_location = (stock_1500_close - stock_low_through_1500) / day_range
        atr14_pct = prior_completed_atr14 / previous_completed_stock_close
        intraday_range_pct = day_range / previous_completed_stock_close
        nifty_return = nifty_1500_close / previous_completed_nifty_close - 1
        relative_return = downside_return - nifty_return

        computed = [recomputed_z20, downside_return, drawdown_from_day_high, recovery_from_day_low,
                    close_location, atr14_pct, intraday_range_pct, nifty_return, relative_return]
        if not all(_finite(v) for v in computed):
            abstain_reasons["NON_FINITE_COMPUTED_FEATURE"] += 1
            out_rows.append(FeatureRow(security_key=sec_key, event_t0=ev["event_t0"],
                                        abstain=True, abstain_reason="NON_FINITE_COMPUTED_FEATURE"))
            continue

        out_rows.append(FeatureRow(
            security_key=sec_key, event_t0=ev["event_t0"], abstain=False, abstain_reason="",
            event_z20=recomputed_z20, downside_return=downside_return,
            drawdown_from_day_high=drawdown_from_day_high, recovery_from_day_low=recovery_from_day_low,
            close_location=close_location, atr14_pct=atr14_pct, intraday_range_pct=intraday_range_pct,
            nifty_return=nifty_return, relative_return=relative_return,
            stock_1500_close=stock_1500_close, stock_high_through_1500=stock_high_through_1500,
            stock_low_through_1500=stock_low_through_1500,
            previous_completed_stock_close=previous_completed_stock_close,
            prior_completed_session_atr14=prior_completed_atr14, resolver_atr14_t0=resolver_atr14_t0,
            nifty_1500_close=nifty_1500_close, previous_completed_nifty_close=previous_completed_nifty_close,
        ))

    # --- Write feature CSV: frozen 9-feature order first, diagnostics after ---
    fieldnames = (["security_key", "event_t0", "abstain", "abstain_reason"] + FROZEN_FEATURE_ORDER
                  + ["stock_1500_close", "stock_high_through_1500", "stock_low_through_1500",
                     "previous_completed_stock_close", "prior_completed_session_atr14",
                     "resolver_atr14_t0", "nifty_1500_close", "previous_completed_nifty_close"])
    assert fieldnames[4:13] == FROZEN_FEATURE_ORDER, "feature-order schema drift"

    with FEATURES_CSV.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            w.writerow(asdict(r))

    # --- Integrity checks ---
    output_keys = {(r.security_key, r.event_t0) for r in out_rows}
    audit_keys = {(ev["security_key"], ev["event_t0"]) for ev in events}
    n_complete = sum(1 for r in out_rows if not r.abstain)
    n_abstained = sum(1 for r in out_rows if r.abstain)

    report = {
        "row_counts": {
            "audit_events": len(events),
            "feature_rows_written": len(out_rows),
            "feature_complete": n_complete,
            "abstained": n_abstained,
            "matches_frozen_label_set_count": len(out_rows) == len(events) == 5467,
        },
        "exact_event_key_join": {
            "output_keys_equal_audit_keys": output_keys == audit_keys,
            "orphaned_in_output": len(output_keys - audit_keys),
            "missing_from_output": len(audit_keys - output_keys),
        },
        "no_duplicate_keys": {"pass": len(seen_keys) == len(events)},
        "causal_timing": {
            "event_z20_recomputation_mismatches": event_z20_mismatches,
            "through_1500_close_matches_daily_series_close_1500_mismatches": close1500_mismatches,
            "atr14_prior_session_vs_resolver_atr14_t0": {
                "both_computable": atr14_pairs_both_present,
                "differ": atr14_pairs_differ,
                "note": "Confirms atr14_pct's prior-completed-session ATR14 is a genuinely "
                        "different computation from the resolver's t0-inclusive atr14_t0, not "
                        "an accidental duplicate.",
            },
        },
        "nifty_alignment": {
            "nifty_date_not_found": abstain_reasons.get("NIFTY_DATE_NOT_FOUND", 0),
            "nifty_insufficient_history": abstain_reasons.get("NIFTY_INSUFFICIENT_HISTORY", 0),
            "nifty_1500_close_missing": abstain_reasons.get("NIFTY_1500_CLOSE_MISSING", 0),
            "nifty_previous_close_non_positive": abstain_reasons.get("NIFTY_PREVIOUS_CLOSE_NON_POSITIVE", 0),
        },
        "missing_non_finite_abstentions": dict(abstain_reasons),
        "frozen_window_enforcement": {
            "events_checked": len(events),
            "violations": frozen_window_violations,
            "note": "Re-ran check_event_eligibility on each event's actual (backward-only) "
                    "feature lookback window, narrower than the label generator's forward-"
                    "inclusive window - a real, independent re-check, not an assumption.",
        },
        "feature_order_schema_compliance": {
            "frozen_feature_order": FROZEN_FEATURE_ORDER,
            "feature_spec_sha256_referenced": FEATURE_SPEC_SHA256,
            "csv_columns_5_to_13_match_frozen_order": fieldnames[4:13] == FROZEN_FEATURE_ORDER,
        },
        "no_classifier_fit": True,
        "validation_holdout_untouched": True,
    }

    with INTEGRITY_JSON.open("w", encoding="utf-8") as h:
        json.dump(report, h, indent=2)

    print(f"\nWrote {len(out_rows)} feature rows ({n_complete} complete, {n_abstained} abstained) -> {FEATURES_CSV}")
    print(f"Integrity report -> {INTEGRITY_JSON}")
    print(f"\nAbstain reasons: {dict(abstain_reasons)}")
    print(f"event_z20 recomputation mismatches: {event_z20_mismatches}")
    print(f"Frozen-window violations: {frozen_window_violations}")
    print("\nNo classifier fit. No model. VALIDATION and HOLDOUT untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
