"""Tests for v34_bridge_runner_core.py's EA1-R1 quote-fetch degrade
classification (_is_safe_to_degrade_quote_exception / the try/except
around _fetch_live_price() inside _drive_entering()).

Direct successor to the 2026-08-17 Terminal B incident: a bare ltp()
failure used to unconditionally halt_plan() the whole plan, silently
(halt_plan()'s own `reason` argument discarded). This proves the new
behavior: a provably pre-mutation, provably-transient-or-429 exception
degrades this ONE leg for ONE cycle (leaving it PENDING, logging a real
audit event) instead of halting the whole plan - and that anything else
still fails closed exactly as before.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from kiteconnect.exceptions import NetworkException

from test_v34_bridge_runner_core import ALL_LIVE_QUOTES, _approve
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import FakeBroker, flat_running_state
from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import LegStatus, RebalancePlanStatus, RebalancePlanStore, create_rebalance_plan
from v34_bridge_runner_core import _is_safe_to_degrade_quote_exception, drive_plan_one_cycle
from v34_bridge_target_portfolio import build_target_portfolio

SIGNAL_DATE = date(2026, 8, 14)


class _LTPFailsOnce(FakeBroker):
    """Raises the configured exception on the FIRST ltp() call for
    `fails_for_symbol`, then behaves normally forever after - proves a
    degraded leg genuinely recovers on the very next cycle, not just that
    it avoids halting."""

    def __init__(self, *, fails_for_symbol: str, exc_to_raise: Exception):
        super().__init__()
        self._fails_for_symbol = fails_for_symbol
        self._exc_to_raise = exc_to_raise
        self._already_failed = False

    def ltp(self, symbols):
        if symbols == [self._fails_for_symbol] and not self._already_failed:
            self._already_failed = True
            raise self._exc_to_raise
        return self.quotes


def _setup(tmp_path, *, raw_broker):
    target = build_target_portfolio(quotes=ALL_LIVE_QUOTES, signal_date=SIGNAL_DATE, positions=2)
    diff = compute_rebalance_diff(current_portfolio={}, target=target)
    plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
    plan_store = RebalancePlanStore(tmp_path)
    plan_store.save(plan)
    _approve(plan)
    plan_store.save(plan)

    raw_broker.quotes = dict(ALL_LIVE_QUOTES)
    raw_broker.place_order_fn = lambda **kwargs: f"ORD-{kwargs['tradingsymbol']}"

    from portfolio_brain_v9 import SECTORS as REAL_SECTORS
    from v34_p02_accounting import initial_checkpoint
    from v34_p02_state import Config
    cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
    context = make_context(
        sector_lookup=REAL_SECTORS,
        checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital),
    )
    engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
        state=flat_running_state(), raw_broker=raw_broker, cfg=cfg, context=context,
    )

    while plan.status not in (RebalancePlanStatus.ENTERING, RebalancePlanStatus.HALTED):
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
    assert plan.status == RebalancePlanStatus.ENTERING
    return plan, plan_store, engine, terminator, audit


# ---------------------------------------------------------------------------
# Unit-level classifier tests
# ---------------------------------------------------------------------------

class TestIsSafeToDegradeQuoteException:
    def test_kite_429_is_safe_to_degrade(self):
        assert _is_safe_to_degrade_quote_exception(NetworkException("Too many requests", code=429)) is True

    def test_kite_502_503_504_are_safe_to_degrade(self):
        for code in (502, 503, 504):
            assert _is_safe_to_degrade_quote_exception(NetworkException("x", code=code)) is True

    def test_stdlib_timeout_and_connection_errors_are_safe_to_degrade(self):
        assert _is_safe_to_degrade_quote_exception(TimeoutError("x")) is True
        assert _is_safe_to_degrade_quote_exception(ConnectionError("x")) is True

    def test_an_unrecognized_kite_code_is_not_safe_to_degrade(self):
        assert _is_safe_to_degrade_quote_exception(NetworkException("x", code=400)) is False

    def test_a_plain_unrelated_exception_is_not_safe_to_degrade(self):
        assert _is_safe_to_degrade_quote_exception(RuntimeError("no live quote available for SBIN")) is False
        assert _is_safe_to_degrade_quote_exception(ValueError("something structurally wrong")) is False


# ---------------------------------------------------------------------------
# Real Terminal B reproduction, now with the fix in place - the fixed
# counterpart to test_ea1_incident_reproduction_20260817.py's
# TestTerminalBBareLTPFailureReproduction.
# ---------------------------------------------------------------------------

class TestQuoteFetch429DegradesInsteadOfHalting:
    def test_429_during_ltp_leaves_the_plan_entering_not_halted(self, tmp_path):
        raw_broker = _LTPFailsOnce(
            fails_for_symbol="LAURUSLABS",
            exc_to_raise=NetworkException("Too many requests", code=429),
        )
        plan, plan_store, engine, terminator, audit = _setup(tmp_path, raw_broker=raw_broker)
        assert set(plan.diff.enters) == {"LAURUSLABS", "SHRIRAMFIN"}

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # attempts LAURUSLABS, hits the 429

        # --- The fix: no halt, no permanent death ---
        assert plan.status == RebalancePlanStatus.ENTERING
        assert plan.enter_status["LAURUSLABS"] == LegStatus.PENDING  # untouched, will retry
        assert terminator.halted is False

        # --- The fix's other half: this is now VISIBLE, unlike before ---
        assert audit.has("QUOTE_FETCH_DEGRADED")
        degrade_event = next(kwargs for et, kwargs in audit.events if et == "QUOTE_FETCH_DEGRADED")
        assert degrade_event["symbol"] == "LAURUSLABS"
        assert "429" in degrade_event["exception_message"] or "Too many requests" in degrade_event["exception_message"]

    def test_the_degraded_leg_genuinely_recovers_on_the_next_cycle(self, tmp_path):
        raw_broker = _LTPFailsOnce(
            fails_for_symbol="LAURUSLABS",
            exc_to_raise=NetworkException("Too many requests", code=429),
        )
        plan, plan_store, engine, terminator, audit = _setup(tmp_path, raw_broker=raw_broker)

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # degrades
        assert plan.enter_status["LAURUSLABS"] == LegStatus.PENDING

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)  # retries - _LTPFailsOnce now succeeds
        assert plan.enter_status["LAURUSLABS"] == LegStatus.SUBMITTED  # genuinely got through this time
        assert terminator.halted is False


class TestQuoteFetchUnrecognizedExceptionStillHaltsAsBefore:
    def test_a_structurally_unexpected_exception_still_halts_the_plan(self, tmp_path):
        raw_broker = _LTPFailsOnce(fails_for_symbol="LAURUSLABS", exc_to_raise=ValueError("malformed response shape"))
        plan, plan_store, engine, terminator, audit = _setup(tmp_path, raw_broker=raw_broker)

        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)

        # Unchanged, deliberately - an exception this module doesn't
        # recognize as safe must still fail closed, not be softened by
        # this fix.
        assert plan.status == RebalancePlanStatus.HALTED
        assert not audit.has("QUOTE_FETCH_DEGRADED")
