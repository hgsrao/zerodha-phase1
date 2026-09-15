import pytest

from zerodha_intraday_costs import (
    BROKERAGE_CAP,
    breakeven_move_pct,
    buy_cost,
    round_trip_bps_equivalent,
    sell_cost,
)


class TestBuyCost:
    def test_zero_value_is_zero_cost(self):
        assert buy_cost(0.0).total == 0.0

    def test_brokerage_uses_the_lower_of_percentage_or_flat_cap_small_trade(self):
        # At Rs 10,000, 0.03% = Rs 3, well under the Rs 20 cap.
        result = buy_cost(10_000.0)
        assert result.brokerage == pytest.approx(3.0)

    def test_brokerage_caps_at_twenty_rupees_for_a_large_trade(self):
        # At Rs 1,00,000, 0.03% = Rs 30 > cap, so the flat Rs 20 applies.
        result = buy_cost(100_000.0)
        assert result.brokerage == pytest.approx(BROKERAGE_CAP)

    def test_buy_side_has_no_stt(self):
        assert buy_cost(100_000.0).stt == 0.0

    def test_rejects_negative_value(self):
        with pytest.raises(ValueError):
            buy_cost(-1.0)


class TestSellCost:
    def test_stt_is_lower_than_delivery_and_sell_side_only(self):
        result = sell_cost(100_000.0)
        assert result.stt == pytest.approx(25.0)  # 0.025%, vs delivery's Rs 100 at 0.1%

    def test_no_dp_charge_field_exists_for_intraday(self):
        result = sell_cost(100_000.0)
        assert not hasattr(result, "dp_charge")

    def test_rejects_negative_value(self):
        with pytest.raises(ValueError):
            sell_cost(-1.0)


class TestEconomics:
    def test_round_trip_is_meaningfully_cheaper_than_delivery(self):
        import zerodha_delivery_costs as delivery
        for value in (25_000.0, 100_000.0, 500_000.0):
            assert round_trip_bps_equivalent(value) < delivery.round_trip_bps_equivalent(value)

    def test_breakeven_move_is_still_a_real_hurdle_not_free(self):
        # Scalping's whole premise is capturing moves far smaller than this.
        assert breakeven_move_pct(100_000.0) > 0.05  # more than 5 bps just to break even

    def test_zero_value_breakeven_is_zero(self):
        assert breakeven_move_pct(0.0) == 0.0
