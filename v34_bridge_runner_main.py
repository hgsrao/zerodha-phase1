"""V11 -> P02 Rebalance Bridge — runner container entrypoint (`docker run` CMD).

Wires environment -> a real TradingEngineV34P02 stack -> run_forever()
(v34_bridge_runner_entrypoint.py). This file is deliberately thin: every
decision of substance - the polling cadence, the structural one-quote-
call-per-tick rule, the 429 backoff, and now (Phase 3.6) the entire
production-state startup sequence - lives elsewhere, in already-sealed
modules. This file's only job is environment wiring and lock lifecycle.

Credential handling is shared with v34_bridge_trigger_main.py via
v34_bridge_kite_credentials.build_kite_client_from_env() - never
prompted for, never logged, never hardcoded here.

LIVE_TRADING_ENABLED is a module-level constant, hardcoded False, exactly
matching run_production.py's own established convention - never wired to
an environment variable, so no container configuration can ever flip it.
Stays False after Phase 3.6, same as every phase before it.

PHASE 3.6 STATUS: the "REMAINING GAP" every earlier phase's docstring
named here - durable BotState/AuthorizerRegistry/DailyAccounting stores,
real audit/alert/lock/terminator, and an AuthorizationContext daily-
accounting provider - is now built (v34_bridge_runner_startup.
build_production_engine, itself pure integration of already-sealed
3.1-3.5A components; see that module's own docstring for the full,
traced 14-step startup sequence). `_build_engine()` no longer raises.

LOCK LIFECYCLE, OWNERSHIP MADE OBVIOUS FROM THIS CODE, NOT HIDDEN A FRAME
DEEPER: `TradingEngineV34P02.__init__` itself is what calls `RunnerLock.
acquire()` (frozen, traced - see v34_bridge_runner_startup.py's own
docstring for why this can't be split into "main() acquires first, then
builds the engine" without weakening RunnerLock's own double-acquire
guard). What THIS file guarantees instead: once `_build_engine()`
returns an engine, `main()`'s own `finally` block calls `engine.
lock_provider.release()` unconditionally on any normal Python-level
exit from `run_forever()` - a halt (which now makes run_forever() raise,
Phase 3.6's other fix) still goes through this same `finally`, so a
halted daemon does not also leak the lock and block a legitimate
restart. Abnormal process death (SIGKILL, OOM) is NOT this file's
job to handle - that's the entire point of `RunnerLock` being a real
OS-level lock (Phase 3.4B): the kernel releases it automatically,
no PID-file cleanup logic needed here or anywhere.

PRE-LIVE GATE 3 (DP-charge account configuration) - `DP_CHARGE_PER_
SYMBOL` MUST be present and valid in production, with no silent default.
`_parse_dp_charge_per_symbol()` below fails closed on missing, empty,
non-numeric, NaN/infinite, zero, or negative values - refusing engine
construction entirely rather than letting a misconfigured/absent DP rate
be discovered for the first time on a real overnight delivery exit. This
is enforced at THIS layer (the actual production entrypoint), not inside
`build_production_engine()` itself, which deliberately keeps `dp_charge_
per_symbol: Optional[Decimal] = None` as a general, reusable integration
primitive (other callers - tests, in particular - legitimately construct
a production-shaped engine without caring about DP charges at all; the
3.5A accounting layer's own independent fail-closed guarantee, proven
directly in test_v34_bridge_runner_startup.py, is what protects THAT
path if a future caller ever gets this wrong). The two checks are
deliberately layered, not redundant: this one is "production must never
launch without an explicit rate"; the accounting layer's is "even if
something upstream slips, an actual DP-triggering event still refuses to
silently proceed."

EA-1 (Execution Authority Gate, Stage 1 - Online Shadow Execution
Validation) - `SHADOW_MODE` IS environment-controlled, deliberately
unlike `LIVE_TRADING_ENABLED`. This is safe specifically because
`shadow_mode` can never itself grant execution authority: it only ever
changes HOW the already-hardcoded-False `LIVE_TRADING_ENABLED` refusal
is handled (a loud engine halt vs. a quiet, durably-audited, per-order
decline) - see v34_bridge_shadow_broker_client.py's own docstring, and
`ShadowModeBrokerClient.__init__`'s own structural refusal to wrap
anything whose `live_trading_enabled` isn't exactly `False`. Defaults to
`False` (today's unchanged hard-halt behavior) unless the environment
explicitly opts in with the literal string `"true"` - matching the same
fail-safe-default discipline every other flag in this file already uses.
"""

from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

LIVE_TRADING_ENABLED = False  # Hardcoded. Never read from the environment. Never change this to True here.


def _parse_dp_charge_per_symbol(raw: Optional[str]) -> Decimal:
    """FAIL_CLOSED, matching this project's established convention for
    every other env-derived production value (build_kite_client_from_
    env()'s own KITE_API_KEY/KITE_ACCESS_TOKEN checks). Refuses missing,
    empty, non-numeric, NaN/infinite, zero, and negative values - a DP
    charge of exactly 0 is exactly as wrong as a missing one (Zerodha's
    published tariff is never zero for a resident-individual account;
    silently accepting 0 would look identical to "no charge needed" and
    defeat the entire point of this gate)."""
    if raw is None or not raw.strip():
        raise RuntimeError("FAIL_CLOSED: DP_CHARGE_PER_SYMBOL environment variable is missing or empty.")
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise RuntimeError(f"FAIL_CLOSED: DP_CHARGE_PER_SYMBOL is not a valid decimal: {raw!r}.")
    if not value.is_finite():
        raise RuntimeError(f"FAIL_CLOSED: DP_CHARGE_PER_SYMBOL must be finite (not NaN/Infinity), got {raw!r}.")
    if value <= 0:
        raise RuntimeError(f"FAIL_CLOSED: DP_CHARGE_PER_SYMBOL must be positive, got {raw!r}.")
    return value


def _parse_kite_rate_governor_dir(raw: Optional[str]) -> Path:
    """FAIL_CLOSED, same discipline as _parse_dp_charge_per_symbol above.

    EA1-R1 (2026-08-19): required, not optional, deliberately. The whole
    reason kite_request_governor.py exists is that this account's Kite
    credentials are reused across every process the owner runs ("I want
    to use the same access token for all the runs") - an unset governor
    directory would silently reproduce exactly the ungoverned-concurrent-
    access gap the 2026-08-17/2026-08-19 incidents exposed (see
    EA1_INCIDENT_EVIDENCE_20260817_20260819/), not a harmless default.
    MUST be set to the SAME path across every process sharing this
    account's credentials (both V11 terminals at minimum) - a per-
    terminal value here would defeat the whole point just as surely as
    leaving it unset."""
    if raw is None or not raw.strip():
        raise RuntimeError(
            "FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR environment variable is missing or empty. "
            "Must be set to the SAME shared directory across every process using this "
            "account's Kite credentials - see kite_request_governor.py's module docstring."
        )
    return Path(raw.strip())


def _build_engine(*, on_event):
    """Constructs the real, fully-wired TradingEngineV34P02 production
    stack via v34_bridge_runner_startup.build_production_engine() - see
    that module's own docstring for the complete 14-step sequence. May
    return an already-`terminator.halted` engine (a persisted halt found
    at load, or one triggered by startup reconciliation itself) - that is
    a correct, honest result, not an error; run_forever() (Phase 3.6's
    other fix) is what refuses to poll it further."""
    from portfolio_brain_v9 import SECTORS
    from v34_bridge_kite_broker_client import KiteBrokerClient  # noqa: F401 (imported by build_production_engine; kept here so a missing dependency fails loudly at the same import stage as before)
    from v34_bridge_kite_credentials import build_kite_client_from_env
    from v34_bridge_runner_startup import ProductionRunnerPaths, build_production_engine
    from v34_p02_state import Config
    from kite_request_governor import KiteRequestGovernor

    kite = build_kite_client_from_env()
    data_dir = Path(os.environ.get("RUNNER_DATA_DIR", "/data/runner"))
    dp_charge_per_symbol = _parse_dp_charge_per_symbol(os.environ.get("DP_CHARGE_PER_SYMBOL"))
    shadow_mode = os.environ.get("SHADOW_MODE", "").strip().lower() == "true"
    governor_dir = _parse_kite_rate_governor_dir(os.environ.get("KITE_RATE_GOVERNOR_DIR"))
    kite_rate_governor = KiteRequestGovernor(state_dir=governor_dir)

    cfg = Config(
        alert_webhook_url="unused-file-backed-alerting-instead",
        trial_capital=Decimal(os.environ.get("TRIAL_CAPITAL", "100000")),
    )

    return build_production_engine(
        paths=ProductionRunnerPaths(data_dir=data_dir), kite=kite, live_trading_enabled=LIVE_TRADING_ENABLED,
        cfg=cfg, sector_lookup=SECTORS, dp_charge_per_symbol=dp_charge_per_symbol, on_event=on_event,
        shadow_mode=shadow_mode, kite_rate_governor=kite_rate_governor,
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("v34_bridge_runner")
    log.info("LIVE_TRADING_ENABLED=%s (hardcoded in this file, not environment-configurable)", LIVE_TRADING_ENABLED)
    log.info("SHADOW_MODE=%s (environment-configurable; can only ever soften a disabled-execution decline, never grant execution authority)", os.environ.get("SHADOW_MODE", "").strip().lower() == "true")

    from external_momentum_shadow import EXTERNAL_UNIVERSE, UNIVERSE_MODE
    log.info("UNIVERSE_MODE=%s (%d symbols) - lets the original and expanded universes run as two separate processes, never mixed within one", UNIVERSE_MODE, len(EXTERNAL_UNIVERSE))

    plans_dir = Path(os.environ.get("PLAN_STORE_DIR", "/data/plans"))
    log.info("plan store directory: %s", plans_dir)
    data_dir_preview = os.environ.get("RUNNER_DATA_DIR", "/data/runner")
    log.info("runner data directory: %s", data_dir_preview)
    log.info("KITE_RATE_GOVERNOR_DIR=%s - MUST be identical across every process sharing this "
              "account's Kite credentials to actually coordinate (EA1-R1)", os.environ.get("KITE_RATE_GOVERNOR_DIR"))

    from v34_bridge_rebalance_plan import RebalancePlanStore
    from v34_bridge_runner_entrypoint import POLL_INTERVAL_SECONDS, RATE_LIMIT_BACKOFF_SECONDS, run_forever
    from v34_bridge_plan_incident_sidecar import PlanIncidentSidecar

    store = RebalancePlanStore(plans_dir)
    sidecar = PlanIncidentSidecar(plans_dir / "plan_events.jsonl")  # EA1-R1 - real reason, durably persisted
    engine = _build_engine(on_event=log.info)  # see module docstring's lock-lifecycle note

    try:
        if engine.terminator.halted:
            log.error("engine halted at startup: %s - exiting without entering run_forever().", engine.terminator.reason)
            return
        log.info("starting runner: poll_interval=%ss rate_limit_backoff=%ss", POLL_INTERVAL_SECONDS, RATE_LIMIT_BACKOFF_SECONDS)
        run_forever(engine=engine, store=store, on_event=log.info, sidecar=sidecar)
    finally:
        # Guaranteed on any normal Python-level exit (a halt now raises,
        # so it goes through here too) - abnormal process death is the
        # OS lock's own job, not this block's. See module docstring.
        engine.lock_provider.release()


if __name__ == "__main__":
    main()
