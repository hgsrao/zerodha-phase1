"""
REVISION 04: Completed Trade Contract Tests

Verify that SealedRunEvaluation properly validates CompletedTrade records
and enforces the broker/ledger contract.
"""

import pytest
from datetime import datetime, timedelta
from revision4.research_target import (
    CompletedTrade,
    ExitReason,
    SealedRunEvaluation,
    BenchmarkConfig,
)


class TestCompletedTradeContract:
    """Verify CompletedTrade record validation."""

    def test_rejects_missing_trade_id(self):
        """trade_id is required."""
        with pytest.raises(ValueError, match="trade_id required"):
            SealedRunEvaluation.create(
                completed_trades=[
                    CompletedTrade(
                        trade_id="",  # EMPTY
                        symbol="INFY",
                        entry_timestamp="2024-08-01T09:15:00Z",
                        entry_price=3000.0,
                        entry_cost=10.0,
                        exit_timestamp="2024-08-01T14:00:00Z",
                        exit_price=3010.0,
                        exit_cost=10.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=10.0,
                        gross_pnl=100.0,
                        net_pnl=80.0,
                    ),
                ],
                starting_equity=100_000.0,
                ending_equity=100_080.0,
            )

    def test_rejects_duplicate_trade_ids(self):
        """trade_id must be unique."""
        with pytest.raises(ValueError, match="Duplicate trade IDs"):
            SealedRunEvaluation.create(
                completed_trades=[
                    CompletedTrade(
                        trade_id="trade_1",
                        symbol="INFY",
                        entry_timestamp="2024-08-01T09:15:00Z",
                        entry_price=3000.0,
                        entry_cost=10.0,
                        exit_timestamp="2024-08-01T14:00:00Z",
                        exit_price=3010.0,
                        exit_cost=10.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=10.0,
                        gross_pnl=100.0,
                        net_pnl=80.0,
                    ),
                    CompletedTrade(
                        trade_id="trade_1",  # DUPLICATE
                        symbol="TCS",
                        entry_timestamp="2024-08-01T10:00:00Z",
                        entry_price=4000.0,
                        entry_cost=15.0,
                        exit_timestamp="2024-08-01T15:00:00Z",
                        exit_price=4020.0,
                        exit_cost=15.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=5.0,
                        gross_pnl=100.0,
                        net_pnl=70.0,
                    ),
                ],
                starting_equity=100_000.0,
                ending_equity=100_150.0,
            )

    def test_rejects_exit_before_entry(self):
        """exit_timestamp must be after entry_timestamp."""
        with pytest.raises(ValueError, match="exit must be after entry"):
            SealedRunEvaluation.create(
                completed_trades=[
                    CompletedTrade(
                        trade_id="trade_1",
                        symbol="INFY",
                        entry_timestamp="2024-08-01T14:00:00Z",
                        entry_price=3000.0,
                        entry_cost=10.0,
                        exit_timestamp="2024-08-01T09:15:00Z",  # BEFORE entry
                        exit_price=3010.0,
                        exit_cost=10.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=10.0,
                        gross_pnl=100.0,
                        net_pnl=80.0,
                    ),
                ],
                starting_equity=100_000.0,
                ending_equity=100_080.0,
            )

    def test_rejects_invalid_timestamp_format(self):
        """Timestamps must be valid ISO format."""
        with pytest.raises(ValueError, match="invalid timestamp"):
            SealedRunEvaluation.create(
                completed_trades=[
                    CompletedTrade(
                        trade_id="trade_1",
                        symbol="INFY",
                        entry_timestamp="Aug 1, 2024",  # INVALID ISO format
                        entry_price=3000.0,
                        entry_cost=10.0,
                        exit_timestamp="2024-08-01T14:00:00Z",
                        exit_price=3010.0,
                        exit_cost=10.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=10.0,
                        gross_pnl=100.0,
                        net_pnl=80.0,
                    ),
                ],
                starting_equity=100_000.0,
                ending_equity=100_080.0,
            )

    def test_rejects_mismatched_gross_pnl(self):
        """gross_pnl must equal (exit - entry) * qty."""
        with pytest.raises(ValueError, match="gross_pnl mismatch"):
            SealedRunEvaluation.create(
                completed_trades=[
                    CompletedTrade(
                        trade_id="trade_1",
                        symbol="INFY",
                        entry_timestamp="2024-08-01T09:15:00Z",
                        entry_price=3000.0,
                        entry_cost=10.0,
                        exit_timestamp="2024-08-01T14:00:00Z",
                        exit_price=3010.0,
                        exit_cost=10.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=10.0,
                        gross_pnl=99.0,  # Should be 100.0, wrong!
                        net_pnl=79.0,
                    ),
                ],
                starting_equity=100_000.0,
                ending_equity=100_079.0,
            )

    def test_rejects_mismatched_net_pnl(self):
        """net_pnl must equal gross - entry_cost - exit_cost."""
        with pytest.raises(ValueError, match="net_pnl mismatch"):
            SealedRunEvaluation.create(
                completed_trades=[
                    CompletedTrade(
                        trade_id="trade_1",
                        symbol="INFY",
                        entry_timestamp="2024-08-01T09:15:00Z",
                        entry_price=3000.0,
                        entry_cost=10.0,
                        exit_timestamp="2024-08-01T14:00:00Z",
                        exit_price=3010.0,
                        exit_cost=10.0,
                        exit_reason=ExitReason.TARGET_HIT,
                        quantity=10.0,
                        gross_pnl=100.0,
                        net_pnl=75.0,  # Should be 80.0, wrong!
                    ),
                ],
                starting_equity=100_000.0,
                ending_equity=100_075.0,
            )


class TestReconciliation:
    """Verify triple reconciliation: trades ↔ daily ↔ equity."""

    def test_rejects_trades_daily_mismatch(self):
        """Sum of trade P&L must equal sum of daily P&L."""
        # Create two trades on different days
        trade1 = CompletedTrade(
            trade_id="trade_1",
            symbol="INFY",
            entry_timestamp="2024-08-01T09:15:00Z",
            entry_price=3000.0,
            entry_cost=10.0,
            exit_timestamp="2024-08-01T14:00:00Z",
            exit_price=3010.0,
            exit_cost=10.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=10.0,
            gross_pnl=100.0,
            net_pnl=80.0,
        )
        trade2 = CompletedTrade(
            trade_id="trade_2",
            symbol="TCS",
            entry_timestamp="2024-08-02T09:15:00Z",
            entry_price=4000.0,
            entry_cost=15.0,
            exit_timestamp="2024-08-02T14:00:00Z",
            exit_price=4010.0,
            exit_cost=15.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=5.0,
            gross_pnl=50.0,
            net_pnl=20.0,
        )
        # Total P&L from trades: 100 (trade1 = 80 + 20 from trade2)
        # But ending_equity implies 90 (not 100)
        with pytest.raises(ValueError, match="reconciliation failed"):
            SealedRunEvaluation.create(
                completed_trades=[trade1, trade2],
                starting_equity=100_000.0,
                ending_equity=100_090.0,  # MISMATCH: should be 100_100
            )

    def test_accepts_valid_two_trade_month(self):
        """Valid two-trade month passes all reconciliations."""
        trade1 = CompletedTrade(
            trade_id="trade_1",
            symbol="INFY",
            entry_timestamp="2024-08-01T09:15:00Z",
            entry_price=3000.0,
            entry_cost=10.0,
            exit_timestamp="2024-08-01T14:00:00Z",
            exit_price=3010.0,
            exit_cost=10.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=10.0,
            gross_pnl=100.0,
            net_pnl=80.0,
        )
        trade2 = CompletedTrade(
            trade_id="trade_2",
            symbol="TCS",
            entry_timestamp="2024-08-02T09:15:00Z",
            entry_price=4000.0,
            entry_cost=15.0,
            exit_timestamp="2024-08-02T14:00:00Z",
            exit_price=4010.0,
            exit_cost=15.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=5.0,
            gross_pnl=50.0,
            net_pnl=20.0,
        )
        # Total P&L: 80 + 20 = 100
        eval = SealedRunEvaluation.create(
            completed_trades=[trade1, trade2],
            starting_equity=100_000.0,
            ending_equity=100_100.0,  # ✓ Reconciles
        )

        assert eval.total_trades == 2
        assert eval.total_wins == 2
        assert eval.total_losses == 0
        assert eval.total_profit_factor == float("inf")
        assert eval.total_net_pnl == 100.0
        assert eval.total_trading_sessions == 2


class TestProfitFactorDerivation:
    """Verify profit factor is derived, not supplied."""

    def test_derives_profit_factor_two_wins(self):
        """With two profitable trades, profit_factor = win_sum / loss_sum."""
        trade1 = CompletedTrade(
            trade_id="trade_1",
            symbol="INFY",
            entry_timestamp="2024-08-01T09:15:00Z",
            entry_price=3000.0,
            entry_cost=10.0,
            exit_timestamp="2024-08-01T14:00:00Z",
            exit_price=3010.0,
            exit_cost=10.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=10.0,
            gross_pnl=100.0,
            net_pnl=80.0,
        )
        trade2 = CompletedTrade(
            trade_id="trade_2",
            symbol="TCS",
            entry_timestamp="2024-08-02T09:15:00Z",
            entry_price=4000.0,
            entry_cost=15.0,
            exit_timestamp="2024-08-02T14:00:00Z",
            exit_price=4010.0,
            exit_cost=15.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=5.0,
            gross_pnl=50.0,
            net_pnl=20.0,
        )
        eval = SealedRunEvaluation.create(
            completed_trades=[trade1, trade2],
            starting_equity=100_000.0,
            ending_equity=100_100.0,
        )

        # gross_profit = 80 + 20 = 100
        # gross_loss = 0
        # profit_factor = 100 / 0 = inf
        assert eval.total_profit_factor == float("inf")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
