import sys
sys.path.insert(0, ".")

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.contracts import EffectiveConfig, TradePlan
from revision2.dataset_manifest import DatasetManifest
import revision2_external.position_sizing_pyportfolioopt as sizing_module
from revision2_external.position_sizing_pyportfolioopt import (
    INTRADAY_15MIN_PERIODS_PER_YEAR,
    PyPortfolioOptPositionManagerBox,
    compute_portfolio_weights,
)


def _config():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    return EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)


def test_real_weights_sum_to_one_and_are_nonnegative():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbols = [f.symbol for f in manifest.files[:6]]
    prices = {}
    for symbol in symbols:
        frame = loader._load_symbol_csv(symbol)
        prices[symbol] = frame.tail(2000).set_index("timestamp")["close"]

    weights = compute_portfolio_weights(prices)
    assert set(weights.keys()) == set(symbols)
    assert all(w >= -1e-9 for w in weights.values())
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_degenerate_input_falls_back_to_equal_weight_not_a_crash():
    # Too few bars for a meaningful covariance estimate.
    prices = {"A": pd.Series([100.0, 100.1, 100.2]), "B": pd.Series([50.0, 50.05, 50.1])}
    weights = compute_portfolio_weights(prices)
    assert weights == {"A": 0.5, "B": 0.5}


def test_mvo_uses_completed_15_minute_prices_and_intraday_annualization(monkeypatch):
    captured = {}

    def fake_mean_historical_return(prices, frequency):
        captured["mean_prices"] = prices
        captured["mean_frequency"] = frequency
        return pd.Series(0.01, index=prices.columns)

    class FakeShrinkage:
        def __init__(self, prices, frequency):
            captured["cov_prices"] = prices
            captured["cov_frequency"] = frequency

        def ledoit_wolf(self):
            return pd.DataFrame(
                [[1.0, 0.0], [0.0, 1.0]], index=["A", "B"], columns=["A", "B"],
            )

    class FakeEfficientFrontier:
        def __init__(self, _mu, _cov):
            pass

        def max_sharpe(self):
            return {"A": 0.5, "B": 0.5}

        def clean_weights(self):
            return {"A": 0.5, "B": 0.5}

    monkeypatch.setattr(sizing_module.expected_returns, "mean_historical_return", fake_mean_historical_return)
    monkeypatch.setattr(sizing_module.risk_models, "CovarianceShrinkage", FakeShrinkage)
    monkeypatch.setattr(sizing_module, "EfficientFrontier", FakeEfficientFrontier)

    index = pd.date_range("2024-01-02 09:15", periods=2_000, freq="min", tz="Asia/Kolkata")
    prices = {
        "A": pd.Series(range(100, 2_100), index=index, dtype=float),
        "B": pd.Series(range(200, 2_200), index=index, dtype=float),
    }
    weights = compute_portfolio_weights(prices)

    assert weights == {"A": 0.5, "B": 0.5}
    assert captured["mean_frequency"] == INTRADAY_15MIN_PERIODS_PER_YEAR
    assert captured["cov_frequency"] == INTRADAY_15MIN_PERIODS_PER_YEAR
    assert len(captured["mean_prices"]) < len(index) / 10
    assert captured["mean_prices"].index[-1] < index[-1]  # partial trailing bin excluded


def test_sizing_uses_optimizer_weight_only_as_a_one_way_risk_derater():
    box = PyPortfolioOptPositionManagerBox()
    config = _config()
    plan = TradePlan(side="BUY", entry_price=100.0, stop_price=95.0, target_price=110.0,
                      minimum_hold_bars=2, maximum_hold_bars=20)

    # A below-equal weight can derate ATR risk. An above-equal weight must
    # never increase it above the equal-weight baseline.
    tiny_weight_qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="X",
                                    portfolio_weights={"X": 0.001, "Y": 0.999}, max_exposure_per_symbol_fraction=1.0)
    generous_weight_qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="X",
                                        portfolio_weights={"X": 0.5, "Y": 0.5}, max_exposure_per_symbol_fraction=1.0)
    high_weight_qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="X",
                                    portfolio_weights={"X": 0.99, "Y": 0.01}, max_exposure_per_symbol_fraction=1.0)
    assert tiny_weight_qty < generous_weight_qty
    assert high_weight_qty == generous_weight_qty
    assert tiny_weight_qty >= 0
    assert box.last_sizing_telemetry["conviction_derate"] == 1.0


def test_pre_clips_to_the_hard_safety_exposure_cap_even_with_a_concentrated_weight():
    # Regression: PyPortfolioOpt's max-Sharpe solution can put ~100% of
    # weight into one symbol -- a real optimizer output that would size a
    # position Gate08SymbolConcentration (unchanged, in-house) always
    # rejects. This proves the sizer itself now respects that same hard
    # cap, so trades aren't proposed only to be rejected downstream.
    box = PyPortfolioOptPositionManagerBox()
    config = _config()
    plan = TradePlan(side="BUY", entry_price=100.0, stop_price=95.0, target_price=110.0,
                      minimum_hold_bars=2, maximum_hold_bars=20)
    qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="X",
                       portfolio_weights={"X": 1.0, "Y": 0.0}, max_exposure_per_symbol_fraction=0.15)
    notional = qty * plan.entry_price
    assert notional <= 1_000_000.0 * 0.15 + 1e-6


def test_zero_weight_for_an_unlisted_symbol_zeroes_sizing():
    box = PyPortfolioOptPositionManagerBox()
    config = _config()
    plan = TradePlan(side="BUY", entry_price=100.0, stop_price=95.0, target_price=110.0,
                      minimum_hold_bars=2, maximum_hold_bars=20)
    qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="NOT_IN_WEIGHTS", portfolio_weights={}, max_exposure_per_symbol_fraction=1.0)
    assert qty == 0
