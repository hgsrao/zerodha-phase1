"""Phase 3.6 — daily accounting rollover.

New module, deliberately a NAMED, standalone helper (per the user's own
explicit instruction) rather than logic inlined into `_build_engine()` -
testable without starting the daemon, exactly like every other pure
decision function in this project.

WHEN THIS RUNS, TRACED FROM THE FROZEN ENGINE, NOT INVENTED: the frozen
engine's own `reconcile_startup()` (institutional_engine_v34_p02_
multipos_candidate.py) rolls `BotState.trading_day` forward ONLY when it
runs - and it only runs when `state.status == EngineStatus.STARTUP`,
which itself only happens at a genuine process restart (or after
`clear_halt_and_reconcile()`). The frozen engine has NO periodic,
intraday day-boundary check at all - a daemon that stays running across
midnight without restarting simply never rolls `BotState.trading_day`
until its next restart. This module's own trading-day rollover is
deliberately kept at the SAME cadence, for consistency: `DailyAccounting
State.trading_day` is rolled once, during the Phase 3.6 startup sequence
(step 11, "refresh/reconcile daily accounting"), not on a separate
intraday timer. This is a traced, matched design choice, not a
limitation invented here - solving "what if the daemon runs for multiple
days without restarting" would mean giving the daily-accounting layer a
STRONGER rollover guarantee than the frozen engine's own BotState has,
which is not this module's place to invent.

WHAT ROLLS AND WHAT SURVIVES UNTOUCHED:

- `checkpoint` advances through `roll_daily_checkpoint()` (v34_p02_
  accounting.py, frozen) - the only function allowed to advance
  `trial_high_water_mark`/`prior_close_equity` (spec §6's cadence pin).
  This module does not reimplement that formula; it only decides WHEN to
  call it and supplies the caller-computed `fresh_equity_at_rollover`.
- The just-ended trading day's final `daily_pnl` (computed via
  `compute_daily_pnl()`, also frozen, from the pre-roll checkpoint's own
  `prior_close_equity` and the caller-supplied fresh equity) is sealed
  into `sealed_daily_pnl_series[old_trading_day]` EXACTLY ONCE - the
  function's own idempotency guard (`state.trading_day == current_
  trading_day` -> no-op) is what prevents a second call for the same
  transition from sealing twice.
- `sealed_daily_pnl_series` is trimmed to a trailing window after
  sealing - `compute_rolling_week_pnl()`'s own docstring (v34_p02_
  accounting.py) explicitly delegates this to the caller ("responsible
  for trimming... to the trailing window"), so it must happen somewhere,
  and this is that somewhere.

  POLICY AUTHORIZED (Pre-Live Broker Validation Gate 1, 2026-08-15) -
  previously shipped as `PROVISIONAL_ROLLING_WEEK_WINDOW_CALENDAR_DAYS`,
  flagged explicitly rather than silently settled, because the frozen
  spec never numerically defines "rolling week" anywhere (grepped, zero
  hits) and the candidate readings produce materially different
  `ROLLING_WEEK_HALT` decisions from identical P&L history (7 calendar
  days ~ 5 trading sessions and resets continuously; 7 trading days
  spans 9-11 calendar days; a Monday-Friday NSE week resets at a
  boundary instead of rolling). The user has now explicitly authorized:
  "Rolling week = the continuously trailing 7 calendar days, including
  the current trading day" - the literal reading of "rolling," and
  deliberately more conservative than a Monday-Friday reset, since a
  loss cannot disappear merely because a new calendar week begins.
  `ROLLING_WEEK_WINDOW_CALENDAR_DAYS` below implements exactly that:
  verified by direct computation (not just code inspection) that the
  trim keeps the prior 6 sealed calendar days plus today's own live
  `daily_pnl` (added separately, by `compute_rolling_week_pnl` itself,
  frozen) = 7 calendar days total, inclusive of today - no change to
  this module's own trim arithmetic was needed once the policy was
  stated; only the "provisional" framing is retired here.
- `cumulative_realized_pnl`, `cumulative_charges`, `booked_trade_ids`,
  `accounted_charge_by_order_id`, `dp_charges_booked`, and - the
  explicitly named acceptance criterion - `open_position_basis` are all
  carried forward COMPLETELY UNTOUCHED. An overnight position's cost
  basis surviving a day-rollover is not special-cased here; it simply
  isn't one of the fields this function ever writes to.
- Nothing here reclassifies a carried-over CNC position as a new entry -
  that was never this module's concern in the first place:
  `entries_today`/`turnover_today` (v34_bridge_daily_accounting.py) are
  recomputed fresh from THAT DAY's broker orders/trades every cycle, not
  derived from `open_position_basis` at all, so a position opened on an
  earlier day simply has no matching order in today's (day-scoped)
  broker response to be miscounted from.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict

from v34_bridge_daily_accounting_state import DailyAccountingState
from v34_p02_accounting import compute_daily_pnl, roll_daily_checkpoint

ROLLING_WEEK_WINDOW_CALENDAR_DAYS = 7  # authorized policy - see module docstring's "POLICY AUTHORIZED" note (Pre-Live Gate 1, 2026-08-15)


def _trim_to_trailing_window(sealed: Dict[str, Decimal], *, current_trading_day: str, window_days: int) -> Dict[str, Decimal]:
    cutoff = date.fromisoformat(current_trading_day) - timedelta(days=window_days)
    return {day: pnl for day, pnl in sealed.items() if date.fromisoformat(day) > cutoff}


def roll_daily_accounting_if_needed(
    state: DailyAccountingState, *, current_trading_day: str, fresh_equity_at_rollover: Decimal,
) -> DailyAccountingState:
    """Pure. Returns `state` completely unchanged (same object) if
    `current_trading_day` already matches `state.trading_day` - the
    idempotency guard that makes "seals the prior day exactly once" true
    even if this is called more than once around the same transition.
    `fresh_equity_at_rollover` must already be computed by the caller
    (I/O - a broker positions/orders/quotes fetch through `build_
    portfolio_snapshot`, v34_p02_accounting.py) BEFORE calling this;
    this function makes no broker calls itself."""
    if state.trading_day == current_trading_day:
        return state

    sealed_pnl_for_ended_day = compute_daily_pnl(
        equity=fresh_equity_at_rollover, prior_close_equity=state.checkpoint.prior_close_equity,
    )
    sealed = dict(state.sealed_daily_pnl_series)
    sealed[state.trading_day] = sealed_pnl_for_ended_day
    sealed = _trim_to_trailing_window(sealed, current_trading_day=current_trading_day, window_days=ROLLING_WEEK_WINDOW_CALENDAR_DAYS)

    new_checkpoint = roll_daily_checkpoint(
        previous_checkpoint=state.checkpoint, new_trading_day=current_trading_day,
        fresh_equity_at_rollover=fresh_equity_at_rollover,
    )

    return DailyAccountingState(
        trading_day=current_trading_day,
        cumulative_realized_pnl=state.cumulative_realized_pnl,
        cumulative_charges=state.cumulative_charges,
        checkpoint=new_checkpoint,
        sealed_daily_pnl_series=sealed,
        booked_trade_ids=state.booked_trade_ids,
        accounted_charge_by_order_id=state.accounted_charge_by_order_id,
        dp_charges_booked=state.dp_charges_booked,
        open_position_basis=state.open_position_basis,
    )
