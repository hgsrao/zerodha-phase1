"""Run today's live shadow candidates through the REAL production risk gate
(RunnerEntryAuthorizer's full pipeline) - no reimplementation, no bypass of
LIVE_TRADING_ENABLED, and never touches the real production state files.

What this answers, honestly: "did all the conditions get checked, and would
they have passed?" Until this module existed, the real answer was
unknowable while LIVE_TRADING_ENABLED=False, because
RunnerEntryAuthorizer.authorize_buy halts on its very first line whenever
trading is disabled - it never reaches the deeper checks at all.

v2 correction: the first version of this module only called the shallow
P03RiskController.evaluate_entry() (capital ceiling, an inert daily-loss
figure, kill switch). It missed the real, tighter P0-3B-F policy layer that
lives in RunnerEntryAuthorizer._apply_policy_and_reserve() - daily hard
halt, rolling week halt, trial drawdown halt, daily entry lock, per-trade
risk sizing, the (currently hardcoded-to-one) simultaneous-position limit,
daily entry count, same-symbol lock, entry cooldown, and daily turnover.
This version calls authorize_buy()'s real internals directly - _fresh_snapshot,
controller.evaluate_entry, and _apply_policy_and_reserve - skipping only the
live_trading_enabled check at the very top (which is what makes this a dry
run instead of live dispatch; authorize_buy itself is never called).

Isolation, by construction:
- Reads candidates from shadow_strategy_telemetry.json /
  orb_shadow_telemetry.json - files the read-only collectors already write.
  Never re-fetches quotes itself.
- The ONLY broker calls made here are the same read-only calls
  get_daily_risk_snapshot() already makes in production: profile(),
  positions(), orders(), order_charges(). No order is ever placed, modified,
  or cancelled - place_order/modify_order/cancel_order are never imported or
  called by this module.
- Its own durable risk state goes to entry_gate_dry_run_risk_state.json -
  never bot_state_v34.json, never bot_state_v34_p03_entry_control.json (the
  real production risk-controller state file). A halt condition, or a
  reserved "entry" from a would-be-allowed candidate, detected here can
  never affect the real production runner. Letting reservations accumulate
  in this isolated file across a day's cycles is deliberate: it lets later
  cycles correctly show DAILY_ENTRY_LIMIT/ENTRY_COOLDOWN/SAME_SYMBOL_DAILY_LOCK
  the way they'd actually appear if this had been trading for real today.
- The kill-switch file check does point at the real KILL_SWITCH path - that
  one is a pure filesystem existence check, and reporting it honestly is
  the whole point of this tool.
- capital_limit and every risk-percentage field are read directly off a
  real Config() instance (the production defaults), not duplicated
  constants - they can never silently drift out of sync with production.

v3 addition (2026-08-14): whenever a candidate reaches ALL_CONDITIONS_PASSED,
this now also constructs and validates the matching hypothetical exit order -
answering "what would the protective exit have looked like" the same
honest way the entry side already does. KiteBrokerAdapter.submit_emergency_exit
cannot be called directly for this the same way RunnerEntryAuthorizer.
authorize_buy() couldn't for entries: it halts on its live_trading check
before validating anything else, and unlike the entry side there is no
deeper "real method" to call instead - the validation logic lives in that
same gated function. So validate_exit_payload() below is a deliberate,
checked mirror of submit_emergency_exit()'s validation chain (everything
after its live_trading gate, before its final self.kite.place_order call) -
never a broker call, never touches self.kite.place_order, and is tested
against the same edge cases the real function guards against. The trigger
price formula (one tick below LTP, rounded down) is copied from the real
engine's only caller of submit_emergency_exit
(institutional_engine_v34_p01d_candidate.py's PROTECTION-state handling),
not invented - that is the actual real trigger this system would compute.
get_tick_size() IS a real, read-only broker call (kite.instruments("NSE"));
its result is cached per symbol for this process's lifetime, since tick
sizes do not change intraday and instruments() is a large payload not
worth re-fetching every cycle.

v4 addition (2026-08-24): also constructs a hypothetical take-profit order
alongside the hypothetical emergency exit - explicitly disclosed as NOT a
mirror of anything in the real engine, unlike every other "hypothetical_*"
value here. institutional_engine_v34_p01d_candidate.py's entire state
machine has exactly one exit path (confirmed by reading every state name
in the file); there is no take-profit concept to mirror. This answers a
different, hypothetical question instead - "if given P02 Pillar II's own
2:1 reward:risk convention, what would that order look like?" - purely for
comparability with the other tracks this project runs. Tagged
V3.4_HYPOTHETICAL_TP_NOT_NATIVE (deliberately not "V3.4_..." like the real
tags) so it can never be mistaken for the real engine's own output. See
compute_hypothetical_take_profit()'s own docstring for the exact formula.

v4 addition (2026-08-14): RunnerEntryAuthorizer._apply_policy_and_reserve()
(real production code, still untouched) records that a symbol was reserved,
but never records the exact price at that moment - only the resulting risk
figure. That made today's first-ever realized hypothetical P&L a
reconstruction (backed out of the real turnover_today total plus the
closest available price snapshot) rather than an exact figure. Fixed here,
in this tool only, by additionally appending the exact reservation-moment
price to entry_gate_dry_run_position_log.json whenever a candidate reaches
ALL_CONDITIONS_PASSED - a separate, append-only, per-trading-day log this
module owns. compute_realized_hypothetical_pnl() then marks every recorded
entry against current prices using the same real cost schedule the
momentum shadow itself uses. Entries recorded before this feature existed
are not retroactively exact - only today's log onward is.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

# 2026-08-24, owner request: this module's real kite.profile()/positions()/
# orders()/order_charges() calls were uncoordinated with every other live
# process sharing this Kite account. Wrapped generically (rather than
# enumerating each method by name) so it stays correct even if
# KiteBrokerAdapter/get_daily_risk_snapshot() starts calling something new
# later - and so run_production_p01d_candidate.py itself never needs
# touching for this. All calls made through this path are read-only
# account-info lookups (never quote()/historical_data()/place_order()),
# so DEFAULT (10/s) is the correct governor class, same convention as
# every other DEFAULT-classed caller in this project.
from kite_request_governor import DEFAULT as GOV_DEFAULT, KiteRequestGovernor

from institutional_engine_v34_p01d_candidate import Config
from run_production_p01d_candidate import (
    P03_KILL_SWITCH_FILE,
    RunnerEntryAuthorizer,
    RunnerEntryStateStore,
)
from zerodha_delivery_costs import buy_cost, sell_cost

IST = ZoneInfo("Asia/Kolkata")

# The real production defaults - alert_webhook_url/max_daily_loss are the
# only two Config fields main() ever overrides that matter here (and
# max_daily_loss isn't threaded into the risk gate at all - see
# BRAIN_RESEARCH_SPEC_V15_RISK_CONFIG_REALIGNMENT.md), so this reproduces
# the same authorizer parameters run_production_p01d_candidate.py's main()
# constructs, without duplicating each field by hand.
_DEFAULT_CONFIG = Config(alert_webhook_url="", max_daily_loss=Decimal("2000"))

ISOLATED_STATE_FILE = "entry_gate_dry_run_risk_state.json"
OUTPUT_FILE = "entry_gate_dry_run_telemetry.json"
POSITION_LOG_FILE = "entry_gate_dry_run_position_log.json"


class _GovernedKite:
    """Wraps a real KiteConnect instance so every method call is gated
    through the shared cross-process governor before it reaches Kite -
    generic (via __getattr__) rather than enumerating profile/positions/
    orders/order_charges by name, so it stays correct if
    KiteBrokerAdapter/get_daily_risk_snapshot() ever calls something new.
    Every call reachable through this dry-run path is a read-only
    account-info lookup, never quote()/historical_data()/place_order(),
    so DEFAULT (10/s) is the correct class throughout."""

    def __init__(self, kite, governor: KiteRequestGovernor):
        self._kite = kite
        self._governor = governor

    def __getattr__(self, name):
        attr = getattr(self._kite, name)
        if not callable(attr):
            return attr

        def _gated(*args, **kwargs):
            self._governor.acquire(GOV_DEFAULT)
            return attr(*args, **kwargs)
        return _gated


class _SingleFetchBrokerView:
    """Wraps a real broker so get_daily_risk_snapshot() only ever actually
    hits the broker once per instance, however many times it's called after
    that (cached result, or cached exception, replayed identically).

    Why this exists: RunnerEntryAuthorizer._fresh_snapshot() AND
    P03RiskController.evaluate_entry() (reached via authorizer.controller)
    each independently call get_daily_risk_snapshot() - that's intentional
    in the real authorize_buy() double-check flow, which re-observes the
    broker right before a real dispatch to catch races. There is no
    dispatch here to protect, and evaluating several candidates a fraction
    of a second apart in one poll cycle doesn't need a materially fresher
    snapshot for each one - it multiplies broker API calls for no benefit,
    and previously caused occasional transient rate-limit failures. One
    fetch per RunnerEntryAuthorizer instance (i.e. once per run_once()
    cycle - a fresh authorizer, and therefore a fresh wrapped broker, is
    built every cycle) is the right cost for this tool's purpose. The real
    production runner's own broker adapter is never wrapped or touched.
    """

    def __init__(self, broker):
        self._broker = broker
        self._snapshot = None
        self._error = None
        self._fetched = False

    def __getattr__(self, name):
        return getattr(self._broker, name)

    def get_daily_risk_snapshot(self):
        if not self._fetched:
            self._fetched = True
            try:
                self._snapshot = self._broker.get_daily_risk_snapshot()
            except Exception as exc:
                self._error = exc
        if self._error is not None:
            raise self._error
        return self._snapshot


def build_authorizer(broker, *, state_path: Path, config: Config = _DEFAULT_CONFIG) -> RunnerEntryAuthorizer:
    return RunnerEntryAuthorizer(
        broker=_SingleFetchBrokerView(broker),
        state_store=RunnerEntryStateStore(str(state_path)),
        kill_switch_file=P03_KILL_SWITCH_FILE,
        capital_limit=config.trial_capital,
        target_risk_pct=config.target_risk_pct,
        max_trade_risk_pct=config.max_trade_risk_pct,
        daily_entry_lock_pct=config.daily_entry_lock_pct,
        daily_hard_halt_pct=config.daily_hard_halt_pct,
        rolling_week_halt_pct=config.rolling_week_halt_pct,
        trial_drawdown_halt_pct=config.trial_drawdown_halt_pct,
        daily_turnover_pct=config.daily_turnover_pct,
        stop_loss_pct=config.stop_loss_pct,
        max_daily_entries=config.max_daily_entries,
        max_simultaneous_positions=config.max_simultaneous_positions,
        entry_cooldown_seconds=config.entry_cooldown_seconds,
    )


def build_candidate_order(symbol: str, price: float, quantity: int) -> Optional[dict[str, Any]]:
    """The exact shape the production runner would submit, minus dispatch."""
    if price <= 0 or quantity <= 0:
        return None
    return {
        "tradingsymbol": symbol,
        "exchange": "NSE",
        "transaction_type": "BUY",
        "quantity": quantity,
        "price": price,
        "product": "MIS",
        "order_type": "LIMIT",
        "tag": "V3.4_ENTRY",
    }


_TICK_SIZE_CACHE: dict[str, Decimal] = {}


def get_cached_tick_size(broker, symbol: str) -> Decimal:
    """get_tick_size() is a real, read-only broker call (kite.instruments("NSE")) -
    cached per symbol for this process's lifetime since tick sizes are stable
    intraday and instruments() returns the full NSE instrument list."""
    if symbol not in _TICK_SIZE_CACHE:
        _TICK_SIZE_CACHE[symbol] = broker.get_tick_size(symbol)
    return _TICK_SIZE_CACHE[symbol]


def compute_emergency_exit_trigger(source_ltp: Decimal, tick_size: Decimal) -> Decimal:
    """Exactly institutional_engine_v34_p01d_candidate.py's PROTECTION-state
    formula: one tick below LTP, rounded down to the nearest tick - the real
    trigger this system computes for an emergency exit, not an invented one."""
    from decimal import ROUND_DOWN
    raw_trigger = source_ltp - tick_size
    return (raw_trigger / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size


def validate_exit_payload(
    *, symbol: str, quantity: int, trigger_price: Decimal, source_ltp: Decimal,
    tick_size: Decimal, market_protection: Decimal = Decimal("-1"), tag: str = "V3.4_EXIT",
) -> None:
    """Deliberate, checked mirror of KiteBrokerAdapter.submit_emergency_exit()'s
    validation chain - everything after its live_trading gate, before its
    final self.kite.place_order call. Raises RuntimeError with the identical
    message text the real function would raise, for the identical reason.
    Never calls the broker; never places, modifies, or cancels an order."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise RuntimeError("SAFETY_HALT: Emergency exit symbol is invalid.")
    if symbol != symbol.strip():
        raise RuntimeError("SAFETY_HALT: Emergency exit symbol contains surrounding whitespace.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise RuntimeError("SAFETY_HALT: Emergency exit quantity must be a positive integer.")

    try:
        trigger = Decimal(str(trigger_price))
        ltp = Decimal(str(source_ltp))
        tick = Decimal(str(tick_size))
        protection = Decimal(str(market_protection))
    except Exception as exc:
        raise RuntimeError(f"SAFETY_HALT: Emergency exit numeric parameter is malformed: {exc}") from exc

    if ltp <= 0:
        raise RuntimeError("SAFETY_HALT: Emergency exit source LTP must be positive.")
    if tick <= 0:
        raise RuntimeError("SAFETY_HALT: Emergency exit tick size must be positive.")
    if trigger <= 0:
        raise RuntimeError("SAFETY_HALT: Emergency exit trigger price must be positive.")
    if not trigger < ltp:
        raise RuntimeError("SAFETY_HALT: Emergency SELL SL-M trigger must be strictly below source LTP.")

    tick_units = trigger / tick
    if tick_units != tick_units.to_integral_value():
        raise RuntimeError("SAFETY_HALT: Emergency exit trigger price is not aligned to instrument tick size.")

    if protection != Decimal("-1"):
        raise RuntimeError("SAFETY_HALT: V3.4 emergency market protection is frozen to -1 (automatic).")

    if tag != "V3.4_EXIT":
        raise RuntimeError("SAFETY_HALT: Invalid V3.4 emergency exit tag.")


def evaluate_hypothetical_exit(broker, *, symbol: str, quantity: int, source_ltp: float) -> dict[str, Any]:
    """Build and validate the exit order that would protect the position this
    entry candidate just (hypothetically) opened. Never places, modifies, or
    cancels an order - broker.get_tick_size() (read-only) is the only broker
    call made here."""
    ltp = Decimal(str(source_ltp))
    try:
        tick_size = get_cached_tick_size(broker, symbol)
    except Exception as exc:
        return {
            "order_payload": None, "valid": False,
            "reason": f"TICK_SIZE_UNAVAILABLE: {exc}",
        }

    trigger = compute_emergency_exit_trigger(ltp, tick_size)
    order_payload = {
        "tradingsymbol": symbol, "exchange": "NSE", "transaction_type": "SELL",
        "quantity": quantity, "product": "MIS", "order_type": "SL-M",
        "trigger_price": str(trigger), "market_protection": "-1", "tag": "V3.4_EXIT",
    }
    try:
        validate_exit_payload(
            symbol=symbol, quantity=quantity, trigger_price=trigger,
            source_ltp=ltp, tick_size=tick_size,
        )
        return {"order_payload": order_payload, "valid": True, "reason": None, "tick_size": str(tick_size)}
    except RuntimeError as exc:
        return {"order_payload": order_payload, "valid": False, "reason": str(exc), "tick_size": str(tick_size)}


# ---------------------------------------------------------------------------
# Hypothetical take-profit — 2026-08-24, owner request.
#
# HONEST DISCLOSURE: unlike compute_emergency_exit_trigger() (a byte-for-byte
# mirror of a real formula institutional_engine_v34_p01d_candidate.py
# actually computes), this has NO equivalent anywhere in that engine - its
# entire state machine has exactly one exit path (the emergency SL-M above),
# confirmed by reading every state name in the file. There is no
# "PROFIT_TARGET" state, no target-price order type, nothing to mirror.
# This function does not describe P01D V3.4's real behavior; it answers a
# different, explicitly hypothetical question: "if this engine WERE given a
# 2:1 reward:risk target (P02 Pillar II's own convention, reused here
# purely for comparability - not because P01D actually uses it), what would
# that order look like?" Tagged V3.4_HYPOTHETICAL_TP_NOT_NATIVE, deliberately
# NOT "V3.4_..." like the real entry/exit tags, so a log line can never be
# mistaken for something the real engine produced. Never wired into
# run_production_p01d_candidate.py or institutional_engine_v34_p01d_
# candidate.py - both are untouched by this addition.
# ---------------------------------------------------------------------------
REWARD_RISK_MULTIPLE = Decimal("2.0")  # P02 Pillar II's convention (p02_core.py's REWARD_RISK_MULTIPLE), reused for comparability only


def compute_hypothetical_take_profit(
    entry_price: Decimal, emergency_exit_trigger: Decimal, tick_size: Decimal,
    reward_risk_multiple: Decimal = REWARD_RISK_MULTIPLE,
) -> Decimal:
    """target = entry + reward_risk_multiple * (entry - emergency_exit_trigger),
    rounded DOWN to the nearest tick - same conservative rounding direction
    compute_emergency_exit_trigger() already uses (a SELL limit rounded down
    fills at least as easily as the unrounded target, never harder)."""
    from decimal import ROUND_DOWN
    risk_per_share = entry_price - emergency_exit_trigger
    raw_target = entry_price + (reward_risk_multiple * risk_per_share)
    return (raw_target / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size


def validate_take_profit_payload(
    *, symbol: str, quantity: int, target_price: Decimal, entry_price: Decimal,
    tick_size: Decimal, tag: str = "V3.4_HYPOTHETICAL_TP_NOT_NATIVE",
) -> None:
    """Same checked-mirror discipline as validate_exit_payload(), adapted
    for a take-profit direction (target strictly ABOVE entry, not below)."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit symbol is invalid.")
    if symbol != symbol.strip():
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit symbol contains surrounding whitespace.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit quantity must be a positive integer.")

    try:
        target = Decimal(str(target_price))
        entry = Decimal(str(entry_price))
        tick = Decimal(str(tick_size))
    except Exception as exc:
        raise RuntimeError(f"SAFETY_HALT: Hypothetical take-profit numeric parameter is malformed: {exc}") from exc

    if entry <= 0:
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit entry price must be positive.")
    if tick <= 0:
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit tick size must be positive.")
    if target <= 0:
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit target price must be positive.")
    if not target > entry:
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit target must be strictly above entry price.")

    tick_units = target / tick
    if tick_units != tick_units.to_integral_value():
        raise RuntimeError("SAFETY_HALT: Hypothetical take-profit target price is not aligned to instrument tick size.")

    if tag != "V3.4_HYPOTHETICAL_TP_NOT_NATIVE":
        raise RuntimeError("SAFETY_HALT: Invalid hypothetical take-profit tag.")


def evaluate_hypothetical_take_profit(
    broker, *, symbol: str, quantity: int, entry_price: float, emergency_exit_trigger: str,
) -> dict[str, Any]:
    """Build and validate a 2:1-reward:risk LIMIT SELL that would take profit
    on the position this candidate just (hypothetically) opened - explicitly
    NOT part of P01D V3.4's real design, see module comment above. Never
    places, modifies, or cancels an order; broker.get_tick_size() (read-only)
    is the only broker call made here."""
    entry = Decimal(str(entry_price))
    trigger = Decimal(str(emergency_exit_trigger))
    try:
        tick_size = get_cached_tick_size(broker, symbol)
    except Exception as exc:
        return {
            "order_payload": None, "valid": False,
            "reason": f"TICK_SIZE_UNAVAILABLE: {exc}",
        }

    target = compute_hypothetical_take_profit(entry, trigger, tick_size)
    order_payload = {
        "tradingsymbol": symbol, "exchange": "NSE", "transaction_type": "SELL",
        "quantity": quantity, "product": "MIS", "order_type": "LIMIT",
        "price": str(target), "tag": "V3.4_HYPOTHETICAL_TP_NOT_NATIVE",
    }
    try:
        validate_take_profit_payload(
            symbol=symbol, quantity=quantity, target_price=target,
            entry_price=entry, tick_size=tick_size,
        )
        return {
            "order_payload": order_payload, "valid": True, "reason": None,
            "tick_size": str(tick_size), "reward_risk_multiple": str(REWARD_RISK_MULTIPLE),
        }
    except RuntimeError as exc:
        return {
            "order_payload": order_payload, "valid": False, "reason": str(exc),
            "tick_size": str(tick_size), "reward_risk_multiple": str(REWARD_RISK_MULTIPLE),
        }


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def record_entry_price(
    log_path: Path, *, trading_day: str, symbol: str, quantity: int,
    entry_price: float, timestamp: str, source: str,
) -> None:
    """Append-only, per-trading-day log of the exact reservation-moment
    price - the one thing _apply_policy_and_reserve() (real, untouched
    production code) does not itself persist. Never called more than once
    per symbol per day in practice: the real SAME_SYMBOL_DAILY_LOCK gate
    already guarantees a symbol can only reach ALL_CONDITIONS_PASSED once
    per trading day, so this naturally can't duplicate an entry."""
    log = _read_json(log_path) or {}
    day_entries = list(log.get(trading_day, []))
    day_entries.append({
        "symbol": symbol, "quantity": quantity, "entry_price": entry_price,
        "timestamp": timestamp, "source": source,
    })
    log[trading_day] = day_entries
    target = Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def compute_realized_hypothetical_pnl(
    log_path: Path, *, trading_day: str, current_prices: dict[str, float],
) -> dict[str, Any]:
    """Exact - not reconstructed - hypothetical P&L for every entry recorded
    on `trading_day`, marked against `current_prices` using the real
    Zerodha delivery cost schedule on both legs (same methodology the
    momentum shadow itself uses). A symbol with no current price available
    is reported but excluded from the total."""
    log = _read_json(log_path) or {}
    entries = log.get(trading_day, [])
    positions = []
    total_net_pnl = Decimal("0")
    total_entry_notional = Decimal("0")
    for entry in entries:
        symbol = entry["symbol"]
        quantity = int(entry["quantity"])
        entry_price = Decimal(str(entry["entry_price"]))
        entry_notional = quantity * entry_price
        total_entry_notional += entry_notional
        current = current_prices.get(symbol)
        if current is None:
            positions.append({**entry, "current_price": None, "net_pnl": None, "status": "PRICE_UNAVAILABLE"})
            continue
        exit_price = Decimal(str(current))
        exit_notional = quantity * exit_price
        buy_fees = Decimal(str(buy_cost(float(entry_notional)).total))
        sell_fees = Decimal(str(sell_cost(float(exit_notional)).total))
        net_pnl = (exit_notional - entry_notional) - buy_fees - sell_fees
        total_net_pnl += net_pnl
        positions.append({
            **entry, "current_price": str(exit_price),
            "buy_cost": str(buy_fees), "sell_cost": str(sell_fees),
            "net_pnl": str(net_pnl), "status": "MARKED",
        })
    return {
        "trading_day": trading_day,
        "positions": positions,
        "total_entry_notional": str(total_entry_notional),
        "total_net_pnl": str(total_net_pnl),
    }


def read_latest_candidates(
    *, momentum_path: Path = Path("shadow_strategy_telemetry.json"),
    orb_path: Path = Path("orb_shadow_telemetry.json"),
) -> list[dict[str, Any]]:
    """Pull today's current top candidate from each already-running shadow
    observer's telemetry file. Never calls the broker; pure file reads."""
    candidates: list[dict[str, Any]] = []

    momentum = _read_json(momentum_path)
    external = momentum.get("external_variant") if isinstance(momentum, dict) else None
    if isinstance(external, dict) and external.get("status") == "MARKED":
        for row in external.get("selected") or []:
            price = row.get("live_price")
            symbol = row.get("symbol")
            if isinstance(symbol, str) and isinstance(price, (int, float)) and price > 0:
                candidates.append({"source": "EXTERNAL_CROSS_SECTIONAL_12_1", "symbol": symbol, "price": float(price)})

    orb = _read_json(orb_path)
    if isinstance(orb, dict) and orb.get("decision") == "HYPOTHETICAL_BUY":
        top = orb.get("top_candidate")
        if isinstance(top, dict):
            symbol, price = top.get("symbol"), top.get("last_price")
            if isinstance(symbol, str) and isinstance(price, (int, float)) and price > 0:
                candidates.append({"source": "ORB_EXPLORATORY", "symbol": symbol, "price": float(price)})

    return candidates


def evaluate_against_real_risk_gate(
    *, authorizer: RunnerEntryAuthorizer, broker, symbol: str, price: float,
    snapshot, live_trading_enabled: bool = False,
) -> dict[str, Any]:
    """Run the full real P0-3B-F pipeline for `symbol`/`price` and report the
    outcome alongside the unconditional physical-dispatch block.

    Mirrors RunnerEntryAuthorizer.authorize_buy()'s body exactly, except:
    - it is never called through authorize_buy() itself, so the
      live_trading_enabled halt at that method's first line is never reached;
    - it does the single-snapshot happy path, not authorize_buy()'s
      snapshot-changed-before-submission double-check, since there is no
      dispatch here for that race-condition guard to protect;
    - the snapshot is fetched once per poll cycle by the caller (run_once)
      and shared across every candidate evaluated that cycle, rather than
      re-fetched per candidate - evaluating several candidates a fraction of
      a second apart never needs a materially fresher snapshot for each one,
      and re-fetching per candidate multiplies broker API calls for no
      benefit (this used to call _fresh_snapshot() itself, once per
      candidate, and occasionally hit a transient rate limit as a result).

    live_trading_enabled is accepted only to be asserted False - this tool
    has no way to make it True and refuses to run if it somehow were.
    """
    if live_trading_enabled:
        raise RuntimeError(
            "SAFETY_HALT: entry_gate_dry_run refuses to operate with "
            "live_trading_enabled=True. This tool is dry-run only."
        )

    quantity = authorizer.risk_based_quantity(Decimal(str(price)))
    order = build_candidate_order(symbol, price, quantity)

    stage = "SIZING"
    allowed = quantity > 0
    reason = None if allowed else "ZERO_RISK_BASED_QUANTITY"

    if allowed:
        stage = "CAPITAL_AND_DAILY_LOSS_GATE"
        decision = authorizer.controller.evaluate_entry(symbol, quantity, Decimal(str(price)))
        if not decision.allowed:
            allowed, reason = False, decision.reason or "ENTRY_DENIED"

    if allowed:
        stage = "P0_3B_F_POLICY_GATE"
        try:
            authorizer._apply_policy_and_reserve(
                snapshot=snapshot, symbol=symbol, quantity=quantity, price=price,
            )
        except RuntimeError as exc:
            allowed, reason = False, str(exc)

    hypothetical_exit = None
    hypothetical_take_profit = None
    if allowed:
        stage = "ALL_CONDITIONS_PASSED"
        hypothetical_exit = evaluate_hypothetical_exit(
            broker, symbol=symbol, quantity=quantity, source_ltp=price,
        )
        if hypothetical_exit["valid"]:
            hypothetical_take_profit = evaluate_hypothetical_take_profit(
                broker, symbol=symbol, quantity=quantity, entry_price=price,
                emergency_exit_trigger=hypothetical_exit["order_payload"]["trigger_price"],
            )

    return {
        "mode": "READ_ONLY_RISK_GATE_DRY_RUN",
        "authoritative_strategy": False,
        "order_payload": order,
        "hypothetical_exit": hypothetical_exit,
        "hypothetical_take_profit": hypothetical_take_profit,
        "risk_gate": {
            "evaluated_by": "RunnerEntryAuthorizer real pipeline: "
                            "_fresh_snapshot -> controller.evaluate_entry -> _apply_policy_and_reserve",
            "quantity": quantity,
            "final_stage_reached": stage,
            "allowed": allowed,
            "reason": reason,
            "capital_limit": str(authorizer.capital_limit),
            "target_risk": str(authorizer.target_risk),
            "max_trade_risk": str(authorizer.max_trade_risk),
            "daily_hard_halt": str(authorizer.daily_hard_halt),
            "rolling_week_halt": str(authorizer.rolling_week_halt),
            "trial_drawdown_halt": str(authorizer.trial_drawdown_halt),
        },
        "physical_dispatch_gate": {
            "status": "BLOCKED_LIVE_TRADING_DISABLED",
            "live_trading_enabled": False,
            "note": (
                "Unconditional. This line does not depend on the risk-gate "
                "result above - even a fully-allowed candidate stops here."
            ),
        },
        "capabilities": {
            "broker_write": False,
            "order_api": False,
            "request_entry": False,
            "state_mutation_isolated_to": ISOLATED_STATE_FILE,
        },
    }


def run_once(*, broker, output: Path = Path(OUTPUT_FILE),
             state_path: Path = Path(ISOLATED_STATE_FILE),
             position_log_path: Path = Path(POSITION_LOG_FILE)) -> dict[str, Any]:
    authorizer = build_authorizer(broker, state_path=state_path)
    candidates = read_latest_candidates()
    evaluations = []
    now = datetime.now(IST)
    trading_day = now.date().isoformat()

    snapshot = None
    snapshot_error = None
    if candidates:
        try:
            snapshot = authorizer._fresh_snapshot()
        except RuntimeError as exc:
            snapshot_error = str(exc)

    for candidate in candidates:
        if snapshot is None:
            result = {
                "mode": "READ_ONLY_RISK_GATE_DRY_RUN", "authoritative_strategy": False,
                "order_payload": None,
                "hypothetical_exit": None,
                "hypothetical_take_profit": None,
                "risk_gate": {
                    "evaluated_by": "RunnerEntryAuthorizer real pipeline (snapshot fetch failed)",
                    "quantity": None, "final_stage_reached": "BROKER_SNAPSHOT",
                    "allowed": False, "reason": snapshot_error,
                    "capital_limit": str(authorizer.capital_limit),
                    "target_risk": str(authorizer.target_risk), "max_trade_risk": str(authorizer.max_trade_risk),
                    "daily_hard_halt": str(authorizer.daily_hard_halt),
                    "rolling_week_halt": str(authorizer.rolling_week_halt),
                    "trial_drawdown_halt": str(authorizer.trial_drawdown_halt),
                },
                "physical_dispatch_gate": {
                    "status": "BLOCKED_LIVE_TRADING_DISABLED", "live_trading_enabled": False,
                    "note": "Unconditional - never reached the risk gate this cycle either way.",
                },
                "capabilities": {
                    "broker_write": False, "order_api": False, "request_entry": False,
                    "state_mutation_isolated_to": ISOLATED_STATE_FILE,
                },
            }
        else:
            result = evaluate_against_real_risk_gate(
                authorizer=authorizer, broker=broker, symbol=candidate["symbol"], price=candidate["price"],
                snapshot=snapshot,
            )
            if result["risk_gate"]["allowed"] and result["risk_gate"]["final_stage_reached"] == "ALL_CONDITIONS_PASSED":
                record_entry_price(
                    position_log_path, trading_day=trading_day, symbol=candidate["symbol"],
                    quantity=result["risk_gate"]["quantity"], entry_price=candidate["price"],
                    timestamp=now.isoformat(), source=candidate["source"],
                )
        result["candidate_source"] = candidate["source"]
        evaluations.append(result)

    telemetry = {
        "observed_at": now.isoformat(),
        "candidates_found": len(candidates),
        "evaluations": evaluations,
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(telemetry, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)
    return telemetry


def main() -> int:
    import time

    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        print("[BLOCK] KITE_API_KEY and KITE_ACCESS_TOKEN must already exist in this terminal.")
        return 2

    governor_dir = os.getenv("KITE_RATE_GOVERNOR_DIR")
    if not governor_dir:
        print("[BLOCK] KITE_RATE_GOVERNOR_DIR must be set - same shared directory every other "
              "process sharing this Kite account uses, so requests are coordinated across all "
              "of them. See kite_request_governor.py's own module docstring.")
        return 2
    governor = KiteRequestGovernor(state_dir=Path(governor_dir))

    from kiteconnect import KiteConnect
    from run_production_p01d_candidate import KiteBrokerAdapter

    raw_kite = KiteConnect(api_key=api_key)
    raw_kite.set_access_token(access_token)
    kite = _GovernedKite(raw_kite, governor)
    profile = kite.profile()
    account_id = profile.get("user_id") if isinstance(profile, dict) else None
    broker = KiteBrokerAdapter(kite, live_trading=False, expected_account_id=account_id)

    print("V3.4 ENTRY GATE DRY RUN - real RunnerEntryAuthorizer pipeline, zero orders")
    print(f"Kite rate governor: {governor_dir}")
    print(f"Capital limit: Rs {_DEFAULT_CONFIG.trial_capital}")
    print(f"Isolated risk state: {ISOLATED_STATE_FILE} (never the real production state)")
    print("Reading candidates from shadow_strategy_telemetry.json / orb_shadow_telemetry.json")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            telemetry = run_once(broker=broker)
            for row in telemetry["evaluations"]:
                gate = row["risk_gate"]
                print(
                    f"{telemetry['observed_at']} source={row['candidate_source']} "
                    f"symbol={row['order_payload']['tradingsymbol'] if row['order_payload'] else '-'} "
                    f"qty={gate['quantity']} stage={gate['final_stage_reached']} "
                    f"allowed={gate['allowed']} reason={gate['reason']} "
                    f"-> {row['physical_dispatch_gate']['status']}"
                )
                exit_info = row.get("hypothetical_exit")
                if exit_info is not None:
                    ep = exit_info.get("order_payload") or {}
                    print(
                        f"    hypothetical emergency exit: valid={exit_info['valid']} "
                        f"trigger={ep.get('trigger_price', '-')} reason={exit_info['reason']}"
                    )
                tp_info = row.get("hypothetical_take_profit")
                if tp_info is not None:
                    tpp = tp_info.get("order_payload") or {}
                    print(
                        f"    hypothetical take-profit (2:1, NOT native to V3.4): valid={tp_info['valid']} "
                        f"target={tpp.get('price', '-')} reason={tp_info['reason']}"
                    )
            if not telemetry["evaluations"]:
                print(f"{telemetry['observed_at']} no active candidate from either shadow observer")
            time.sleep(60.0)
    except KeyboardInterrupt:
        print("Entry gate dry run stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
