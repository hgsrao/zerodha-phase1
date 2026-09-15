from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from anchored_walk_forward_v12 import run_anchored_walk_forward
from brain_research_lab import Candle
from momentum_risk_managed_v12 import load_daily_universe, resample_daily, simulate
from portfolio_brain_v9 import PortfolioConfig, SECTORS

IST = ZoneInfo("Asia/Kolkata")


def _bar(y, mo, d, h, mi, o, h_, l, c, v):
    from datetime import datetime
    return Candle(datetime(y, mo, d, h, mi, tzinfo=IST), o, h_, l, c, v)


class TestResampleDaily:
    def test_aggregates_intraday_bars_into_one_daily_bar(self):
        bars = [
            _bar(2024, 1, 2, 9, 15, 100, 105, 99, 102, 1000),
            _bar(2024, 1, 2, 10, 15, 102, 108, 101, 104, 1500),
            _bar(2024, 1, 2, 11, 15, 104, 106, 96, 97, 1200),
        ]
        result = resample_daily(bars)
        assert len(result) == 1
        day = result[0]
        assert day.open == 100  # first bar's open
        assert day.high == 108  # max high across the day
        assert day.low == 96    # min low across the day
        assert day.close == 97  # last bar's close
        assert day.volume == 3700  # summed

    def test_splits_by_calendar_day(self):
        bars = [
            _bar(2024, 1, 2, 9, 15, 100, 101, 99, 100, 100),
            _bar(2024, 1, 3, 9, 15, 100, 103, 99, 101, 200),
        ]
        result = resample_daily(bars)
        assert len(result) == 2
        assert result[0].timestamp.date() != result[1].timestamp.date()

    def test_empty_input(self):
        assert resample_daily([]) == []


# ---------------------------------------------------------------------------
# Integration tests against the real cached data.
# ---------------------------------------------------------------------------

def _load_real_daily():
    try:
        return load_daily_universe()
    except (RuntimeError, FileNotFoundError):
        pytest.skip("cached historical CSVs are not present in this environment")


class TestSimulateDecisionWindowGate:
    def test_window_before_warmup_yields_no_trades(self):
        daily_series = _load_real_daily()
        cfg = PortfolioConfig(starting_capital=100_000.0)
        all_dates = sorted(set.intersection(*(
            {c.timestamp.date() for c in bars} for bars in daily_series.values()
        )))
        history_start = all_dates[0]
        # Two months in: nowhere near the 13-month formation+skip warmup.
        early_cutoff = all_dates[40]
        result = simulate(
            "X", 4, True, daily_series, cfg, decision_window=(history_start, early_cutoff),
        )
        assert result["trades"] == []

    def test_full_history_window_reproduces_ungated_baseline(self):
        daily_series = _load_real_daily()
        cfg = PortfolioConfig(starting_capital=100_000.0)
        all_dates = sorted(set.intersection(*(
            {c.timestamp.date() for c in bars} for bars in daily_series.values()
        )))
        history_start, history_end = all_dates[0], all_dates[-1] + timedelta(days=1)

        baseline = simulate("X", 4, True, daily_series, cfg, decision_window=None)
        gated = simulate("X", 4, True, daily_series, cfg, decision_window=(history_start, history_end))
        assert len(baseline["trades"]) > 0
        assert [t["net_pnl"] for t in gated["trades"]] == [t["net_pnl"] for t in baseline["trades"]]

    def test_stops_are_never_gated_by_the_decision_window(self):
        """A position opened during a narrow window must still be able to
        stop out on a later day that the window excludes from new entries -
        protecting an existing position is not a new entry."""
        daily_series = _load_real_daily()
        cfg = PortfolioConfig(starting_capital=100_000.0)
        all_dates = sorted(set.intersection(*(
            {c.timestamp.date() for c in bars} for bars in daily_series.values()
        )))
        history_start = all_dates[0]
        # Just wide enough to clear the 13-month formation warmup and reach
        # exactly one rebalance date, then close a few days later - any
        # position that survives past the window boundary can then only be
        # resolved by a stop, since no further rebalance can fire.
        narrow_end = history_start + timedelta(days=414)
        result = simulate(
            "X", 4, True, daily_series, cfg, decision_window=(history_start, narrow_end),
        )
        stop_exits_after_window = [
            t for t in result["trades"]
            if t["reason"] == "STOP" and date.fromisoformat(t["exit_time"]) >= narrow_end
        ]
        assert stop_exits_after_window, (
            "expected at least one stop-loss exit to fire after the decision "
            "window closed - stops must not be gated"
        )


class TestSimulateRiskDiscipline:
    def test_every_trade_has_a_sector_from_the_frozen_map(self):
        daily_series = _load_real_daily()
        cfg = PortfolioConfig(starting_capital=100_000.0)
        result = simulate("X", 4, True, daily_series, cfg)
        for trade in result["trades"]:
            assert trade["sector"] == SECTORS[trade["symbol"]]

    def test_sector_control_never_holds_two_open_positions_in_one_sector(self):
        daily_series = _load_real_daily()
        cfg = PortfolioConfig(starting_capital=100_000.0)
        result = simulate("X", 4, True, daily_series, cfg)
        # Reconstruct simultaneous holdings from entry/exit dates.
        events = []
        for trade in result["trades"]:
            events.append((trade["entry_time"], 1, trade["sector"]))
            events.append((trade["exit_time"], -1, trade["sector"]))
        events.sort()
        open_sectors: dict[str, int] = {}
        for _, delta, sector in events:
            open_sectors[sector] = open_sectors.get(sector, 0) + delta
            assert open_sectors[sector] <= 1


class TestAnchoredWalkForwardV12Smoke:
    def test_produces_well_formed_report_on_real_data(self):
        daily_series = _load_real_daily()
        cfg = PortfolioConfig(starting_capital=100_000.0)
        report = run_anchored_walk_forward(daily_series, cfg)

        import json
        json.dumps(report)  # must not raise

        assert report["research_version"] == "V12_ANCHORED_WALK_FORWARD"
        assert report["total_folds"] == 5
        assert len(report["folds"]) == 5
        for fold in report["folds"]:
            assert fold["selected_variant"] in {"V12_TOP4_SECTOR", "V12_TOP2_SECTOR"}
        assert 0 <= report["profitable_folds"] <= report["total_folds"]
        assert "aggregate_bootstrap" in report
        assert report["limitations"]
