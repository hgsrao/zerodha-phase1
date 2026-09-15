from datetime import date, timedelta
from pathlib import Path

import pytest

from anchored_walk_forward_v10 import (
    DEFAULT_VARIANT,
    bootstrap_summary,
    phase_stats,
    phase_trades,
    run_anchored_walk_forward,
    select_variant,
)
from brain_research_lab import Candle, load_candles_csv
from portfolio_brain_v9 import PortfolioConfig, SECTORS, simulate


# ---------------------------------------------------------------------------
# Pure unit tests: phase filtering, selection, bootstrap. No data on disk.
# ---------------------------------------------------------------------------

def _trade(entry_date: str, net_pnl: float) -> dict:
    return {
        "symbol": "X", "sector": "TECH",
        "entry_time": f"{entry_date}T10:15:00+05:30",
        "exit_time": f"{entry_date}T12:15:00+05:30",
        "reason": "TIME", "entry_notional": 1000.0, "exit_notional": 1000.0 + net_pnl,
        "net_pnl": net_pnl,
    }


class TestPhaseTrades:
    def test_filters_to_half_open_interval(self):
        trades = [_trade("2024-01-01", 10), _trade("2024-06-01", 20), _trade("2024-12-31", 30)]
        result = phase_trades(trades, date(2024, 1, 1), date(2024, 6, 1))
        assert [t["net_pnl"] for t in result] == [10]

    def test_empty_input(self):
        assert phase_trades([], date(2024, 1, 1), date(2024, 6, 1)) == []


class TestPhaseStats:
    def test_computes_win_rate_and_profit_factor(self):
        trades = [_trade("2024-01-01", 100), _trade("2024-01-02", -40), _trade("2024-01-03", 20)]
        stats = phase_stats(trades, starting_capital=10_000)
        assert stats["trades"] == 3
        assert stats["net_pnl"] == 80
        assert stats["net_return_on_starting_capital"] == pytest.approx(0.008)
        assert stats["win_rate"] == pytest.approx(2 / 3)
        assert stats["profit_factor"] == pytest.approx(120 / 40)

    def test_no_losses_gives_none_profit_factor(self):
        stats = phase_stats([_trade("2024-01-01", 5)], starting_capital=10_000)
        assert stats["profit_factor"] is None

    def test_empty_trades(self):
        stats = phase_stats([], starting_capital=10_000)
        assert stats["trades"] == 0
        assert stats["net_pnl"] == 0
        assert stats["win_rate"] is None
        assert stats["profit_factor"] is None


class TestSelectVariant:
    def test_picks_best_training_return_above_minimum(self):
        by_variant = {
            "TOP1": [_trade("2024-01-01", 5)] * 5,
            "TOP2_SECTOR": [_trade("2024-01-01", 50)] * 5,
            "TOP4_SECTOR": [_trade("2024-01-01", 10)] * 5,
        }
        selected, reason = select_variant(by_variant, starting_capital=10_000, minimum_trades=5)
        assert selected == "TOP2_SECTOR"
        assert reason == "BEST_TRAINING_NET_RETURN"

    def test_falls_back_to_default_when_no_variant_has_enough_trades(self):
        by_variant = {
            "TOP1": [_trade("2024-01-01", 500)] * 2,  # great return, too few trades
            "TOP2_SECTOR": [],
            "TOP4_SECTOR": [_trade("2024-01-01", 1)] * 3,
        }
        selected, reason = select_variant(by_variant, starting_capital=10_000, minimum_trades=5)
        assert selected == DEFAULT_VARIANT
        assert "INSUFFICIENT_TRAINING_TRADES" in reason

    def test_ignores_a_high_return_built_on_too_few_trades(self):
        # TOP1's single lucky trade must not beat a merely-eligible variant.
        by_variant = {
            "TOP1": [_trade("2024-01-01", 900)],
            "TOP4_SECTOR": [_trade("2024-01-01", 5)] * 5,
        }
        selected, _ = select_variant(by_variant, starting_capital=10_000, minimum_trades=5)
        assert selected == "TOP4_SECTOR"


class TestBootstrapSummary:
    def test_all_positive_pnls_give_a_positive_interval(self):
        result = bootstrap_summary([10.0, 12.0, 8.0, 15.0, 9.0], iterations=500, seed=1)
        assert result["ci_95_low"] > 0
        assert result["probability_total_non_positive"] == 0.0

    def test_all_negative_pnls_give_a_negative_interval(self):
        result = bootstrap_summary([-10.0, -12.0, -8.0, -15.0, -9.0], iterations=500, seed=1)
        assert result["ci_95_high"] < 0
        assert result["probability_total_non_positive"] == 1.0

    def test_empty_input_is_handled_without_dividing_by_zero(self):
        result = bootstrap_summary([])
        assert result["trade_count"] == 0
        assert result["ci_95_low"] is None
        assert result["probability_total_non_positive"] is None

    def test_deterministic_given_a_fixed_seed(self):
        pnls = [3.0, -1.0, 4.0, -1.5, 2.2, -0.7]
        a = bootstrap_summary(pnls, iterations=1000, seed=42)
        b = bootstrap_summary(pnls, iterations=1000, seed=42)
        assert a == b


# ---------------------------------------------------------------------------
# Integration tests against the real cached historical data: prove the
# decision_window gate on simulate() actually prevents look-ahead, using the
# exact same function the live report and the harness both call.
# ---------------------------------------------------------------------------

def _load_real_series():
    paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    if len(paths) != 20:
        pytest.skip("cached historical CSVs are not present in this environment")
    series = {p.name.split("_", 2)[1]: load_candles_csv(p, 100) for p in paths}
    market_path = next(Path("historical_data_market_60minute").glob("NSE_NIFTY*60minute*.csv"))
    market = load_candles_csv(market_path, 100)
    return series, market


class TestSimulateDecisionWindowGate:
    def test_window_before_any_possible_signal_yields_no_trades(self):
        series, market = _load_real_series()
        cfg = PortfolioConfig()
        history_start = market[0].timestamp.date()
        # 60 bars is inside the strategy's own warmup (120-bar lookback); no
        # decision bar can fire in here regardless of the gate.
        early_cutoff = market[60].timestamp.date()
        result = simulate(
            "TOP4_SECTOR", 4, 4, True, series, market, cfg,
            decision_window=(history_start, early_cutoff),
        )
        assert result["trades"] == []

    def test_full_history_window_reproduces_ungated_baseline(self):
        series, market = _load_real_series()
        cfg = PortfolioConfig()
        history_start = market[0].timestamp.date()
        history_end = market[-1].timestamp.date() + timedelta(days=1)

        baseline = simulate("TOP4_SECTOR", 4, 4, True, series, market, cfg, decision_window=None)
        gated = simulate(
            "TOP4_SECTOR", 4, 4, True, series, market, cfg,
            decision_window=(history_start, history_end),
        )
        assert len(baseline["trades"]) > 0
        assert [t["net_pnl"] for t in gated["trades"]] == [t["net_pnl"] for t in baseline["trades"]]

    def test_narrowing_the_window_never_increases_trade_count(self):
        series, market = _load_real_series()
        cfg = PortfolioConfig()
        history_start = market[0].timestamp.date()
        midpoint = market[len(market) // 2].timestamp.date()
        history_end = market[-1].timestamp.date() + timedelta(days=1)

        full = simulate(
            "TOP4_SECTOR", 4, 4, True, series, market, cfg,
            decision_window=(history_start, history_end),
        )
        half = simulate(
            "TOP4_SECTOR", 4, 4, True, series, market, cfg,
            decision_window=(history_start, midpoint),
        )
        assert len(half["trades"]) <= len(full["trades"])


# ---------------------------------------------------------------------------
# End-to-end smoke test on a small synthetic dataset: validates orchestration
# (fold structure, JSON-serializable output, aggregate/bootstrap wiring)
# without the cost of the full 20-symbol / 3-year / 3-variant real run.
# ---------------------------------------------------------------------------

def _synthetic_series(symbols: list[str], bars: int, seed: int = 7):
    import random
    from datetime import datetime, time as dtime, timedelta as td
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    rng = random.Random(seed)
    session_hours = [dtime(h, 15) for h in (9, 10, 11, 12, 13, 14)]
    series = {}
    market = []
    day = date(2023, 4, 3)
    price_by_symbol = {symbol: 100.0 + 10 * i for i, symbol in enumerate(symbols)}
    market_price = 1000.0
    produced = 0
    while produced < bars:
        if day.weekday() < 5:
            for hour_time in session_hours:
                ts = datetime.combine(day, hour_time, tzinfo=ist)
                market_price *= 1 + rng.uniform(-0.003, 0.0035)
                market.append(Candle(ts, market_price, market_price * 1.001,
                                     market_price * 0.999, market_price, 1_000_000))
                for symbol in symbols:
                    price_by_symbol[symbol] *= 1 + rng.uniform(-0.004, 0.0045)
                    price = price_by_symbol[symbol]
                    series.setdefault(symbol, []).append(Candle(
                        ts, price, price * 1.002, price * 0.998, price,
                        rng.uniform(50_000, 150_000),
                    ))
                produced += 1
        day += td(days=1)
    return series, market


class TestRunAnchoredWalkForwardSmoke:
    def test_produces_well_formed_json_serializable_report(self):
        symbols = [s for s in list(SECTORS)[:3]]
        series, market = _synthetic_series(symbols, bars=900)
        cfg = PortfolioConfig(starting_capital=100_000.0)
        windows = (
            (date(2023, 6, 1), date(2023, 9, 1)),
            (date(2023, 9, 1), date(2023, 12, 1)),
        )
        # Only the frozen universe's symbols are valid SECTORS keys; give the
        # harness the full sector map's series shape by only including the
        # symbols this synthetic dataset actually generated.
        report = run_anchored_walk_forward(
            {s: series[s] for s in symbols}, market, cfg, windows=windows,
        )
        import json
        json.dumps(report)  # must not raise

        assert report["research_version"] == "V10_ANCHORED_WALK_FORWARD"
        assert report["total_folds"] == 2
        assert len(report["folds"]) == 2
        for fold in report["folds"]:
            assert fold["selected_variant"] in {"TOP1", "TOP2_SECTOR", "TOP4_SECTOR"}
            assert set(fold["training_stats_by_variant"]) == {"TOP1", "TOP2_SECTOR", "TOP4_SECTOR"}
        assert "aggregate_out_of_sample" in report
        assert "aggregate_bootstrap" in report
        assert report["limitations"]
