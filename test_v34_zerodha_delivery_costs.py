import pytest

from zerodha_delivery_costs import DP_CHARGE_INCL_GST, buy_cost, round_trip_bps_equivalent, sell_cost


class TestBuyCost:
    def test_zero_value_is_zero_cost(self):
        result = buy_cost(0.0)
        assert result.total == 0.0

    def test_components_at_a_round_number(self):
        result = buy_cost(100_000.0)
        assert result.stt == pytest.approx(100.0)          # 0.1%
        assert result.exchange_txn == pytest.approx(2.97)   # 0.00297%
        assert result.sebi == pytest.approx(0.01)           # Rs 10/crore
        assert result.stamp_duty == pytest.approx(15.0)     # 0.015%
        assert result.dp_charge == 0.0                      # buy side never has DP
        assert result.gst == pytest.approx((2.97 + 0.01) * 0.18)

    def test_rejects_negative_value(self):
        with pytest.raises(ValueError):
            buy_cost(-1.0)


class TestSellCost:
    def test_includes_flat_dp_charge_by_default(self):
        result = sell_cost(100_000.0)
        assert result.dp_charge == pytest.approx(DP_CHARGE_INCL_GST)
        assert result.stamp_duty == 0.0  # stamp duty is buy-side only

    def test_dp_charge_can_be_disabled(self):
        result = sell_cost(100_000.0, charge_dp=False)
        assert result.dp_charge == 0.0

    def test_dp_charge_does_not_scale_with_trade_value(self):
        small = sell_cost(1_000.0)
        large = sell_cost(1_000_000.0)
        assert small.dp_charge == large.dp_charge == pytest.approx(DP_CHARGE_INCL_GST)

    def test_rejects_negative_value(self):
        with pytest.raises(ValueError):
            sell_cost(-1.0)


class TestRoundTripBpsEquivalent:
    def test_zero_value_is_zero(self):
        assert round_trip_bps_equivalent(0.0) == 0.0

    def test_smaller_trades_cost_more_in_bps_terms(self):
        """The flat DP charge means proportional cost falls as size rises -
        this is the whole reason a flat-bps model misrepresents small trades."""
        small = round_trip_bps_equivalent(5_000.0)
        large = round_trip_bps_equivalent(1_000_000.0)
        assert small > large

    def test_is_meaningfully_above_the_previously_assumed_20bps_round_trip(self):
        # cost_bps_per_side=5 + slippage_bps_per_side=5, round trip, used
        # everywhere in this project before this module existed.
        previously_assumed_round_trip_bps = 20.0
        for value in (10_000.0, 25_000.0, 50_000.0):
            assert round_trip_bps_equivalent(value) > previously_assumed_round_trip_bps
