import sys
sys.path.insert(0, ".")

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.contracts import EffectiveConfig, TradePlan
from revision2.dataset_manifest import DatasetManifest
from revision2_external.position_sizing_pyportfolioopt import (
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
        prices[symbol] = frame.tail(2000).reset_index(drop=True)["close"]

    weights = compute_portfolio_weights(prices)
    assert set(weights.keys()) == set(symbols)
    assert all(w >= -1e-9 for w in weights.values())
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_degenerate_input_falls_back_to_equal_weight_not_a_crash():
    # Too few bars for a meaningful covariance estimate.
    prices = {"A": pd.Series([100.0, 100.1, 100.2]), "B": pd.Series([50.0, 50.05, 50.1])}
    weights = compute_portfolio_weights(prices)
    assert weights == {"A": 0.5, "B": 0.5}


def test_sizing_respects_the_optimized_weight_as_a_ceiling():
    box = PyPortfolioOptPositionManagerBox()
    config = _config()
    plan = TradePlan(side="BUY", entry_price=100.0, stop_price=95.0, target_price=110.0,
                      minimum_hold_bars=2, maximum_hold_bars=20)

    # A trivially small weight should cap quantity well below the
    # capital_per_trade_fraction-driven raw size.
    tiny_weight_qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="X",
                                    portfolio_weights={"X": 0.001}, max_exposure_per_symbol_fraction=1.0)
    generous_weight_qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="X",
                                        portfolio_weights={"X": 0.5}, max_exposure_per_symbol_fraction=1.0)
    assert tiny_weight_qty < generous_weight_qty
    assert tiny_weight_qty >= 0


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
                       portfolio_weights={"X": 1.0}, max_exposure_per_symbol_fraction=0.15)
    notional = qty * plan.entry_price
    assert notional <= 1_000_000.0 * 0.15 + 1e-6


def test_zero_weight_for_an_unlisted_symbol_zeroes_sizing():
    box = PyPortfolioOptPositionManagerBox()
    config = _config()
    plan = TradePlan(side="BUY", entry_price=100.0, stop_price=95.0, target_price=110.0,
                      minimum_hold_bars=2, maximum_hold_bars=20)
    qty, _ = box.size(plan, 1_000_000.0, 1.0, config, symbol="NOT_IN_WEIGHTS", portfolio_weights={}, max_exposure_per_symbol_fraction=1.0)
    assert qty == 0
