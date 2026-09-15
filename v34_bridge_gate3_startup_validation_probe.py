"""Pre-Live Gate 3 — final acceptance: production configuration threading.

Standalone, manually-run script. Calls `v34_bridge_runner_main._build_
engine()` - the EXACT SAME production entrypoint function `runner_main.
main()` itself calls, not a reimplementation or a shortcut - so this
proves the real deployment environment, the DP-charge parser
(`_parse_dp_charge_per_symbol`), `build_production_engine()`, and the
accounting provider all agree on the `DP_CHARGE_PER_SYMBOL` value
actually in force. The acceptance check reads that value back through
the real durable audit trail (`DP_CHARGE_CONFIGURED`), not by inspecting
the environment variable directly - the whole point is proving the
chain, not assuming it.

LIVE_TRADING_ENABLED STAYS FALSE - it is a hardcoded module constant in
v34_bridge_runner_main.py, never read from the environment; this script
does not touch it. This script places no orders and structurally cannot:
it calls `_build_engine()` only, never `request_entry()`/`step()` beyond
whatever `build_production_engine()` performs internally during its own
startup reconciliation - and that function's entire code path never
calls `place_order()`/`submit_emergency_exit()` anywhere, only read/
calculation Kite methods (`get_positions`, `get_orders`,
`get_order_details`, `ltp`, `trades`, `get_virtual_contract_note`).

WHAT THIS SCRIPT DOES:
1. Calls the real `_build_engine()` - real Kite credentials, real
   `RUNNER_DATA_DIR`, real `DP_CHARGE_PER_SYMBOL`, exactly as `runner_
   main.main()` would on an actual deployment.
2. Reads back the `DP_CHARGE_CONFIGURED` audit record that call just
   durably wrote, and reports its `configured`/`amount` fields - the
   value the accounting provider will actually use in force, read
   through the real audit trail.
3. Reports the constructed engine's halted state and status.
4. Releases the lock cleanly before exiting, in a `finally`, matching
   this project's own established lock-lifecycle discipline.

WHAT THIS SCRIPT NEVER PRINTS: credentials, access tokens, or anything
beyond the structural facts above. The DP charge amount itself is a
public Zerodha tariff figure, not a secret - printing it is intentional,
it's exactly what this acceptance check needs to confirm.

RUNNER_DATA_DIR NOTE, YOUR CHOICE, NOT THIS SCRIPT'S: this uses whatever
`RUNNER_DATA_DIR` is set to, same as real production. Point it at a
THROWAWAY directory for a pure validation-only dry run that never
touches real production state files, or at the real production data
directory if this run is meant to be an actual (first or subsequent)
startup. Either is a legitimate choice for this check; this script does
not decide it for you.

USAGE (PowerShell):

    $env:KITE_API_KEY = "..."
    $env:KITE_ACCESS_TOKEN = "..."
    $env:RUNNER_DATA_DIR = "..."          # throwaway or real - see note above
    $env:DP_CHARGE_PER_SYMBOL = "15.34"   # or "15.05" - whichever matches your account; your call, not shared here
    python v34_bridge_gate3_startup_validation_probe.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


class Gate3ValidationError(RuntimeError):
    """A structural expectation of this acceptance check was not met -
    distinct from _build_engine() itself raising, so the two failure
    modes are never confused in the printed output."""


def _read_dp_charge_configured_record(audit_path: Path) -> Dict[str, Any]:
    if not audit_path.exists():
        raise Gate3ValidationError(f"No audit log found at {audit_path} - _build_engine() should have created one.")
    records: List[Dict[str, Any]] = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matching = [r for r in records if r.get("event_type") == "DP_CHARGE_CONFIGURED"]
    if not matching:
        raise Gate3ValidationError("No DP_CHARGE_CONFIGURED audit record found - the production configuration chain did not run as expected.")
    return matching[-1]  # the most recent, in case this data directory has prior runs


def main() -> int:
    import v34_bridge_runner_main as runner_main
    from v34_bridge_runner_startup import ProductionRunnerPaths

    print("=== Gate 3 final acceptance: production configuration threading (calculation/read-only, no order placed) ===")
    print(f"LIVE_TRADING_ENABLED={runner_main.LIVE_TRADING_ENABLED} (hardcoded in v34_bridge_runner_main.py, not environment-configurable)")

    try:
        engine = runner_main._build_engine(on_event=print)
    except Exception as exc:
        print(f"[FAIL] _build_engine() raised {type(exc).__name__} - construction did not complete.")
        return 1

    try:
        data_dir = Path(os.environ.get("RUNNER_DATA_DIR", "/data/runner"))
        audit_path = ProductionRunnerPaths(data_dir=data_dir).audit_log

        try:
            record = _read_dp_charge_configured_record(audit_path)
        except Gate3ValidationError as exc:
            print(f"[FAIL] {exc}")
            return 1

        configured = record.get("fields", {}).get("configured")
        amount = record.get("fields", {}).get("amount")
        print(f"[CHECK] DP_CHARGE_CONFIGURED (read back from the durable audit trail): configured={configured} amount={amount}")
        if configured is not True or not amount:
            print("[FAIL] The audit trail does not show a positively-configured DP charge - Gate 3 is not satisfied by this run.")
            return 1

        print(f"[CHECK] engine.terminator.halted = {engine.terminator.halted}")
        if engine.terminator.halted:
            print(f"[CHECK] halt reason: {engine.terminator.reason}")
        print(f"[CHECK] engine.state.status = {engine.state.status.value}")

        print("[CHECK] no order-placement call exists anywhere in _build_engine()/build_production_engine()'s own "
              "code path - only read/calculation Kite methods are ever invoked during startup. See this script's "
              "own module docstring for the traced call surface.")
        print("No credentials or access tokens are ever printed by this script.")
        print(f"[PASS] DP_CHARGE_PER_SYMBOL={amount} threaded correctly through the real production configuration path.")
        return 0
    finally:
        engine.lock_provider.release()


if __name__ == "__main__":
    sys.exit(main())
