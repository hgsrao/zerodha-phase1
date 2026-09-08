"""Strict three-period, 48-symbol sandbox replay.

This runner does not calibrate parameters. It invokes the sealed V3 validator
over three chronological, non-overlapping periods. Each period has separate
portfolio and safety state to preserve train/validation/test isolation; state
is continuous within each period.
"""

from __future__ import annotations

import json
from pathlib import Path

from revision4.validate_48symbol_sealed import DATA_DIR, MANIFEST_PATH, run_48symbol_validation


PERIODS = (
    ("train", "2023-09-01", "2024-08-31"),
    ("validation", "2024-09-01", "2025-08-31"),
    ("untouched_test", "2025-09-01", "2026-08-24"),
)


def run_multi_year_replay(manifest_path: str = MANIFEST_PATH,
                          data_dir: str = DATA_DIR) -> dict:
    """Run sealed evaluation periods only; never learn or alter configuration."""
    reports = {}
    for name, start, end in PERIODS:
        print(f"[PERIOD {name}] {start} through {end}", flush=True)
        report = run_48symbol_validation(
            manifest_path=manifest_path, data_dir=data_dir,
            month_start=start, month_end=end,
        )
        reports[name] = report
        print(
            f"[PERIOD {name}] {report['status']} | "
            f"fills={report['metrics']['fills']} | "
            f"reconciliation={report['reconciliation']['exact']}", flush=True,
        )

    # This is evidence only. Test output cannot feed back into the prior
    # period's configuration or become an optimizer input.
    return {"kind": "sealed_three_period_sandbox_replay", "calibration_performed": False,
            "periods": reports}


if __name__ == "__main__":
    result = run_multi_year_replay()
    output = Path("diagnostic_output/three_period_v3_sandbox_report.json")
    output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(f"[SEALED REPORT] {output}")
