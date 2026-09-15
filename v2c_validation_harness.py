"""V2-C VALIDATION one-shot execution harness. LOCAL FILES ONLY - no
Kite calls, no credentials, no network.

**STATUS AT FREEZE TIME: BUILT, TESTED ON SYNTHETIC FIXTURES, HASH-FROZEN
- NOT YET RUN AGAINST REAL VALIDATION DATA.** Per explicit instruction:
"Hold, do not open yet... After that harness passes synthetic and
fail-closed tests and is hash-frozen, explicitly authorize the
irreversible VALIDATION run." This module has never been invoked
against the real certified VALIDATION-period data as of its freeze -
every result quoted in its own tests comes from synthetic fixtures
(`TESTS/test_v2c_validation_harness_synthetic.py`) or, where TRAIN data
is used for validating the classifier-scoring reimplementation, real
but already-open TRAIN data - never VALIDATION.

**Revised once, after a real independent re-audit found six blocking
defects in the first frozen version** (preserved unmodified at
`SUPERSEDED_VALIDATION_HARNESS_V1_20260818/`): (1) feature scores and
per-event probabilities were not persisted at all - a predictive-gate
FAIL would have left almost no scoring evidence behind - fixed via the
new `EventScore`/`score_all_events()`, written to
`V2C_VALIDATION_FEATURE_SCORES.csv` unconditionally; (2) the bundle
write was not atomic - fixed via `_stage_and_finalize_bundle()`
(staging directory + atomic rename, never a partial final directory);
(3) a failure after VALIDATION reading began was unrecorded - fixed via
an immutable `RUN_STARTED` marker written before any VALIDATION file is
opened, and a hash-manifested `FAILED_INFRASTRUCTURE` bundle on any
exception, both refusing a silent rerun; (4) the naive sigmoid could
raise `OverflowError` on extreme logits - fixed via `_stable_sigmoid()`;
(5) `n_priced_trades` conflated priced, unflagged, and abstained rows -
fixed via a `counts` breakdown (`n_scored`, `n_flagged`, `n_priced`,
`n_untradeable_at_notional`, `n_not_flagged`, `n_abstained`,
`n_excluded_frozen_window`); (6) frozen-window/eligibility exclusions
were silently dropped - fixed via `ExcludedCandidate`, recorded even
though none are expected during VALIDATION.

**Why a one-shot design, not incremental like TRAIN's pipeline**: TRAIN
was built, audited, and frozen in separate independently-auditable
stages (labels, then features, then the fit) precisely because TRAIN
is not sealed - inspecting intermediate TRAIN results carries no
look-ahead risk. VALIDATION is sealed data: examining it in stages
would mean re-running (and thus re-exposing sealed data to) a
human/model decision loop after each partial peek, exactly the failure
mode this harness exists to prevent. So this module runs the entire
pipeline - event discovery, labeling, feature construction, scoring,
pricing, and all gate evaluations - as ONE atomic operation the first
and only time it touches real VALIDATION data, emitting a complete,
immutable audit bundle in that same run. No gate result may be
inspected, reacted to, and rerun; the run happens exactly once.

**Code-level authorization gate, not just a convention**: `main()`
refuses to run against the real certified data directory unless
invoked with the exact CLI flag
`--confirm=AUTHORIZE_IRREVERSIBLE_VALIDATION_RUN_20260818`. Every
individual pipeline function is also independently callable and
testable against synthetic fixture directories without that flag -
synthetic testing was never meant to require pretending to authorize a
real run.

Pipeline, in the order this harness executes it (matches the six
required stages exactly):

  1. Fail-closed verification of every frozen input this run depends
     on (epoch freeze, calendar, certified dataset manifest, TRAIN
     classifier fit + its manifest, economic-gate A2 + its manifest).
  2. VALIDATION event discovery + D0/D0-A1 labeling - REUSES
     `v2c_train_label_generation`'s `build_daily_series`,
     `compute_atr14`, `compute_event_z20`, `resolve_bar`,
     `trading_days_forward`, `load_intraday_bars` verbatim, scoped to
     VALIDATION's frozen epoch (2021-01-01..2022-03-31) instead of
     TRAIN's - never reimplemented, so D0/D0-A1 conformance is
     structurally guaranteed, not just intended.
  3. VALIDATION feature construction - REUSES
     `v2c_train_feature_construction`'s `through_1500_hilo`,
     `get_daily_series`, `get_bars_by_date`, `find_interval_file`,
     `load_security_intervals`, and the exact frozen 9-feature formulas,
     for the exact same causal-timing reason.
  4. Scoring via the frozen TRAIN scaler/model/threshold - a pure
     NumPy reimplementation of `StandardScaler.transform` +
     `LogisticRegression.predict_proba`, verified byte-for-byte against
     real (TRAIN, not VALIDATION) data in this harness's own test
     suite before ever being trusted on VALIDATION features.
  5. Trade pricing for every classifier-flagged event, under A2's
     sizing rule (`floor`, deployed-capital denominator,
     `UNTRADEABLE_AT_NOTIONAL`) and D0-exact exit pricing, priced with
     `ENGINE.v2c_validation_era_costs`.
  6. Gate evaluation, exactly once: the predictive gate
     (AUC-ROC>=0.55 on resolved VALIDATION events), then, only if that
     passes, economic gates E1 (n>=30), E2 (>=3 quarters), E3 (four
     required checks per A2 §3).
  7. Emit a complete, hash-manifested audit bundle in one write.

**V4-A2 - corrected after a real, authorized one-shot run ("Attempt 1")
actually failed in pre-flight.** Precise accounting, not the looser
"nothing was opened" description this file's own docstring used
before this correction: `verify_all_frozen_inputs()` performs
byte-level SHA-256 integrity reads of the certified dataset files
(Stage 1, before event discovery), so Attempt 1 DID read
VALIDATION-period file bytes for hash verification, before crashing on
the separately-checked A2 manifest. It did not parse, inspect, label,
score, summarize, or economically evaluate any VALIDATION-period price
observation - event discovery (Stage 2, the first stage that would
actually interpret a price value) was never entered. This is
preflight integrity verification, not a predictive or economic look at
VALIDATION, and does not invalidate the split (V4-A1's exact bytes
preserved unmodified at
`SUPERSEDED_VALIDATION_HARNESS_V4A1_20260818/`; Attempt 1's own
`RUN_STARTED` marker and `FAILED_INFRASTRUCTURE` bundle are likewise
preserved permanently and never cleared). Root cause: the frozen
economic-gate A2 hash manifest
(`V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json`) records its file
entries relative to `P01D_V2B_REGIME_TWO_PILLAR_20260816/`, while the
TRAIN classifier manifest records its entries relative to the project
root - a real, pre-existing mixed convention across two independently
frozen artifacts, never caught by synthetic tests because the test
fixtures had (wrongly) built their synthetic A2 manifest using the
"corrected" convention rather than replicating reality's actual one.
Fixed by: (1) `_verify_manifest_files()` gaining an explicit `base_dir`
parameter, with each call site in `verify_all_frozen_inputs()` passing
a fixed, hardcoded base per manifest (project root for the classifier
manifest, `P01D_V2B_REGIME_TWO_PILLAR_20260816/` for the A2 manifest) -
not a caller-facing bypass, since neither call site is parameterized
by anything outside this module; (2) correcting the synthetic fixtures
to replicate the real (buggy-looking but actually-frozen) convention;
(3) two new regression tests proving the fix does real work
(`test_a2_manifest_verification_fails_closed_if_resolved_against_the_wrong_base`)
and that pre-flight verification alone - with no VALIDATION event
discovery - now passes against the real, unmodified project files
(`test_real_environment_preflight_passes_without_entering_event_discovery`).

**A second, self-found gap surfaced while fixing the above**: a
`RUN_STARTED` marker's cryptographic validity persists indefinitely by
design (for the audit trail), but nothing previously stopped a
stale-but-genuine marker from an ALREADY-CONCLUDED attempt (success or
FAILED_INFRASTRUCTURE) from re-authorizing a fresh call to
`run_validation_harness()` if that function were ever invoked directly,
bypassing `execute_one_shot_run()`'s own separate pre-checks. Fixed by
(a) renaming `RUN_OUTPUT_DIRNAME`/`FAILED_BUNDLE_DIRNAME` to
`_ATTEMPT2`-suffixed canonical names so this second attempt can never
collide with or be confused with Attempt 1's permanently-preserved
evidence, and (b) `_require_one_shot_precondition()` now also refuses
outright if `RUN_OUTPUT_DIR` or `FAILED_BUNDLE_DIR` already exists,
structurally closing this gap for every future attempt, not just this
one. Two new regression tests prove a valid, well-formed, correctly
bound marker is refused once either bundle directory already exists
(`test_a_stale_but_valid_marker_cannot_reauthorize_reading_data_once_a_success_bundle_exists`,
`..._once_a_failure_bundle_exists`).

HOLDOUT is never read, referenced, or reachable from any function in
this module. `LIVE_TRADING_ENABLED` unaffected throughout; no broker
access; no real order ever considered.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import secrets
import sys
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

# `ENGINE` (needed for the VALIDATION-era cost module) lives inside
# P01D_V2B_REGIME_TWO_PILLAR_20260816/, one level below this script -
# added to sys.path by absolute, script-relative location (NOT cwd), so
# `import ENGINE.xxx` works regardless of the current working directory.
# Data-file paths elsewhere in this module still resolve relative to
# cwd, matching v2c_train_label_generation.py's own convention exactly -
# this insert only affects Python's import machinery, nothing else.
sys.path.insert(0, str((Path(__file__).resolve().parent / "P01D_V2B_REGIME_TWO_PILLAR_20260816")))

import v2c_certify_15min_dataset as cert
import v2c_train_feature_construction as featgen
import v2c_train_label_generation as labelgen
from v2c_frozen_data_validity_exclusions import check_event_eligibility

REAL_BASE = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816")

# The one and only real project root this harness is allowed to run
# destructively against - an absolute path, not a parameter, precisely
# so no caller (test or otherwise) can spoof "this is the real thing"
# by passing a look-alike relative structure. `v2c_train_label_generation`
# and `v2c_train_feature_construction` both resolve their own paths
# relative to the process's current working directory (not a parameter
# either), so this harness follows the same convention deliberately -
# the authorization guard below checks the CURRENT WORKING DIRECTORY,
# since that is what actually determines which files those reused
# functions touch.
REAL_PROJECT_ROOT = Path(r"C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN").resolve()

AUTHORIZATION_TOKEN = "AUTHORIZE_IRREVERSIBLE_VALIDATION_RUN_20260818"

VALIDATION_START = date(2021, 1, 1)
VALIDATION_END = date(2022, 3, 31)
OBSERVATION_TRADING_DAYS = labelgen.OBSERVATION_TRADING_DAYS
EVENT_Z20_THRESHOLD = labelgen.EVENT_Z20_THRESHOLD

FROZEN_FEATURE_ORDER = featgen.FROZEN_FEATURE_ORDER
NOTIONAL_TARGET = 100_000  # Rs 100,000, per A2 SS1

CLASSIFIER_FIT_JSON = REAL_BASE / "V2C_CLASSIFIER_TRAIN_20260818" / "V2C_TRAIN_CLASSIFIER_FIT_20260818.json"
CLASSIFIER_MANIFEST_JSON = REAL_BASE / "V2C_CLASSIFIER_TRAIN_20260818" / "V2C_TRAIN_CLASSIFIER_HASH_MANIFEST_20260818.json"
CLASSIFIER_MANIFEST_SHA = REAL_BASE / "V2C_CLASSIFIER_TRAIN_20260818" / "V2C_TRAIN_CLASSIFIER_HASH_MANIFEST_20260818.json.sha256"

A2_MD = REAL_BASE / "P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md"
A2_SHA = REAL_BASE / "P01D_V2C_ECONOMIC_GATE_ADDENDUM_A2_20260818.md.sha256"
A2_MANIFEST_JSON = REAL_BASE / "V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json"
A2_MANIFEST_SHA = REAL_BASE / "V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json.sha256"

# Attempt 1 (2026-08-18) failed during pre-flight input verification
# (a manifest path-resolution bug, fixed below) - RUN_STARTED and its
# FAILED_INFRASTRUCTURE bundle are preserved PERMANENTLY at these names
# as immutable evidence and are never reused, cleared, or deleted by
# this module. Attempt 2 uses distinct canonical names so it can never
# collide with, overwrite, or be confused with Attempt 1's artifacts.
RUN_OUTPUT_DIRNAME = "V2C_VALIDATION_RUN_20260818_ATTEMPT2"
FAILED_BUNDLE_DIRNAME = "V2C_VALIDATION_RUN_20260818_ATTEMPT2_FAILED_INFRASTRUCTURE"

# A single canonical set of locations - NOT parameters anywhere in the
# public API. The v3 re-audit found that exposing `marker_path`/
# `protected_root`/`out_dir` as caller-facing keyword arguments (added
# for test isolation) was itself a production bypass: any caller could
# point the gate at an arbitrary pre-existing file (an unrelated file
# already on disk satisfied the check, since it only tested `.exists()`)
# and unlock real data with no relationship to any genuine one-shot run.
# Tests now exercise this module's gated behavior exclusively via
# `monkeypatch.setattr` on these module-level constants (a test-only
# mechanism with no production calling convention), never via function
# parameters.
RUN_OUTPUT_DIR = REAL_BASE / RUN_OUTPUT_DIRNAME
FAILED_BUNDLE_DIR = REAL_BASE / FAILED_BUNDLE_DIRNAME
RUN_STARTED_MARKER = REAL_BASE / f"{RUN_OUTPUT_DIRNAME}.RUN_STARTED.json"
RUN_STARTED_MARKER_SHA = REAL_BASE / f"{RUN_OUTPUT_DIRNAME}.RUN_STARTED.json.sha256"


class HarnessRefusal(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_sidecar(target: Path, sidecar: Path, label: str) -> str:
    if not sidecar.exists():
        raise HarnessRefusal(f"REFUSING TO RUN: no sidecar hash for {target.name} ({label}).")
    recorded = sidecar.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = _sha256(target).lower()
    if recorded != actual:
        raise HarnessRefusal(f"REFUSING TO RUN: {target.name} hash mismatch against {sidecar.name} "
                              f"({label}) - recorded {recorded}, actual {actual}.")
    return actual


def _verify_manifest_files(manifest_path: Path, label: str, *, base_dir: Path = Path(".")) -> None:
    """Resolves every path in `manifest_path` relative to `base_dir`
    (defaulting to the current working directory, i.e. the project
    root - matching most frozen-manifest verifiers in this project).

    This parameter exists because the real, already-frozen manifests
    this harness depends on are NOT all written from the same base:
    `V2C_TRAIN_CLASSIFIER_HASH_MANIFEST_20260818.json` (and the dataset
    manifest) record paths relative to the project root, but
    `V2C_ECONOMIC_GATE_A2_HASH_MANIFEST_20260818.json` was built during
    an earlier phase of work with cwd set to
    `P01D_V2B_REGIME_TWO_PILLAR_20260816/`, and records its paths
    relative to THAT directory instead. The first real VALIDATION run
    attempt discovered this the hard way: `verify_all_frozen_inputs()`
    assumed one universal convention and refused (correctly, and before
    event discovery ever began - though after this same function had
    already performed a byte-level SHA-256 integrity read of the
    certified dataset files earlier in Stage 1, which is preflight
    verification, not a predictive or economic look at VALIDATION) on a
    real, pre-existing manifest it could not actually resolve. This
    parameter is not a
    caller-facing bypass of anything - it does not touch the one-shot
    gate, the marker, or VALIDATION access; it only tells this one
    verification step which frozen, already-published manifest's own
    documented base directory to resolve against, and each caller in
    `verify_all_frozen_inputs()` passes a fixed, hardcoded value - never
    something threaded in from outside this module."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        candidate = base_dir / entry["file"].replace("\\", "/")
        if not candidate.exists():
            raise HarnessRefusal(f"REFUSING TO RUN: {label} manifest-listed file {candidate} missing.")
        h = _sha256(candidate).lower()
        if h != entry["sha256"].lower():
            raise HarnessRefusal(f"REFUSING TO RUN: {candidate} hash mismatch against {label} manifest.")


def verify_all_frozen_inputs() -> dict:
    """Stage 1. Fail-closed - raises HarnessRefusal on any mismatch.
    All paths are relative to the current working directory, matching
    `v2c_train_label_generation.py` and `v2c_train_feature_construction.py`'s
    own convention exactly - this harness must be run from the project
    root, same as every script it reuses."""
    results = {}
    results["epoch_freeze"] = labelgen.verify_epoch_freeze_hash()
    results["calendar"] = cert.verify_calendar_hash()

    dataset_manifest = featgen.DATASET_MANIFEST_JSON
    dataset_manifest_sha = featgen.DATASET_MANIFEST_SHA
    results["dataset_manifest"] = _verify_sidecar(dataset_manifest, dataset_manifest_sha, "certified dataset")
    dm = json.loads(dataset_manifest.read_text(encoding="utf-8"))
    dm_dir = Path(dm["data_dir"].replace("\\", "/"))
    for entry in dm["files"]:
        candidate = dm_dir / entry["file"]
        if not candidate.exists():
            raise HarnessRefusal(f"REFUSING TO RUN: certified data file {candidate} missing.")
        if _sha256(candidate).lower() != entry["sha256"].lower():
            raise HarnessRefusal(f"REFUSING TO RUN: {candidate} hash mismatch against dataset manifest.")

    results["classifier_manifest"] = _verify_sidecar(
        CLASSIFIER_MANIFEST_JSON, CLASSIFIER_MANIFEST_SHA, "TRAIN classifier fit")
    # Recorded relative to the project root - see _verify_manifest_files's
    # docstring for why this is a per-manifest, hardcoded choice, not a
    # general parameter threaded in from outside this module.
    _verify_manifest_files(CLASSIFIER_MANIFEST_JSON, "TRAIN classifier fit", base_dir=Path("."))

    results["a2_doc"] = _verify_sidecar(A2_MD, A2_SHA, "economic-gate A2")
    results["a2_manifest"] = _verify_sidecar(A2_MANIFEST_JSON, A2_MANIFEST_SHA, "economic-gate A2 submission")
    # Recorded relative to P01D_V2B_REGIME_TWO_PILLAR_20260816/, not the
    # project root - this manifest was built with that as cwd during an
    # earlier phase of work (see _verify_manifest_files's docstring).
    _verify_manifest_files(A2_MANIFEST_JSON, "economic-gate A2 submission", base_dir=REAL_BASE)

    return results


# ---------------------------------------------------------------------------
# Stage 2: VALIDATION event discovery + D0/D0-A1 labeling
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationEvent:
    security_key: str
    event_t0: str
    event_z20: float
    close_t0_minus_1: float
    close_t0: float
    atr14_t0: float
    recovery_level: float
    stop_level: float
    observation_window_start: str
    observation_window_nominal_end: str
    observation_window_effective_end: str
    truncated_at_epoch_boundary: bool
    label: str
    resolution_timestamp: str
    resolution_price: float | None
    reason_code: str


@dataclass(frozen=True)
class ExcludedCandidate:
    """A would-be qualifying event (event_z20 fired, ATR/stop computable)
    that was excluded before becoming a `ValidationEvent` - recorded with
    its reason even though none are expected during VALIDATION, so an
    exclusion is never silently absent from the audit trail."""
    security_key: str
    event_t0: str
    reason: str
    detail: str
    lookback_start: str
    window_end: str


def discover_validation_events(*, cleaned_dir: Path, summary_csv: Path,
                                calendar: dict, epoch_start: date,
                                epoch_end: date) -> tuple[list[ValidationEvent], list[ExcludedCandidate]]:
    """Stage 2. Identical logic to `v2c_train_label_generation.main()`'s
    event-scanning loop, re-parameterized to VALIDATION's epoch and
    reusing the exact same underlying functions - never reimplemented.
    Returns (events, excluded_candidates) - every frozen-window/eligibility
    exclusion is recorded, not silently dropped."""
    trading_dates = sorted(date.fromisoformat(d) for d in calendar if calendar[d])
    trading_dates_in_epoch = [d for d in trading_dates if epoch_start <= d <= epoch_end]

    with summary_csv.open(newline="", encoding="utf-8") as h:
        summary_rows = list(csv.DictReader(h))

    events: list[ValidationEvent] = []
    excluded: list[ExcludedCandidate] = []
    seen_keys: set[tuple[str, str]] = set()

    for row in summary_rows:
        sec_key = row["security_key"]
        candidates = list(cleaned_dir.glob(f"NSE_{sec_key}_15minute_{row['from']}_{row['through']}.csv"))
        if not candidates:
            continue
        csv_path = candidates[0]

        daily = labelgen.build_daily_series(csv_path, epoch_end)
        if not daily:
            continue
        daily_dates = [b.session_date for b in daily]

        for t0_idx, bar in enumerate(daily):
            t0 = bar.session_date
            if not (epoch_start <= t0 <= epoch_end):
                continue

            event_z20 = labelgen.compute_event_z20(daily, t0_idx)
            if event_z20 is None or not (event_z20 < EVENT_Z20_THRESHOLD):
                continue

            close_t0_minus_1 = daily[t0_idx - 1].close
            close_t0 = daily[t0_idx].close
            atr_t0 = labelgen.compute_atr14(daily, t0_idx)
            if atr_t0 is None:
                continue

            recovery_level = close_t0_minus_1
            stop_level = close_t0 - 2.0 * atr_t0
            if stop_level <= 0:
                continue

            horizon_dates = labelgen.trading_days_forward(daily_dates, t0, OBSERVATION_TRADING_DAYS)
            calendar_horizon = labelgen.trading_days_forward(trading_dates, t0, OBSERVATION_TRADING_DAYS)
            if not calendar_horizon:
                continue
            nominal_window_end = calendar_horizon[-1]
            truncated = nominal_window_end > epoch_end
            window_end = min(nominal_window_end, epoch_end)

            lookback_idx = min(t0_idx - 20, t0_idx - 15)
            lookback_start = daily_dates[max(lookback_idx, 0)]

            eligibility = check_event_eligibility(sec_key, lookback_start, window_end)
            if not eligibility.eligible:
                excluded.append(ExcludedCandidate(
                    security_key=sec_key, event_t0=t0.isoformat(), reason="FROZEN_WINDOW_EXCLUSION",
                    detail=eligibility.reason, lookback_start=lookback_start.isoformat(),
                    window_end=window_end.isoformat(),
                ))
                continue

            event_key = (sec_key, t0.isoformat())
            if event_key in seen_keys:
                raise HarnessRefusal(f"INTEGRITY VIOLATION: duplicate event {event_key}.")
            seen_keys.add(event_key)

            label = "UNRESOLVED"
            resolution_ts = ""
            resolution_price = None

            if not horizon_dates:
                observation_window_start = None
                reason_code = "NO_FORWARD_DATA"
            else:
                observation_window_start = horizon_dates[0]
                if observation_window_start <= t0:
                    raise HarnessRefusal(f"INTEGRITY VIOLATION: observation_window_start "
                                          f"{observation_window_start} not strictly after t0 {t0}.")
                bars = labelgen.load_intraday_bars(csv_path, observation_window_start, window_end)
                reason_code = "EPOCH_BOUNDARY" if truncated else "WINDOW_END"

                for ts, o, h, l, c in bars:
                    lbl, price = labelgen.resolve_bar(o, h, l, c, stop_level, recovery_level)
                    if lbl is not None:
                        resolution_date = date.fromisoformat(ts[:10])
                        if resolution_date < observation_window_start:
                            raise HarnessRefusal(f"INTEGRITY VIOLATION: resolution before window start "
                                                  f"for {sec_key} {t0}.")
                        label = "DETERIORATING" if lbl == "DETERIORATING" else "REVERTING"
                        resolution_ts = ts
                        resolution_price = price
                        reason_code = "STOP_TOUCHED_OR_GAP" if label == "DETERIORATING" else "RECOVERY_CLOSE_OR_GAP"
                        break

            events.append(ValidationEvent(
                security_key=sec_key, event_t0=t0.isoformat(), event_z20=event_z20,
                close_t0_minus_1=close_t0_minus_1, close_t0=close_t0, atr14_t0=atr_t0,
                recovery_level=recovery_level, stop_level=stop_level,
                observation_window_start=(observation_window_start.isoformat() if observation_window_start else ""),
                observation_window_nominal_end=nominal_window_end.isoformat(),
                observation_window_effective_end=window_end.isoformat(),
                truncated_at_epoch_boundary=truncated, label=label,
                resolution_timestamp=resolution_ts, resolution_price=resolution_price,
                reason_code=reason_code,
            ))

    return events, excluded


# ---------------------------------------------------------------------------
# Stage 3: VALIDATION feature construction (causal, reuses TRAIN primitives)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationFeatureRow:
    security_key: str
    event_t0: str
    abstain: bool
    abstain_reason: str
    features: dict | None  # frozen 9-feature dict, in FROZEN_FEATURE_ORDER, or None if abstained


def build_validation_features(events: list[ValidationEvent], *, cleaned_dir: Path,
                               summary_csv: Path, nifty_csv: Path, epoch_end: date) -> list[ValidationFeatureRow]:
    """Stage 3. Reuses `v2c_train_feature_construction`'s primitives
    verbatim - the same causal formulas, the same through-1500 bar
    loading, the same prior-completed-session ATR14 distinction."""
    intervals = featgen.load_security_intervals()
    nifty_daily = featgen.get_daily_series(nifty_csv, epoch_end)
    nifty_index = {b.session_date: i for i, b in enumerate(nifty_daily)}

    out: list[ValidationFeatureRow] = []

    for ev in events:
        if ev.reason_code == "NO_FORWARD_DATA":
            out.append(ValidationFeatureRow(ev.security_key, ev.event_t0, True, "NO_FORWARD_DATA", None))
            continue

        t0 = date.fromisoformat(ev.event_t0)
        csv_path = featgen.find_interval_file(ev.security_key, t0, intervals)
        if csv_path is None:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: no certified interval file for "
                                  f"{ev.security_key} event {t0}.")

        daily = featgen.get_daily_series(csv_path, epoch_end)
        daily_dates = [b.session_date for b in daily]
        try:
            t0_idx = daily_dates.index(t0)
        except ValueError:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: {ev.security_key} {t0} not found in its "
                                  f"own daily series.")

        recomputed_z20 = labelgen.compute_event_z20(daily, t0_idx)
        if recomputed_z20 is None or abs(recomputed_z20 - ev.event_z20) > 1e-6:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: event_z20 recomputation mismatch for "
                                  f"{ev.security_key} {t0}.")

        abstain_reason = ""
        previous_completed_stock_close = daily[t0_idx - 1].close
        stock_1500_close = daily[t0_idx].close_1500
        hilo = featgen.through_1500_hilo(csv_path, t0)
        prior_completed_atr14 = labelgen.compute_atr14(daily, t0_idx - 1) if t0_idx - 1 >= 0 else None

        if hilo is None:
            abstain_reason = "NO_THROUGH_1500_BARS"
        elif prior_completed_atr14 is None:
            abstain_reason = "ATR14_INSUFFICIENT_HISTORY_PRIOR_SESSION"
        elif not previous_completed_stock_close or previous_completed_stock_close <= 0:
            abstain_reason = "PREVIOUS_CLOSE_NON_POSITIVE"
        elif stock_1500_close is None:
            abstain_reason = "STOCK_1500_CLOSE_MISSING"

        if not abstain_reason:
            stock_high, stock_low, hilo_close = hilo
            if abs(hilo_close - stock_1500_close) > 1e-6:
                raise HarnessRefusal(f"INTEGRITY VIOLATION: through-1500 close mismatch for "
                                      f"{ev.security_key} {t0}.")
            day_range = stock_high - stock_low
            if not math.isfinite(day_range) or day_range <= 0:
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
            out.append(ValidationFeatureRow(ev.security_key, ev.event_t0, True, abstain_reason, None))
            continue

        downside_return = stock_1500_close / previous_completed_stock_close - 1
        drawdown_from_day_high = stock_1500_close / stock_high - 1
        recovery_from_day_low = stock_1500_close / stock_low - 1
        close_location = (stock_1500_close - stock_low) / day_range
        atr14_pct = prior_completed_atr14 / previous_completed_stock_close
        intraday_range_pct = day_range / previous_completed_stock_close
        nifty_return = nifty_1500_close / previous_completed_nifty_close - 1
        relative_return = downside_return - nifty_return

        feats = {
            "event_z20": recomputed_z20, "downside_return": downside_return,
            "drawdown_from_day_high": drawdown_from_day_high, "recovery_from_day_low": recovery_from_day_low,
            "close_location": close_location, "atr14_pct": atr14_pct,
            "intraday_range_pct": intraday_range_pct, "nifty_return": nifty_return,
            "relative_return": relative_return,
        }
        if not all(math.isfinite(v) for v in feats.values()):
            out.append(ValidationFeatureRow(ev.security_key, ev.event_t0, True, "NON_FINITE_COMPUTED_FEATURE", None))
            continue

        out.append(ValidationFeatureRow(ev.security_key, ev.event_t0, False, "", feats))

    return out


# ---------------------------------------------------------------------------
# Stage 4: score with the frozen TRAIN scaler/model/threshold
# ---------------------------------------------------------------------------

def load_frozen_classifier(path: Path = CLASSIFIER_FIT_JSON) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_sigmoid(logit: float) -> float:
    """Numerically stable sigmoid - the naive `1/(1+exp(-logit))` raises
    `OverflowError` in CPython for logit far enough negative that
    `exp(-logit)` overflows a float (real risk here: an extreme
    `event_z20` outlier is entirely plausible in real VALIDATION data,
    and `event_z20` is unbounded by construction - only qualifies at
    `< -2.0`, never capped above). Splits on the sign of the logit so
    the exponential argument is always <= 0, which can only underflow
    to 0.0, never overflow."""
    if logit >= 0:
        z = math.exp(-logit)
        return 1.0 / (1.0 + z)
    z = math.exp(logit)
    return z / (1.0 + z)


def score_feature_vector(feats: dict, classifier: dict) -> float:
    """Pure NumPy reimplementation of StandardScaler.transform +
    LogisticRegression.predict_proba, in the frozen feature order.
    Verified byte-identical to sklearn's own output on real TRAIN data
    in TESTS/test_v2c_validation_harness_synthetic.py before ever being
    applied to VALIDATION features."""
    x = np.array([feats[f] for f in classifier["feature_order"]], dtype=float)
    mean = np.array(classifier["scaler"]["mean"], dtype=float)
    scale = np.array(classifier["scaler"]["scale"], dtype=float)
    z = (x - mean) / scale
    coef = np.array(classifier["model"]["coef"], dtype=float)
    intercept = float(classifier["model"]["intercept"])
    logit = float(np.dot(z, coef) + intercept)
    return _stable_sigmoid(logit)


@dataclass(frozen=True)
class EventScore:
    """One row per event, unconditionally - the full scoring evidence
    (key, abstention status, all nine frozen features, probability,
    label, flagged status). Persisted in the audit bundle regardless of
    what any downstream gate decides, so a predictive-gate FAIL still
    leaves the full scoring record intact for inspection."""
    security_key: str
    event_t0: str
    label: str
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
    p_reverting: float | None = None
    flagged: bool = False


_EVENT_SCORE_FIELDNAMES = [f.name for f in fields(EventScore)]


def score_all_events(events: list[ValidationEvent], feature_rows: list[ValidationFeatureRow],
                      classifier: dict) -> list[EventScore]:
    """Stage 4 (full). Scores every feature-complete event exactly once -
    the single source of truth every downstream function (predictive
    gate, pricing) reads from, so scoring is never silently recomputed
    twice with a risk of drifting inconsistently."""
    feat_by_key = {(r.security_key, r.event_t0): r for r in feature_rows}
    threshold = classifier["threshold"]
    out: list[EventScore] = []

    for ev in events:
        key = (ev.security_key, ev.event_t0)
        frow = feat_by_key.get(key)
        if frow is None:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: no feature row for event {key}.")

        if frow.abstain:
            out.append(EventScore(security_key=ev.security_key, event_t0=ev.event_t0, label=ev.label,
                                   abstain=True, abstain_reason=frow.abstain_reason))
            continue

        p_rev = score_feature_vector(frow.features, classifier)
        flagged = p_rev >= threshold
        out.append(EventScore(
            security_key=ev.security_key, event_t0=ev.event_t0, label=ev.label,
            abstain=False, abstain_reason="", p_reverting=p_rev, flagged=flagged,
            **frow.features,
        ))

    return out


# ---------------------------------------------------------------------------
# Stage 5: trade pricing (A2 sizing + D0-exact exit price + VALIDATION costs)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PricedTrade:
    security_key: str
    event_t0: str
    label: str
    p_reverting: float
    flagged: bool
    price_status: str  # PRICED / UNTRADEABLE_AT_NOTIONAL / ABSTAIN_NO_ENTRY / ABSTAIN_NO_FEATURES
    trade_type: str = ""
    quantity: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    entry_value: float = 0.0
    exit_value: float = 0.0
    total_transaction_costs: float = 0.0
    dp_charge_upper_bound: float = 0.0
    net_return: float = 0.0
    net_return_dp_stressed: float = 0.0
    quarter: str = ""


def price_flagged_trade(ev: ValidationEvent, entry_price: float, entry_date: date,
                         exit_price: float, exit_date: date, *, vcost_module) -> dict:
    """A2 sizing rule + costed round trip -> economics dict. Raises
    HarnessRefusal on internal inconsistency; returns
    {"status": "UNTRADEABLE_AT_NOTIONAL"} for the named degenerate case."""
    quantity = int(NOTIONAL_TARGET // entry_price)  # floor, per A2 SS1
    if quantity < 1:
        return {"status": "UNTRADEABLE_AT_NOTIONAL"}

    trade_type = "INTRADAY" if exit_date == entry_date else "DELIVERY"
    if trade_type == "INTRADAY":
        rt = vcost_module.validation_era_intraday_round_trip(
            quantity=quantity, buy_price=entry_price, sell_price=exit_price, trade_date=entry_date)
    else:
        rt = vcost_module.validation_era_delivery_round_trip(
            quantity=quantity, buy_price=entry_price, sell_price=exit_price,
            buy_date=entry_date, sell_date=exit_date)

    entry_value = quantity * entry_price
    exit_value = quantity * exit_price
    total_costs = float(rt.total)
    dp_bound = float(rt.dp_charge_upper_bound)

    net_return = (exit_value - entry_value - total_costs) / entry_value
    net_return_dp_stressed = (exit_value - entry_value - total_costs - dp_bound) / entry_value

    return {
        "status": "PRICED", "trade_type": trade_type, "quantity": quantity,
        "entry_value": entry_value, "exit_value": exit_value,
        "total_transaction_costs": total_costs, "dp_charge_upper_bound": dp_bound,
        "net_return": net_return, "net_return_dp_stressed": net_return_dp_stressed,
    }


def price_all_flagged_events(events: list[ValidationEvent], scores: list[EventScore],
                              *, cleaned_dir: Path, summary_csv: Path,
                              epoch_end: date, vcost_module) -> list[PricedTrade]:
    """Prices every event using the ALREADY-COMPUTED `scores` (from
    `score_all_events`) - never recomputes a probability, so pricing can
    never silently diverge from what was persisted in the audit bundle."""
    intervals = featgen.load_security_intervals()
    score_by_key = {(s.security_key, s.event_t0): s for s in scores}
    out: list[PricedTrade] = []

    for ev in events:
        key = (ev.security_key, ev.event_t0)
        score = score_by_key.get(key)
        if score is None:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: no score row for event {key}.")

        if score.abstain:
            status = "ABSTAIN_NO_ENTRY" if score.abstain_reason == "NO_FORWARD_DATA" else "ABSTAIN_NO_FEATURES"
            out.append(PricedTrade(ev.security_key, ev.event_t0, ev.label, 0.0, False, status))
            continue

        p_rev = score.p_reverting
        flagged = score.flagged
        if not flagged:
            out.append(PricedTrade(ev.security_key, ev.event_t0, ev.label, p_rev, False, "NOT_FLAGGED"))
            continue

        t0 = date.fromisoformat(ev.event_t0)
        window_start = date.fromisoformat(ev.observation_window_start)
        window_end = date.fromisoformat(ev.observation_window_effective_end)
        csv_path = featgen.find_interval_file(ev.security_key, t0, intervals)
        if csv_path is None:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: no interval file for {key}.")

        bars = labelgen.load_intraday_bars(csv_path, window_start, window_end)
        if not bars:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: empty observation window for flagged event {key}.")
        entry_ts, entry_open = bars[0][0], bars[0][1]
        entry_date = date.fromisoformat(entry_ts[:10])
        if entry_date != window_start:
            raise HarnessRefusal(f"INTEGRITY VIOLATION: entry date mismatch for {key}.")

        if ev.label in ("REVERTING", "DETERIORATING"):
            exit_ts = ev.resolution_timestamp
            exit_price = ev.resolution_price
            exit_date = date.fromisoformat(exit_ts[:10])
        else:  # UNRESOLVED, window exhausted (not NO_FORWARD_DATA, already excluded above)
            exit_ts, _, _, _, exit_close = bars[-1]
            exit_date = date.fromisoformat(exit_ts[:10])
            exit_price = exit_close

        econ = price_flagged_trade(ev, entry_open, entry_date, exit_price, exit_date, vcost_module=vcost_module)
        if econ["status"] == "UNTRADEABLE_AT_NOTIONAL":
            out.append(PricedTrade(ev.security_key, ev.event_t0, ev.label, p_rev, True,
                                    "UNTRADEABLE_AT_NOTIONAL"))
            continue

        q = (entry_date.month - 1) // 3 + 1
        out.append(PricedTrade(
            ev.security_key, ev.event_t0, ev.label, p_rev, True, "PRICED",
            trade_type=econ["trade_type"], quantity=econ["quantity"], entry_price=entry_open,
            exit_price=exit_price, entry_value=econ["entry_value"], exit_value=econ["exit_value"],
            total_transaction_costs=econ["total_transaction_costs"],
            dp_charge_upper_bound=econ["dp_charge_upper_bound"], net_return=econ["net_return"],
            net_return_dp_stressed=econ["net_return_dp_stressed"], quarter=f"{entry_date.year}Q{q}",
        ))

    return out


# ---------------------------------------------------------------------------
# Stage 6: gate evaluation, exactly once
# ---------------------------------------------------------------------------

def evaluate_predictive_gate(scores: list[EventScore]) -> dict:
    """Uses the already-computed `scores` (from `score_all_events`) -
    never recomputes a probability."""
    y_true, y_score = [], []
    for s in scores:
        if s.label not in ("REVERTING", "DETERIORATING") or s.abstain:
            continue
        y_true.append(1 if s.label == "REVERTING" else 0)
        y_score.append(s.p_reverting)

    n = len(y_true)
    if n == 0 or len(set(y_true)) < 2:
        return {"verdict": "UNDECIDABLE", "reason": "insufficient class diversity in resolved VALIDATION events",
                "n_resolved_scored": n}

    auc = _auc_roc(y_true, y_score)
    passed = auc >= 0.55
    return {"verdict": "PASS" if passed else "FAIL", "auc_roc": auc, "n_resolved_scored": n,
            "threshold": 0.55}


def _auc_roc(y_true: list[int], y_score: list[float]) -> float:
    """Rank-based AUC (Mann-Whitney U), no sklearn dependency needed here."""
    pos = [s for s, y in zip(y_score, y_true) if y == 1]
    neg = [s for s, y in zip(y_score, y_true) if y == 0]
    if not pos or not neg:
        raise HarnessRefusal("INTEGRITY VIOLATION: AUC requires both classes present.")
    wins = 0.0
    for p in pos:
        for ngv in neg:
            if p > ngv:
                wins += 1.0
            elif p == ngv:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def evaluate_economic_gates(trades: list[PricedTrade]) -> dict:
    priced = [t for t in trades if t.flagged and t.price_status == "PRICED"]
    n = len(priced)

    e1_pass = n >= 30
    if not e1_pass:
        return {"e1": {"verdict": "UNDECIDABLE", "n_flagged_priced": n, "minimum": 30},
                "e2": {"verdict": "NOT_EVALUATED"}, "e3": {"verdict": "NOT_EVALUATED"}}

    quarters = {t.quarter for t in priced}
    e2_pass = len(quarters) >= 3
    if not e2_pass:
        return {"e1": {"verdict": "PASS", "n_flagged_priced": n},
                "e2": {"verdict": "UNDECIDABLE", "n_quarters": len(quarters), "minimum": 3,
                       "quarters_present": sorted(quarters)},
                "e3": {"verdict": "NOT_EVALUATED"}}

    returns = [t.net_return for t in priced]
    returns_dp = [t.net_return_dp_stressed for t in priced]

    mean_return = sum(returns) / n
    win_rate = sum(1 for r in returns if r > 0) / n
    mean_return_dp = sum(returns_dp) / n
    win_rate_dp = sum(1 for r in returns_dp if r > 0) / n

    checks = {
        "mean_return_dp_excluded": {"value": mean_return, "threshold": 0.003, "pass": mean_return >= 0.003},
        "win_rate_dp_excluded": {"value": win_rate, "threshold": 0.50, "pass": win_rate >= 0.50},
        "mean_return_dp_stressed": {"value": mean_return_dp, "threshold": 0.003, "pass": mean_return_dp >= 0.003},
        "win_rate_dp_stressed": {"value": win_rate_dp, "threshold": 0.50, "pass": win_rate_dp >= 0.50},
    }
    e3_pass = all(c["pass"] for c in checks.values())

    return {
        "e1": {"verdict": "PASS", "n_flagged_priced": n},
        "e2": {"verdict": "PASS", "n_quarters": len(quarters), "quarters_present": sorted(quarters)},
        "e3": {"verdict": "PASS" if e3_pass else "FAIL", "checks": checks},
    }


# ---------------------------------------------------------------------------
# Orchestrator - the one-shot run
# ---------------------------------------------------------------------------

def _compute_marker_integrity_hash(fields: dict) -> str:
    """SHA256 over the marker's own fields, canonically serialized
    (sorted keys, so field order can never change the hash). Not a
    secret-keyed MAC - no secret-management infrastructure exists in
    this project to derive one from, and no in-process check can defend
    against a caller with source-code access recomputing the same public
    hash function in any dynamic language. What this DOES provide,
    exactly like every other frozen artifact's `.sha256` sidecar
    elsewhere in this project: detection of accidental or naive
    tampering (a hand-edited timestamp, a marker copied from one run and
    reused for another), and a requirement that deliberate forgery
    explicitly reproduce this function rather than editing one field."""
    canonical = json.dumps(fields, indent=2, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_run_started_marker(*, marker_path: Path, marker_sha_path: Path,
                               out_dir: Path, failed_dir: Path) -> str:
    """Atomic, exclusive marker creation (`mode="x"`, see the docstring
    on the fix this replaced), cryptographically bound to the exact
    `out_dir`/`failed_dir` this run will use, with a `.sha256` sidecar
    written the same exclusive way - the same content+sidecar
    tamper-evidence pattern this project already uses for every other
    frozen artifact. Returns the run_id."""
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = secrets.token_hex(16)
    fields = {
        "run_id": run_id,
        "run_started_utc": datetime.now(timezone.utc).isoformat(),
        "output_dirname": RUN_OUTPUT_DIRNAME,
        "out_dir": str(out_dir.resolve()),
        "failed_dir": str(failed_dir.resolve()),
    }
    integrity_hash = _compute_marker_integrity_hash(fields)
    serialized_bytes = json.dumps(dict(fields, integrity_sha256=integrity_hash), indent=2).encode("utf-8")

    # Binary mode, deliberately - a text-mode write on Windows translates
    # "\n" to "\r\n" unless `newline=""` is passed, which would make the
    # bytes actually on disk differ from whatever was hashed beforehand.
    # Writing bytes directly and hashing those exact bytes (not a
    # pre-write string) guarantees the sidecar can never disagree with
    # the file it describes because of a platform line-ending quirk.
    try:
        with marker_path.open("xb") as h:
            h.write(serialized_bytes)
    except FileExistsError:
        raise HarnessRefusal(
            f"REFUSING TO START: {marker_path} already exists - a run already started at this "
            f"output location (or a concurrent run is already in progress) and must not be "
            f"silently repeated. Investigate (and deliberately clear the marker and its sidecar "
            f"only once you understand what happened) before starting a new run."
        )

    file_hash = hashlib.sha256(marker_path.read_bytes()).hexdigest()
    try:
        with marker_sha_path.open("xb") as h:
            h.write((file_hash + "  " + marker_path.name + "\n").encode("utf-8"))
    except FileExistsError:
        # The marker itself was just newly created above (exclusive-create
        # succeeded) but its sidecar slot was already occupied - an
        # inconsistent state. Left in place, not deleted, for forensics;
        # refuse rather than proceed with an unsealed marker.
        raise HarnessRefusal(
            f"REFUSING TO START: {marker_sha_path} already exists even though {marker_path} was "
            f"just newly created - inconsistent state, investigate before proceeding."
        )
    return run_id


# Exact, closed schema - a marker must have precisely these keys, each of
# the given type. No extra fields, no missing fields, nothing coerced.
_MARKER_SCHEMA: dict[str, type] = {
    "run_id": str,
    "run_started_utc": str,
    "output_dirname": str,
    "out_dir": str,
    "failed_dir": str,
    "integrity_sha256": str,
}
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")  # secrets.token_hex(16)
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _validate_marker_schema(payload) -> dict:
    """Strict schema validation, run BEFORE any hash/identity check makes
    sense to attempt - malformed input must produce a clean
    `HarnessRefusal`, never an unhandled `AttributeError`/`KeyError`/
    `TypeError` from code that assumed a well-shaped dict. Checks, in
    order: it's a JSON object at all; its key set is exactly the schema
    (no missing, no extra); every field has the schema's exact type; and
    three semantic checks a well-typed-but-wrong marker could still
    fail - `output_dirname` matches the frozen constant, `run_id` is a
    well-formed 128-bit hex token, `run_started_utc` is a parseable
    ISO-8601 timestamp and `integrity_sha256` is a well-formed SHA256
    hex digest (so a later mismatch is a genuine content difference, not
    an artifact of a malformed field)."""
    if not isinstance(payload, dict):
        raise HarnessRefusal(f"REFUSING TO RUN: marker content is not a JSON object "
                              f"({type(payload).__name__}) - malformed, refusing.")

    actual_keys = set(payload.keys())
    expected_keys = set(_MARKER_SCHEMA.keys())
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise HarnessRefusal(f"REFUSING TO RUN: marker schema mismatch - missing fields {missing}, "
                              f"unexpected extra fields {extra} - refusing to trust a "
                              f"non-conforming marker.")

    for key, expected_type in _MARKER_SCHEMA.items():
        if not isinstance(payload[key], expected_type) or isinstance(payload[key], bool):
            raise HarnessRefusal(f"REFUSING TO RUN: marker field '{key}' has the wrong type "
                                  f"({type(payload[key]).__name__}, expected {expected_type.__name__}) "
                                  f"- refusing.")

    if payload["output_dirname"] != RUN_OUTPUT_DIRNAME:
        raise HarnessRefusal(f"REFUSING TO RUN: marker's output_dirname "
                              f"({payload['output_dirname']!r}) does not match the frozen constant "
                              f"({RUN_OUTPUT_DIRNAME!r}) - refusing.")
    if not _RUN_ID_PATTERN.match(payload["run_id"]):
        raise HarnessRefusal(f"REFUSING TO RUN: marker's run_id is not a well-formed 128-bit hex "
                              f"token - refusing.")
    if not _SHA256_HEX_PATTERN.match(payload["integrity_sha256"]):
        raise HarnessRefusal(f"REFUSING TO RUN: marker's integrity_sha256 is not a well-formed "
                              f"SHA256 hex digest - refusing.")
    try:
        datetime.fromisoformat(payload["run_started_utc"])
    except ValueError:
        raise HarnessRefusal(f"REFUSING TO RUN: marker's run_started_utc "
                              f"({payload['run_started_utc']!r}) is not a valid ISO-8601 timestamp "
                              f"- refusing.")

    return payload


def _verify_run_started_marker(*, marker_path: Path, marker_sha_path: Path,
                                expected_out_dir: Path, expected_failed_dir: Path) -> dict:
    """Fail-closed on every dimension an arbitrary, malformed, or stale
    file could fail: existence, sidecar presence, sidecar/file hash
    match (catches any post-creation edit), well-formed JSON, exact
    schema conformance (`_validate_marker_schema` - catches missing,
    extra, or wrongly-typed fields, and a handful of semantic checks),
    internal integrity-hash self-consistency (catches a marker whose
    sidecar was regenerated to match tampered content), and exact
    out_dir/failed_dir identity match (catches a genuine, unmodified,
    well-formed marker from a DIFFERENT run being reused or
    misattributed to this one). Returns the verified payload, including
    `run_id` for explicit linkage into whatever bundle this run
    produces."""
    if not marker_path.exists():
        raise HarnessRefusal(
            "REFUSING TO RUN: this would read the real certified VALIDATION-period data, which "
            "remains SEALED, and no RUN_STARTED marker exists proving the one-shot pre-flight "
            "sequence has already run."
        )
    if not marker_sha_path.exists():
        raise HarnessRefusal(f"REFUSING TO RUN: {marker_path} exists but has no sidecar hash - "
                              f"untrusted, refusing rather than assuming it is genuine.")

    recorded = marker_sha_path.read_text(encoding="utf-8").split()[0].strip().lower()
    raw = marker_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest().lower()
    if recorded != actual:
        raise HarnessRefusal(f"REFUSING TO RUN: {marker_path} hash mismatch against its sidecar - "
                              f"modified or corrupted after creation, refusing to trust it.")

    try:
        raw_payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessRefusal(f"REFUSING TO RUN: {marker_path} is not valid JSON ({exc}) - "
                              f"malformed, refusing.")

    payload = _validate_marker_schema(raw_payload)

    claimed_integrity = payload["integrity_sha256"]
    fields = {k: v for k, v in payload.items() if k != "integrity_sha256"}
    if claimed_integrity != _compute_marker_integrity_hash(fields):
        raise HarnessRefusal(f"REFUSING TO RUN: {marker_path}'s internal integrity hash does not "
                              f"match its own recorded fields - tampered content, refusing.")

    if payload["out_dir"] != str(expected_out_dir.resolve()):
        raise HarnessRefusal(f"REFUSING TO RUN: {marker_path}'s recorded out_dir does not match "
                              f"this run's target ({expected_out_dir}) - mismatched marker/output "
                              f"identity, refusing.")
    if payload["failed_dir"] != str(expected_failed_dir.resolve()):
        raise HarnessRefusal(f"REFUSING TO RUN: {marker_path}'s recorded failed_dir does not match "
                              f"this run's target ({expected_failed_dir}) - mismatched "
                              f"marker/output identity, refusing.")

    return payload


def _require_one_shot_precondition() -> str | None:
    """The ONLY gate on real-data access in the pipeline function itself,
    and it takes NO parameters - every path it checks is a hardcoded
    module-level constant. The v3 re-audit found that exposing
    `marker_path`/`protected_root` as keyword arguments was itself a
    production bypass: a caller could point the check at any arbitrary
    pre-existing file (the check only tested `.exists()`) and unlock real
    data with zero relationship to a genuine one-shot run. There is no
    supported way to reach real data from `run_validation_harness()`
    other than via a marker that already exists, at the one canonical
    location, with a valid sidecar, a schema-conforming and internally
    self-consistent payload, and matching out_dir/failed_dir identity -
    which only `execute_one_shot_run()`'s exclusive-create step can
    produce. Tests exercise this via `monkeypatch.setattr` on the module
    constants themselves (see TESTS/), never via a parameter.

    Returns the verified marker's `run_id` when the real-root gate was
    actually exercised, so it can be threaded into the resulting
    bundle for explicit marker-to-result linkage (per the fourth
    re-audit: neither bundle type previously recorded which RUN_STARTED
    marker had authorized it). Returns `None` when this call is away
    from the real project root (every synthetic test) and the gate was
    a no-op - there is no marker to link to in that case.

    Also refuses if EITHER canonical bundle directory already exists -
    found while fixing the real Attempt 1 infrastructure failure: a
    marker's validity alone is necessary but not sufficient. A marker
    stays permanently valid once written (by design, for the audit
    trail), so a stale-but-genuine marker from an ALREADY-CONCLUDED
    attempt (successful or failed) would otherwise still satisfy
    `_verify_run_started_marker` and let this function proceed into
    real data again if called directly, bypassing
    `execute_one_shot_run()`'s own (separate) RUN_OUTPUT_DIR/
    FAILED_BUNDLE_DIR pre-checks entirely. Checking bundle existence
    HERE too closes that gap regardless of which entry point is used."""
    if Path.cwd().resolve() != REAL_PROJECT_ROOT.resolve():
        return None
    if RUN_OUTPUT_DIR.exists():
        raise HarnessRefusal(
            f"REFUSING TO RUN: {RUN_OUTPUT_DIR} already exists - this canonical location already "
            f"has a completed VALIDATION run bundle; refusing to read real data again for it."
        )
    if FAILED_BUNDLE_DIR.exists():
        raise HarnessRefusal(
            f"REFUSING TO RUN: {FAILED_BUNDLE_DIR} already exists - a prior attempt at this "
            f"canonical location already concluded (as an infrastructure failure) and was "
            f"recorded; refusing to read real data again for it, even with an otherwise-valid "
            f"RUN_STARTED marker. A new attempt needs its own distinct canonical names."
        )
    payload = _verify_run_started_marker(marker_path=RUN_STARTED_MARKER, marker_sha_path=RUN_STARTED_MARKER_SHA,
                                          expected_out_dir=RUN_OUTPUT_DIR, expected_failed_dir=FAILED_BUNDLE_DIR)
    return payload["run_id"]


def run_validation_harness() -> dict:
    """The pipeline function. Takes NO parameters that affect data
    access or the one-shot gate - reads relative to the current working
    directory, same as every reused TRAIN function, and its only
    precondition (`_require_one_shot_precondition`) is checked against
    hardcoded module constants exclusively."""
    run_id = _require_one_shot_precondition()

    verify_all_frozen_inputs()

    base = REAL_BASE
    cleaned_dir_real = base / "V2C_15MIN_DATA_CLEANED"
    summary_csv = base / "V2C_15MIN_DATA_ACQUIRED" / "V2C_15MIN_ACQUISITION_SUMMARY.csv"
    nifty_matches = sorted(cleaned_dir_real.glob("NSE_NIFTY 50_15minute_*.csv"))
    if not nifty_matches:
        raise HarnessRefusal(f"REFUSING TO RUN: no certified NIFTY 50 file found in {cleaned_dir_real}.")
    nifty_csv = nifty_matches[0]
    calendar = cert.load_calendar()

    events, excluded = discover_validation_events(cleaned_dir=cleaned_dir_real, summary_csv=summary_csv,
                                                    calendar=calendar, epoch_start=VALIDATION_START,
                                                    epoch_end=VALIDATION_END)
    feature_rows = build_validation_features(events, cleaned_dir=cleaned_dir_real, summary_csv=summary_csv,
                                              nifty_csv=nifty_csv, epoch_end=VALIDATION_END)
    classifier = load_frozen_classifier()
    scores = score_all_events(events, feature_rows, classifier)

    predictive_gate = evaluate_predictive_gate(scores)

    economic_gates = {"e1": {"verdict": "NOT_EVALUATED"}, "e2": {"verdict": "NOT_EVALUATED"},
                       "e3": {"verdict": "NOT_EVALUATED"}}
    trades: list[PricedTrade] = []
    if predictive_gate["verdict"] == "PASS":
        import ENGINE.v2c_validation_era_costs as vcost
        trades = price_all_flagged_events(events, scores, cleaned_dir=cleaned_dir_real,
                                           summary_csv=summary_csv, epoch_end=VALIDATION_END, vcost_module=vcost)
        economic_gates = evaluate_economic_gates(trades)

    counts = {
        "n_events": len(events),
        "n_excluded_frozen_window": len(excluded),
        "n_scored": sum(1 for s in scores if not s.abstain),
        "n_abstained": sum(1 for s in scores if s.abstain),
        "n_flagged": sum(1 for s in scores if s.flagged),
        "n_priced": sum(1 for t in trades if t.price_status == "PRICED"),
        "n_untradeable_at_notional": sum(1 for t in trades if t.price_status == "UNTRADEABLE_AT_NOTIONAL"),
        # Computed directly from `scores`, like `n_flagged` - NOT from
        # `trades`, which is deliberately empty whenever the predictive
        # gate does not pass (pricing is correctly skipped). Deriving
        # this from `trades` would silently report zero unflagged events
        # in exactly the case - a failed predictive gate - where the
        # scoring evidence matters most.
        "n_not_flagged": sum(1 for s in scores if not s.abstain and not s.flagged),
    }
    bundle = {
        # Explicit linkage to the RUN_STARTED marker that authorized this
        # run (None for synthetic/test runs, where the gate was a no-op
        # and there is no real marker to link to) - the fourth re-audit
        # found neither bundle type recorded this at all.
        "run_id": run_id,
        "counts": counts,
        "predictive_gate": predictive_gate, "economic_gates": economic_gates,
        "frozen_inputs_verified": True,
    }
    return {"events": events, "excluded": excluded, "feature_rows": feature_rows, "scores": scores,
            "trades": trades, "run_id": run_id, "bundle": bundle}


def _write_dataclass_csv(path: Path, fieldnames: list[str], rows: list) -> None:
    """Always writes a real header, even for zero rows - a zero-event
    VALIDATION population is a legitimate, auditable outcome, not a
    reason to write a malformed or headerless file."""
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def _stage_and_finalize_bundle(build_files_fn, out_dir: Path) -> Path:
    """Writes every bundle file into a STAGING directory (a sibling of
    `out_dir`, so the rename is atomic on the same filesystem), computes
    a hash manifest over the staged files, then atomically renames
    staging -> `out_dir`. If either `out_dir` or a stale staging
    directory already exists, refuses outright - a completed run is
    immutable, and a leftover staging directory from an earlier
    interrupted attempt must be investigated deliberately, never
    silently overwritten or retried. On any exception during staging,
    the partial staging directory is left in place (not deleted) as
    forensic evidence, and the exception propagates."""
    if out_dir.exists():
        raise HarnessRefusal(f"REFUSING TO OVERWRITE: {out_dir} already exists - a VALIDATION run "
                              f"bundle is immutable once written.")
    staging_dir = out_dir.parent / f".{out_dir.name}.STAGING"
    # `mkdir()` without `exist_ok` is itself atomic (O_EXCL-equivalent at
    # the OS level) - the try/except is the real protection, not a
    # preceding `.exists()` check-then-create, which would carry the
    # same TOCTOU race the RUN_STARTED marker fix above closes.
    try:
        staging_dir.mkdir(parents=True)
    except FileExistsError:
        raise HarnessRefusal(
            f"REFUSING TO RUN: stale staging directory {staging_dir} already exists, evidence of an "
            f"earlier interrupted write (or a concurrent run) - investigate and clear it "
            f"deliberately, never silently."
        )

    build_files_fn(staging_dir)  # left in place on exception - not wrapped in try/except here

    manifest = {"files": []}
    for f in sorted(staging_dir.iterdir()):
        manifest["files"].append({"file": f.name, "sha256": _sha256(f), "size_bytes": f.stat().st_size})
    manifest_path = staging_dir / "V2C_VALIDATION_RUN_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (staging_dir / "V2C_VALIDATION_RUN_HASH_MANIFEST.json.sha256").write_text(
        _sha256(manifest_path) + "  V2C_VALIDATION_RUN_HASH_MANIFEST.json\n", encoding="utf-8")

    staging_dir.rename(out_dir)  # atomic on the same filesystem (same parent directory)
    return out_dir


def write_audit_bundle(result: dict, out_dir: Path) -> Path:
    def build(staging_dir: Path) -> None:
        _write_dataclass_csv(staging_dir / "V2C_VALIDATION_EVENT_AUDIT.csv",
                              [f.name for f in fields(ValidationEvent)], result["events"])
        _write_dataclass_csv(staging_dir / "V2C_VALIDATION_FEATURE_SCORES.csv",
                              _EVENT_SCORE_FIELDNAMES, result["scores"])
        _write_dataclass_csv(staging_dir / "V2C_VALIDATION_EXCLUDED_CANDIDATES.csv",
                              [f.name for f in fields(ExcludedCandidate)], result["excluded"])
        _write_dataclass_csv(staging_dir / "V2C_VALIDATION_TRADE_PRICING.csv",
                              [f.name for f in fields(PricedTrade)], result["trades"])
        with (staging_dir / "V2C_VALIDATION_GATE_VERDICT.json").open("w", encoding="utf-8") as h:
            json.dump(result["bundle"], h, indent=2, default=str)

    return _stage_and_finalize_bundle(build, out_dir)


def _write_failed_infrastructure_bundle(exc: BaseException, stage: str, out_dir: Path, *,
                                         run_id: str | None) -> Path:
    """A run that raises after RUN_STARTED has definitionally already
    begun reading (or attempting to read) VALIDATION data - this must
    never be silently rerun or mistaken for a research-negative gate
    verdict. Produces its own small, hash-manifested, immutable bundle
    naming the failure explicitly, using the same staging+atomic-rename
    mechanism as a successful run. Records `run_id` - the SAME marker
    identity `execute_one_shot_run` just created - so a failure bundle
    is explicitly traceable to the one RUN_STARTED marker that
    authorized the attempt, exactly like a success bundle now is."""
    payload = {
        "status": "FAILED_INFRASTRUCTURE",
        "run_id": run_id,
        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "traceback": traceback.format_exc(),
        "note": "This is an infrastructure/execution failure, not a research gate verdict "
                "(PASS/FAIL/UNDECIDABLE). It must not be treated as a negative result, and this "
                "run must not be silently repeated - investigate before attempting another run.",
    }

    def build(staging_dir: Path) -> None:
        with (staging_dir / "V2C_VALIDATION_FAILURE_RECORD.json").open("w", encoding="utf-8") as h:
            json.dump(payload, h, indent=2)

    return _stage_and_finalize_bundle(build, out_dir)


def execute_one_shot_run(*, authorized: bool = False) -> Path:
    """The real one-shot entry point: writes RUN_STARTED (bound to the
    canonical out_dir/failed_dir, sealed with a sidecar hash) before
    reading any VALIDATION data, runs the full pipeline, and finalizes
    either a successful bundle or a FAILED_INFRASTRUCTURE bundle - never
    leaves a run's outcome unrecorded. Deliberately takes NO path
    parameters - see `_require_one_shot_precondition`'s docstring for
    why; tests exercise every branch via `monkeypatch.setattr` on the
    module-level path constants instead."""
    if Path.cwd().resolve() == REAL_PROJECT_ROOT.resolve() and not authorized:
        raise HarnessRefusal(
            "REFUSING TO RUN: this would read the real certified VALIDATION-period data, "
            "which remains SEALED. Pass authorized=True (CLI: "
            f"--confirm={AUTHORIZATION_TOKEN}) only after explicit, deliberate authorization."
        )

    if RUN_OUTPUT_DIR.exists():
        raise HarnessRefusal(f"REFUSING TO OVERWRITE: {RUN_OUTPUT_DIR} already exists.")
    if FAILED_BUNDLE_DIR.exists():
        raise HarnessRefusal(f"REFUSING TO RUN: {FAILED_BUNDLE_DIR} already exists from an earlier "
                              f"failed run - investigate and clear it deliberately before retrying.")

    # Written before ANY VALIDATION-period file is opened - the first
    # action of the run, full stop. Atomic/exclusive, cryptographically
    # bound to RUN_OUTPUT_DIR/FAILED_BUNDLE_DIR, sealed with a sidecar -
    # see _write_run_started_marker. Once this succeeds,
    # run_validation_harness's own marker-verification (the only gate it
    # has, checked against these same canonical constants) will find and
    # validate it.
    run_id = _write_run_started_marker(marker_path=RUN_STARTED_MARKER, marker_sha_path=RUN_STARTED_MARKER_SHA,
                                        out_dir=RUN_OUTPUT_DIR, failed_dir=FAILED_BUNDLE_DIR)

    stage = "pipeline"
    try:
        result = run_validation_harness()
        # Explicit marker-to-result linkage, checked, not merely
        # asserted: the pipeline's own verified read of the marker
        # (inside run_validation_harness) must report the exact same
        # run_id this entry point just created. Any disagreement means
        # something structurally wrong happened between marker creation
        # and the pipeline's own check (e.g. the marker was replaced
        # mid-run) - refuse to finalize a bundle that cannot be proven
        # to correspond to the run that was actually authorized.
        if result["run_id"] != run_id:
            raise HarnessRefusal(
                f"INTEGRITY VIOLATION: the pipeline's verified run_id ({result['run_id']!r}) does "
                f"not match the run_id this entry point created ({run_id!r}) - refusing to "
                f"finalize a bundle that cannot be proven to correspond to this authorized run."
            )
        stage = "write_audit_bundle"
        return write_audit_bundle(result, RUN_OUTPUT_DIR)
    except BaseException as exc:
        _write_failed_infrastructure_bundle(exc, stage, FAILED_BUNDLE_DIR, run_id=run_id)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    authorized = args.confirm == AUTHORIZATION_TOKEN
    if not authorized:
        print("NOT AUTHORIZED: pass --confirm=" + AUTHORIZATION_TOKEN +
              " only after explicit, deliberate sign-off to run against real VALIDATION data.")
        print("Refusing to proceed. VALIDATION remains sealed.")
        return 1

    out_dir = execute_one_shot_run(authorized=True)
    print(f"VALIDATION run complete. Bundle -> {out_dir}")
    print((out_dir / "V2C_VALIDATION_GATE_VERDICT.json").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
