"""V11 -> P02 Rebalance Bridge — trigger entrypoint (cron / manual `docker run`).

Wires environment -> real Kite reads -> run_trigger() (v34_bridge_trigger.py,
already tested end to end against real R1/Step A/RebalancePlan machinery).
Per R0 §13, this is deliberately short-lived - it computes, writes one
plan (or resumes/no-ops on an existing one), and exits. It is not a
second always-on process; the runner (v34_bridge_runner_main.py) is the
only daemon in this system.

Both of this module's read-only gaps are now closed via
v34_bridge_kite_read_only_client.KiteReadOnlyClient - the shared, real
client (this session's design decision: build it once, use it from both
here and the runner's eventual write-side adapter, rather than three
independent implementations of the same handful of Kite calls):

1. Live quotes for the momentum universe (build_target_portfolio's
   `quotes` parameter) - client.ltp() over EXTERNAL_UNIVERSE
   (external_momentum_shadow.py) - build_target_portfolio ranks across
   the whole universe to pick the top-N, so it needs a quote for every
   symbol in it, not just whatever's currently held.
2. The broker's actual current CNC holdings (compute_rebalance_diff's
   `current_portfolio` parameter) - client.get_positions(), filtered to
   product == "CNC" only, matching P02's own product-filtering
   convention (_matching_positions(..., product=cfg.product) in
   institutional_engine_v34_p02_multipos_candidate.py). R0 §2: the
   broker is the sole source of truth for this; an MIS position sitting
   in the same account is not part of what this CNC-basket bridge
   manages and must never be silently folded into the diff.

Credential handling is shared with v34_bridge_runner_main.py via
v34_bridge_kite_credentials.build_kite_client_from_env() - factored out
once a second real entrypoint needed the identical KITE_API_KEY/
KITE_ACCESS_TOKEN loading logic. Never prompted for, never logged, never
hardcoded here. Missing either one is FAIL_CLOSED (a hard RuntimeError),
not a silent skip.

This module only reads - no order is ever placed by anything here. The
write path (v34_bridge_kite_broker_client.KiteBrokerClient's place_order()/
submit_emergency_exit()) now exists as its own separate, Phase-2 module,
but v34_bridge_runner_main._build_engine() still refuses to start - not
for lack of a write-capable adapter anymore, but for lack of the
surrounding production state (durable stores, audit, the
AuthorizationContext daily-accounting provider) - see that module's own
docstring.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Mapping


def _fetch_live_quotes(client) -> Mapping[str, Dict[str, Any]]:
    """Live LTP for the entire V11 momentum universe - build_target_
    portfolio() ranks across the whole universe to pick the top-N, so it
    needs a quote for every symbol in it, not just whatever's currently
    held."""
    from external_momentum_shadow import EXTERNAL_UNIVERSE
    return client.ltp(list(EXTERNAL_UNIVERSE))


def _fetch_current_portfolio(client) -> Mapping[str, int]:
    """Broker's actual current CNC holdings, converted to {symbol: qty} -
    R0 §2: the broker is the sole source of truth for this. See module
    docstring for why this is filtered to product == "CNC" only."""
    positions = client.get_positions()
    current: Dict[str, int] = {}
    for position in positions:
        if position.get("product") != "CNC":
            continue
        symbol = position.get("tradingsymbol")
        quantity = position.get("quantity")
        if not symbol or not isinstance(quantity, int):
            raise RuntimeError(f"FAIL_CLOSED: malformed CNC position record: {position!r}.")
        if quantity > 0:
            current[symbol] = current.get(symbol, 0) + quantity
    return current


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("v34_bridge_trigger")

    from external_momentum_shadow import EXTERNAL_UNIVERSE, UNIVERSE_MODE
    log.info("UNIVERSE_MODE=%s (%d symbols) - must match the runner process reading the same plan store directory", UNIVERSE_MODE, len(EXTERNAL_UNIVERSE))

    plans_dir = Path(os.environ.get("PLAN_STORE_DIR", "/data/plans"))
    log.info("plan store directory: %s", plans_dir)

    from v34_bridge_kite_credentials import build_kite_client_from_env
    from v34_bridge_kite_read_only_client import KiteReadOnlyClient
    from v34_bridge_rebalance_plan import RebalancePlanStore
    from v34_bridge_trigger import run_trigger
    from kite_request_governor import KiteRequestGovernor

    store = RebalancePlanStore(plans_dir)
    today = date.today()

    # EA1-R1, 2026-08-19: this script runs as its own short-lived process
    # (R0 §13 - not the always-on runner), concurrently with whichever
    # runner daemon(s) share this account's credentials - its own real
    # Kite calls (ltp() over the whole universe, get_positions()) need
    # the SAME cross-process coordination the runner already has, or it's
    # exactly the kind of uncoordinated concurrent access the governor
    # exists to prevent. Required, fail-closed - same discipline as
    # _build_engine()'s own KITE_RATE_GOVERNOR_DIR check.
    governor_dir = os.environ.get("KITE_RATE_GOVERNOR_DIR")
    if not governor_dir:
        raise RuntimeError(
            "FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR environment variable is missing or empty. "
            "Must be the SAME shared directory every process using this account's Kite "
            "credentials uses - see kite_request_governor.py's module docstring."
        )
    governor = KiteRequestGovernor(state_dir=Path(governor_dir))
    log.info("KITE_RATE_GOVERNOR_DIR=%s", governor_dir)

    kite = build_kite_client_from_env()
    client = KiteReadOnlyClient(kite, governor=governor)

    quotes = _fetch_live_quotes(client)
    current_portfolio = _fetch_current_portfolio(client)
    log.info("fetched %d universe quotes, %d current CNC holdings", len(quotes), len(current_portfolio))

    plan, outcome = run_trigger(
        quotes=quotes, current_portfolio=current_portfolio,
        signal_date=today, current_trading_day=today, store=store,
    )

    log_fn = log.warning if outcome == "REFUSED_HALTED" else log.info
    log_fn("trigger run complete: target_id=%s outcome=%s plan_status=%s", plan.target_id, outcome, plan.status.value)


if __name__ == "__main__":
    main()
