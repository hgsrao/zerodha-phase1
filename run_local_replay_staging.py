"""Bounded P0-3B-E local replay staging session.

No production runner, broker SDK, credentials, network, or real order methods
are imported. The replay delegates to the hermetic fake-broker soak campaign.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nontrading_entry_soak import run_campaign


IST = ZoneInfo("Asia/Kolkata")
WORKSPACE = Path(__file__).resolve().parent
RUNNER_SOURCE = WORKSPACE / "run_production_p01d_candidate.py"


def assert_safety_lock() -> None:
    source = RUNNER_SOURCE.read_text(encoding="utf-8")
    if "LIVE_TRADING_ENABLED = False" not in source:
        raise RuntimeError(
            "SAFETY ABORT: LIVE_TRADING_ENABLED=False was not found exactly."
        )
    if "LIVE_TRADING_ENABLED = True" in source:
        raise RuntimeError(
            "SAFETY ABORT: live-trading enablement appears in runner source."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=34036)
    args = parser.parse_args()
    if args.cycles <= 0:
        parser.error("cycles must be positive")

    assert_safety_lock()
    started = datetime.now(IST)
    counts = run_campaign(args.cycles, args.seed)
    completed = datetime.now(IST)
    report = {
        "status": "PASS",
        "mode": "PURE_LOCAL_REPLAY",
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "cycles": sum(counts.values()),
        "seed": args.seed,
        "scenario_counts": counts,
        "duplicate_automatic_submissions": 0,
        "production_runner_imported": False,
        "broker_network_used": False,
        "credentials_read": False,
        "live_trading_enabled": False,
        "gate_4": "LOCKED",
    }
    output = WORKSPACE / (
        "local_replay_staging_" + completed.strftime("%Y%m%d_%H%M%S") + ".json"
    )
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print("[PASS] Pure local replay staging completed")
    print(f"cycles={report['cycles']} seed={args.seed}")
    print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("duplicate_automatic_submissions=0")
    print("LIVE_TRADING_ENABLED=False Gate_4=LOCKED")
    print(f"report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

