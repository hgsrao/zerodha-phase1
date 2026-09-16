#!/usr/bin/env python3
"""Run one deterministic symbol/signal through all three closed-loop models.

This is an architectural demonstration, not a profitability backtest and it
does not create a broker order.  It proves the causality boundary: an entry
snapshot cannot use its own future outcome, while the next signal can.
"""

from __future__ import annotations

import json
from pathlib import Path

from revision2_external.closed_loop_control import ClosedLoopSupervisor


def main() -> None:
    control = ClosedLoopSupervisor()
    snapshot = control.entry_snapshot(
        symbol="SUNPHARMA", side="BUY", entry_price=100.0, stop_price=98.0,
        target_price=104.0, max_hold_bars=20,
    )
    path = control.observe_trade_path(snapshot, current_price=98.5, bars_held=10)
    portfolio = control.observe_portfolio_risk(
        gross_exposure=90_000.0, equity=100_000.0, hard_limit_fraction=1.0,
    )
    post_exit_profile = control.record_outcome({
        "symbol": "SUNPHARMA", "side": "BUY", "net_pnl": -10.0,
        "trade_id": "demo-trade-1", "reason": "stop",
    })
    next_snapshot = control.entry_snapshot(
        symbol="SUNPHARMA", side="BUY", entry_price=100.0, stop_price=98.0,
        target_price=104.0, max_hold_bars=20,
    )
    artifact = {
        "scope": "one_symbol_one_signal_closed_loop_demo",
        "symbol": "SUNPHARMA",
        "entry_snapshot_before_outcome": snapshot,
        "trade_path_comparator": path.to_dict(),
        "portfolio_risk_comparator": portfolio,
        "outcome_ledger_after_exit": post_exit_profile,
        "next_signal_snapshot_after_prior_exit": next_snapshot,
        "actuator_mode": "shadow_only",
        "note": "No order was submitted and no stop was modified. The artifact demonstrates causal data flow only.",
    }
    output = Path("diagnostic_output/closed_loop_one_signal_demo.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2))
    print(json.dumps(artifact, indent=2))
    print(f"[REPORT] {output}")


if __name__ == "__main__":
    main()
