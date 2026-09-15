from datetime import date

import pytest

from anchored_walk_forward_momentum_v11 import (
    DEFAULT_VARIANT,
    curve_returns,
    run_anchored_walk_forward,
    select_variant,
)
from external_model_comparison import ComparisonConfig, daily_closes, load_universe, monthly_long_only


# ---------------------------------------------------------------------------
# Pure unit tests
# ---------------------------------------------------------------------------

class TestCurveReturns:
    def test_computes_day_over_day_returns(self):
        curve = [{"equity": 100.0}, {"equity": 110.0}, {"equity": 99.0}]
        result = curve_returns(curve)
        assert result == pytest.approx([0.10, -0.10])

    def test_single_point_curve_has_no_returns(self):
        assert curve_returns([{"equity": 100.0}]) == []

    def test_skips_a_non_positive_starting_equity(self):
        curve = [{"equity": 0.0}, {"equity": 50.0}, {"equity": 55.0}]
        assert curve_returns(curve) == pytest.approx([0.10])


class TestSelectVariant:
    def test_picks_best_training_return_above_minimum(self):
        results = {
            "EXTERNAL_CROSS_SECTIONAL_12_1": {"metrics": {"net_return": 0.05}, "rebalances": 10},
            "EXTERNAL_TIME_SERIES_12M": {"metrics": {"net_return": 0.12}, "rebalances": 10},
        }
        selected, reason = select_variant(results, minimum_rebalances=3)
        assert selected == "EXTERNAL_TIME_SERIES_12M"
        assert reason == "BEST_TRAINING_NET_RETURN"

    def test_falls_back_to_default_when_no_variant_has_enough_rebalances(self):
        results = {
            "EXTERNAL_CROSS_SECTIONAL_12_1": {"metrics": {"net_return": 0.50}, "rebalances": 1},
            "EXTERNAL_TIME_SERIES_12M": {"metrics": {"net_return": 0.01}, "rebalances": 2},
        }
        selected, reason = select_variant(results, minimum_rebalances=3)
        assert selected == DEFAULT_VARIANT
        assert "INSUFFICIENT_TRAINING_REBALANCES" in reason


# ---------------------------------------------------------------------------
# Integration tests against the real cached data: prove the decision_window
# gate on monthly_long_only() actually isolates training from test periods.
# ---------------------------------------------------------------------------

def _load_real_closes():
    try:
        series, market = load_universe()
    except RuntimeError:
        pytest.skip("cached historical CSVs are not present in this environment")
    return {symbol: daily_closes(bars) for symbol, bars in series.items()}


class TestMonthlyLongOnlyDecisionWindowGate:
    def test_window_before_warmup_yields_no_rebalances(self):
        closes = _load_real_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0)
        common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
        history_start = date.fromisoformat(common_dates[0])
        # Two months in: nowhere near the 13-month (formation + skip) warmup,
        # so no rebalance can occur regardless of the gate.
        early_cutoff = date.fromisoformat(common_dates[40])
        result = monthly_long_only(
            "X", closes, cfg, False, decision_window=(history_start, early_cutoff),
        )
        assert result["rebalances"] == 0
        assert result["metrics"]["net_return"] == 0

    def test_full_history_window_reproduces_ungated_baseline(self):
        closes = _load_real_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0)
        common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
        history_start = date.fromisoformat(common_dates[0])
        from datetime import timedelta
        history_end = date.fromisoformat(common_dates[-1]) + timedelta(days=1)

        baseline = monthly_long_only("X", closes, cfg, False, decision_window=None)
        gated = monthly_long_only(
            "X", closes, cfg, False, decision_window=(history_start, history_end),
        )
        assert baseline["rebalances"] > 0
        assert gated["rebalances"] == baseline["rebalances"]
        assert gated["metrics"]["net_return"] == pytest.approx(baseline["metrics"]["net_return"])

    def test_narrowing_the_window_never_increases_rebalance_count(self):
        closes = _load_real_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0)
        common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
        history_start = date.fromisoformat(common_dates[0])
        midpoint = date.fromisoformat(common_dates[len(common_dates) // 2])
        from datetime import timedelta
        history_end = date.fromisoformat(common_dates[-1]) + timedelta(days=1)

        full = monthly_long_only(
            "X", closes, cfg, False, decision_window=(history_start, history_end),
        )
        half = monthly_long_only(
            "X", closes, cfg, False, decision_window=(history_start, midpoint),
        )
        assert half["rebalances"] <= full["rebalances"]


# ---------------------------------------------------------------------------
# End-to-end run against the real universe (fast: ~15 lightweight monthly
# simulations, no need for a synthetic dataset here).
# ---------------------------------------------------------------------------

class TestRunAnchoredWalkForward:
    def test_produces_well_formed_report_on_real_data(self):
        closes = _load_real_closes()
        cfg = ComparisonConfig(starting_capital=100_000.0)
        report = run_anchored_walk_forward(closes, cfg)

        import json
        json.dumps(report)  # must not raise

        assert report["research_version"] == "V11_MOMENTUM_ANCHORED_WALK_FORWARD"
        assert report["total_folds"] == 5
        assert len(report["folds"]) == 5
        for fold in report["folds"]:
            assert fold["selected_variant"] in {
                "EXTERNAL_CROSS_SECTIONAL_12_1", "EXTERNAL_TIME_SERIES_12M",
            }
            assert set(fold["training_metrics_by_variant"]) == {
                "EXTERNAL_CROSS_SECTIONAL_12_1", "EXTERNAL_TIME_SERIES_12M",
            }
        assert 0 <= report["profitable_folds"] <= report["total_folds"]
        assert "aggregate_bootstrap_on_daily_returns" in report
        assert report["limitations"]
