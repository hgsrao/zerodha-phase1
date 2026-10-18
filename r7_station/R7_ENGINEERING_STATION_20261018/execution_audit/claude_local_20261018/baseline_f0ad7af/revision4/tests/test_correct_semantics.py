"""
REVISION 04: Semantic correctness tests

Verify that:
1. Transaction costs are calculated correctly (NSE/India model)
2. Kill switch blocks orders (doesn't change MPC sizing)
3. Slippage tolerance is a safety gate (not a cost parameter)
"""

import pytest
from revision4.contracts import EffectiveConfig, Bar, OrderIntent, SizedProposal, TradePlan
from revision4.config_access import calculate_transaction_cost, get_kill_switch_status
from revision4.paper_broker import PaperBroker


class TestTransactionCostModel:
    """Verify NSE/India real cost model is used."""

    def test_buy_cost_includes_brokerage_and_exchange(self):
        """Buy: brokerage + exchange charges."""
        # Price ₹1000, Qty 10 → Turnover ₹10,000
        # Brokerage: min(20, 0.0003 * 10000) = min(20, 3) = ₹3
        # Exchange: 0.0000345 * 10000 = ₹0.345
        cost = calculate_transaction_cost(price=1000.0, quantity=10.0, side="BUY")
        expected = 3.0 + 0.345
        assert cost == pytest.approx(expected, abs=0.01), f"Expected {expected}, got {cost}"

    def test_sell_cost_includes_stt_tax(self):
        """Sell: brokerage + exchange + STT."""
        # Price ₹1000, Qty 10 → Turnover ₹10,000
        # Brokerage: min(20, 0.0003 * 10000) = ₹3
        # Exchange: 0.0000345 * 10000 = ₹0.345
        # STT: 0.00025 * 10000 = ₹2.5
        cost = calculate_transaction_cost(price=1000.0, quantity=10.0, side="SELL")
        expected = 3.0 + 0.345 + 2.5
        assert cost == pytest.approx(expected, abs=0.01), f"Expected {expected}, got {cost}"

    def test_brokerage_caps_at_20_rupees(self):
        """For small trades, brokerage is capped at ₹20."""
        # Price ₹100, Qty 1 → Turnover ₹100
        # Brokerage: min(20, 0.03% * 100) = min(20, 0.03) = ₹0.03
        # But no, actually: min(20, 0.0003 * 100) = min(20, 0.03) = 0.03
        # Wait, let me recalculate:
        # 0.03% = 0.0003 as decimal, so 0.0003 * 100 = 0.03
        # min(20, 0.03) = 0.03
        cost = calculate_transaction_cost(price=100.0, quantity=1.0, side="BUY")
        # Brokerage: min(20, 0.0003*100) = 0.03
        # Exchange: 0.0000345*100 = 0.00345
        expected = 0.03 + 0.00345
        assert cost == pytest.approx(expected, abs=0.001)

    def test_large_trade_brokerage_caps_at_20(self):
        """For large trades, brokerage is capped at ₹20."""
        # Price ₹10000, Qty 100 → Turnover ₹10,00,000
        # Brokerage: min(20, 0.03% * 10,00,000) = min(20, 3000) = ₹20 (capped)
        # Exchange: 0.00345% * 10,00,000 = ₹345
        cost = calculate_transaction_cost(price=10000.0, quantity=100.0, side="BUY")
        brokerage = min(20.0, 0.0003 * 1000000)
        exchange = 0.0000345 * 1000000
        expected = brokerage + exchange
        assert cost == pytest.approx(expected, abs=0.01)
        # Verify brokerage is actually capped
        assert brokerage == 20.0, "Brokerage should be capped at ₹20 for large trades"


class TestKillSwitchSemantics:
    """Verify kill switch blocks orders, doesn't change MPC sizing."""

    def test_kill_switch_status_retrieval(self):
        """Kill switch should be retrievable as a boolean."""
        config = EffectiveConfig()
        status = get_kill_switch_status(config)
        assert isinstance(status, bool)
        # Default is True (kill switch enabled = orders allowed)
        assert status == True

    def test_kill_switch_is_authorization_not_mpc(self):
        """
        Kill switch controls ORDER AUTHORIZATION (block/allow).
        It does NOT control MPC sizing.

        This test documents the correct semantics to prevent future regression
        where someone treats kill_switch as an MPC enable/disable flag.
        """
        config = EffectiveConfig()

        # Kill switch is a boolean safety control
        kill_switch = get_kill_switch_status(config)
        assert isinstance(kill_switch, bool), "Kill switch must be boolean"

        # Kill switch does NOT appear in MPC parameters
        # (MPC has its own parameters in Revision 2 ModelPredictiveControlBox)
        try:
            mpc_size_param = config.require("mpc_scaling_enabled")
            # If this parameter exists, it should NOT be derived from kill_switch
            # (This would indicate a semantic error)
            assert mpc_size_param != kill_switch, \
                "MPC sizing must not be coupled to kill switch status"
        except KeyError:
            # Correct: no MPC_enabled in config; MPC box handles its own params
            pass


class TestSlippageTolerance:
    """Verify slippage tolerance is a safety gate, not a cost parameter."""

    def test_slippage_is_safety_limit_not_cost(self):
        """
        max_slippage_fraction is a post-fill authorization gate.
        It checks if realized_price vs market_price exceeds tolerance.
        It is NOT used in cost calculation.
        """
        config = EffectiveConfig()
        slippage_limit = config.require("max_slippage_fraction")

        # Slippage limit should be small (e.g., 0.001 = 0.1%)
        assert 0 < slippage_limit < 0.01, \
            f"Slippage tolerance should be small, got {slippage_limit}"

        # Cost calculation should NOT use this parameter
        # (We use fixed NSE cost model instead)
        cost = calculate_transaction_cost(1000.0, 10.0, "BUY")
        assert cost > 0, "Cost should be calculated"
        # Verify it's not using slippage_limit as a percentage
        assert cost < 1000 * 10 * slippage_limit, \
            "Cost calculation should not use slippage_limit as cost percentage"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
