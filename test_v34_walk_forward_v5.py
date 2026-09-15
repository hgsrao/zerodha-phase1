from types import SimpleNamespace

import pytest

from walk_forward_v5 import r_value, stats


def test_r_value_normalizes_by_initial_stop_risk():
    trade = SimpleNamespace(entry_price=100, stop_price=98, quantity=2, net_pnl=2)
    assert r_value(trade) == pytest.approx(0.5)


def test_stats_reports_expectancy_and_profit_factor_in_r():
    result = stats([1.0, -0.5, 0.5])
    assert result["trades"] == 3
    assert result["expectancy_r"] == pytest.approx(1 / 3)
    assert result["profit_factor_r"] == pytest.approx(3.0)
