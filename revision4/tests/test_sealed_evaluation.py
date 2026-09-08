"""
REVISION 04: Sealed Run Evaluation Tests

Verify that the evaluator:
1. REJECTS incomplete data (no defaults)
2. Derives session count from actual daily results
3. Computes attainment from real daily P&L
4. Does NOT define acceptance thresholds
"""

import pytest
from revision4.research_target import (
    DailyResult,
    SealedRunEvaluation,
    BenchmarkConfig,
)


class TestEvaluatorFailsClosed:
    """Verify evaluator rejects incomplete reports."""

    def test_rejects_empty_daily_results(self):
        """Must have at least one day of results."""
        with pytest.raises(ValueError, match="daily_results cannot be empty"):
            SealedRunEvaluation.create(
                daily_results=[],  # EMPTY
                starting_equity=100_000.0,
                ending_equity=101_000.0,
                total_profit_factor=1.5,
            )

    def test_rejects_missing_starting_equity(self):
        """Starting equity is required, no default."""
        day = DailyResult(
            date="2024-08-01",
            net_pnl=500.0,
            trades=2,
            wins=2,
            losses=0,
            max_dd_pct=0.5,
            safety_violations=0,
        )
        with pytest.raises(ValueError, match="starting_equity"):
            SealedRunEvaluation.create(
                daily_results=[day],
                starting_equity=None,  # MISSING
                ending_equity=100_500.0,
                total_profit_factor=float("inf"),  # All wins
            )

    def test_rejects_missing_profit_factor(self):
        """Profit factor must be explicit, no default like 1.0."""
        day = DailyResult(
            date="2024-08-01",
            net_pnl=500.0,
            trades=2,
            wins=2,
            losses=0,
            max_dd_pct=0.5,
            safety_violations=0,
        )
        with pytest.raises(ValueError, match="total_profit_factor must be explicit"):
            SealedRunEvaluation.create(
                daily_results=[day],
                starting_equity=100_000.0,
                ending_equity=100_500.0,
                total_profit_factor=None,  # MISSING
            )


class TestSessionDerivation:
    """Verify session count comes from actual data, not defaults."""

    def test_derives_session_count_from_daily_results(self):
        """Session count = len(daily_results), never assumed."""
        # 5 actual trading days
        days = [
            DailyResult(
                date=f"2024-08-{i:02d}",
                net_pnl=400.0 + (i * 100),
                trades=2 + i,
                wins=1 + i,
                losses=1,
                max_dd_pct=1.0,
                safety_violations=0,
            )
            for i in range(1, 6)
        ]

        eval = SealedRunEvaluation.create(
            daily_results=days,
            starting_equity=100_000.0,
            ending_equity=104_500.0,  # 5 * 500 + 2500
            total_profit_factor=5.0,
        )

        assert eval.total_trading_sessions == 5, "Should have 5 sessions"
        assert eval.avg_daily_net_pnl == 900.0, "Total 4500 / 5 = 900"


class TestBenchmarkAttainment:
    """Verify benchmark attainment computed from real daily P&L."""

    def test_counts_sessions_above_benchmark(self):
        """Sessions above ₹400 are counted from actual daily P&L."""
        days = [
            DailyResult(date="2024-08-01", net_pnl=500.0, trades=1, wins=1, losses=0, max_dd_pct=0, safety_violations=0),  # Above
            DailyResult(date="2024-08-02", net_pnl=300.0, trades=1, wins=0, losses=1, max_dd_pct=1, safety_violations=0),  # Below
            DailyResult(date="2024-08-03", net_pnl=450.0, trades=1, wins=1, losses=0, max_dd_pct=0, safety_violations=0),  # Above
            DailyResult(date="2024-08-04", net_pnl=350.0, trades=1, wins=0, losses=1, max_dd_pct=1, safety_violations=0),  # Below
            DailyResult(date="2024-08-05", net_pnl=400.0, trades=1, wins=1, losses=0, max_dd_pct=0, safety_violations=0),  # Exactly at threshold (counts as above)
        ]

        eval = SealedRunEvaluation.create(
            daily_results=days,
            starting_equity=100_000.0,
            ending_equity=102_000.0,
            total_profit_factor=3.0,
        )

        assert eval.sessions_above_benchmark == 3, "Should have 3 days >= ₹400 (500, 450, 400)"
        assert eval.sessions_below_benchmark == 2, "Should have 2 days < ₹400"
        assert eval.attainment_rate == 0.6, "3/5 = 60%"

    def test_benchmark_is_informational_only(self):
        """Benchmark config is for reporting, not decision-making."""
        day = DailyResult(
            date="2024-08-01",
            net_pnl=500.0,
            trades=1,
            wins=1,
            losses=0,
            max_dd_pct=0,
            safety_violations=0,
        )

        # Can use custom benchmark
        custom_benchmark = BenchmarkConfig(daily_net_pnl_benchmark=600.0)

        eval = SealedRunEvaluation.create(
            daily_results=[day],
            starting_equity=100_000.0,
            ending_equity=100_500.0,
            total_profit_factor=float("inf"),
            benchmark=custom_benchmark,
        )

        # With ₹600 benchmark, ₹500 is below
        assert eval.sessions_below_benchmark == 1, "₹500 < ₹600 benchmark"
        assert eval.attainment_rate == 0.0, "0/1 above ₹600"


class TestReportGeneration:
    """Verify report generation shows all metrics clearly."""

    def test_report_includes_all_required_fields(self):
        """Report must show starting/ending equity, P&L, sessions, metrics."""
        days = [
            DailyResult(
                date="2024-08-01",
                net_pnl=500.0,
                trades=2,
                wins=2,
                losses=0,
                max_dd_pct=0.5,
                safety_violations=0,
            ),
        ]

        eval = SealedRunEvaluation.create(
            daily_results=days,
            starting_equity=100_000.0,
            ending_equity=100_500.0,
            total_profit_factor=float("inf"),
        )

        report = eval.to_report()

        # Verify all key metrics appear
        assert "100,000.00" in report, "Starting equity"
        assert "100,500.00" in report, "Ending equity"
        assert "500.00" in report, "Net P&L"
        assert "1" in report, "Sessions"
        assert "500.00" in report, "Avg daily P&L"
        assert "0.5%" in report or "0.50%" in report, "Max drawdown"
        assert "2" in report, "Total trades"
        assert "2" in report, "Winning trades"
        assert "Profit Factor" in report, "Profit factor"
        assert "Safety Violations" in report, "Safety violations"
        assert "BENCHMARK ATTAINMENT" in report, "Benchmark section"
        assert "SEALED MONTH RUN EVALUATION" in report, "Title"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
