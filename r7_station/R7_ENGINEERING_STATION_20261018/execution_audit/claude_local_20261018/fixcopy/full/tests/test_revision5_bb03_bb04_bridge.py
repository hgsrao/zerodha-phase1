"""BB03/BB04 advisory hand-off regressions."""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, MarketSnapshot, PASignal
from revision2_external.data_certification_pandera import certify_bars
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
from revision5 import supervisory_bridge
from revision5.supervisory_bridge import Revision5SupervisoryBridge


def _bars(count=80):
    timestamps = pd.date_range("2026-01-01 09:15", periods=count, freq="min")
    close = [100.0 + index * 0.4 for index in range(count)]
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": close,
        "high": [value + 0.2 for value in close],
        "low": [value - 0.2 for value in close],
        "close": close,
        "volume": [1000.0 + index * 10.0 for index in range(count)],
    })


def _config(registry, **overrides):
    values = {name: spec.default for name, spec in registry.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)


def test_bb03_fixed_validation_mode_reaches_real_certifier():
    registry = CanonicalParameterRegistry()
    assert registry.get("data_validation_mode").black_box == "L2DataCertifier"
    assert registry.get("data_validation_mode").calibratable is False

    certified, audit = certify_bars(
        _bars(),
        validation_mode="strict",
    )
    assert len(certified) == audit["output_rows"]
    with pytest.raises(ValueError, match="fixed strict"):
        certify_bars(_bars(), validation_mode="permissive")


def test_bb04_parameter_changes_actual_talib_output():
    registry = CanonicalParameterRegistry()
    bars = _bars()
    snapshot = MarketSnapshot("INFY", "2026-01-01T10:34:00", bars)

    base = TALibPredictiveAnalyticsBox()
    base.calibrate("INFY", bars.iloc[:60])
    base_signal, _ = base.evaluate(snapshot, _config(registry))

    derated = TALibPredictiveAnalyticsBox()
    derated.calibrate("INFY", bars.iloc[:60])
    derated_signal, _ = derated.evaluate(
        snapshot,
        _config(registry, base_dp_dt_multiplier=0.5),
    )

    assert base_signal.momentum != derated_signal.momentum


def test_bb03_bb04_outputs_propagate_as_information_only():
    signal = PASignal(
        symbol="INFY",
        timestamp="2026-01-01T10:34:00",
        direction=1,
        confidence=0.7,
        momentum=0.2,
        volatility=0.01,
        vwap_deviation=0.03,
        volume_confirmation=0.4,
        exit_confidence=0.2,
        quality_band="amber",
    )
    result = Revision5SupervisoryBridge().snapshot_bb03_bb04(
        certification_audit={
            "input_rows": 80,
            "output_rows": 80,
            "exact_duplicates_removed": 0,
        },
        analytics_signal=signal,
    )

    assert result.certification.output_rows == 80
    assert result.analytics.momentum == pytest.approx(0.2)
    assert result.analytics.direction == 1
    assert result.certification.authority == "INFORMATION_ONLY"
    assert result.analytics.authority == "INFORMATION_ONLY"
    assert not hasattr(result.analytics, "action")


def test_bridge_has_no_direct_plant_control_dependency():
    source = inspect.getsource(supervisory_bridge)
    for forbidden in (
        "CentralPlantMasterDCS",
        "MachineBay",
        "evaluate_entry(",
        "evaluate_admission(",
        "from revision5.ccpp_unified_plant import",
        "from revision5.governor import",
    ):
        assert forbidden not in source
