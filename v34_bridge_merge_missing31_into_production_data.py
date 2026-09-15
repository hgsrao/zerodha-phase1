"""One-off merge: run the 31 newly-fetched raw symbols through the same
correction pass the existing 20 symbols already went through
(prepare_research_ohlcv.prepare), then merge the corrected files into
historical_data_60minute_extended_ready/ - WITHOUT destroying the
existing correction_audit.csv, which prepare() would otherwise silently
overwrite (it opens the audit file in "w" mode, not append).

Sequence:
1. prepare() the raw new data into a throwaway temp folder, capturing
   its returned audit list directly rather than trusting the file it
   writes (that file gets overwritten by step 2, which is fine - the
   in-memory list is what actually gets merged).
2. Copy every corrected new-symbol CSV into the real production folder.
3. Read the EXISTING correction_audit.csv from that folder, append the
   new audit rows to it (not overwrite), write it back.

Run once. Not part of the automated test suite (touches real data
files, not something to run on every CI pass).
"""

from __future__ import annotations

import csv
import shutil
import tempfile
from pathlib import Path

from prepare_research_ohlcv import prepare

RAW_DIR = Path(__file__).parent / "historical_data_60minute_new31_raw"
PRODUCTION_DIR = Path(__file__).parent / "historical_data_60minute_extended_ready"
AUDIT_FIELDS = ("file", "timestamp", "field", "original", "corrected", "reason")


def main() -> None:
    print(f"Source (raw, just fetched): {RAW_DIR}")
    print(f"Target (real production folder): {PRODUCTION_DIR}\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print("Running prepare_research_ohlcv.prepare() on the new raw data...")
        new_audit = prepare(RAW_DIR, tmp_path)
        print(f"  {len(new_audit)} OHLC correction(s) made in the new data.")
        for row in new_audit:
            print(f"    {row['file']} @ {row['timestamp']}: {row['field']} {row['original']} -> {row['corrected']} ({row['reason']})")

        print("\nCopying corrected new-symbol files into the production folder...")
        copied = 0
        for csv_file in sorted(tmp_path.glob("*.csv")):
            if csv_file.name == "correction_audit.csv":
                continue  # that one's regenerated properly below, not copied raw
            shutil.copy2(csv_file, PRODUCTION_DIR / csv_file.name)
            copied += 1
        print(f"  {copied} file(s) copied.")

    print("\nMerging audit records (preserving existing history, not overwriting)...")
    existing_audit_path = PRODUCTION_DIR / "correction_audit.csv"
    existing_rows = []
    if existing_audit_path.exists():
        with existing_audit_path.open("r", newline="", encoding="utf-8") as fh:
            existing_rows = list(csv.DictReader(fh))
    print(f"  {len(existing_rows)} existing record(s) preserved.")

    combined = existing_rows + new_audit
    with existing_audit_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(combined)
    print(f"  {len(combined)} total record(s) now in {existing_audit_path.name}.")

    total_files = len(list(PRODUCTION_DIR.glob("NSE_*60minute*.csv")))
    print(f"\nProduction folder now has {total_files} symbol data files total.")


if __name__ == "__main__":
    main()
