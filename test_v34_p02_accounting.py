"""Tests for v34_p02_accounting.py (P02-D).

Priority scenarios per the P02-D review: cost-basis aggregation, pending
BUY exposure, the reservation-to-order dedup trap, appreciation vs
committed capital, exit-day non-double-counting, daily-cadence checkpoint
rollover, HWM/drawdown immunity to intraday polling frequency, unexplained
equity-change detection, and mark-independence of cost-basis figures. Plus
strict broker-payload validation (missing/non-numeric/negative/impossible
values, duplicated identity, inconsistent product, unresolved status).
"""

from decimal import Decimal

import pytest

from v34_p02_accounting import (
    PortfolioRiskSnapshot,
    build_portfolio_snapshot,
    compute_committed_capital,
    compute_daily_pnl,
    compute_deployed_capital,
    compute_equity,
    compute_gross_market_exposure,
    compute_pending_buy_exposure,
    compute_reserved_entry_capital,
    compute_rolling_week_pnl,
    compute_trial_drawdown,
    compute_unrealized_pnl,
    initial_checkpoint,
    roll_daily_checkpoint,
    verify_no_unexplained_equity_change,
)
from v34_p02_state import BrokerObservationContractViolation, Config, DailyAccountingCheckpoint, EntryReservation


def cnc_position(symbol, qty, avg_price, product="CNC"):
    return {"tradingsymbol": symbol, "product": product, "quantity": qty, "average_price": str(avg_price)}


def buy_order(order_id, symbol, qty, filled, price, status="OPEN", product="CNC"):
    return {
        "order_id": order_id, "tradingsymbol": symbol, "transaction_type": "BUY", "product": product,
        "status": status, "quantity": qty, "filled_quantity": filled, "price": str(price),
    }


def cfg(**overrides):
    defaults = dict(alert_webhook_url="x", trial_capital=Decimal("100000"))
    defaults.update(overrides)
    return Config(**defaults)


# ---------------------------------------------------------------------------
# 1. Cost-basis aggregation across multiple positions
# ---------------------------------------------------------------------------

class TestDeployedCapital:
    def test_three_open_cnc_positions_aggregate_correctly(self):
        positions = [
            cnc_position("RELIANCE", 10, "2500.00"),
            cnc_position("INFY", 20, "1500.50"),
            cnc_position("TCS", 5, "3800.00"),
        ]
        total = compute_deployed_capital(positions, product="CNC")
        assert total == Decimal("10") * Decimal("2500.00") + Decimal("20") * Decimal("1500.50") + Decimal("5") * Decimal("3800.00")

    def test_other_product_positions_are_excluded(self):
        positions = [cnc_position("RELIANCE", 10, "2500.00", product="MIS")]
        assert compute_deployed_capital(positions, product="CNC") == Decimal("0")

    def test_zero_quantity_position_contributes_nothing(self):
        positions = [cnc_position("RELIANCE", 0, "0")]
        assert compute_deployed_capital(positions, product="CNC") == Decimal("0")

    def test_does_not_require_quotes_and_is_valid_with_no_marks_available(self):
        # Mark-independence (priority #10): this function doesn't even
        # accept a quotes parameter - it is structurally incapable of
        # failing on missing marks.
        positions = [cnc_position("RELIANCE", 10, "2500.00")]
        assert compute_deployed_capital(positions, product="CNC") == Decimal("25000.00")


# ---------------------------------------------------------------------------
# 2. Pending BUY exposure counted exactly once
# ---------------------------------------------------------------------------

class TestPendingBuyExposure:
    def test_one_pending_buy_order_counted_once(self):
        orders = [buy_order("O1", "INFY", 10, 0, "1500.00")]
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("15000.00")

    def test_multiple_pending_buy_orders_sum_correctly(self):
        orders = [buy_order("O1", "INFY", 10, 0, "1500.00"), buy_order("O2", "TCS", 2, 0, "3800.00")]
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("15000.00") + Decimal("7600.00")

    def test_only_unfilled_quantity_counts(self):
        orders = [buy_order("O1", "INFY", 10, 4, "1500.00")]  # 6 unfilled
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("6") * Decimal("1500.00")

    def test_fully_filled_order_contributes_nothing(self):
        orders = [buy_order("O1", "INFY", 10, 10, "1500.00")]
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("0")

    def test_sell_orders_are_excluded(self):
        orders = [dict(buy_order("O1", "INFY", 10, 0, "1500.00"), transaction_type="SELL")]
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("0")

    def test_terminal_status_orders_are_excluded(self):
        orders = [buy_order("O1", "INFY", 10, 0, "1500.00", status="REJECTED")]
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("0")

    def test_wrong_product_orders_are_excluded(self):
        orders = [buy_order("O1", "INFY", 10, 0, "1500.00", product="MIS")]
        assert compute_pending_buy_exposure(orders, product="CNC") == Decimal("0")

    def test_active_unfilled_buy_with_zero_price_fails_closed(self):
        orders = [buy_order("O1", "INFY", 10, 0, "0")]
        with pytest.raises(BrokerObservationContractViolation, match="non-positive price"):
            compute_pending_buy_exposure(orders, product="CNC")


# ---------------------------------------------------------------------------
# 3. The reservation-to-order dedup trap
# ---------------------------------------------------------------------------

class TestReservationDedup:
    def test_reservation_with_no_matching_broker_order_counts_in_full(self):
        reservations = {"INFY": EntryReservation(symbol="INFY", sector="IT", reserved_capital=Decimal("15000"), reserved_at="2026-08-14T09:20:00+00:00")}
        assert compute_reserved_entry_capital(reservations, orders=[]) == Decimal("15000")

    def test_reservation_whose_order_is_now_broker_visible_is_excluded(self):
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY", "product": "CNC",
            "order_type": "LIMIT", "quantity": 10, "price": "1500", "tag": "V3.4_P02_ENTRY",
        }
        reservation = EntryReservation(symbol="INFY", sector="IT", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fingerprint)
        order = {
            "order_id": "O1", "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
            "product": "CNC", "order_type": "LIMIT", "quantity": 10, "price": "1500", "tag": "V3.4_P02_ENTRY", "status": "OPEN",
        }
        assert compute_reserved_entry_capital({"INFY": reservation}, orders=[order]) == Decimal("0")

    def test_the_double_count_trap_itself_committed_capital_counts_the_entry_exactly_once(self):
        # This is the exact scenario flagged as the biggest P02-D trap:
        # a reservation whose order has just become broker-visible must
        # contribute to CommittedCapital via PendingBuyExposure only -
        # never via both ReservedEntryCapital AND PendingBuyExposure.
        fingerprint = {
            "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY", "product": "CNC",
            "order_type": "LIMIT", "quantity": 10, "price": "1500", "tag": "V3.4_P02_ENTRY",
        }
        reservation = EntryReservation(symbol="INFY", sector="IT", reserved_capital=Decimal("15000"), reserved_at="x", entry_fingerprint=fingerprint)
        order = {
            "order_id": "O1", "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
            "product": "CNC", "order_type": "LIMIT", "quantity": 10, "price": "1500", "tag": "V3.4_P02_ENTRY",
            "status": "OPEN", "filled_quantity": 0,
        }
        pending = compute_pending_buy_exposure([order], product="CNC")
        reserved = compute_reserved_entry_capital({"INFY": reservation}, orders=[order])
        committed = compute_committed_capital(deployed_capital=Decimal("0"), pending_buy_exposure=pending, reserved_entry_capital=reserved)
        assert pending == Decimal("15000")
        assert reserved == Decimal("0")
        assert committed == Decimal("15000")  # not 30000

    def test_reservation_with_no_fingerprint_yet_always_counts_reserved_not_pending(self):
        # Before the order has been submitted at all - only ReservedEntryCapital
        # carries the capital, PendingBuyExposure correctly sees nothing yet.
        reservation = EntryReservation(symbol="INFY", sector="IT", reserved_capital=Decimal("15000"), reserved_at="x")
        reserved = compute_reserved_entry_capital({"INFY": reservation}, orders=[])
        pending = compute_pending_buy_exposure([], product="CNC")
        assert reserved == Decimal("15000")
        assert pending == Decimal("0")

    def test_reservation_symbol_disagreeing_with_its_dict_key_fails_closed(self):
        reservation = EntryReservation(symbol="WRONG", sector="IT", reserved_capital=Decimal("1"), reserved_at="x")
        with pytest.raises(BrokerObservationContractViolation, match="disagrees with its own dict key"):
            compute_reserved_entry_capital({"INFY": reservation}, orders=[])


# ---------------------------------------------------------------------------
# 4. Appreciation vs committed capital
# ---------------------------------------------------------------------------

class TestAppreciationDoesNotAffectCommittedCapital:
    def test_appreciation_changes_gross_exposure_not_committed_capital(self):
        positions = [cnc_position("RELIANCE", 10, "2500.00")]
        deployed = compute_deployed_capital(positions, product="CNC")
        committed = compute_committed_capital(deployed_capital=deployed, pending_buy_exposure=Decimal("0"), reserved_entry_capital=Decimal("0"))

        gross_before = compute_gross_market_exposure(positions, product="CNC", quotes={"NSE:RELIANCE": {"last_price": 2500}})
        gross_after = compute_gross_market_exposure(positions, product="CNC", quotes={"NSE:RELIANCE": {"last_price": 3500}})

        assert committed == Decimal("25000.00")
        assert gross_before == Decimal("25000")
        assert gross_after == Decimal("35000")
        # Committed capital is computed purely from cost basis and never
        # recomputed from gross exposure - proven structurally, not just
        # by re-asserting the same unchanged value.
        assert compute_deployed_capital(positions, product="CNC") == deployed


# ---------------------------------------------------------------------------
# 5. Exit-day accounting does not double-count
# ---------------------------------------------------------------------------

class TestExitDayAccounting:
    def test_a_position_sold_today_contributes_only_todays_incremental_move(self):
        # The user's own worked example: entry 1000, held overnight to
        # 1100 (yesterday's close - already embedded in prior_close_equity
        # via yesterday's unrealized MTM), sold today at 1150. Today's
        # true economic contribution is 1150-1100=50, not the full
        # 1150-1000=150 realized gain.
        trial_capital = Decimal("100000")
        # Yesterday: 1 share open, avg_entry=1000, marked at 1100 close.
        yesterday_unrealized = (Decimal("1100") - Decimal("1000")) * 1
        prior_close_equity = compute_equity(
            trial_capital=trial_capital, cumulative_realized_pnl=Decimal("0"),
            cumulative_charges=Decimal("0"), unrealized_pnl=yesterday_unrealized,
        )
        assert prior_close_equity == Decimal("100100")

        # Today: sold at 1150 - position now closed, realized_pnl becomes 150,
        # unrealized is 0 (nothing open), no charges for clean arithmetic.
        today_equity = compute_equity(
            trial_capital=trial_capital, cumulative_realized_pnl=Decimal("150"),
            cumulative_charges=Decimal("0"), unrealized_pnl=Decimal("0"),
        )
        daily_pnl_today = compute_daily_pnl(equity=today_equity, prior_close_equity=prior_close_equity)
        assert daily_pnl_today == Decimal("50")  # not 150


# ---------------------------------------------------------------------------
# 6-8. Daily-cadence checkpoint rollover / HWM immunity
# ---------------------------------------------------------------------------

class TestDailyCadence:
    def test_restart_on_a_new_trading_day_seeds_prior_close_equity_exactly(self):
        previous = DailyAccountingCheckpoint(
            trading_day="2026-08-13", prior_close_equity=Decimal("100000"),
            day_start_equity=Decimal("100000"), trial_high_water_mark=Decimal("101000"),
        )
        fresh_equity = Decimal("100850")  # first authoritative snapshot on the new day
        rolled = roll_daily_checkpoint(previous_checkpoint=previous, new_trading_day="2026-08-14", fresh_equity_at_rollover=fresh_equity)
        assert rolled.trading_day == "2026-08-14"
        assert rolled.prior_close_equity == fresh_equity
        assert rolled.day_start_equity == fresh_equity

    def test_hwm_advances_on_rollover_when_new_equity_exceeds_it(self):
        previous = DailyAccountingCheckpoint(trading_day="2026-08-13", prior_close_equity=Decimal("100000"), day_start_equity=Decimal("100000"), trial_high_water_mark=Decimal("100000"))
        rolled = roll_daily_checkpoint(previous_checkpoint=previous, new_trading_day="2026-08-14", fresh_equity_at_rollover=Decimal("103000"))
        assert rolled.trial_high_water_mark == Decimal("103000")

    def test_hwm_does_not_regress_on_rollover_when_new_equity_is_lower(self):
        previous = DailyAccountingCheckpoint(trading_day="2026-08-13", prior_close_equity=Decimal("100000"), day_start_equity=Decimal("100000"), trial_high_water_mark=Decimal("103000"))
        rolled = roll_daily_checkpoint(previous_checkpoint=previous, new_trading_day="2026-08-14", fresh_equity_at_rollover=Decimal("99000"))
        assert rolled.trial_high_water_mark == Decimal("103000")

    def test_hwm_changes_only_on_the_allowed_daily_cadence_not_via_live_snapshots(self):
        checkpoint = DailyAccountingCheckpoint(trading_day="2026-08-14", prior_close_equity=Decimal("100000"), day_start_equity=Decimal("100000"), trial_high_water_mark=Decimal("100000"))
        config = cfg()
        positions = [cnc_position("RELIANCE", 10, "2500.00")]

        # Simulate many intraday polls with a rising, then falling, price -
        # a naive "update HWM every observation" design would move the HWM
        # here. build_portfolio_snapshot must not - it doesn't even return
        # a checkpoint, only reads the existing one.
        for price in (2600, 2700, 2650, 2500, 2900, 2400):
            snap = build_portfolio_snapshot(
                positions=positions, orders=[], quotes={"NSE:RELIANCE": {"last_price": price}},
                reservations={}, cfg=config, checkpoint=checkpoint,
                cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"),
                sealed_daily_pnl_series={},
            )
            assert isinstance(snap, PortfolioRiskSnapshot)

        assert checkpoint.trial_high_water_mark == Decimal("100000")  # untouched, still the object's original value

    def test_intraday_drawdown_does_not_silently_create_a_new_hwm(self):
        checkpoint = DailyAccountingCheckpoint(trading_day="2026-08-14", prior_close_equity=Decimal("100000"), day_start_equity=Decimal("100000"), trial_high_water_mark=Decimal("105000"))
        # A large intraday spike well above the existing HWM.
        drawdown_at_spike = compute_trial_drawdown(equity=Decimal("110000"), trial_high_water_mark=checkpoint.trial_high_water_mark, trial_capital=Decimal("100000"))
        # Drawdown is negative (above the frozen HWM) precisely because the
        # HWM has NOT been silently bumped up to 110000 mid-day.
        assert drawdown_at_spike < 0
        assert checkpoint.trial_high_water_mark == Decimal("105000")


# ---------------------------------------------------------------------------
# 9. Unexplained equity change
# ---------------------------------------------------------------------------

class TestUnexplainedEquityChange:
    def test_matching_equity_does_not_raise(self):
        verify_no_unexplained_equity_change(computed_equity=Decimal("100050.00"), expected_equity_from_ledger=Decimal("100050.00"))

    def test_small_rounding_difference_within_tolerance_does_not_raise(self):
        verify_no_unexplained_equity_change(computed_equity=Decimal("100050.005"), expected_equity_from_ledger=Decimal("100050.00"))

    def test_unexplained_jump_raises(self):
        with pytest.raises(BrokerObservationContractViolation, match="Unexplained equity change"):
            verify_no_unexplained_equity_change(computed_equity=Decimal("110000.00"), expected_equity_from_ledger=Decimal("100000.00"))


# ---------------------------------------------------------------------------
# 10. Missing/stale marks: fail closed for mark-dependent metrics only
# ---------------------------------------------------------------------------

class TestMarkDependence:
    def test_missing_mark_fails_closed_for_gross_exposure(self):
        positions = [cnc_position("RELIANCE", 10, "2500.00")]
        with pytest.raises(BrokerObservationContractViolation, match="No market mark"):
            compute_gross_market_exposure(positions, product="CNC", quotes={})

    def test_missing_mark_fails_closed_for_unrealized_pnl(self):
        positions = [cnc_position("RELIANCE", 10, "2500.00")]
        with pytest.raises(BrokerObservationContractViolation, match="No market mark"):
            compute_unrealized_pnl(positions, product="CNC", quotes={})

    def test_stale_or_malformed_mark_fails_closed(self):
        positions = [cnc_position("RELIANCE", 10, "2500.00")]
        with pytest.raises(BrokerObservationContractViolation, match="Invalid market mark"):
            compute_gross_market_exposure(positions, product="CNC", quotes={"NSE:RELIANCE": {"last_price": -5}})

    def test_missing_marks_do_not_prevent_cost_basis_computation(self):
        # The independence property, proven structurally: deployed capital,
        # pending exposure, reservations, and committed capital never take
        # a quotes argument at all.
        positions = [cnc_position("RELIANCE", 10, "2500.00")]
        deployed = compute_deployed_capital(positions, product="CNC")
        pending = compute_pending_buy_exposure([], product="CNC")
        reserved = compute_reserved_entry_capital({}, orders=[])
        committed = compute_committed_capital(deployed_capital=deployed, pending_buy_exposure=pending, reserved_entry_capital=reserved)
        assert committed == Decimal("25000.00")

    def test_build_portfolio_snapshot_fails_closed_when_a_mark_is_missing(self):
        checkpoint = initial_checkpoint(trading_day="2026-08-14", trial_capital=Decimal("100000"))
        with pytest.raises(BrokerObservationContractViolation):
            build_portfolio_snapshot(
                positions=[cnc_position("RELIANCE", 10, "2500.00")], orders=[], quotes={},
                reservations={}, cfg=cfg(), checkpoint=checkpoint,
                cumulative_realized_pnl=Decimal("0"), cumulative_charges=Decimal("0"), sealed_daily_pnl_series={},
            )


# ---------------------------------------------------------------------------
# Strict payload validation
# ---------------------------------------------------------------------------

class TestStrictPositionValidation:
    def test_missing_quantity_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="non-numeric quantity"):
            compute_deployed_capital([{"tradingsymbol": "X", "product": "CNC", "average_price": "10"}], product="CNC")

    def test_non_numeric_average_price_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="non-numeric average_price"):
            compute_deployed_capital([cnc_position("X", 10, "not-a-number")], product="CNC")

    def test_negative_average_price_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="invalid average_price"):
            compute_deployed_capital([cnc_position("X", 10, "-5")], product="CNC")

    def test_nonzero_quantity_with_zero_average_price_is_impossible_and_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="impossible cost basis"):
            compute_deployed_capital([cnc_position("X", 10, "0")], product="CNC")

    def test_inconsistent_product_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="unsupported or missing product"):
            compute_deployed_capital([cnc_position("X", 10, "10", product="NRML")], product="CNC")

    def test_duplicated_position_identity_fails_closed(self):
        positions = [cnc_position("RELIANCE", 10, "2500"), cnc_position("RELIANCE", 5, "2500")]
        with pytest.raises(BrokerObservationContractViolation, match="Duplicate broker position identity"):
            compute_deployed_capital(positions, product="CNC")

    def test_boolean_quantity_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="boolean quantity"):
            compute_deployed_capital([{"tradingsymbol": "X", "product": "CNC", "quantity": True, "average_price": "10"}], product="CNC")

    def test_non_list_positions_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="not a list"):
            compute_deployed_capital({"not": "a list"}, product="CNC")


class TestStrictOrderValidation:
    def test_missing_order_id_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="valid order_id"):
            compute_pending_buy_exposure([{"tradingsymbol": "X", "transaction_type": "BUY", "product": "CNC", "status": "OPEN", "quantity": 1, "price": "10"}], product="CNC")

    def test_filled_exceeding_quantity_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="exceeds quantity"):
            compute_pending_buy_exposure([buy_order("O1", "X", 5, 10, "10")], product="CNC")

    def test_negative_quantity_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="negative quantity"):
            compute_pending_buy_exposure([buy_order("O1", "X", -5, 0, "10")], product="CNC")

    def test_unresolved_transaction_type_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="invalid transaction_type"):
            compute_pending_buy_exposure([dict(buy_order("O1", "X", 5, 0, "10"), transaction_type="SHORT")], product="CNC")

    def test_missing_status_fails_closed(self):
        order = buy_order("O1", "X", 5, 0, "10")
        del order["status"]
        with pytest.raises(BrokerObservationContractViolation, match="no valid status"):
            compute_pending_buy_exposure([order], product="CNC")

    def test_duplicated_order_id_fails_closed(self):
        orders = [buy_order("O1", "X", 5, 0, "10"), buy_order("O1", "Y", 3, 0, "20")]
        with pytest.raises(BrokerObservationContractViolation, match="Duplicate broker order_id"):
            compute_pending_buy_exposure(orders, product="CNC")

    def test_non_numeric_price_fails_closed(self):
        with pytest.raises(BrokerObservationContractViolation, match="non-numeric price"):
            compute_pending_buy_exposure([buy_order("O1", "X", 5, 0, "not-a-price")], product="CNC")


# ---------------------------------------------------------------------------
# Aggregate builder sanity
# ---------------------------------------------------------------------------

class TestBuildPortfolioSnapshotIntegration:
    def test_full_snapshot_with_realistic_multi_position_portfolio(self):
        checkpoint = initial_checkpoint(trading_day="2026-08-14", trial_capital=Decimal("100000"))
        positions = [cnc_position("RELIANCE", 10, "2500.00"), cnc_position("INFY", 20, "1500.00")]
        orders = [buy_order("O1", "TCS", 2, 0, "3800.00")]
        reservations = {}
        quotes = {"NSE:RELIANCE": {"last_price": 2550}, "NSE:INFY": {"last_price": 1480}}

        snap = build_portfolio_snapshot(
            positions=positions, orders=orders, quotes=quotes, reservations=reservations,
            cfg=cfg(), checkpoint=checkpoint, cumulative_realized_pnl=Decimal("0"),
            cumulative_charges=Decimal("50"), sealed_daily_pnl_series={},
        )

        expected_deployed = Decimal("10") * Decimal("2500") + Decimal("20") * Decimal("1500")
        expected_pending = Decimal("2") * Decimal("3800")
        assert snap.deployed_capital == expected_deployed
        assert snap.pending_buy_exposure == expected_pending
        assert snap.committed_capital == expected_deployed + expected_pending
        expected_unrealized = (Decimal("2550") - Decimal("2500")) * 10 + (Decimal("1480") - Decimal("1500")) * 20
        assert snap.unrealized_pnl == expected_unrealized
        assert snap.equity == Decimal("100000") + Decimal("0") - Decimal("50") + expected_unrealized
