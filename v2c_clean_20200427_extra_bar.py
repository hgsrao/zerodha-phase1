"""V2-C narrow data-cleaning rule: CONFIRMED_UPSTREAM_EXTRA_BAR_ARTIFACT,
2020-04-27, 15:30 bar. LOCAL FILES ONLY - no Kite calls, no credentials,
no network.

FROZEN RULE (per instruction, do not broaden): for NSE equity data
only, on 2020-04-27, remove any bar with bar-open timestamp exactly
15:30 ONLY where that bar is currently causing the documented
certification anomaly (i.e. only in the files where certification
independently flagged it as an unexplained out-of-session bar - not
"every file that happens to contain this timestamp", though in
practice these are the same set). No other timestamp, date, symbol, or
field may be changed.

Evidence this rests on (see V2C_CALENDAR_DATABASE_CORRECTION_20260818.md
"Round 8"): 15:30 sits outside the frozen regular 15-minute bar-open
grid; the anomaly reproduces identically on a fresh, independent Kite
pull for the exact three diagnostic-sampled affected symbols
(HDFCBANK, INFY, RELIANCE); it stays absent in unaffected controls
(SBIN, ADANIPORTS, ASIANPAINT) and the NIFTY 50 control; no independent
NSE source was found for altered equity-session mechanics on this
date. This is a persistent vendor/API representation artifact, not a
legitimate session event - a data-engineering correction, not a
research judgment.

Method: LINE-LEVEL removal, not parse-and-reserialize. The raw file is
read as raw bytes/lines; the one matching data line (identified by its
exact leading timestamp field) is removed; every other line is copied
through completely untouched, byte-for-byte, including line endings.
This is a stronger guarantee than "the same values after
re-serialization" - it makes "no other timestamp, date, symbol, or
field may be changed" a byte-identity guarantee for every kept line,
not just a semantic one.

Preserves both realities, per instruction:
  - RAW file: untouched, in V2C_15MIN_DATA_ACQUIRED/ (never written to
    by this script).
  - CLEANED derivative: written to V2C_15MIN_DATA_CLEANED/, mirroring
    every acquired file (affected files get the one-line removal;
    every other file - unaffected stocks, NIFTY 50, LTIM - is copied
    byte-identical, so the cleaned directory is a complete, ready-to-
    certify dataset on its own).
  - Transformation manifest (V2C_20200427_CLEANING_MANIFEST.csv):
    one row per AFFECTED file - security_key, raw_row_count,
    cleaned_row_count, removed_row_timestamp, removed_row_ohlcv,
    raw_sha256, cleaned_sha256, reason.
  - Full hash inventory (V2C_20200427_CLEANING_HASH_INVENTORY.csv):
    one row per ALL acquired files (affected and unaffected) -
    raw_sha256 vs cleaned_sha256, so an unaffected file's two hashes
    matching exactly is itself a verified invariant, not assumed.

Fail-closed: refuses (skips that file, reports it, nonzero exit) if
the target row is absent, appears more than once, or the file's
certification-affected status can't be independently confirmed against
the current certification report. Never guesses, never force-removes.
Asserts exactly one row removed per affected file and zero changes to
every other line - both checked explicitly, not assumed from the
algorithm's design alone.

Does NOT rerun certification itself - that is a deliberate separate
step (v2c_certify_15min_dataset.py --data-dir ...), specified in the
sequence as a way of proving the cleaned derivative is correct rather
than assuming it, per instruction: "do not assume this clears all 28
until that rerun proves it."
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

BASE = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816")
RAW_DIR = BASE / "V2C_15MIN_DATA_ACQUIRED"
CLEANED_DIR = BASE / "V2C_15MIN_DATA_CLEANED"
CERT_REPORT = RAW_DIR / "V2C_15MIN_CERTIFICATION_REPORT_20260818.json"
MANIFEST_PATH = CLEANED_DIR / "V2C_20200427_CLEANING_MANIFEST.csv"
HASH_INVENTORY_PATH = CLEANED_DIR / "V2C_20200427_CLEANING_HASH_INVENTORY.csv"

TARGET_DATE = "2020-04-27"
TARGET_TIME = "15:30:00"
REASON = "CONFIRMED_UPSTREAM_EXTRA_BAR_ARTIFACT"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_affected_files() -> dict[str, str]:
    """Reads the CURRENT certification report - not a hardcoded list -
    so this script is self-verifying against live evidence, not a
    stale guess. Returns {filename: security_key} for every interval
    the report currently flags with an unexplained 2020-04-27
    out-of-session bar."""
    report = json.loads(CERT_REPORT.read_text(encoding="utf-8"))
    affected = {}
    for row in report["per_interval"]:
        if TARGET_DATE in row.get("unexplained_out_of_session_dates", {}):
            affected[row["file"]] = row["security_key"]
    nifty = report.get("nifty50") or {}
    if TARGET_DATE in nifty.get("unexplained_out_of_session_dates", {}):
        raise SystemExit("REFUSING TO RUN: NIFTY 50 now shows the 2020-04-27 anomaly too - "
                          "this contradicts the Round 8 diagnostic finding (NIFTY was an "
                          "unaffected control). Do not proceed; investigate the discrepancy first.")
    return affected


def clean_one_file(raw_path: Path, cleaned_path: Path) -> dict:
    """Line-level removal. Returns a manifest-row dict, or raises on
    any fail-closed condition (target row absent/duplicated)."""
    raw_bytes = raw_path.read_bytes()
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    text = raw_bytes.decode("utf-8")
    # Preserve exact line-ending convention (observed CRLF) by splitting
    # on the literal terminator and keeping it, rather than using
    # universal-newline splitlines() which would normalize endings.
    if "\r\n" in text:
        eol = "\r\n"
    elif "\n" in text:
        eol = "\n"
    else:
        raise ValueError(f"{raw_path.name}: no recognizable line ending found")
    lines = text.split(eol)
    # split() on a trailing terminator leaves one empty trailing element;
    # preserve that structure exactly on rejoin.

    target_prefix = f"{TARGET_DATE}T{TARGET_TIME}"
    matches = [i for i, line in enumerate(lines) if line.startswith(target_prefix)]
    if len(matches) != 1:
        raise ValueError(f"{raw_path.name}: expected exactly 1 line starting with "
                          f"{target_prefix!r}, found {len(matches)} - REFUSING to modify this file")

    idx = matches[0]
    removed_line = lines[idx]
    removed_fields = removed_line.split(",")
    if len(removed_fields) != 6:
        raise ValueError(f"{raw_path.name}: matched line does not look like a 6-field OHLCV "
                          f"row: {removed_line!r} - REFUSING to modify this file")
    removed_ohlcv = {
        "timestamp": removed_fields[0], "open": removed_fields[1], "high": removed_fields[2],
        "low": removed_fields[3], "close": removed_fields[4], "volume": removed_fields[5],
    }

    # Count real data rows before/after (exclude header line 0 and the
    # possible empty trailing element from the final split).
    raw_row_count = sum(1 for l in lines[1:] if l.strip())

    cleaned_lines = lines[:idx] + lines[idx + 1:]
    cleaned_row_count = sum(1 for l in cleaned_lines[1:] if l.strip())

    if cleaned_row_count != raw_row_count - 1:
        raise ValueError(f"{raw_path.name}: row-count assertion failed - raw={raw_row_count}, "
                          f"cleaned={cleaned_row_count}, expected cleaned=raw-1 - REFUSING to write")

    # Byte-identity check on every kept line (paranoia, not trust in the
    # slicing above): every line in cleaned_lines must appear at its
    # original position in `lines`, shifted by -1 after idx.
    for pos, line in enumerate(cleaned_lines):
        expected = lines[pos] if pos < idx else lines[pos + 1]
        if line != expected:
            raise ValueError(f"{raw_path.name}: byte-identity check failed at line {pos} - "
                              f"REFUSING to write a corrupted file")

    cleaned_text = eol.join(cleaned_lines)
    cleaned_bytes = cleaned_text.encode("utf-8")

    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cleaned_path.with_suffix(cleaned_path.suffix + ".tmp")
    tmp_path.write_bytes(cleaned_bytes)
    tmp_path.replace(cleaned_path)

    cleaned_sha256 = hashlib.sha256(cleaned_path.read_bytes()).hexdigest()

    return {
        "raw_row_count": raw_row_count,
        "cleaned_row_count": cleaned_row_count,
        "removed_row_timestamp": removed_ohlcv["timestamp"],
        "removed_row_open": removed_ohlcv["open"],
        "removed_row_high": removed_ohlcv["high"],
        "removed_row_low": removed_ohlcv["low"],
        "removed_row_close": removed_ohlcv["close"],
        "removed_row_volume": removed_ohlcv["volume"],
        "raw_sha256": raw_sha256,
        "cleaned_sha256": cleaned_sha256,
        "reason": REASON,
    }


def copy_unchanged(raw_path: Path, cleaned_path: Path) -> tuple[str, str]:
    """Byte-identical copy for files this rule does not touch. Returns
    (raw_sha256, cleaned_sha256) - these must be equal; asserted, not
    assumed."""
    raw_bytes = raw_path.read_bytes()
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cleaned_path.with_suffix(cleaned_path.suffix + ".tmp")
    tmp_path.write_bytes(raw_bytes)
    tmp_path.replace(cleaned_path)
    cleaned_sha256 = hashlib.sha256(cleaned_path.read_bytes()).hexdigest()
    if cleaned_sha256 != raw_sha256:
        raise ValueError(f"{raw_path.name}: unchanged-copy hash mismatch - this must never happen")
    return raw_sha256, cleaned_sha256


def main() -> int:
    if not RAW_DIR.exists():
        raise SystemExit(f"REFUSING TO RUN: {RAW_DIR} not found")

    affected = find_affected_files()
    print(f"Affected files per the CURRENT certification report: {len(affected)}")
    if len(affected) != 28:
        print(f"WARNING: expected 28 affected files per the last-known certification review, "
              f"found {len(affected)}. Proceeding on the report's actual current contents, "
              f"not the expected count - but flagging the discrepancy for review.")

    all_raw_files = sorted(RAW_DIR.glob("NSE_*.csv"))
    manifest_rows = []
    hash_inventory_rows = []
    failures = []

    for raw_path in all_raw_files:
        cleaned_path = CLEANED_DIR / raw_path.name
        if raw_path.name in affected:
            try:
                result = clean_one_file(raw_path, cleaned_path)
            except ValueError as e:
                print(f"[FAIL-CLOSED] {e}")
                failures.append(str(e))
                continue
            manifest_rows.append({"security_key": affected[raw_path.name], "file": raw_path.name, **result})
            hash_inventory_rows.append({"file": raw_path.name, "affected": True,
                                         "raw_sha256": result["raw_sha256"], "cleaned_sha256": result["cleaned_sha256"]})
            print(f"[CLEANED] {raw_path.name}: removed 1 row ({result['removed_row_timestamp']}), "
                  f"{result['raw_row_count']} -> {result['cleaned_row_count']} rows")
        else:
            raw_sha, cleaned_sha = copy_unchanged(raw_path, cleaned_path)
            hash_inventory_rows.append({"file": raw_path.name, "affected": False,
                                         "raw_sha256": raw_sha, "cleaned_sha256": cleaned_sha})

    if failures:
        print(f"\n{len(failures)} FAIL-CLOSED failures - CLEANED_DIR is INCOMPLETE/INCONSISTENT.")
        print("Do not proceed to certification rerun until every failure above is resolved.")
        return 1

    with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as h:
        fieldnames = ["security_key", "file", "raw_row_count", "cleaned_row_count",
                      "removed_row_timestamp", "removed_row_open", "removed_row_high",
                      "removed_row_low", "removed_row_close", "removed_row_volume",
                      "raw_sha256", "cleaned_sha256", "reason"]
        w = csv.DictWriter(h, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(manifest_rows)

    with HASH_INVENTORY_PATH.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=["file", "affected", "raw_sha256", "cleaned_sha256"])
        w.writeheader()
        w.writerows(hash_inventory_rows)

    print(f"\n{len(manifest_rows)} files cleaned (exactly 1 row removed each, verified).")
    print(f"{len(hash_inventory_rows) - len(manifest_rows)} files copied byte-identical "
          f"(raw_sha256 == cleaned_sha256, verified for every one).")
    print(f"Manifest -> {MANIFEST_PATH}")
    print(f"Hash inventory -> {HASH_INVENTORY_PATH}")
    print("\nRAW files in V2C_15MIN_DATA_ACQUIRED/ are UNTOUCHED.")
    print("CLEANED derivatives are in V2C_15MIN_DATA_CLEANED/ - a complete, self-contained dataset.")
    print("Next step (separate, deliberate, not automatic): rerun the full certification")
    print("against V2C_15MIN_DATA_CLEANED/ to PROVE the cleanup resolves the anomaly, not assume it.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
