"""Strict three-period, 48-symbol sandbox replay.

This runner does not calibrate parameters. It invokes the sealed V3 validator
over three chronological, non-overlapping periods. Each period has separate
portfolio and safety state to preserve train/validation/test isolation; state
is continuous within each period.
"""

from __future__ import annotations

import json
import argparse
from pathlib import Path

import pandas as pd

from revision4.validate_48symbol_sealed import DATA_DIR, MANIFEST_PATH, run_48symbol_validation


PERIODS = (
    ("train", "2023-09-01", "2024-08-31"),
    ("validation", "2024-09-01", "2025-08-31"),
    ("untouched_test", "2025-09-01", "2026-08-24"),
)


def run_multi_year_replay(manifest_path: str = MANIFEST_PATH,
                          data_dir: str = DATA_DIR,
                          period_name: str = "train",
                          duration_months: int | None = None) -> dict:
    """Run one sealed period, optionally its chronological prefix.

    A prefix run is an operational validation checkpoint only.  It neither
    tunes configuration nor grants access to the later validation/test data.
    """
    selected = [period for period in PERIODS if period[0] == period_name]
    if len(selected) != 1:
        raise ValueError(f"unknown sealed period: {period_name}")
    if duration_months is not None and duration_months not in {1, 3, 6, 9, 12}:
        raise ValueError("duration_months must be one of 1, 3, 6, 9, or 12")

    reports = {}
    for name, start, end in selected:
        effective_end = end
        if duration_months is not None:
            # All prefixes start on the first calendar day, so MonthEnd gives
            # the final included date without a bare/inferred timedelta unit.
            prefix_end = (pd.Timestamp(start) + pd.offsets.MonthEnd(duration_months)).date().isoformat()
            effective_end = min(prefix_end, end)

        print(f"[PERIOD {name}] {start} through {effective_end}", flush=True)
        report = run_48symbol_validation(
            manifest_path=manifest_path, data_dir=data_dir,
            month_start=start, month_end=effective_end,
        )
        reports[name] = report
        print(
            f"[PERIOD {name}] {report['status']} | "
            f"fills={report['metrics']['fills']} | "
            f"reconciliation={report['reconciliation']['exact']}", flush=True,
        )

    # This is evidence only. Test output cannot feed back into the prior
    # period's configuration or become an optimizer input.
    return {"kind": "sealed_period_sandbox_replay", "calibration_performed": False,
            "duration_months": duration_months, "periods": reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one explicit sealed V3 sandbox period")
    parser.add_argument("--period", choices=[name for name, _, _ in PERIODS], default="train")
    parser.add_argument("--months", type=int, choices=(1, 3, 6, 9, 12),
                        help="Run only this chronological prefix of the selected period")
    args = parser.parse_args()
    result = run_multi_year_replay(period_name=args.period, duration_months=args.months)
    suffix = f"_{args.months}m" if args.months is not None else ""
    output = Path(f"diagnostic_output/{args.period}{suffix}_v3_sandbox_report.json")
    output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(f"[SEALED REPORT] {output}")
