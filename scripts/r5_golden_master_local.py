"""Machine-local golden-master gate for the shared R5 orchestrator.

The stored golden master (golden_master/r5_shared_orchestrator, written at 500abab on another
machine) cannot be reproduced byte-for-byte here: the HMM EM fit differs in the last floating-point
digit between machines (BLAS/SIMD summation order), which changes every telemetry hash while every
decision (orders, fills, trades, gross and net P&L) is identical.  Verified at cb10fac on
2026-10-15: all 17 scenarios match the stored funnels exactly and mismatch only in float telemetry.

This wrapper therefore runs the SAME scenario matrix (imported, not copied) and gives two gates:

  --check-decisions        exact decision-level equality with the stored golden master
                           (funnel, exit reasons, report keys); never writes the stored files.
  --write-local DIR        capture a full byte-level reference on THIS machine (e.g. from an
                           untouched cb10fac checkout).
  --check-local DIR        byte-identical comparison of every report and section hash with DIR.

    PYTHONPATH="$PWD" python scripts/r5_golden_master_local.py --check-decisions
    PYTHONPATH="$PWD" python scripts/r5_golden_master_local.py --check-local local_workspace/golden_local/cb10fac
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capture_r5_golden_master as gm  # noqa: E402

DECISION_KEYS = ("funnel", "exit_reasons", "report_keys")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-decisions", action="store_true")
    mode.add_argument("--write-local", type=Path)
    mode.add_argument("--check-local", type=Path)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()

    matrix = gm.scenario_matrix()
    names = args.only or list(matrix)
    results = {}
    for name in names:
        results[name] = gm.summarize(gm.run_scenario(matrix[name]))
        f = results[name]["funnel"]
        print(f"{name:<44} {results[name]['report_sha256'][:16]}  orders={f['orders_submitted']} "
              f"trades={f['completed_trades']} net={f['net_pnl']}", flush=True)

    if args.write_local:
        args.write_local.mkdir(parents=True, exist_ok=True)
        (args.write_local / "baseline.json").write_text(json.dumps({"scenarios": results}, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.write_local / 'baseline.json'}")
        return 0

    if args.check_decisions:
        expected = json.loads((gm.OUT_DIR / "baseline.json").read_text())["scenarios"]
        failures = [(n, [k for k in DECISION_KEYS if expected.get(n, {}).get(k) != results[n][k]])
                    for n in names if any(expected.get(n, {}).get(k) != results[n][k] for k in DECISION_KEYS)]
        label = "DECISION-LEVEL GOLDEN MASTER (stored 500abab baseline)"
    else:
        expected = json.loads((args.check_local / "baseline.json").read_text())["scenarios"]
        failures = []
        for n in names:
            if expected.get(n) != results[n]:
                diff = [k for k, v in results[n]["sections_sha256"].items()
                        if expected.get(n, {}).get("sections_sha256", {}).get(k) != v]
                failures.append((n, diff or ["funnel/report"]))
        label = f"LOCAL BYTE-LEVEL GOLDEN MASTER ({args.check_local})"
    for name, diff in failures:
        print(f"MISMATCH {name}: {diff}")
    print(f"{label}:", "PASS" if not failures else f"FAIL ({len(failures)}/{len(names)})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
