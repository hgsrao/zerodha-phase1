#!/usr/bin/env python3
"""Real-time monitor for calibration completion.

Watches the output directory for calibration result files and automatically
runs the diagnostic parser when they appear. Useful for long-running
48-symbol calibrations that take 2-4 hours.

Usage:
    python3 scripts/watch_calibration_results.py [--poll-interval SECONDS]

Example:
    # Check for results every 30 seconds
    python3 scripts/watch_calibration_results.py --poll-interval 30
"""

import json
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional


class CalibrationWatcher:
    """Watch for calibration results and auto-parse when ready."""

    EXTERNAL_RESULT = "external_engine_48symbol_1month_calibration_summary.json"
    INHOUSE_RESULT = "inhouse_engine_48symbol_1month_calibration_summary.json"

    def __init__(self, output_dir: Path = None, poll_interval: int = 30):
        if output_dir is None:
            output_dir = Path(__file__).resolve().parents[1] / "output_external_engine"
        self.output_dir = output_dir
        self.poll_interval = poll_interval
        self.found_external = False
        self.found_inhouse = False

    def watch(self) -> None:
        """Start watching for results."""
        print(f"🔍 Calibration Watcher Started")
        print(f"   Output directory: {self.output_dir}")
        print(f"   Poll interval: {self.poll_interval}s")
        print(f"   Watching for:")
        print(f"      - {self.EXTERNAL_RESULT}")
        print(f"      - {self.INHOUSE_RESULT}")
        print("\n   (Press Ctrl+C to stop)\n")

        try:
            while True:
                self._check_and_parse()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n\n✋ Watcher stopped by user.")
            sys.exit(0)

    def _check_and_parse(self) -> None:
        """Check for results and parse if found."""
        now = datetime.now().strftime("%H:%M:%S")

        # Check external engine
        external_path = self.output_dir / self.EXTERNAL_RESULT
        if external_path.exists() and not self.found_external:
            print(f"\n✅ [{now}] EXTERNAL ENGINE RESULTS FOUND!")
            self._parse_result("external")
            self.found_external = True

        # Check in-house engine
        inhouse_path = self.output_dir / self.INHOUSE_RESULT
        if inhouse_path.exists() and not self.found_inhouse:
            print(f"\n✅ [{now}] IN-HOUSE ENGINE RESULTS FOUND!")
            self._parse_result("inhouse")
            self.found_inhouse = True

        # If both found, we're done
        if self.found_external and self.found_inhouse:
            print(f"\n{'='*90}")
            print("✅ BOTH CALIBRATIONS COMPLETE - Running full diagnostic panel...")
            print(f"{'='*90}\n")
            self._run_full_diagnostics()
            sys.exit(0)

        # Status update every 30 seconds
        if int(time.time()) % 30 == 0:
            status = []
            if self.found_external:
                status.append("✓ External")
            else:
                status.append("⏳ External")
            if self.found_inhouse:
                status.append("✓ In-House")
            else:
                status.append("⏳ In-House")
            print(f"[{now}] Status: {' | '.join(status)}")

    def _parse_result(self, engine: str) -> None:
        """Parse a single engine's result."""
        result_file = (
            self.EXTERNAL_RESULT if engine == "external" else self.INHOUSE_RESULT
        )
        result_path = self.output_dir / result_file

        try:
            with open(result_path) as f:
                data = json.load(f)

            # Extract key metrics
            best_report = data.get("best_report", {})
            best_params = data.get("best_params", {})

            saturation_total = (
                best_report.get("saturation_exit_pa_count", 0)
                + best_report.get("saturation_exit_studies_count", 0)
            )
            net_pnl = best_report.get("net_pnl", 0)
            win_rate = best_report.get("win_rate_pct", 0)
            trailing_atr = best_params.get("trailing_stop_atr_mult", "N/A")

            print(f"\n   Engine: {engine}")
            print(f"   Saturation Exits: {saturation_total}")
            print(f"   Win Rate: {win_rate:.2f}%")
            print(f"   Net P&L: ₹{net_pnl:,.2f}")
            print(f"   Optimal ATR Mult: {trailing_atr}")

        except Exception as e:
            print(f"   ⚠ Error parsing {engine}: {e}")

    def _run_full_diagnostics(self) -> None:
        """Run the full diagnostic parser."""
        parser_script = Path(__file__).resolve().parents[1] / "scripts" / "parse_calibration_diagnostics.py"

        try:
            subprocess.run([sys.executable, str(parser_script), "--both"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Diagnostic parser failed: {e}")
            sys.exit(1)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Watch for calibration results and auto-parse when ready."
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between checks (default: 30)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Path to calibration output directory",
    )

    args = parser.parse_args()

    watcher = CalibrationWatcher(output_dir=args.output_dir, poll_interval=args.poll_interval)
    watcher.watch()


if __name__ == "__main__":
    main()
