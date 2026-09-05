import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, PASignal
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox


def _config():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    return EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)


def _calm_then_shock_bars(n_calm=200, n_shock=100, seed=5):
    rng = np.random.default_rng(seed)
    price = 1000.0
    closes = []
    for _ in range(n_calm):
        price *= 1 + rng.normal(0, 0.001)
        closes.append(price)
    for _ in range(n_shock):
        price *= 1 + rng.normal(0, 0.02)
        closes.append(price)
    return pd.DataFrame({"close": closes})


def _signal(confidence=0.8, quality_band="green"):
    return PASignal(symbol="TEST", timestamp="t", direction=1, confidence=confidence, momentum=0.5,
                     volatility=0.01, vwap_deviation=0.1, volume_confirmation=0.2, quality_band=quality_band)


def test_calm_regime_can_approve_a_strong_signal():
    box = HMMIntelligentDiscriminationBox()
    bars = _calm_then_shock_bars()
    box.calibrate("TEST", bars.iloc[:120])
    # Feed a run of calm closes so the classifier settles into the calm state.
    decision = None
    for close in bars["close"].iloc[120:180]:
        decision, _ = box.evaluate(_signal(), _config(), latest_close=close)
    assert decision.approved, f"expected calm-regime approval, got: {decision.reason}"


def test_stressed_regime_vetoes_entry_even_with_a_strong_signal():
    box = HMMIntelligentDiscriminationBox()
    bars = _calm_then_shock_bars()
    box.calibrate("TEST", bars.iloc[:120])
    decision = None
    # Warm the feature window on calm bars first, then run into the shock segment.
    for close in bars["close"].iloc[120:200]:
        box.evaluate(_signal(), _config(), latest_close=close)
    for close in bars["close"].iloc[200:280]:
        decision, _ = box.evaluate(_signal(confidence=0.95, quality_band="green"), _config(), latest_close=close)
    assert not decision.approved
    assert "stressed" in decision.reason.lower()
