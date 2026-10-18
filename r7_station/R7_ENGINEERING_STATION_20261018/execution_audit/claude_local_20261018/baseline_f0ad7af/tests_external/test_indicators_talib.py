import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.boxes import PredictiveAnalyticsBox
from revision2.contracts import EffectiveConfig, MarketSnapshot
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox


def _bars(rows=400, seed=7):
    rng = np.random.default_rng(seed)
    price = 1000.0
    idx = pd.date_range("2024-01-02 09:15", periods=rows, freq="min", tz="Asia/Kolkata")
    data = []
    for i in range(rows):
        drift = 0.0012 * (1 if (i // 30) % 2 == 0 else -1)
        shock = rng.normal(0, 0.003)
        price = max(1.0, price * (1 + drift + shock))
        open_ = price * (1 - 0.0004)
        high = max(open_, price) * 1.003
        low = min(open_, price) * 0.997
        volume = max(100, int(4000 + rng.normal(0, 500)))
        data.append({"timestamp": idx[i], "open": open_, "high": high, "low": low, "close": price, "volume": volume})
    return pd.DataFrame(data)


def _config():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    return EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256), registry


def test_talib_pa_runs_and_produces_finite_signals_on_real_shape_data():
    bars = _bars()
    config, _ = _config()
    box = TALibPredictiveAnalyticsBox()
    box.calibrate("TEST", bars.iloc[:60])
    for bar_idx in range(60, 120):
        snapshot = MarketSnapshot(symbol="TEST", timestamp=str(bars.iloc[bar_idx]["timestamp"]), bars=bars.iloc[:bar_idx + 1])
        signal, trace = box.evaluate(snapshot, config)
        assert np.isfinite(signal.confidence)
        assert np.isfinite(signal.momentum)
        assert np.isfinite(signal.volatility)
        assert signal.direction in (-1, 0, 1)
        assert len(trace) > 0


def test_talib_atr_and_original_atr_are_correlated_but_not_identical():
    # Documents the real, expected difference: talib.ATR uses Wilder
    # smoothing, the original box uses a simple mean of True Range. Same
    # underlying quantity, genuinely different numbers -- this proves that
    # rather than asserting it away.
    bars = _bars()
    config, _ = _config()

    original = PredictiveAnalyticsBox()
    original.calibrate("TEST", bars.iloc[:60])
    talib_box = TALibPredictiveAnalyticsBox()
    talib_box.calibrate("TEST", bars.iloc[:60])

    original_vols, talib_vols = [], []
    for bar_idx in range(60, 200):
        snapshot = MarketSnapshot(symbol="TEST", timestamp=str(bars.iloc[bar_idx]["timestamp"]), bars=bars.iloc[max(0, bar_idx - 299):bar_idx + 1])
        o_signal, _ = original.evaluate(snapshot, config)
        t_signal, _ = talib_box.evaluate(snapshot, config)
        original_vols.append(o_signal.volatility)
        talib_vols.append(t_signal.volatility)

    correlation = np.corrcoef(original_vols, talib_vols)[0, 1]
    assert correlation > 0.6, f"expected the two ATR variants to be meaningfully correlated, got {correlation}"
    assert original_vols != talib_vols, "expected genuinely different numbers (Wilder vs simple-mean smoothing)"
