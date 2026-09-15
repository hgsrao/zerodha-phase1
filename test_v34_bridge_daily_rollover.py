"""Tests for v34_bridge_daily_rollover.py."""

from decimal import Decimal

from v34_bridge_daily_accounting_state import PositionBasis, initial_daily_accounting_state
from v34_bridge_daily_rollover import ROLLING_WEEK_WINDOW_CALENDAR_DAYS, roll_daily_accounting_if_needed
from v34_p02_accounting import compute_rolling_week_pnl


def state_with(**overrides):
    state = initial_daily_accounting_state(trading_day="2026-08-14", trial_capital=Decimal("100000"))
    for k, v in overrides.items():
        state = _replace(state, **{k: v})
    return state


def _replace(state, **kwargs):
    from dataclasses import replace
    return replace(state, **kwargs)


class TestNoOpWhenAlreadyCurrent:
    def test_same_trading_day_returns_the_identical_object_unchanged(self):
        state = state_with()
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-14", fresh_equity_at_rollover=Decimal("999999"))
        assert result is state  # literally the same object - proves no mutation attempt happened at all


class TestRollsForwardOnANewDay:
    def test_trading_day_advances(self):
        state = state_with()
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("100000"))
        assert result.trading_day == "2026-08-15"

    def test_seals_the_ended_days_daily_pnl_exactly_once(self):
        state = state_with()  # checkpoint.prior_close_equity == 100000 (day-1 bootstrap)
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("101500"))
        assert result.sealed_daily_pnl_series["2026-08-14"] == Decimal("1500")

    def test_calling_it_twice_in_a_row_does_not_reseal(self):
        state = state_with()
        once = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("101500"))
        twice = roll_daily_accounting_if_needed(once, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("999999"))
        assert twice is once  # idempotency guard - second call is a pure no-op

    def test_checkpoint_advances_through_the_frozen_roll_daily_checkpoint_function(self):
        state = state_with()
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("105000"))
        assert result.checkpoint.prior_close_equity == Decimal("105000")
        assert result.checkpoint.trial_high_water_mark == Decimal("105000")  # new HWM > old (100000)
        assert result.checkpoint.trading_day == "2026-08-15"

    def test_high_water_mark_never_decreases_on_a_down_day(self):
        state = state_with()  # HWM starts at 100000
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("95000"))
        assert result.checkpoint.trial_high_water_mark == Decimal("100000")  # unchanged, not dropped to 95000


class TestCumulativeAndOpenBasisSurviveUntouched:
    def test_cumulative_realized_pnl_and_charges_are_carried_forward(self):
        state = state_with(cumulative_realized_pnl=Decimal("5000"), cumulative_charges=Decimal("123.45"))
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("105000"))
        assert result.cumulative_realized_pnl == Decimal("5000")
        assert result.cumulative_charges == Decimal("123.45")

    def test_an_overnight_open_position_basis_survives_the_roll_untouched(self):
        basis = {"RELIANCE": PositionBasis(entry_order_id="ORD-A", quantity=50, avg_price=Decimal("2500"), entry_trading_day="2026-08-14")}
        state = state_with(open_position_basis=basis)
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("105000"))
        assert result.open_position_basis == basis

    def test_booked_trade_ids_and_charge_ledgers_are_carried_forward(self):
        state = state_with(
            booked_trade_ids=frozenset({"T1", "T2"}), accounted_charge_by_order_id={"ORD-A": Decimal("12.5")},
            dp_charges_booked=frozenset({("2026-08-10", "INFY")}),
        )
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("105000"))
        assert result.booked_trade_ids == frozenset({"T1", "T2"})
        assert result.accounted_charge_by_order_id == {"ORD-A": Decimal("12.5")}
        assert result.dp_charges_booked == frozenset({("2026-08-10", "INFY")})


class TestTrailingWindowTrim:
    def test_entries_older_than_the_window_are_dropped(self):
        state = state_with(sealed_daily_pnl_series={
            "2026-08-01": Decimal("100"),  # far older than 7 days before 08-15
            "2026-08-10": Decimal("200"),
        })
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("100000"))
        assert "2026-08-01" not in result.sealed_daily_pnl_series
        assert "2026-08-10" in result.sealed_daily_pnl_series  # within the window
        assert "2026-08-14" in result.sealed_daily_pnl_series  # the just-sealed day itself


class TestAuthorizedRollingWeekPolicy:
    """Pre-Live Broker Validation Gate 1 (2026-08-15): "Rolling week = the
    continuously trailing 7 calendar days, including the current trading
    day." Pins the authorized policy as an explicit, dedicated regression
    - not just inferable from TestTrailingWindowTrim's own window-edge
    test above."""

    def test_the_window_constant_is_seven(self):
        assert ROLLING_WEEK_WINDOW_CALENDAR_DAYS == 7

    def test_the_sealed_series_after_rollover_spans_exactly_six_prior_days(self):
        # Six PRIOR sealed days + today's own live daily_pnl (added
        # separately by compute_rolling_week_pnl itself, not sealed here)
        # = seven calendar days total, inclusive of today - the exact
        # authorized policy, not just "a plausible interpretation."
        state = state_with(sealed_daily_pnl_series={
            f"2026-08-{d:02d}": Decimal("10") for d in range(1, 14)  # 2026-08-01 through 2026-08-13
        })
        result = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("100000"))
        # The window ending 2026-08-15 (today) keeps 2026-08-09 .. 2026-08-14 (6 days).
        assert set(result.sealed_daily_pnl_series) == {f"2026-08-{d:02d}" for d in range(9, 15)}

    def test_compute_rolling_week_pnl_over_the_trimmed_series_plus_today_equals_a_true_seven_day_sum(self):
        # End-to-end proof against the frozen P02-D formula itself, not
        # just this module's own trimming in isolation: seed a full 10
        # calendar days of PnL, roll forward, then feed the result into
        # compute_rolling_week_pnl (frozen) alongside a live "today" PnL
        # - the total must equal exactly the last 7 calendar days' sum
        # (today + 6 prior), not 5, not 10.
        # 2026-08-14 (state_with's own trading_day) deliberately NOT
        # seeded here - roll_daily_accounting_if_needed will freshly
        # compute and overwrite that entry itself when it seals "the
        # just-ended day," so pre-seeding it would only be silently
        # replaced, not summed.
        daily_values = {f"2026-08-{d:02d}": Decimal(d) for d in range(5, 14)}  # 08-05 .. 08-13, distinct values
        state = state_with(sealed_daily_pnl_series=daily_values)  # checkpoint.prior_close_equity == 100000 (day-1 bootstrap)
        rolled = roll_daily_accounting_if_needed(state, current_trading_day="2026-08-15", fresh_equity_at_rollover=Decimal("100000"))
        freshly_sealed_08_14 = rolled.sealed_daily_pnl_series["2026-08-14"]
        assert freshly_sealed_08_14 == Decimal("0")  # equity unchanged from the day-1 bootstrap checkpoint

        today_live_pnl = Decimal("999")  # today's own live figure, computed separately in production
        total = compute_rolling_week_pnl(daily_pnl_today=today_live_pnl, sealed_daily_pnl_series=rolled.sealed_daily_pnl_series)

        expected_prior_six = sum(Decimal(d) for d in range(9, 14)) + freshly_sealed_08_14  # 08-09..08-13 (seeded) + 08-14 (freshly computed)
        assert total == today_live_pnl + expected_prior_six  # exactly 7 calendar days, not more, not fewer
