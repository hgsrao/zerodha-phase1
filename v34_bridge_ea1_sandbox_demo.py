"""EA-1 sandbox demonstration — NOT a real Zerodha connection.

Standalone, manually-run script. Builds a real, fully-wired production
engine via `build_production_engine(shadow_mode=True)` against a FAKE
Kite connection (`FakeKiteConnectWithOrders`, the same test double every
offline test in this project already uses — no real credentials, no
real network call, no real Zerodha account touched anywhere in this
file), drives one real `request_entry()`/`step()` cycle through the
real, unmodified frozen engine, and prints the ACTUAL resulting engine
state plus the ACTUAL raw `WOULD_SUBMIT` audit record written to disk —
inspectable, not summarized.

WHY THIS EXISTS: to make the shadow-mode mechanism's real behavior
visible end to end, on demand, without needing real credentials or
touching the real production data directory. Complements (does not
replace) the real automated test suite - `test_v34_bridge_shadow_broker_
client.py` and `test_v34_bridge_runner_startup.py::TestShadowModeEndToEnd`
are what actually prove correctness; this script is a readable, re-runnable
demonstration of the same mechanism for a human to inspect directly.

Each run uses a fresh, disposable sandbox directory under the system
temp directory (never the real production `RUNNER_DATA_DIR`) and deletes
any prior run's directory first, so repeated runs never accumulate state
or interfere with each other.

USAGE:
    python v34_bridge_ea1_sandbox_demo.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders
from v34_bridge_runner_startup import ProductionRunnerPaths, build_production_engine
from v34_p02_state import Config

SANDBOX_DIR = Path(tempfile.gettempdir()) / "v34_bridge_ea1_sandbox_demo_run"


class FixedClock:
    """A known-good NSE trading moment (Friday, 10:30 IST) - deterministic,
    so this demo's output doesn't depend on when it happens to be run."""

    def now(self) -> datetime:
        return datetime(2026, 8, 14, 5, 0, 0, tzinfo=timezone.utc)


def main() -> None:
    if SANDBOX_DIR.exists():
        shutil.rmtree(SANDBOX_DIR)

    kite = FakeKiteConnectWithOrders()  # FAKE - not a real Zerodha connection
    kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]

    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("100000"), product="CNC")
    sector_lookup = {"RELIANCE": "ENERGY"}

    print("=== EA-1 sandbox demo: shadow_mode=True, live_trading_enabled=False, FAKE kite connection ===")
    print(f"sandbox data directory: {SANDBOX_DIR}\n")

    engine = build_production_engine(
        paths=ProductionRunnerPaths(data_dir=SANDBOX_DIR), kite=kite, live_trading_enabled=False,
        cfg=cfg, sector_lookup=sector_lookup, dp_charge_per_symbol=Decimal("15.34"),
        clock=FixedClock(), shadow_mode=True, on_event=print,
    )
    print(f"\nengine.broker.raw_broker class = {engine.broker.raw_broker.__class__.__name__}")
    print(f"engine.state.status (before entry attempt) = {engine.state.status.value}")

    print("\n=== Driving a real request_entry()/step() cycle ===")
    engine.request_entry(symbol="RELIANCE", quantity=10, price=Decimal("2500"))
    result = engine.step()
    print(f"step() result = {result}")
    print(f"engine.state.status (after) = {engine.state.status.value}")
    print(f"engine.terminator.halted = {engine.terminator.halted}")
    print(f"'RELIANCE' in engine.state.active_trades = {'RELIANCE' in engine.state.active_trades}")
    print(f"kite.place_order_calls (real broker write calls) = {kite.place_order_calls}")

    engine.lock_provider.release()

    print("\n=== Raw WOULD_SUBMIT audit record, exactly as written to disk ===")
    audit_path = SANDBOX_DIR / "audit.jsonl"
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["event_type"] == "WOULD_SUBMIT":
            print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
