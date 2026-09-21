"""BB04 engineering initial values, not calibrated; real TA-Lib consumer tests."""
from collections import deque
import ast
import inspect

import numpy as np
import pandas as pd
import pytest
import talib

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, MarketSnapshot
from revision2_external import indicators_talib as module
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
from revision2_external.startup_validation import validate_runtime_parameters


def config(**overrides):
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    values.update(overrides)
    assert not validate_runtime_parameters(registry, values)
    return EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)


def bars(count=80, price=100.0):
    x = np.arange(count)
    close = price + np.sin(x * 0.6) * 0.3 + x * 0.003
    return pd.DataFrame(dict(
        timestamp=pd.date_range("2026-01-01", periods=count, freq="min"),
        open=close, high=close + 0.2, low=close - 0.2, close=close,
        volume=1000 + np.sin(x * 0.7) * 100 + x,
    ))


def output(cfg, frame=None, history=(), ratio=None):
    frame = bars() if frame is None else frame
    box = TALibPredictiveAnalyticsBox()
    box.calibrate("TEST", frame.iloc[:60], cfg)
    # Controlled calibration state isolates each branch; indicators still run
    # through real TA-Lib and evaluate(), without mocked outputs.
    box._scale["TEST"] = dict(dp_scale=0.02, dv_scale=0.3, baseline_vol=0.01)
    if ratio is not None:
        atr = talib.ATR(frame.high.to_numpy(), frame.low.to_numpy(),
                        frame.close.to_numpy(), timeperiod=20)[-1]
        box._scale["TEST"]["baseline_vol"] = atr / frame.close.iloc[-1] / ratio
    if history:
        box._history["TEST"] = deque(history, maxlen=20)
    return box.evaluate(MarketSnapshot("TEST", "now", frame), cfg)


# Each case varies only the named parameter on identical bars and initial state.
@pytest.mark.parametrize("name,low,high,field,overrides,history,ratio", [
    ("momentum_normalization_divisor", 1.5, 6.0, "momentum", {}, (), None),
    ("pa_vwap_normalization_divisor", 1.5, 6.0, "vwap_deviation", {}, (), None),
    ("pa_volume_normalization_divisor", 1.5, 6.0, "volume_confirmation", {}, (), None),
    ("pa_persistence_threshold_divisor", 1.0, 3.0, "confidence", {}, (), None),
    ("pa_persistence_bonus_gain", 0.0, 0.25, "confidence", {}, (), None),
    ("pa_persistence_bonus_cap", 1.0, 2.5, "confidence",
     {"signal_persistence_requirement": 2.5, "pa_persistence_threshold_divisor": 3.0}, (), None),
    ("pa_direction_activation_fraction", 0.0, 1.0, "direction",
     {"entry_confidence_threshold": 0.3}, (), None),
    ("pa_persistence_lookback", 2, 5, "confidence",
     {"entry_signal_smoothing_window": 1}, (-0.1, -0.1, -0.1, -0.1, 0.1), None),
    ("pa_low_vol_ratio_boundary", 0.5, 0.9, "confidence",
     {"low_vol_regime_multiplier": 0.8, "medium_vol_regime_multiplier": 1.5}, (), 0.7),
    ("pa_high_vol_ratio_boundary", 1.1, 2.0, "confidence",
     {"medium_vol_regime_multiplier": 0.7, "high_vol_regime_multiplier": 1.5}, (), 1.5),
    ("pa_green_confidence_multiplier", 1.0, 1.25, "confidence",
     {"entry_signal_smoothing_window": 8, "pa_persistence_bonus_gain": 0.0},
     (0.88,) * 7, None),
    ("pa_amber_confidence_multiplier", 0.7, 1.0, "confidence",
     {"entry_signal_smoothing_window": 8, "pa_persistence_bonus_gain": 0.0},
     (0.65,) * 7, None),
    ("pa_red_confidence_multiplier", 0.25, 0.75, "confidence", {}, (), None),
])
def test_real_consumer_sensitivity(name, low, high, field, overrides, history, ratio):
    first, trace = output(config(**overrides, **{name: low}), history=history, ratio=ratio)
    second, _ = output(config(**overrides, **{name: high}), history=history, ratio=ratio)
    assert getattr(first, field) != pytest.approx(getattr(second, field))
    assert any(item.parameter == name for item in trace)
    if field == "direction":
        assert first.direction == 1
        assert second.direction == 0


@pytest.mark.parametrize("name,low,high,price", [
    ("pa_atr_absolute_floor", 0.001, 0.01, 0.1),
    ("pa_atr_fallback_price_fraction", 0.001, 0.01, 100.0),
])
def test_atr_fallback_real_volatility(name, low, high, price):
    frame = bars()
    for column in ("open", "high", "low", "close"):
        frame[column] = price
    first, _ = output(config(**{name: low}), frame)
    second, _ = output(config(**{name: high}), frame)
    assert first.volatility != second.volatility
    for value, signal in ((low, first), (high, second)):
        floor = value if name == "pa_atr_absolute_floor" else 0.001
        fraction = value if name == "pa_atr_fallback_price_fraction" else 0.005
        assert signal.volatility == pytest.approx(max(floor, price * fraction) / price)


def test_auto_warmup_real_consumer_sensitivity():
    frame = bars(120)
    # Different variance across the warmup windows, without any injected scales.
    frame.loc[:29, "close"] += np.sin(np.arange(30)) * 2
    frame["high"] = frame.close + 0.2
    frame["low"] = frame.close - 0.2
    snapshot = MarketSnapshot("TEST", "now", frame)
    signals = [TALibPredictiveAnalyticsBox().evaluate(
        snapshot, config(pa_auto_warmup_bars=value))[0] for value in (30, 120)]
    assert signals[0].momentum != pytest.approx(signals[1].momentum)


def test_atr_one_owner_default_explicit_and_runtime_change():
    frame = bars(100)
    frame.loc[40:70, "high"] += np.linspace(0, 5, 31)
    warmup = frame.iloc[:75].copy()
    snapshot = MarketSnapshot("TEST", "now", frame)
    legacy = TALibPredictiveAnalyticsBox()
    legacy.calibrate("TEST", warmup)
    explicit = TALibPredictiveAnalyticsBox()
    explicit.calibrate("TEST", warmup, config())
    assert legacy._scale == explicit._scale
    results = []
    for period in (10, 30):
        cfg = config(atr_calculation_period=period)
        fresh = TALibPredictiveAnalyticsBox()
        fresh.calibrate("TEST", warmup, cfg)
        actual, _ = legacy.evaluate(snapshot, cfg)
        expected, _ = fresh.evaluate(snapshot, cfg)
        assert actual.volatility == expected.volatility
        assert actual.momentum == expected.momentum
        baseline = talib.ATR(warmup.high.to_numpy(), warmup.low.to_numpy(),
                             warmup.close.to_numpy(), timeperiod=period)
        assert legacy._scale["TEST"]["baseline_vol"] == pytest.approx(
            np.nanmean(baseline) / warmup.close.iloc[-1])
        pd.testing.assert_frame_equal(legacy._warmup["TEST"], warmup)
        results.append(actual)
    assert results[0].volatility != pytest.approx(results[1].volatility)


@pytest.mark.parametrize("count", [1, 2, 10])
def test_numerical_safeguards_are_finite(count):
    frame = bars(count)
    for column in ("open", "high", "low", "close"):
        frame[column] = 0.0
    frame["volume"] = 0.0
    signal, _ = TALibPredictiveAnalyticsBox().evaluate(
        MarketSnapshot("TEST", "now", frame), config())
    for field in ("momentum", "confidence", "volatility", "vwap_deviation", "volume_confirmation"):
        assert np.isfinite(getattr(signal, field))


def test_registry_identity_metadata_and_bounds():
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    new = [spec for spec in registry.params.values()
           if "ENGINEERING_INITIAL_VALUE" in spec.notes and "BB04" in spec.notes]
    assert len(new) == 16
    assert len(registry.params) == 114
    assert len(registry.calibratable_names()) == 87
    for spec in new:
        assert spec.black_box == "PA"
        assert spec.minimum <= spec.default <= spec.maximum
        assert spec.calibratable
        assert "NOT_CALIBRATED" in spec.notes
        for invalid in (spec.minimum - 1, spec.maximum + 1):
            values = {key: item.default for key, item in registry.params.items()}
            values[spec.name] = invalid
            assert validate_runtime_parameters(registry, values)


def anonymous_operating_literals(source):
    tree = ast.parse(source)
    allowed = set()
    for node in ast.walk(tree):
        # Explicit context allow-list, not permission for these values anywhere.
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_NUMERICAL_EPSILON"
            for target in node.targets):
            if isinstance(node.value, ast.Constant) and node.value.value == 1e-6:
                allowed.add(id(node.value))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if (ast.unparse(node.left) == "float(roc_series[-1])"
                    and isinstance(node.right, ast.Constant) and node.right.value == 100.0):
                allowed.add(id(node.right))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "max":
            if [ast.unparse(arg) for arg in node.args] == [
                "entry_smoothing", "exit_smoothing", "pa_persistence_lookback", "20"]:
                allowed.add(id(node.args[-1]))
    # 0/1 are identities, sign encodings, indices, clip and TA-Lib domain limits.
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float)
            and node.value not in (0, 1) and id(node) not in allowed]


def test_no_anonymous_operating_literals():
    assert anonymous_operating_literals(inspect.getsource(module)) == []


@pytest.mark.parametrize("fragment", [
    "momentum = momentum_z / 3.0", "atr = max(0.001, close[-1] * 0.005)",
    "direction = entry_threshold * 0.2", "recent = history[-5:]",
    "atr_period = 14", "bonus = 0.1 * min(requirement, 2.0)",
])
def test_literal_audit_detects_operational_regressions(fragment):
    assert anonymous_operating_literals(fragment)
