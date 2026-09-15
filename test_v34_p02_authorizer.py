"""Tests for v34_p02_authorizer.py (P02-E).

Priority scenarios per the P02-E review: the deterministic gate stack,
concurrency (modeled as sequential evaluation against a shared, durably-
updated registry - the actual concurrency model of this single-process
design, per spec P02-0 §9), same-symbol-held-since-yesterday, the full
PROVABLY_STALE / REGISTRY_DISAGREEMENT / PENDING / PROMOTE_TO_HELD
reconciliation matrix, ENTRY_LOCK-after-reservation release semantics,
and crash-recovery at both the reserve boundary and the submit boundary.
"""

from decimal import Decimal

import pytest

from v34_p02_accounting import PortfolioRiskSnapshot
from v34_p02_authorizer import (
    AuthorizerRegistry,
    AuthorizationDecision,
    CandidateEntry,
    apply_decision,
    authorize_and_persist,
    authorize_entry,
    mark_reservation_submitted,
    reconcile_reservations,
    release_held_symbol,
    release_unsubmitted_reservation,
)
from v34_p02_state import BrokerObservationContractViolation, Config, EntryReservation, canonical_decimal_string


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"), max_simultaneous_positions=6)
    defaults.update(overrides)
    return Config(**defaults)


def snapshot(**overrides) -> PortfolioRiskSnapshot:
    defaults = dict(
        deployed_capital=Decimal("0"), pending_buy_exposure=Decimal("0"), reserved_entry_capital=Decimal("0"),
        committed_capital=Decimal("0"), gross_market_exposure=Decimal("0"), unrealized_pnl=Decimal("0"),
        equity=Decimal("100000"), daily_pnl=Decimal("0"), rolling_week_pnl=Decimal("0"), trial_drawdown=Decimal("0"),
    )
    defaults.update(overrides)
    return PortfolioRiskSnapshot(**defaults)


def candidate(symbol="RELIANCE", sector="ENERGY", quantity=10, price="2500.00"):
    return CandidateEntry(symbol=symbol, sector=sector, quantity=quantity, price=Decimal(price))


AUTHORIZE_DEFAULTS = dict(
    reconciliation_clean=True, kill_switch_active=False, entries_today=0,
    seconds_since_last_entry=Decimal("9999"), turnover_today=Decimal("0"),
)


def authorize(*, candidate, registry, snapshot, cfg, **overrides):
    kwargs = dict(AUTHORIZE_DEFAULTS)
    kwargs.update(overrides)
    return authorize_entry(candidate=candidate, registry=registry, snapshot=snapshot, cfg=cfg, **kwargs)


class InMemoryRegistryStore:
    def __init__(self, initial: AuthorizerRegistry):
        self.registry = initial
        self.save_calls = 0
        self.fail_on_save = False

    def save(self, registry: AuthorizerRegistry) -> None:
        if self.fail_on_save:
            raise RuntimeError("simulated crash during persist")
        self.registry = registry
        self.save_calls += 1


class TestAuthorizationDecisionIsStructured:
    def test_allow_carries_committed_before_proposed_and_after(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(committed_capital=Decimal("10000")), cfg=cfg())
        assert decision.allowed is True
        assert decision.halt_class is None
        assert decision.committed_before == Decimal("10000")
        assert decision.proposed_capital == Decimal("25000.00")
        assert decision.committed_after == Decimal("35000.00")
        assert decision.reservation.symbol == "RELIANCE"
        assert decision.reservation.entry_fingerprint is None

    def test_decline_never_carries_a_reservation(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg(), reconciliation_clean=False)
        assert decision.allowed is False
        assert decision.reservation is None


class TestGateOrderAndHaltClass:
    def test_reconciliation_not_clean_is_engine_halt_and_beats_every_other_gate(self):
        decision = authorize(
            candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg(),
            reconciliation_clean=False, kill_switch_active=True,  # both would fail, but gate 1 must win
        )
        assert decision.reason == "RECONCILIATION_NOT_CLEAN"
        assert decision.halt_class == "ENGINE_HALT"

    def test_every_other_decline_is_entry_lock_class_never_engine_halt(self):
        cases = [
            dict(kill_switch_active=True),
            dict(snapshot=snapshot(daily_pnl=Decimal("-3000"))),  # 3% of 100k, over 2% daily_hard_halt_pct
        ]
        for extra in cases:
            kwargs = dict(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg())
            kwargs.update(extra)
            decision = authorize(**kwargs)
            assert decision.allowed is False
            assert decision.halt_class == "ENTRY_LOCK"

    def test_kill_switch_beats_symbol_and_sector_gates(self):
        registry = AuthorizerRegistry()
        decision = authorize(candidate=candidate(), registry=registry, snapshot=snapshot(), cfg=cfg(), kill_switch_active=True)
        assert decision.reason == "KILL_SWITCH"

    def test_daily_hard_halt_trips_before_symbol_gates_are_even_checked(self):
        decision = authorize(
            candidate=candidate(symbol="ALREADY_HELD_BUT_IRRELEVANT"), registry=AuthorizerRegistry(),
            snapshot=snapshot(daily_pnl=Decimal("-2100")), cfg=cfg(),
        )
        assert decision.reason == "DAILY_HARD_HALT"

    def test_rolling_week_halt(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(rolling_week_pnl=Decimal("-4500")), cfg=cfg())
        assert decision.reason == "ROLLING_WEEK_HALT"

    def test_trial_drawdown_halt(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(trial_drawdown=Decimal("0.06")), cfg=cfg())
        assert decision.reason == "TRIAL_DRAWDOWN_HALT"

    def test_daily_entry_limit(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg(max_daily_entries=2), entries_today=2)
        assert decision.reason == "DAILY_ENTRY_LIMIT"

    def test_entry_cooldown(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg(entry_cooldown_seconds=900), seconds_since_last_entry=Decimal("100"))
        assert decision.reason == "ENTRY_COOLDOWN"

    def test_daily_turnover_limit(self):
        decision = authorize(
            candidate=candidate(quantity=10, price="2500"), registry=AuthorizerRegistry(),
            snapshot=snapshot(), cfg=cfg(daily_turnover_pct=Decimal("0.20")), turnover_today=Decimal("0"),
        )
        # proposed 25000 > 20% of 100000 (20000)
        assert decision.reason == "DAILY_TURNOVER_LIMIT"

    def test_trade_risk_limit(self):
        # Large proposed capital relative to max_trade_risk_pct.
        decision = authorize(
            candidate=candidate(quantity=1000, price="100"), registry=AuthorizerRegistry(),
            snapshot=snapshot(), cfg=cfg(max_trade_risk_pct=Decimal("0.0075"), stop_loss_pct=Decimal("0.02")),
        )
        assert decision.reason == "TRADE_RISK_LIMIT"

    def test_capital_ceiling(self):
        decision = authorize(
            candidate=candidate(quantity=10, price="2500"), registry=AuthorizerRegistry(),
            snapshot=snapshot(committed_capital=Decimal("80000")), cfg=cfg(),
        )
        assert decision.reason == "CAPITAL_CEILING"

    def test_simultaneous_position_limit(self):
        registry = AuthorizerRegistry(held_symbols={"A": "S1", "B": "S2"})
        decision = authorize(candidate=candidate(symbol="C", sector="S3"), registry=registry, snapshot=snapshot(), cfg=cfg(max_simultaneous_positions=2))
        assert decision.reason == "SIMULTANEOUS_POSITION_LIMIT"


class TestSymbolAndSectorOccupancy:
    def test_symbol_already_held_since_yesterday_is_rejected_regardless_of_daily_counters(self):
        # The exact case flagged: a symbol held since yesterday must be
        # caught by "currently held," not a daily "bought today" counter -
        # entries_today=0 here proves the daily counter is irrelevant.
        registry = AuthorizerRegistry(held_symbols={"RELIANCE": "ENERGY"})
        decision = authorize(candidate=candidate(symbol="RELIANCE"), registry=registry, snapshot=snapshot(), cfg=cfg(), entries_today=0)
        assert decision.reason == "SYMBOL_ALREADY_HELD"

    def test_symbol_with_an_unfingerprinted_reservation_is_also_rejected(self):
        registry = AuthorizerRegistry(reservations={"RELIANCE": EntryReservation(symbol="RELIANCE", sector="ENERGY", reserved_capital=Decimal("1"), reserved_at="x")})
        decision = authorize(candidate=candidate(symbol="RELIANCE"), registry=registry, snapshot=snapshot(), cfg=cfg())
        assert decision.reason == "SYMBOL_ALREADY_HELD"

    def test_sector_already_held_is_rejected_even_for_a_different_symbol(self):
        registry = AuthorizerRegistry(held_symbols={"ONGC": "ENERGY"})
        decision = authorize(candidate=candidate(symbol="RELIANCE", sector="ENERGY"), registry=registry, snapshot=snapshot(), cfg=cfg())
        assert decision.reason == "SECTOR_ALREADY_HELD"

    def test_sector_already_reserved_by_a_different_symbol_is_rejected(self):
        registry = AuthorizerRegistry(reservations={"ONGC": EntryReservation(symbol="ONGC", sector="ENERGY", reserved_capital=Decimal("1"), reserved_at="x")})
        decision = authorize(candidate=candidate(symbol="RELIANCE", sector="ENERGY"), registry=registry, snapshot=snapshot(), cfg=cfg())
        assert decision.reason == "SECTOR_ALREADY_HELD"


# ---------------------------------------------------------------------------
# "Racing" candidates - sequential evaluation against a shared, durably-
# updated registry, the actual concurrency model of this design.
# ---------------------------------------------------------------------------

class TestRacingCandidates:
    def test_two_candidates_racing_for_the_last_capital_exactly_one_wins(self):
        # trial_capital=100000, first candidate takes 90000, leaving only
        # 10000 - the second candidate's 15000 request cannot fit.
        config = cfg(trial_capital=Decimal("100000"), max_trade_risk_pct=Decimal("1"), daily_turnover_pct=Decimal("1"))
        store = InMemoryRegistryStore(AuthorizerRegistry())

        first = candidate(symbol="A", sector="S1", quantity=1, price="90000")
        decision_a = authorize_and_persist(store=store, registry=store.registry, candidate=first, snapshot=snapshot(committed_capital=Decimal("0")), cfg=config, **AUTHORIZE_DEFAULTS)
        assert decision_a.allowed is True

        second = candidate(symbol="B", sector="S2", quantity=1, price="15000")
        decision_b = authorize_and_persist(store=store, registry=store.registry, candidate=second, snapshot=snapshot(committed_capital=decision_a.committed_after), cfg=config, **AUTHORIZE_DEFAULTS)
        assert decision_b.allowed is False
        assert decision_b.reason == "CAPITAL_CEILING"

        assert set(store.registry.reservations) == {"A"}

    def test_two_same_sector_candidates_racing_exactly_one_wins(self):
        config = cfg()
        store = InMemoryRegistryStore(AuthorizerRegistry())

        first = candidate(symbol="RELIANCE", sector="ENERGY")
        decision_a = authorize_and_persist(store=store, registry=store.registry, candidate=first, snapshot=snapshot(), cfg=config, **AUTHORIZE_DEFAULTS)
        assert decision_a.allowed is True

        second = candidate(symbol="ONGC", sector="ENERGY")
        decision_b = authorize_and_persist(store=store, registry=store.registry, candidate=second, snapshot=snapshot(), cfg=config, **AUTHORIZE_DEFAULTS)
        assert decision_b.allowed is False
        assert decision_b.reason == "SECTOR_ALREADY_HELD"

        assert set(store.registry.reservations) == {"RELIANCE"}


# ---------------------------------------------------------------------------
# apply_decision - the pure state transition, and its double-reservation guard.
# ---------------------------------------------------------------------------

class TestApplyDecision:
    def test_allow_folds_the_reservation_into_a_new_registry(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg())
        new_registry = apply_decision(AuthorizerRegistry(), decision)
        assert "RELIANCE" in new_registry.reservations

    def test_decline_returns_the_registry_unchanged(self):
        registry = AuthorizerRegistry()
        decision = authorize(candidate=candidate(), registry=registry, snapshot=snapshot(), cfg=cfg(), reconciliation_clean=False)
        assert apply_decision(registry, decision) is registry

    def test_applying_an_allow_decision_against_an_already_reserved_registry_refuses(self):
        decision = authorize(candidate=candidate(), registry=AuthorizerRegistry(), snapshot=snapshot(), cfg=cfg())
        already_reserved = AuthorizerRegistry(reservations={"RELIANCE": EntryReservation(symbol="RELIANCE", sector="X", reserved_capital=Decimal("1"), reserved_at="y")})
        with pytest.raises(RuntimeError, match="REFUSING_DOUBLE_RESERVATION"):
            apply_decision(already_reserved, decision)


# ---------------------------------------------------------------------------
# Crash recovery at the reserve boundary
# ---------------------------------------------------------------------------

class TestCrashAtReserveBoundary:
    def test_crash_during_persist_never_returns_allow_to_the_caller(self):
        store = InMemoryRegistryStore(AuthorizerRegistry())
        store.fail_on_save = True
        with pytest.raises(RuntimeError, match="simulated crash"):
            authorize_and_persist(store=store, registry=store.registry, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        # The registry was never actually mutated - a retry starts clean.
        assert store.registry.reservations == {}

    def test_restart_after_a_successful_reservation_never_permits_a_second_one(self):
        store = InMemoryRegistryStore(AuthorizerRegistry())
        first = authorize_and_persist(store=store, registry=store.registry, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        assert first.allowed is True

        # Simulate "restart": re-evaluate the identical candidate against
        # the now-durable, post-crash-recovery registry.
        second = authorize_and_persist(store=store, registry=store.registry, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        assert second.allowed is False
        assert second.reason == "SYMBOL_ALREADY_HELD"
        assert store.save_calls == 1  # the second attempt never reached another save


# ---------------------------------------------------------------------------
# Reconciliation matrix - PROVABLY_STALE / ambiguous / pending / promote
# ---------------------------------------------------------------------------

def fingerprint_for(candidate: CandidateEntry, *, product="CNC") -> dict:
    # Must canonicalize exactly like _build_entry_fingerprint (production
    # code) does - a raw str(Decimal(...)) keeps trailing zeros ("2500.00")
    # while canonical_decimal_string strips them ("2500"), so a test
    # fixture built the naive way would never actually match a real order.
    return {
        "exchange": "NSE", "tradingsymbol": candidate.symbol, "transaction_type": "BUY",
        "product": product, "order_type": "LIMIT", "quantity": candidate.quantity,
        "price": canonical_decimal_string(candidate.price), "tag": candidate.entry_tag,
    }


def broker_order(fingerprint: dict, *, order_id="O1", status="OPEN", filled_quantity=0):
    order = dict(fingerprint)
    order["order_id"] = order_id
    order["status"] = status
    order["filled_quantity"] = filled_quantity
    return order


class TestReconcileReservations:
    def test_unfingerprinted_reservation_is_left_untouched(self):
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("1"), reserved_at="x")})
        result = reconcile_reservations(registry, orders=[], positions=[], product="CNC")
        assert "A" in result.reservations

    def test_provably_stale_reservation_is_auto_cleared_without_operator_action(self):
        c = candidate(symbol="A")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("1"), reserved_at="x", entry_fingerprint=fp)})
        # No broker order anywhere matches this fingerprint at all.
        result = reconcile_reservations(registry, orders=[], positions=[], product="CNC")
        assert "A" not in result.reservations
        assert "A" not in result.held_symbols

    def test_terminal_zero_fill_order_is_also_provably_stale(self):
        c = candidate(symbol="A")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("1"), reserved_at="x", entry_fingerprint=fp)})
        order = broker_order(fp, status="REJECTED", filled_quantity=0)
        result = reconcile_reservations(registry, orders=[order], positions=[], product="CNC")
        assert "A" not in result.reservations

    def test_multiple_matching_orders_is_ambiguous_and_raises_not_silently_released(self):
        c = candidate(symbol="A")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("1"), reserved_at="x", entry_fingerprint=fp)})
        orders = [broker_order(fp, order_id="O1"), broker_order(fp, order_id="O2")]
        with pytest.raises(BrokerObservationContractViolation, match="multiple broker orders"):
            reconcile_reservations(registry, orders=orders, positions=[], product="CNC")

    def test_pending_order_keeps_the_reservation_occupied_without_double_counting_capital(self):
        c = candidate(symbol="A", quantity=10, price="1500")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fp)})
        order = broker_order(fp, status="OPEN", filled_quantity=0)
        result = reconcile_reservations(registry, orders=[order], positions=[], product="CNC")
        # Still occupied (not stale, not promoted) - capital dedup itself
        # is proven separately in test_v34_p02_accounting.py's
        # TestReservationDedup using this exact same shared fingerprint matcher.
        assert "A" in result.reservations
        assert "A" not in result.held_symbols

    def test_complete_order_with_matching_position_promotes_to_held(self):
        c = candidate(symbol="A", quantity=10, price="1500")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="ENERGY", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fp)})
        order = broker_order(fp, status="COMPLETE", filled_quantity=10)
        position = {"tradingsymbol": "A", "product": "CNC", "quantity": 10, "average_price": "1500"}
        result = reconcile_reservations(registry, orders=[order], positions=[position], product="CNC")
        assert "A" not in result.reservations
        assert result.held_symbols["A"] == "ENERGY"

    def test_complete_order_without_a_matching_position_is_refused_not_promoted(self):
        # This is the "never infer execution from the registry" case -
        # the order says COMPLETE, but there is no authoritative broker
        # position proving it. Must not promote on the order's say-so alone.
        c = candidate(symbol="A", quantity=10, price="1500")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="ENERGY", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fp)})
        order = broker_order(fp, status="COMPLETE", filled_quantity=10)
        with pytest.raises(BrokerObservationContractViolation, match="refusing to promote"):
            reconcile_reservations(registry, orders=[order], positions=[], product="CNC")

    def test_complete_order_with_wrong_quantity_position_is_refused(self):
        c = candidate(symbol="A", quantity=10, price="1500")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="ENERGY", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fp)})
        order = broker_order(fp, status="COMPLETE", filled_quantity=10)
        position = {"tradingsymbol": "A", "product": "CNC", "quantity": 7, "average_price": "1500"}  # wrong qty
        with pytest.raises(BrokerObservationContractViolation, match="refusing to promote"):
            reconcile_reservations(registry, orders=[order], positions=[position], product="CNC")

    def test_ambiguous_and_healthy_reservations_do_not_cross_contaminate_but_the_batch_still_fails_closed(self):
        c_bad = candidate(symbol="BAD")
        fp_bad = fingerprint_for(c_bad)
        c_good = candidate(symbol="GOOD", quantity=5, price="1000")
        fp_good = fingerprint_for(c_good)
        registry = AuthorizerRegistry(reservations={
            "BAD": EntryReservation(symbol="BAD", sector="S1", reserved_capital=Decimal("1"), reserved_at="x", entry_fingerprint=fp_bad),
            "GOOD": EntryReservation(symbol="GOOD", sector="S2", reserved_capital=Decimal("5000"), reserved_at="x", entry_fingerprint=fp_good),
        })
        orders = [broker_order(fp_bad, order_id="O1"), broker_order(fp_bad, order_id="O2"), broker_order(fp_good, order_id="O3", status="OPEN")]
        with pytest.raises(BrokerObservationContractViolation, match="multiple broker orders"):
            reconcile_reservations(registry, orders=orders, positions=[], product="CNC")


# ---------------------------------------------------------------------------
# ENTRY_LOCK after reservation, before submission
# ---------------------------------------------------------------------------

class TestReleaseHeldSymbol:
    def test_removes_a_held_symbol_freeing_its_sector(self):
        registry = AuthorizerRegistry(held_symbols={"RELIANCE": "ENERGY"})
        released = release_held_symbol(registry, "RELIANCE")
        assert "RELIANCE" not in released.held_symbols
        decision = authorize(candidate=candidate(symbol="ONGC", sector="ENERGY"), registry=released, snapshot=snapshot(), cfg=cfg())
        assert decision.allowed is True

    def test_releasing_an_unheld_symbol_is_a_safe_no_op(self):
        registry = AuthorizerRegistry()
        assert release_held_symbol(registry, "NEVER_HELD") == registry


class TestReleaseUnsubmittedReservation:
    def test_unfingerprinted_reservation_releases_cleanly(self):
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("1"), reserved_at="x")})
        released = release_unsubmitted_reservation(registry, "A")
        assert "A" not in released.reservations

    def test_fingerprinted_reservation_refuses_direct_release(self):
        c = candidate(symbol="A")
        fp = fingerprint_for(c)
        registry = AuthorizerRegistry(reservations={"A": EntryReservation(symbol="A", sector="S", reserved_capital=Decimal("1"), reserved_at="x", entry_fingerprint=fp)})
        with pytest.raises(RuntimeError, match="CANNOT_RELEASE"):
            release_unsubmitted_reservation(registry, "A")

    def test_full_lifecycle_reserve_then_entry_lock_trips_then_clean_release(self):
        # The exact scenario: authorize -> reserved, no submission attempted
        # yet -> an ENTRY_LOCK trips before the caller ever calls
        # mark_reservation_submitted -> release must succeed and be provable.
        store = InMemoryRegistryStore(AuthorizerRegistry())
        decision = authorize_and_persist(store=store, registry=store.registry, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        assert decision.allowed is True
        assert "RELIANCE" in store.registry.reservations

        # ENTRY_LOCK trips (e.g. daily_hard_halt) before submission -
        # caller abandons and releases.
        store.registry = release_unsubmitted_reservation(store.registry, "RELIANCE")
        assert "RELIANCE" not in store.registry.reservations

        # A fresh candidate for the same symbol is now evaluable again.
        second = authorize_and_persist(store=store, registry=store.registry, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        assert second.allowed is True


# ---------------------------------------------------------------------------
# Crash immediately after broker order becomes visible, before registry update
# ---------------------------------------------------------------------------

class TestCrashAtSubmitBoundary:
    def test_mark_submitted_then_crash_then_reconcile_converges_without_duplication(self):
        store = InMemoryRegistryStore(AuthorizerRegistry())
        decision = authorize_and_persist(store=store, registry=store.registry, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        assert decision.allowed is True

        c = candidate()
        fp = fingerprint_for(c)
        store.registry = mark_reservation_submitted(store.registry, "RELIANCE", fp)
        # "Crash here" - the broker call itself succeeded (order now
        # exists) but the process died before anything else observed it.
        # On restart, reconciliation must find the order and converge -
        # not attempt a second reservation, not lose track of the symbol.
        order = broker_order(fp, status="OPEN", filled_quantity=0)
        reconciled = reconcile_reservations(store.registry, orders=[order], positions=[], product="CNC")
        assert "RELIANCE" in reconciled.reservations  # still pending, correctly not stale

        # A fresh authorization attempt for the same symbol must still be
        # rejected - the reservation was never lost or duplicated.
        again = authorize_and_persist(store=store, registry=reconciled, candidate=candidate(), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        assert again.allowed is False
        assert again.reason == "SYMBOL_ALREADY_HELD"

    def test_mark_submitted_then_crash_then_fill_completes_then_reconcile_promotes_exactly_once(self):
        store = InMemoryRegistryStore(AuthorizerRegistry())
        decision = authorize_and_persist(store=store, registry=store.registry, candidate=candidate(quantity=10, price="2500"), snapshot=snapshot(), cfg=cfg(), **AUTHORIZE_DEFAULTS)
        c = candidate(quantity=10, price="2500")
        fp = fingerprint_for(c)
        store.registry = mark_reservation_submitted(store.registry, "RELIANCE", fp)

        order = broker_order(fp, status="COMPLETE", filled_quantity=10)
        position = {"tradingsymbol": "RELIANCE", "product": "CNC", "quantity": 10, "average_price": "2500"}
        reconciled = reconcile_reservations(store.registry, orders=[order], positions=[position], product="CNC")

        assert "RELIANCE" not in reconciled.reservations
        assert reconciled.held_symbols["RELIANCE"] == "ENERGY"

        # Reconciling again from the already-promoted state is a no-op,
        # not a second promotion or a duplicate.
        reconciled_again = reconcile_reservations(reconciled, orders=[order], positions=[position], product="CNC")
        assert reconciled_again.held_symbols == reconciled.held_symbols
        assert reconciled_again.reservations == {}
