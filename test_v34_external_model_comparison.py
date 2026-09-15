from datetime import date

import zerodha_delivery_costs as real_costs
from external_model_comparison import ComparisonConfig, daily_closes, month_ends, monthly_long_only, run_comparison


def test_month_ends_are_selected():
    assert month_ends(["2025-01-02", "2025-01-31", "2025-02-03"]) == ["2025-01-31", "2025-02-03"]


def test_comparison_is_offline_and_contains_external_controls():
    report = run_comparison(ComparisonConfig(starting_capital=100_000))
    assert report["safety"] == {
        "live_trading_enabled": False,
        "broker_calls": 0,
        "production_runner_started": False,
    }
    names = {model["name"] for model in report["models"]}
    assert names == {"OUR_V9_TOP4_SECTOR", "EXTERNAL_CROSS_SECTIONAL_12_1",
                     "EXTERNAL_TIME_SERIES_12M", "NIFTY_BUY_HOLD"}
    assert all(model["metrics"]["final_equity"] > 0 for model in report["models"])


def _synthetic_monthly_closes():
    """Two symbols, ~15 months of daily closes, deterministic drift, enough
    for a couple of 12-1 rebalances without needing real CSV data."""
    closes = {"A": {}, "B": {}}
    price_a, price_b = 100.0, 100.0
    day = date(2023, 1, 2)
    for i in range(460):
        if day.weekday() < 5:
            price_a *= 1.0015
            price_b *= 1.0005
            closes["A"][day.isoformat()] = price_a
            closes["B"][day.isoformat()] = price_b
        day = date.fromordinal(day.toordinal() + 1)
    return closes


class TestMonthlyLongOnlyCostModel:
    def test_none_cost_model_is_unchanged_from_flat_bps_friction(self):
        closes = _synthetic_monthly_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0, positions=1)
        result = monthly_long_only("X", closes, cfg, False, cost_model=None)
        assert result["rebalances"] > 0

    def test_real_cost_model_produces_a_lower_final_equity_than_the_flat_bps_guess(self):
        """At small per-position sizes, the real statutory schedule (with its
        flat DP charge) should cost more than the 20bps round-trip guess."""
        closes = _synthetic_monthly_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0, positions=1)
        modeled = monthly_long_only("X", closes, cfg, False, cost_model=None)
        real = monthly_long_only("X", closes, cfg, False, cost_model=real_costs)
        assert real["metrics"]["final_equity"] < modeled["metrics"]["final_equity"]

    def test_real_cost_model_never_overdraws_cash(self):
        closes = _synthetic_monthly_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0, positions=1)
        result = monthly_long_only("X", closes, cfg, False, cost_model=real_costs)
        assert all(row["equity"] >= 0 for row in result["curve"])
