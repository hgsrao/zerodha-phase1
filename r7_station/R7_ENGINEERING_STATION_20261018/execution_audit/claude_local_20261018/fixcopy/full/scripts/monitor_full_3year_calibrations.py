#!/usr/bin/env python3
"""Monitor full 3-year calibrations in real time."""
import time
import json
import subprocess
from pathlib import Path

def get_ps_info(pattern: str) -> str:
    """Get uptime and status for a process."""
    try:
        result = subprocess.run(
            f"ps aux | grep '{pattern}' | grep -v grep | awk '{{print $9, $10, $11}}'",
            shell=True, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or "NOT RUNNING"
    except Exception as e:
        return f"ERROR: {e}"

def load_checkpoint(path: Path) -> dict:
    """Load checkpoint JSON if it exists."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def print_status():
    """Print live status."""
    ext_log = Path("calibration_external_3year.log")
    inh_log = Path("calibration_inhouse_3year.log")
    ext_ckpt = Path("output_external_engine/external_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json")
    inh_ckpt = Path("output_inhouse_engine/inhouse_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json")

    print("\n" + "=" * 100)
    print(f"FULL 3-YEAR CALIBRATION MONITOR - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # External Engine Status
    print("\n[EXTERNAL ENGINE - HMM Regime Detection]")
    ext_info = get_ps_info("run_external_engine.*FULL_3YEAR")
    print(f"  Process: {ext_info if ext_info != 'NOT RUNNING' else '✗ NOT RUNNING'}")
    if ext_log.exists():
        size = ext_log.stat().st_size
        mod_time = time.time() - ext_log.stat().st_mtime
        print(f"  Log size: {size:,} bytes (updated {mod_time:.0f}s ago)")
        with open(ext_log) as f:
            lines = f.readlines()
            if lines:
                print(f"  Last line: {lines[-1].strip()[:80]}")

    ext_data = load_checkpoint(ext_ckpt)
    if ext_data:
        print(f"  Candidates evaluated: {len(ext_data.get('candidates', []))}")
        print(f"  Candidates accepted: {sum(1 for c in ext_data.get('candidates', []) if c.get('accepted'))}")
        trades_by_cand = [len(c.get('report', {}).get('trades', [])) for c in ext_data.get('candidates', [])]
        if trades_by_cand:
            print(f"  Trades per candidate: min={min(trades_by_cand)}, max={max(trades_by_cand)}, avg={sum(trades_by_cand)/len(trades_by_cand):.1f}")

    # In-House Engine Status
    print("\n[IN-HOUSE ENGINE - Vanilla Volatility Regime]")
    inh_info = get_ps_info("run_inhouse_engine.*FULL_3YEAR")
    print(f"  Process: {inh_info if inh_info != 'NOT RUNNING' else '✗ NOT RUNNING'}")
    if inh_log.exists():
        size = inh_log.stat().st_size
        mod_time = time.time() - inh_log.stat().st_mtime
        print(f"  Log size: {size:,} bytes (updated {mod_time:.0f}s ago)")
        with open(inh_log) as f:
            lines = f.readlines()
            if lines:
                print(f"  Last line: {lines[-1].strip()[:80]}")

    inh_data = load_checkpoint(inh_ckpt)
    if inh_data:
        print(f"  Candidates evaluated: {len(inh_data.get('candidates', []))}")
        print(f"  Candidates accepted: {sum(1 for c in inh_data.get('candidates', []) if c.get('accepted'))}")
        trades_by_cand = [len(c.get('report', {}).get('trades', [])) for c in inh_data.get('candidates', [])]
        if trades_by_cand:
            print(f"  Trades per candidate: min={min(trades_by_cand)}, max={max(trades_by_cand)}, avg={sum(trades_by_cand)/len(trades_by_cand):.1f}")

    print("\n" + "=" * 100)
    print("Waiting 60 seconds before next check... (Ctrl+C to stop)")
    print("=" * 100)

if __name__ == "__main__":
    try:
        while True:
            print_status()
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")
