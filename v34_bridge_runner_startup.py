"""Phase 3.6 — production runner startup sequence.

New module. The one place that wires every already-sealed component
(3.1-3.5A) into a real `TradingEngineV34P02` instance. Pure integration:
this module invents no new accounting, persistence, broker, or
authorization semantics - every decision of substance was already made
and tested in its owning component. Where this module DOES make a
choice (the trading-day rollover cadence, the kill-switch mechanism), it
is because the owning component explicitly said that choice belongs to
"a later production-wiring pass" (v34_p02_broker_adapter.py's own words)
and the choice itself is a small, clearly-scoped primitive (a sentinel
file, calling an already-frozen function at an already-traced boundary),
not new business logic.

THE FIXED STARTUP SEQUENCE (frozen for this phase, matching the order
the user specified, adapted only where the frozen engine's own
constructor bundles two of the numbered steps into one call it doesn't
expose separately - noted inline exactly where and why):

 1. Acquire OS runner lock
 2. Load BotState
 3. Load AuthorizerRegistry
 4. Load DailyAccountingState
 5. Construct audit / alert / terminator
 6. Construct Kite read/write client
 7. Obtain fresh broker observations
 8. Reconcile fingerprinted AuthorizerRegistry reservations
 9. Establish restart boundary (persisted non-halted state -> STARTUP,
    persist that transition, log what was overwritten)
10. Run P02 startup reconciliation against broker reality
11. Refresh/reconcile daily accounting (including day-rollover)
12. Construct the authoritative AuthorizationContext provider
13. Verify kill-switch / reconciliation cleanliness (diagnostic - does
    not block construction; a kill switch only ever gates NEW ENTRIES,
    never background position management - "entry controls are not
    liquidation controls" is a project-wide invariant already, not
    something this module invents)
14. Return the fully-wired engine - only once this function returns does
    anything resembling ordinary RUNNING operation become reachable at
    all; the caller's run_forever() loop is what actually performs it.

WHY STEPS 1-2 ARE NOT TWO SEPARATE LINES OF CODE HERE: `TradingEngineV34
P02.__init__` itself calls `self.lock_provider.acquire()` immediately
followed by `self.store.load(self.clock.now().date())` (traced, both on
consecutive lines) - there is no way to call the engine's constructor
without both happening together, and no hook to run code between them.
This module does NOT attempt to pre-acquire the lock itself and hand the
engine an already-acquired one - `RunnerLock.acquire()` (Phase 3.4B)
correctly refuses a double-acquire from the same instance as a
programming-contract violation, and weakening that guarantee just to
match this pseudocode literally would be a real regression to a sealed
component for no safety benefit. Instead: this module constructs a
not-yet-acquired `RunnerLock` and lets the frozen engine's own
constructor be the sole caller of `.acquire()`; the top-level caller
(v34_bridge_runner_main.py) retains a plain reference to that same
`RunnerLock` object and is the one that guarantees `.release()` in a
`finally` block - ownership is still obvious from reading that code, it
is just `engine.lock_provider`, not a separately-threaded variable.

STEP 9's NUANCE, STATED EXPLICITLY PER THE USER'S OWN INSTRUCTION: this
module never mutates or discards the loaded BotState before reconciling
it. `EngineBotStateStoreAdapter.last_loaded_state` (v34_bridge_engine_
adapters.py) captures an independent snapshot of exactly what was on
disk at load time; this module reads that snapshot's `status`/
`halt_reason` and writes them into a durable audit record
(`STARTUP_FORCED_FROM_PERSISTED_STATE`) BEFORE ever overwriting
`engine.state.status` - so a restart from persisted `RUNNING`,
`ENTRY_UNKNOWN`, or any other in-flight status remains fully
reconstructible from the audit trail, not silently erased.

WHAT THIS MODULE DELIBERATELY DOES NOT DO: it does not invent a second
opinion about entries_today/turnover_today/realized-P&L math (that's
v34_bridge_daily_accounting.py's), does not invent a second lock
semantics (v34_bridge_runner_lock.py's), does not invent a second
BotState reconciliation policy (institutional_engine_v34_p02_multipos_
candidate.py's own reconcile_startup(), frozen), and does not decide
what `kill_switch_active` MEANS to authorization (v34_p02_authorizer.py's
gate 2 already decided that; this module only supplies the boolean).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from institutional_engine_v34_p02_multipos_candidate import TradingEngineV34P02
from v34_bridge_alert_sink import AlertSink
from v34_bridge_audit_sink import AuditSink
from v34_bridge_authorizer_registry_store import AuthorizerRegistryStore as DurableAuthorizerRegistryStore
from v34_bridge_botstate_store import BotStateStore
from v34_bridge_daily_accounting import build_authorization_context, reconcile_and_persist
from v34_bridge_daily_accounting_state import initial_daily_accounting_state
from v34_bridge_daily_accounting_store import DailyAccountingStore
from v34_bridge_daily_rollover import roll_daily_accounting_if_needed
from v34_bridge_engine_adapters import EngineAuthorizerRegistryAdapter, EngineBotStateStoreAdapter
from v34_bridge_kill_switch import is_kill_switch_active
from kite_request_governor import DEFAULT as GOV_DEFAULT
from kite_request_governor import KiteRequestGovernor
from v34_bridge_kite_broker_client import KiteBrokerClient
from v34_bridge_kite_read_only_client import KiteReadOnlyClient
from v34_bridge_runner_lock import RunnerLock
from v34_bridge_terminator import Terminator
from v34_p02_accounting import build_portfolio_snapshot
from v34_p02_authorizer import reconcile_reservations
from v34_p02_broker_adapter import KiteBrokerAdapterMultiPos
from v34_p02_state import Config, EngineStatus


class SystemClock:
    """The real-clock implementation of the `.now()` shape every fake
    clock in this project's tests already provides - tz-aware UTC,
    nothing else."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class RealSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass
class ProductionRunnerPaths:
    """Every durable file this startup sequence owns, gathered under one
    data directory - a single place to point at a persistent volume."""
    data_dir: Path

    @property
    def bot_state(self) -> Path:
        return self.data_dir / "bot_state.json"

    @property
    def authorizer_registry(self) -> Path:
        return self.data_dir / "authorizer_registry.json"

    @property
    def daily_accounting(self) -> Path:
        return self.data_dir / "daily_accounting.json"

    @property
    def audit_log(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def alert_log(self) -> Path:
        return self.data_dir / "alerts.jsonl"

    @property
    def runner_lock(self) -> Path:
        return self.data_dir / "runner.lock"

    @property
    def kill_switch(self) -> Path:
        return self.data_dir / "KILL_SWITCH"


def build_production_engine(
    *,
    paths: ProductionRunnerPaths,
    kite: Any,
    live_trading_enabled: bool,
    cfg: Config,
    sector_lookup: Dict[str, str],
    dp_charge_per_symbol: Optional[Decimal] = None,
    clock: Optional[Any] = None,
    on_event: Callable[[str], None] = lambda msg: None,
    shadow_mode: bool = False,
    kite_rate_governor: Optional[KiteRequestGovernor] = None,
) -> TradingEngineV34P02:
    """Runs the fixed 14-step sequence documented in the module docstring
    and returns a fully-wired, real TradingEngineV34P02 - or an engine
    that is already `terminator.halted` if the persisted state (loaded
    at step 2, inside the engine's own constructor) or startup
    reconciliation (step 10) demanded it. The caller (v34_bridge_runner_
    main.py) is responsible for checking `engine.terminator.halted`
    before entering `run_forever()` - this function does not raise on a
    halt; a halted engine returned cleanly IS the correct, honest result
    of a startup sequence that did its job.

    `shadow_mode` (EA-1, defaulted False): when True, wraps the write-
    capable broker in `ShadowModeBrokerClient` - real Kite connectivity,
    real reconciliation, real accounting, real authorization gates, but
    every authorized BUY attempt is durably logged (`WOULD_SUBMIT`) and
    then declined via the frozen engine's own `EntryPolicyDeclinedError`
    path instead of ever reaching a real broker call. Independent of, and
    layered UNDER, `live_trading_enabled` - `live_trading_enabled=True`
    with `shadow_mode=True` is not a safe combination this function tries
    to make sense of; the caller is responsible for keeping these
    orthogonal (v34_bridge_runner_main.py never sets shadow_mode when
    LIVE_TRADING_ENABLED could be True, and LIVE_TRADING_ENABLED is a
    hardcoded constant there, never environment-controlled, regardless
    of this flag).

    `kite_rate_governor` (EA1-R1, 2026-08-19): optional, defaults to None
    (no rate governance - matches every existing caller/test unchanged).
    When supplied, threaded into KiteBrokerAdapterMultiPos so every real
    Kite call the adapter makes (positions/orders/ltp/place_order) waits
    its turn under the shared, cross-process ledger - see
    kite_request_governor.py's own module docstring for why this has to
    be a genuinely shared primitive: the owner confirmed multiple
    processes (both V11 terminals, at minimum) authenticate with the SAME
    KITE_API_KEY, so an ungoverned adapter in even one process can exhaust
    the whole account's rate ceiling regardless of what every other
    process does correctly. Also threaded into accounting_read_client
    (its own separate KiteReadOnlyClient instance) and into
    ShadowModeBrokerClient (which makes several of its OWN additional
    real Kite calls internally while building a WOULD_SUBMIT audit
    record - a gap found by tracing that class's own place_order() body,
    not covered by gating the outer adapter call alone)."""
    clock = clock or SystemClock()

    # Step 3: load AuthorizerRegistry (durable read; independent of the
    # engine, safe to do before it exists).
    authreg_adapter = EngineAuthorizerRegistryAdapter(DurableAuthorizerRegistryStore(paths.authorizer_registry))

    # Step 4: load DailyAccountingState. Bootstrapped fresh (day-1) using
    # today's date if nothing was ever persisted - the same "fresh state
    # looks like this" convention v34_bridge_daily_accounting_state.
    # initial_daily_accounting_state already defines, not a new one.
    accounting_store = DailyAccountingStore(paths.daily_accounting)
    accounting_state = accounting_store.load()
    if accounting_state is None:
        accounting_state = initial_daily_accounting_state(trading_day=str(clock.now().date()), trial_capital=cfg.trial_capital)
        accounting_store.save(accounting_state)
    accounting_state_holder = {"state": accounting_state}

    # Step 5: audit / alert / terminator.
    audit = AuditSink(paths.audit_log, clock=clock)
    alert = AlertSink(paths.alert_log, clock=clock)
    terminator = Terminator()

    # Pre-Live Gate 3 forensic record: what DP-charge configuration was
    # actually active at this startup - logged as early as possible
    # (audit didn't exist before this line), unconditionally, regardless
    # of whether construction later halts or fails. The numeric amount is
    # a public Zerodha tariff, not a secret, and is fine to record
    # durably; the environment-variable NAME/mechanism it came from is
    # deliberately not logged - only that a value was (or wasn't)
    # configured, and what it was.
    audit.log(
        "DP_CHARGE_CONFIGURED", configured=dp_charge_per_symbol is not None,
        amount=(str(dp_charge_per_symbol) if dp_charge_per_symbol is not None else None),
    )

    # Step 6: Kite read/write client. `raw_broker` is the complete
    # protocol KiteBrokerAdapterMultiPos wraps (Phase 2, sealed);
    # `accounting_read_client` is a second, independent KiteReadOnlyClient
    # over the SAME kite connection, needed only for the daily-accounting
    # refresh's extra read surface (trades()/get_virtual_contract_note())
    # that KiteBrokerClient deliberately does not expose publicly (Phase
    # 1A) - composition, not a new client class.
    raw_broker = KiteBrokerClient(kite, live_trading_enabled=live_trading_enabled, product=cfg.product)
    accounting_read_client = KiteReadOnlyClient(kite, governor=kite_rate_governor)

    # EA-1 (Execution Authority Gate, Stage 1 - Online Shadow Execution
    # Validation): opt-in only, defaulted False. Wraps `raw_broker` so
    # every downstream reference (step 7's fresh observations, the day-
    # rollover quote fetch, _context_provider() below) transparently gets
    # the shadow-wrapped version - reads behave identically either way;
    # only place_order() changes (see v34_bridge_shadow_broker_client.py's
    # own docstring for the full reasoning: WOULD_SUBMIT audit + a frozen,
    # already-tested EntryPolicyDeclinedError decline, never a real
    # broker call; submit_emergency_exit() stays a pure, unmodified pass-
    # through - shadow mode never touches the liquidation path). Logged
    # durably and unconditionally, same forensic-record discipline as
    # DP_CHARGE_CONFIGURED above - Monday's evidence must be able to
    # prove, after the fact, whether a given session even HAD execution
    # authority disabled via shadow mode specifically (distinct from the
    # separate, always-true `LIVE_TRADING_ENABLED=False` fact).
    audit.log("SHADOW_MODE_ACTIVE", shadow_mode=shadow_mode)
    if shadow_mode:
        from v34_bridge_shadow_broker_client import ShadowModeBrokerClient
        raw_broker = ShadowModeBrokerClient(
            raw_broker, audit=audit, clock=clock, cfg=cfg,
            authreg_adapter=authreg_adapter, accounting_state_holder=accounting_state_holder,
            governor=kite_rate_governor,
        )

    def _charge_calculator(order_params: Dict[str, Any]) -> Dict[str, Any]:
        return accounting_read_client.get_virtual_contract_note_for_one_order(order_params)

    # Step 12 (built now, invoked later): the AuthorizationContext
    # provider KiteBrokerAdapterMultiPos calls on every real BUY attempt.
    # `engine_holder` resolves the construction-order cycle: the adapter
    # (and therefore this closure) must exist BEFORE the engine does (the
    # engine's constructor takes `broker=adapter`), but this closure needs
    # to read the engine's own live status - it is only ever CALLED later,
    # from inside step()'s dispatch, by which point `engine_holder["engine"]`
    # is populated below.
    engine_holder: Dict[str, TradingEngineV34P02] = {}

    def _context_provider():
        engine = engine_holder["engine"]
        if kite_rate_governor is not None:
            kite_rate_governor.acquire(GOV_DEFAULT)
        try:
            fresh_orders = raw_broker.get_orders()
            fresh_trades = accounting_read_client.get_trades()
        except Exception as exc:
            # EA1-R1: visibility only, never a new degrade/retry policy -
            # this call sits inside KiteBrokerAdapterMultiPos.place_order()'s
            # own call chain (ctx = self.context_provider() is its first
            # line), so a real failure here is already correctly caught by
            # the frozen engine's own _step_entry_submit() exception
            # classification, exactly like a place_order() failure itself
            # would be - nothing here should soften or reclassify that.
            # This just makes the failure visible in the SAME audit trail
            # before it propagates, rather than only surfacing as whatever
            # the frozen engine's own generic handling produces.
            audit.log("ACCOUNTING_CONTEXT_REFRESH_FAILED", exception_class=type(exc).__name__, exception_message=str(exc))
            raise
        new_state = reconcile_and_persist(
            accounting_store, accounting_state_holder["state"], orders=fresh_orders, trades=fresh_trades,
            cfg=cfg, charge_calculator=_charge_calculator, dp_charge_per_symbol=dp_charge_per_symbol,
        )
        accounting_state_holder["state"] = new_state
        return build_authorization_context(
            new_state, orders=fresh_orders, trades=fresh_trades, cfg=cfg, now=clock.now(),
            reconciliation_clean=(engine.state.status == EngineStatus.RUNNING),
            kill_switch_active=is_kill_switch_active(paths.kill_switch),
            sector_lookup=sector_lookup,
        )

    adapter = KiteBrokerAdapterMultiPos(
        raw_broker=raw_broker, authorizer_store=authreg_adapter, cfg=cfg,
        context_provider=_context_provider, governor=kite_rate_governor,
    )

    # Steps 1-2 (see module docstring for why they're not split further):
    # lock acquisition + BotState load, both inside this one frozen call.
    botstate_adapter = EngineBotStateStoreAdapter(BotStateStore(paths.bot_state))
    lock = RunnerLock(paths.runner_lock, clock=clock)
    engine = TradingEngineV34P02(
        broker=adapter, clock=clock, sleeper=RealSleeper(), store=botstate_adapter,
        audit=audit, alert=alert, lock=lock, terminator=terminator, cfg=cfg,
    )
    engine_holder["engine"] = engine

    loaded = botstate_adapter.last_loaded_state  # exactly what was on disk (or freshly bootstrapped) at step 2

    if engine.terminator.halted:
        # __init__ itself already halted on a persisted RECONCILIATION_HALT
        # or clearance_required snapshot - steps 7-13 would be pointless
        # (and step 8 in particular must never touch the AuthorizerRegistry
        # while the engine's own safety state says "unresolved") - return
        # exactly as-is, matching this function's own documented contract.
        on_event(f"engine halted on load: loaded_status={loaded.status.value} reason={loaded.halt_reason!r}")
        return engine

    # Steps 7-13, wrapped: LOCK-LEAK GUARD, found and fixed via Gate 3's
    # own end-to-end DP-charge fail-closed test. The lock is already held
    # (acquired inside TradingEngineV34P02.__init__ above) by this point.
    # If anything in steps 7-13 raises - the accounting layer's own
    # DailyAccountingReconciliationError on a DP-triggering close with no
    # rate configured is the concrete case that surfaced this, but the
    # guard is general - this function's caller never receives an
    # `engine` reference to release the lock with, because this function
    # itself never returns one. Without this guard the lock would stay
    # held for the rest of THIS PROCESS's lifetime (not forever - the OS
    # still releases it on process death, per RunnerLock's own design -
    # but a caller that catches this exception and retries within the
    # same process, e.g. after fixing a misconfiguration, would wrongly
    # see RunnerLockHeldError on the retry). Mirrors the exact same
    # guarantee v34_bridge_runner_main.py's own `finally: engine.
    # lock_provider.release()` already provides for failures AFTER a
    # successful return - this is that same discipline, one layer
    # earlier, for failures that happen INSIDE construction itself.
    try:
        # Step 7: fresh broker observations.
        positions = raw_broker.get_positions()
        orders = raw_broker.get_orders()

        # Step 8: reconcile fingerprinted reservations against fresh
        # reality - the caller contract v34_bridge_authorizer_registry_
        # store.py's own docstring names explicitly. Persisted via the
        # adapter (persist-first, mirror-second - see v34_bridge_engine_
        # adapters.py).
        reconciled_registry = reconcile_reservations(authreg_adapter.registry, orders=orders, positions=positions, product=cfg.product)
        authreg_adapter.save(reconciled_registry)
        audit.log("AUTHORIZER_RESERVATIONS_RECONCILED_AT_STARTUP", reservation_count=len(reconciled_registry.reservations), held_count=len(reconciled_registry.held_symbols))
        on_event(f"reservations reconciled: {len(reconciled_registry.reservations)} open, {len(reconciled_registry.held_symbols)} held")

        # Step 9: establish restart boundary. Log the loaded status/reason
        # BEFORE overwriting anything - see module docstring's step-9 note.
        if loaded.status != EngineStatus.STARTUP:
            audit.log(
                "STARTUP_FORCED_FROM_PERSISTED_STATE", loaded_status=loaded.status.value,
                loaded_halt_reason=loaded.halt_reason, loaded_trading_day=loaded.trading_day,
            )
            on_event(f"forcing STARTUP: persisted status was {loaded.status.value} (trading_day={loaded.trading_day})")
            engine.state.status = EngineStatus.STARTUP
            engine.store.save(engine.state)

        # Step 10: run P02 startup reconciliation against broker reality -
        # step() itself calls reconcile_startup() while status == STARTUP
        # (traced: institutional_engine_v34_p02_multipos_candidate.py's own
        # step() dispatch), including the frozen engine's own trading-day
        # rollover for BotState.
        engine.step()
        if engine.terminator.halted:
            on_event(f"startup reconciliation halted the engine: {engine.state.halt_reason!r}")
            return engine

        # Step 11: refresh/reconcile daily accounting, including day-
        # rollover - kept at the SAME cadence as the frozen engine's own
        # BotState.trading_day rollover (see v34_bridge_daily_rollover.
        # py's own docstring for why this is a traced, matched design
        # choice, not an arbitrary one).
        current_state = accounting_state_holder["state"]
        if current_state.trading_day != engine.state.trading_day:
            active_positions = [p for p in positions if p.get("product") == cfg.product and p.get("quantity")]
            quotes: Dict[str, Any] = {}
            if active_positions:
                symbols = sorted({p["tradingsymbol"] for p in active_positions})
                quotes = raw_broker.ltp(symbols)
            fresh_equity = build_portfolio_snapshot(
                positions=positions, orders=orders, quotes=quotes, reservations=reconciled_registry.reservations,
                cfg=cfg, checkpoint=current_state.checkpoint, cumulative_realized_pnl=current_state.cumulative_realized_pnl,
                cumulative_charges=current_state.cumulative_charges, sealed_daily_pnl_series=current_state.sealed_daily_pnl_series,
            ).equity
            current_state = roll_daily_accounting_if_needed(current_state, current_trading_day=engine.state.trading_day, fresh_equity_at_rollover=fresh_equity)
            accounting_store.save(current_state)
            accounting_state_holder["state"] = current_state
            audit.log("DAILY_ACCOUNTING_ROLLED_FORWARD", new_trading_day=current_state.trading_day, fresh_equity=str(fresh_equity))
            on_event(f"daily accounting rolled forward to {current_state.trading_day}")

        orders_after_reconciliation = raw_broker.get_orders()
        trades_at_startup = accounting_read_client.get_trades()
        # Gate 3 requirement #5's exact failure point: if a genuinely
        # overnight/demat-debited position closes here and dp_charge_per_
        # symbol is None, reconcile_and_persist (v34_bridge_daily_
        # accounting.py, 3.5A) raises DailyAccountingReconciliationError
        # - accounting's own applicability decision, not this function's
        # (see module docstring: "does not invent a second opinion" about
        # accounting math). This function's only job on that failure is
        # to not leak the lock - see this try block's own header comment.
        current_state = reconcile_and_persist(
            accounting_store, accounting_state_holder["state"], orders=orders_after_reconciliation, trades=trades_at_startup,
            cfg=cfg, charge_calculator=_charge_calculator, dp_charge_per_symbol=dp_charge_per_symbol,
        )
        accounting_state_holder["state"] = current_state

        # Step 12: the AuthorizationContext provider was already built
        # above (had to be, for the engine's own construction) - nothing
        # further to do here.

        # Step 13: verify kill-switch / reconciliation cleanliness - a
        # diagnostic log, not a construction gate. A kill switch never
        # blocks engine construction or background position management
        # (entry controls are not liquidation controls, project-wide).
        kill_switch_active = is_kill_switch_active(paths.kill_switch)
        on_event(f"startup complete: engine_status={engine.state.status.value} kill_switch_active={kill_switch_active}")

        # Step 14: return the fully-wired engine.
        return engine
    except Exception:
        engine.lock_provider.release()
        raise
