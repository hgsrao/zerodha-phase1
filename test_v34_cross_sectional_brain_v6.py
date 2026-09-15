import pytest

from datetime import datetime, timedelta

from brain_research_lab import Candle
from cross_sectional_brain_v6 import RankingConfig, forward_outcome, percentile_ranks, summary


def test_percentile_ranks_handle_order_and_ties():
    ranks = percentile_ranks({"A": 1, "B": 2, "C": 2, "D": 4})
    assert ranks["A"] == 0
    assert ranks["B"] == pytest.approx(0.5)
    assert ranks["C"] == pytest.approx(0.5)
    assert ranks["D"] == 1


def test_summary_is_explicit_for_no_observations():
    result = summary([])
    assert result["observations"] == 0
    assert result["mean_net_return"] is None


def test_patient_limit_can_remain_unfilled():
    start = datetime(2025, 1, 1, 9, 15)
    candles = [Candle(start + timedelta(hours=i), 100.5, 101, 100.4, 100.5, 100)
               for i in range(30)]
    config = RankingConfig(entry_pullback_bps=25, entry_valid_bars=6)
    assert forward_outcome(candles, 0, config) is None
