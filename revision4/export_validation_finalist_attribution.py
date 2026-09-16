"""Re-run sealed validation finalists and export their authoritative trade ledgers.

This is a diagnostic exporter.  It does not alter parameters, registry values,
or safety controls, and it never opens the untouched test period.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from revision4.trade_cost_attribution import attribution_report
from revision4.validate_48symbol_sealed import run_48symbol_validation


SOURCE = Path("diagnostic_output/ray_optuna_corrected_202309.json")
OUT_DIR = Path("diagnostic_output/ray_optuna_corrected_202309_attribution")


def main() -> None:
    source = json.loads(SOURCE.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "source": str(SOURCE),
        "validation_period": source["validation_period"],
        "untouched_test_period": source["untouched_test_period"],
        "finalists": [],
    }
    for number, finalist in enumerate(source["validation_trials"], start=1):
        print(f"[FINALIST {number}/{len(source['validation_trials'])}] sealed validation replay", flush=True)
        report = run_48symbol_validation(
            month_start="2023-10-02", month_end="2023-10-06",
            calibration_overrides=finalist["params"],
        )
        if not report["reconciliation"]["exact"]:
            raise RuntimeError(f"finalist {number}: reconciliation is not exact")
        attribution = attribution_report(report["completed_trade_ledger"])
        if abs(attribution["net_pnl"] - report["financials"]["realized_pnl"]) > 0.01:
            raise RuntimeError(f"finalist {number}: attribution does not match sealed P&L")
        stem = OUT_DIR / f"finalist_{number}"
        (stem.with_suffix(".json")).write_text(json.dumps({
            "params": finalist["params"], "sealed_run": report, "attribution": attribution,
        }, indent=2, default=str) + "\n")
        with stem.with_suffix(".csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=attribution["trades"][0].keys())
            writer.writeheader()
            writer.writerows(attribution["trades"])
        summary["finalists"].append({
            "number": number, "params": finalist["params"],
            "status": report["status"], "reconciliation_exact": report["reconciliation"]["exact"],
            "attribution": {key: attribution[key] for key in (
                "trade_count", "gross_pnl", "total_cost", "net_pnl", "cost_share_of_gross",
                "by_symbol", "by_exit_reason", "by_entry_hour_ist",
            )},
            "report": str(stem.with_suffix(".json")), "ledger_csv": str(stem.with_suffix(".csv")),
        })
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
