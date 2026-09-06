#!/usr/bin/env python3
"""Passes several real signals through Boxes 1-5 of the external engine and
prints exactly what each one produces -- real code, real INFY data, not a
re-derivation. Used to judge, box by box, whether each one is structurally
a controller that needs a closed feedback loop, or is correctly open-loop
by its own nature.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.contracts import EffectiveConfig, MarketSnapshot
from revision2.dataset_manifest import DatasetManifest
from revision2.boxes import DataIngestionBox
from revision2_external.data_certification_pandera import certify_bars
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from revision2_external.startup_validation import validate_runtime_parameters, validate_safety_contract

SYMBOL = "INFY"


def main():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}

    print("=" * 100)
    print("BOX 1 -- StartupCapabilityLock (Pydantic)")
    print("=" * 100)
    errors_valid = validate_runtime_parameters(registry, values)
    print(f"real, valid config -> errors: {errors_valid}")
    bad_values = dict(values)
    bad_values["entry_confidence_threshold"] = 5.0  # real registry range is 0.3-0.8
    errors_invalid = validate_runtime_parameters(registry, bad_values)
    print(f"one real out-of-range value (entry_confidence_threshold=5.0) -> errors: {errors_invalid}")
    safety_values = {name: spec.default for name, spec in registry.safety_params.items()}
    safety_errors = validate_safety_contract(registry, safety_values)
    print(f"real safety contract -> errors: {safety_errors}")

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    full_frame = loader._load_symbol_csv(SYMBOL)
    # Real window this session already traced by hand (2023-07-13
    # 15:10-15:21): known to contain real, non-degenerate signal activity
    # (two real ID approvals), unlike day-1's very first hour.
    mask = full_frame["timestamp"].dt.strftime("%Y-%m-%d %H:%M") >= "2023-07-13 15:14"
    frame = full_frame[mask].head(200).reset_index(drop=True)
    warmup_frame = full_frame[full_frame["timestamp"] < frame["timestamp"].iloc[0]].tail(60).reset_index(drop=True)

    config = EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)

    print()
    print("=" * 100)
    print("BOX 2 -- DataIngestion (unchanged, in-house allow/deny filter)")
    print("=" * 100)
    ingestion = DataIngestionBox()
    for sym in [SYMBOL, "MARUTI", "NOTASYMBOL_XYZ"]:
        admitted, reason, _ = ingestion.admit(sym, config)
        print(f"admit('{sym}') -> admitted={admitted}, reason={reason!r}")

    print()
    print("=" * 100)
    print("BOX 3 -- L2DataCertifier (Pandera)")
    print("=" * 100)
    good_frame, good_audit = certify_bars(frame.copy())
    print(f"real, valid 200-bar window -> {len(good_frame)} bars certified, audit={good_audit}")
    bad_frame = frame.copy()
    bad_frame.loc[50, "high"] = bad_frame.loc[50, "low"] - 1.0  # real, invalid OHLC geometry
    try:
        certified_bad, bad_audit = certify_bars(bad_frame)
        print(f"one bar with high < low -> {len(certified_bad)} bars certified (dropped {200-len(certified_bad)}), audit={bad_audit}")
    except Exception as exc:
        print(f"one bar with high < low -> certify_bars raised: {exc}")

    print()
    print("=" * 100)
    print("BOX 4 -- PredictiveAnalytics (TA-Lib), 5 real consecutive signals")
    print("=" * 100)
    combined = pd.concat([warmup_frame, frame], ignore_index=True)
    warmup_len = len(warmup_frame)
    pa = TALibPredictiveAnalyticsBox()
    pa.calibrate(SYMBOL, combined.iloc[:warmup_len])
    signals = []
    for bar_idx in range(warmup_len, warmup_len + 5):
        snapshot = MarketSnapshot(symbol=SYMBOL, timestamp=str(combined.iloc[bar_idx]["timestamp"]),
                                    bars=combined.iloc[max(0, bar_idx - 19):bar_idx + 1])
        signal, _ = pa.evaluate(snapshot, config)
        signals.append(signal)
        print(f"bar {bar_idx} ({signal.timestamp}): direction={signal.direction:+d}  "
              f"confidence={signal.confidence:.4f}  exit_confidence={signal.exit_confidence:.4f}  "
              f"momentum={signal.momentum:.4f}  volatility={signal.volatility:.6f}  "
              f"quality_band={signal.quality_band}")

    print()
    print("=" * 100)
    print("BOX 5 -- IntelligentDiscrimination (HMM regime + threshold gate), same 5 signals")
    print("=" * 100)
    id_box = HMMIntelligentDiscriminationBox()
    id_box.calibrate(SYMBOL, combined.iloc[:warmup_len])
    for bar_idx, signal in zip(range(warmup_len, warmup_len + 5), signals):
        decision, _ = id_box.evaluate(signal, config, latest_close=float(combined.iloc[bar_idx]["close"]))
        print(f"bar {bar_idx}: approved={decision.approved}  reason={decision.reason!r}  "
              f"confidence={decision.confidence:.4f}  risk_reward={decision.risk_reward_ratio:.4f}  "
              f"timing_quality={decision.timing_quality:.4f}")


if __name__ == "__main__":
    main()
