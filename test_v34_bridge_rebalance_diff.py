"""Tests for v34_bridge_rebalance_diff.py (Step A).

Uses dummy current-portfolio data against the real R1 TargetPortfolio
built from a real captured V11 basket - the same golden fixture R1's own
tests are pinned to.

REGENERATED 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
own fixture - the universe grew from 20 to 50 symbols this session,
which genuinely changes V11's real top-4 output (see that file's own
docstring for the full explanation).
"""

from datetime import date

import pytest

from v34_bridge_rebalance_diff import RebalanceDiff, RebalanceDiffError, compute_rebalance_diff
from v34_bridge_target_portfolio import build_target_portfolio

YESTERDAYS_REAL_QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
    "NSE:HINDALCO": {"last_price": 974.5},
    "NSE:ADANIENT": {"last_price": 3009.2},
}

# Real R1 target, regenerated 2026-08-16: LAURUSLABS 13, SHRIRAMFIN 23, HINDALCO 25, ADANIENT 8.


@pytest.fixture(scope="module")
def real_target():
    return build_target_portfolio(quotes=YESTERDAYS_REAL_QUOTES, signal_date=date(2026, 8, 14))


class TestPureKeep:
    def test_current_exactly_matches_target_everything_is_kept(self, real_target):
        current = {"LAURUSLABS": 13, "SHRIRAMFIN": 23, "HINDALCO": 25, "ADANIENT": 8}
        diff = compute_rebalance_diff(current_portfolio=current, target=real_target)
        assert diff.keep == {"LAURUSLABS", "SHRIRAMFIN", "HINDALCO", "ADANIENT"}
        assert diff.exits == {}
        assert diff.enters == {}
        assert diff.target_id == real_target.target_id


class TestPureEnter:
    def test_empty_current_portfolio_everything_is_an_enter(self, real_target):
        diff = compute_rebalance_diff(current_portfolio={}, target=real_target)
        assert diff.keep == frozenset()
        assert diff.exits == {}
        assert dict(diff.enters) == {"LAURUSLABS": 13, "SHRIRAMFIN": 23, "HINDALCO": 25, "ADANIENT": 8}


class TestPureExit:
    def test_held_symbol_not_in_target_is_an_exit(self, real_target):
        current = {"LAURUSLABS": 13, "SHRIRAMFIN": 23, "HINDALCO": 25, "ADANIENT": 8, "RELIANCE": 100}
        diff = compute_rebalance_diff(current_portfolio=current, target=real_target)
        assert diff.keep == {"LAURUSLABS", "SHRIRAMFIN", "HINDALCO", "ADANIENT"}
        assert dict(diff.exits) == {"RELIANCE": 100}
        assert diff.enters == {}


class TestResizeExpandsToExitPlusEnter:
    def test_same_symbol_different_quantity_is_never_a_keep(self, real_target):
        current = {"LAURUSLABS": 10}  # held 10, target wants 13
        diff = compute_rebalance_diff(current_portfolio=current, target=real_target)
        assert "LAURUSLABS" not in diff.keep
        assert dict(diff.exits)["LAURUSLABS"] == 10   # full current quantity
        assert dict(diff.enters)["LAURUSLABS"] == 13  # full target quantity - never a delta/adjustment


class TestMixedRealisticRebalance:
    def test_all_four_outcomes_together_against_the_real_golden_fixture(self, real_target):
        # Held before this rebalance: RELIANCE (not in target -> EXIT),
        # LAURUSLABS at the wrong size (-> RESIZE, expands to EXIT+ENTER),
        # HINDALCO at the exact target size (-> KEEP). SHRIRAMFIN and
        # ADANIENT are newly wanted (-> pure ENTER).
        current = {"RELIANCE": 50, "LAURUSLABS": 10, "HINDALCO": 25}
        diff = compute_rebalance_diff(current_portfolio=current, target=real_target)

        assert diff.keep == {"HINDALCO"}
        assert dict(diff.exits) == {"RELIANCE": 50, "LAURUSLABS": 10}
        assert dict(diff.enters) == {"LAURUSLABS": 13, "SHRIRAMFIN": 23, "ADANIENT": 8}
        assert diff.target_id == real_target.target_id
        assert dict(diff.computed_from_current) == current


class TestFailClosed:
    def test_zero_quantity_in_current_portfolio_raises(self, real_target):
        with pytest.raises(RebalanceDiffError, match="non-positive quantity"):
            compute_rebalance_diff(current_portfolio={"RELIANCE": 0}, target=real_target)

    def test_negative_quantity_raises(self, real_target):
        with pytest.raises(RebalanceDiffError, match="non-positive quantity"):
            compute_rebalance_diff(current_portfolio={"RELIANCE": -5}, target=real_target)

    def test_boolean_quantity_raises(self, real_target):
        with pytest.raises(RebalanceDiffError, match="not an integer"):
            compute_rebalance_diff(current_portfolio={"RELIANCE": True}, target=real_target)

    def test_non_integer_quantity_raises(self, real_target):
        with pytest.raises(RebalanceDiffError, match="not an integer"):
            compute_rebalance_diff(current_portfolio={"RELIANCE": "ten"}, target=real_target)

    def test_empty_symbol_key_raises(self, real_target):
        with pytest.raises(RebalanceDiffError, match="invalid symbol"):
            compute_rebalance_diff(current_portfolio={"": 10}, target=real_target)


class TestImmutability:
    def test_exits_mapping_rejects_item_assignment(self, real_target):
        diff = compute_rebalance_diff(current_portfolio={"RELIANCE": 50}, target=real_target)
        with pytest.raises(TypeError):
            diff.exits["NEWSYM"] = 1

    def test_diff_fields_reject_reassignment(self, real_target):
        diff = compute_rebalance_diff(current_portfolio={}, target=real_target)
        with pytest.raises(Exception):
            diff.target_id = "TAMPERED"

    def test_keep_is_a_real_frozenset(self, real_target):
        current = {"LAURUSLABS": 13}
        diff = compute_rebalance_diff(current_portfolio=current, target=real_target)
        assert isinstance(diff.keep, frozenset)
        with pytest.raises(AttributeError):
            diff.keep.add("SHOULDNT_WORK")
