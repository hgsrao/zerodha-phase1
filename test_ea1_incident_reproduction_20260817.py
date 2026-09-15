"""EA1-R1 remediation, step 2: incident-reproduction tests.

HISTORICAL RECORD, UPDATED 2026-08-19 AFTER STEPS 4/5 SHIPPED: these two
tests originally reproduced the two real 2026-08-17/2026-08-19 EA-1
failures against the unfixed build (both permanently HALTED the plan,
one with the reason unrecoverable, the other with zero audit trail at
all) - see EA1_INCIDENT_EVIDENCE_20260817_20260819/INCIDENT_TIMELINE_
20260817_20260819.md for the full forensic record. Both remediations
have since landed (v34_bridge_runner_core.py: _is_safe_to_degrade_quote_
exception for Terminal B's class, _try_auto_resolve_entry_submit_halt for
Terminal A's), so these exact scenarios NO LONGER reproduce a permanent
halt - which is the fix working, not a broken test.

Rather than delete this file or silently rewrite its assertions to match
new behavior (which would erase the historical proof these bugs were
real), both tests are marked xfail(strict=True): the ORIGINAL "bug"
assertions are kept exactly as they were, now expected to fail. If either
regresses back to the old, unfixed behavior, strict=True turns that into
an XPASS failure - this file becomes a permanent regression trap for
these two specific defects, not just a historical footnote.

The tests that prove the FIXED behavior directly (not just "the old bug
doesn't reproduce anymore") live in their own files: Terminal B's fix in
test_v34_bridge_runner_core_quote_degrade.py, Terminal A's in
test_v34_bridge_runner_core_auto_resolve.py.

No real broker/network/credentials anywhere in this file - same fully
mocked FakeBroker/real-engine harness test_v34_bridge_runner_core.py
already established, extended minimally and locally (not by modifying
the shared FakeBroker fixture) for the one new failure mode (a raw
ltp() exception) neither existing test file had ever exercised before
this - grepped before writing this originally: zero references to
429/NetworkException anywhere in the existing test suite, confirming
this was genuinely new coverage, not a duplicate.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from kiteconnect.exceptions import NetworkException

from institutional_engine_v34_p02_multipos_candidate import PositionStatus
from test_v34_bridge_runner_core import ALL_LIVE_QUOTES, TOP7_LIVE_QUOTES
from test_v34_p02_lifecycle_integration import make_context, make_stack
from test_v34_p02_multipos_engine import FakeBroker
from v34_bridge_rebalance_diff import compute_rebalance_diff
from v34_bridge_rebalance_plan import LegStatus, RebalancePlanStatus, RebalancePlanStore, create_rebalance_plan
from v34_bridge_rebalance_transitions import approve_plan
from v34_bridge_runner_core import drive_plan_one_cycle
from v34_bridge_target_portfolio import build_target_portfolio
from v34_p02_state import EngineStatus, EntryPolicyDeclinedError

SIGNAL_DATE = date(2026, 8, 14)


def _approve(plan, *, current_trading_day=SIGNAL_DATE):
    approve_plan(plan, current_trading_day=current_trading_day, now=datetime.now(timezone.utc))
    return plan


# Real ranked-by-momentum top-4 for SIGNAL_DATE, reusing the SAME fixture
# test_v34_bridge_runner_core.py already proved works: LAURUSLABS(PHARMA),
# SHRIRAMFIN(FINANCE), HINDALCO(METALS), ADANIENT(INDUSTRIAL) - "the real
# top-4 no longer shares a sector" per that file's own docstring, so no
# unrelated sector-clash decline muddies what this test is reproducing.
# build_target_portfolio() independently ranks by its own real momentum
# formation data - it is NOT free to hand-pick arbitrary symbols, which is
# exactly why this reuses the already-proven fixture rather than inventing
# a new quote dict (an earlier draft of this test tried TITAN/rank-5 and
# got WAITING_FOR_ALL_QUOTES, since the real top-4 doesn't include it).
FOUR_SECTOR_SYMBOLS = ("LAURUSLABS", "SHRIRAMFIN", "HINDALCO", "ADANIENT")


class FakeBrokerLTPFailsForOneSymbol(FakeBroker):
    """Extends FakeBroker locally, in this file only - not a shared-
    fixture change - to reproduce Terminal B's exact failure mode: a bare
    ltp() call (v34_bridge_runner_core._fetch_live_price(), NOT the
    engine's own audited place_order() path) raising a real Kite
    exception. FakeBroker's own .ltp() has no failure hook today because
    nothing before this incident ever needed one."""

    def __init__(self, *, fails_for_symbol: str, exc_to_raise: Exception):
        super().__init__()
        self._fails_for_symbol = fails_for_symbol
        self._exc_to_raise = exc_to_raise

    def ltp(self, symbols):
        if symbols == [self._fails_for_symbol] or symbols == [f"NSE:{self._fails_for_symbol}"]:
            raise self._exc_to_raise
        return self.quotes


def _build_plan(quotes, *, enters_n, tmp_path):
    target = build_target_portfolio(quotes=quotes, signal_date=SIGNAL_DATE, positions=enters_n)
    diff = compute_rebalance_diff(current_portfolio={}, target=target)
    plan = create_rebalance_plan(diff, target=target, current_trading_day=SIGNAL_DATE)
    plan_store = RebalancePlanStore(tmp_path)
    plan_store.save(plan)
    _approve(plan)
    plan_store.save(plan)
    return plan, plan_store


def _drive_to_entering(plan, engine, plan_store):
    while plan.status not in (RebalancePlanStatus.ENTERING, RebalancePlanStatus.HALTED):
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)


# ---------------------------------------------------------------------------
# Terminal A reproduction: real Kite 429 during an actual order-submission
# attempt (SUNPHARMA 08-17 / SBIN 08-19). Three siblings decline cleanly
# first, exactly as the real audit.jsonl shows, before the fourth hits the
# hard halt.
# ---------------------------------------------------------------------------

class TestTerminalA429DuringSubmissionReproduction:
    @pytest.mark.xfail(
        strict=True,
        reason="FIXED 2026-08-19 by _try_auto_resolve_entry_submit_halt() "
               "(v34_bridge_runner_core.py) - a 429 during submission now "
               "auto-clears instead of permanently halting the plan. Kept "
               "as a regression trap: if this ever unexpectedly PASSES "
               "again, that means the old bug came back.",
    )
    def test_429_during_place_order_hard_halts_and_the_plan_loses_its_reason(self, tmp_path):
        plan, plan_store = _build_plan(TOP7_LIVE_QUOTES, enters_n=4, tmp_path=tmp_path)
        assert set(plan.diff.enters) == set(FOUR_SECTOR_SYMBOLS)

        raw_broker = FakeBroker()
        raw_broker.quotes = dict(TOP7_LIVE_QUOTES)

        def place_order_fn(**kwargs):
            symbol = kwargs["tradingsymbol"]
            if symbol == "ADANIENT":
                # The real failure: a genuine Kite rate-limit rejection,
                # verbatim the same exception class/code the real
                # SUNPHARMA/SBIN/SHRIRAMFIN incidents hit.
                raise NetworkException("Too many requests", code=429)
            # The other three: exactly the clean EA1_SHADOW_MODE decline
            # every non-halted leg got in the real incident.
            raise EntryPolicyDeclinedError(
                "EA1_SHADOW_MODE: execution authority disabled for this session"
            )

        raw_broker.place_order_fn = place_order_fn

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
        context = make_context(
            sector_lookup=REAL_SECTORS,
            checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital),
        )
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=__import__("test_v34_p02_multipos_engine").flat_running_state(),
            raw_broker=raw_broker, cfg=cfg, context=context,
        )

        _drive_to_entering(plan, engine, plan_store)
        assert plan.status == RebalancePlanStatus.ENTERING

        # Submit each PENDING leg one at a time (real bridge sequencing),
        # then one more cycle to observe outcomes via engine.step().
        for _ in range(8):
            if plan.status == RebalancePlanStatus.HALTED:
                break
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)

        # --- Reproduces the real incident ---
        assert plan.status == RebalancePlanStatus.HALTED
        assert terminator.halted is True
        assert engine.state.status == EngineStatus.RECONCILIATION_HALT
        assert "429" in (terminator.reason or "") or "Too many requests" in (terminator.reason or "")

        # --- Reproduces the real DEFECT: the plan itself never learns why ---
        # RebalancePlan has no field for it; halt_plan()'s `reason` argument
        # is discarded entirely. This assertion is the reproduction of the
        # observability gap itself, not incidental.
        plan_dict = plan.to_dict()
        assert "halt_reason" not in plan_dict
        assert not any("429" in str(v) for v in plan_dict.values())

        # --- Reproduces the real DEFECT: permanently, silently dead ---
        # Matches TestHaltOnExitFailure's own precedent assertion, applied
        # to the ENTERING-side 429 case, which no existing test covered.
        result = drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert result == "HALTED"
        calls_before = len(raw_broker.place_order_calls)
        drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)
        assert len(raw_broker.place_order_calls) == calls_before  # zero further activity, forever


# ---------------------------------------------------------------------------
# Terminal B reproduction: a bare, un-audited ltp() failure during
# pre-submission pricing (LAURUSLABS, inferred cause of the real 08-17
# Terminal B halt - the exact NetworkException message was never
# recoverable, which is itself the defect this test also proves).
# ---------------------------------------------------------------------------

class TestTerminalBBareLTPFailureReproduction:
    @pytest.mark.xfail(
        strict=True,
        reason="FIXED 2026-08-19 by _is_safe_to_degrade_quote_exception() "
               "(v34_bridge_runner_core.py) - a NetworkException during "
               "pre-submission pricing now degrades (skip, audit, retry "
               "next cycle) instead of permanently halting the plan with "
               "zero audit trail. Kept as a regression trap.",
    )
    def test_ltp_exception_during_pre_submission_pricing_halts_with_zero_audit_trail(self, tmp_path):
        # Reuses the same proven fixture as TestHappyPathNoSectorClash
        # (test_v34_bridge_runner_core.py): ALL_LIVE_QUOTES + positions=2
        # -> real top-2 by momentum = LAURUSLABS, SHRIRAMFIN, no clash.
        plan, plan_store = _build_plan(ALL_LIVE_QUOTES, enters_n=2, tmp_path=tmp_path)
        assert set(plan.diff.enters) == {"LAURUSLABS", "SHRIRAMFIN"}

        raw_broker = FakeBrokerLTPFailsForOneSymbol(
            fails_for_symbol="LAURUSLABS",
            exc_to_raise=NetworkException("Network error: could not communicate with the OMS"),
        )
        raw_broker.quotes = dict(ALL_LIVE_QUOTES)
        raw_broker.place_order_fn = lambda **kwargs: (_ for _ in ()).throw(
            EntryPolicyDeclinedError("EA1_SHADOW_MODE: execution authority disabled for this session")
        )

        from portfolio_brain_v9 import SECTORS as REAL_SECTORS
        from v34_p02_accounting import initial_checkpoint
        from v34_p02_state import Config
        cfg = Config(alert_webhook_url="x", trial_capital=Decimal("300000"))
        context = make_context(
            sector_lookup=REAL_SECTORS,
            checkpoint=initial_checkpoint(trading_day="2026-08-14", trial_capital=cfg.trial_capital),
        )
        engine, raw_broker, adapter, authorizer_store, store, terminator, audit, ctx_holder = make_stack(
            state=__import__("test_v34_p02_multipos_engine").flat_running_state(),
            raw_broker=raw_broker, cfg=cfg, context=context,
        )

        _drive_to_entering(plan, engine, plan_store)
        assert plan.status == RebalancePlanStatus.ENTERING

        for _ in range(6):
            if plan.status == RebalancePlanStatus.HALTED:
                break
            drive_plan_one_cycle(plan=plan, engine=engine, store=plan_store)

        # --- Reproduces the real incident: plan halted ---
        assert plan.status == RebalancePlanStatus.HALTED

        # --- Reproduces the real DEFECT: the ENGINE never even halted ---
        # (unlike Terminal A) - this is a purely bridge-side failure,
        # invisible to the engine's own terminator/status.
        assert terminator.halted is False
        assert engine.state.status != EngineStatus.RECONCILIATION_HALT

        # --- Reproduces the single most important defect: ZERO audit trail ---
        # This is exactly why the real Terminal B incident's root cause
        # could only ever be inferred, never proven, from the real audit.jsonl.
        assert not audit.has("LTP_FETCH_FAILED")
        assert not any("LAURUSLABS" in str(kwargs) and "ltp" in str(kwargs).lower() for _, kwargs in audit.events)
        assert not any("NetworkException" in str(kwargs) or "communicate" in str(kwargs) for _, kwargs in audit.events)

        # --- No plan-level reason either, same gap as Terminal A ---
        plan_dict = plan.to_dict()
        assert "halt_reason" not in plan_dict
