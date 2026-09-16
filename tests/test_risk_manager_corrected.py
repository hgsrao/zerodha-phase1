"""
Block 5 Risk Manager - Corrected Tests (FRACTION convention)
"""

import pytest
from decimal import Decimal
from blocks.block_5_risk_manager import RiskManager, Mode


class TestRiskManagerCorrected:
    """Test corrected Risk Manager with FRACTION units."""

    @pytest.fixture
    def manager(self):
        return RiskManager(
            max_drawdown=0.20,      # FRACTION: 0.20 = 20%
            max_daily_loss=Decimal('-50000'),
            target_atr_pct=0.03     # FRACTION: 0.03 = 3%
        )

    def test_normal_conditions(self, manager):
        """Normal conditions → NORMAL mode."""
        report = manager.calculate(
            current_equity=Decimal('1000000'),
            peak_equity=Decimal('1000000'),
            daily_pnl=Decimal('0'),
            volatility_pct=0.02,    # 2% ATR (FRACTION)
            bid_ask_spread_pct=0.0005,
            order_book_depth=50000,
            open_positions=[],
            broker_healthy=True,
            data_freshness_ms=500,
            circuit_breaker_healthy=True,
            consecutive_losses=0
        )

        assert report.mode == Mode.NORMAL
        assert report.risk_capacity > 0.70

    def test_18_percent_drawdown_maps_to_derated(self, manager):
        """18% drawdown on 20% limit → DERATED (NOT HALT)."""
        report = manager.calculate(
            current_equity=Decimal('820000'),  # 18% down
            peak_equity=Decimal('1000000'),
            daily_pnl=Decimal('0'),
            volatility_pct=0.02,
            bid_ask_spread_pct=0.0005,
            order_book_depth=50000,
            open_positions=[],
            broker_healthy=True,
            data_freshness_ms=500,
            circuit_breaker_healthy=True,
            consecutive_losses=0
        )

        # Drawdown capacity = (0.20 - 0.18) / 0.20 = 0.10
        # 0.10 capacity → should map to DERATED (0.40 <= capacity < 0.70)
        assert report.current_drawdown == pytest.approx(0.18, abs=0.01)
        assert report.mode == Mode.DERATED, f"Expected DERATED but got {report.mode.value}"

    def test_hard_halt_on_max_drawdown_exceeded(self, manager):
        """21% drawdown exceeds 20% limit → HARD_HALT."""
        report = manager.calculate(
            current_equity=Decimal('790000'),  # 21% down
            peak_equity=Decimal('1000000'),
            daily_pnl=Decimal('0'),
            volatility_pct=0.02,
            bid_ask_spread_pct=0.0005,
            order_book_depth=50000,
            open_positions=[],
            broker_healthy=True,
            data_freshness_ms=500,
            circuit_breaker_healthy=True,
            consecutive_losses=0
        )

        assert report.hard_halt is True
        assert "drawdown" in report.hard_halt_reason.lower()

    def test_hard_halt_on_daily_loss_exceeded(self, manager):
        """Daily P&L <= limit → HARD_HALT."""
        report = manager.calculate(
            current_equity=Decimal('950000'),
            peak_equity=Decimal('1000000'),
            daily_pnl=Decimal('-50000'),  # Exactly at limit
            volatility_pct=0.02,
            bid_ask_spread_pct=0.0005,
            order_book_depth=50000,
            open_positions=[],
            broker_healthy=True,
            data_freshness_ms=500,
            circuit_breaker_healthy=True,
            consecutive_losses=0
        )

        assert report.hard_halt is True

    def test_volatility_capacity_reduces_with_high_atr(self, manager):
        """High volatility (5% when target 3%) → capacity reduced."""
        report = manager.calculate(
            current_equity=Decimal('1000000'),
            peak_equity=Decimal('1000000'),
            daily_pnl=Decimal('0'),
            volatility_pct=0.05,    # 5% > target 3%
            bid_ask_spread_pct=0.0005,
            order_book_depth=50000,
            open_positions=[],
            broker_healthy=True,
            data_freshness_ms=500,
            circuit_breaker_healthy=True,
            consecutive_losses=0
        )

        # volatility_capacity = 0.03 / 0.05 = 0.60
        assert report.capacities.volatility == pytest.approx(0.60, abs=0.01)
        assert report.mode in [Mode.DERATED, Mode.MINIMUM]

    def test_recovery_thresholds_prevent_oscillation(self, manager):
        """Recovery threshold (0.75) > entry threshold (0.70)."""
        # This tests the hysteresis logic
        # Entry to NORMAL: capacity >= 0.70
        # Recovery from DERATED to NORMAL: capacity >= 0.75

        # Start in DERATED mode
        manager.current_mode = Mode.DERATED

        # Capacity at 0.72 (> 0.70 entry, < 0.75 recovery)
        # Should STAY in DERATED (not flip)
        report = manager.calculate(
            current_equity=Decimal('999000'),
            peak_equity=Decimal('1000000'),
            daily_pnl=Decimal('0'),
            volatility_pct=0.02,
            bid_ask_spread_pct=0.0005,
            order_book_depth=50000,
            open_positions=[],
            broker_healthy=True,
            data_freshness_ms=500,
            circuit_breaker_healthy=True,
            consecutive_losses=0
        )

        # Mode should NOT flip due to hysteresis
        assert report.mode == Mode.DERATED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
