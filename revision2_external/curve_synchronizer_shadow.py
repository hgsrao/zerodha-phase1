"""Causal, single-symbol curve synchronizer for shadow research only.

This is *not* the macro grid synchronizer.  It measures SUNPHARMA's own
completed-bar curve: Hilbert dominant-cycle phase, phase velocity, detrended
amplitude, price-slope reversal, and volume participation.  A detected event
is queued for a next-bar shadow fill; it never reaches an engine gate or
broker.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd
import talib

from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def _wrapped_delta_degrees(current: float, previous: float) -> float:
    """Signed shortest angular displacement in degrees, in [-180, 180)."""
    return float((current - previous + 180.0) % 360.0 - 180.0)


class CurveSynchronizerShadow:
    """Extract causal curve state and queue conservative reversal hypotheses."""

    def __init__(self, ledger: StudyEntryShadowLedger, min_history: int = 64) -> None:
        self.ledger = ledger
        self.min_history = max(64, int(min_history))

    def observe(self, symbol: str, index: int, timestamp: object, bar: Any, history: pd.DataFrame, atr: float) -> Dict[str, Any]:
        close = history["close"].to_numpy(dtype=float)
        volume = history["volume"].to_numpy(dtype=float)
        high = history["high"].to_numpy(dtype=float)
        low = history["low"].to_numpy(dtype=float)
        observation: Dict[str, Any] = {
            "timestamp": str(timestamp), "symbol": symbol, "index": index,
            "sensor": "single_symbol_curve_synchronizer", "curve_ready": False,
        }
        if len(close) < self.min_history:
            self.ledger.observations.append(observation)
            return observation

        # All values derive from `history`, which ends at the completed
        # decision bar.  No subsequent bar is used in either detection or
        # normalization.
        ema = talib.EMA(close, timeperiod=20)
        phase_series = talib.HT_DCPHASE(close)
        phase, prior_phase = float(phase_series[-1]), float(phase_series[-2])
        current_atr = max(float(atr), 1e-6)
        slope_r = float((close[-1] - close[-2]) / current_atr)
        prior_slope_r = float((close[-2] - close[-3]) / current_atr)
        amplitude_r = abs(float(close[-1] - ema[-1])) / current_atr if np.isfinite(ema[-1]) else 0.0
        volume_mean = float(np.mean(volume[-21:-1])) if len(volume) > 21 else float(np.mean(volume[:-1]))
        volume_ratio = float(volume[-1] / max(volume_mean, 1e-12))
        phase_velocity = _wrapped_delta_degrees(phase, prior_phase)
        valid_phase = bool(np.isfinite(phase) and np.isfinite(prior_phase))

        side = None
        # Phase is a locator/telemetry variable, not an assumed universal
        # "zero means reversal" rule.  Causal slope reversal is the actuator
        # candidate; a stable phase estimate, non-noise amplitude and normal
        # or greater participation are the shadow confirmation.
        stable_cycle = valid_phase and 0.0 < abs(phase_velocity) <= 90.0
        participating = volume_ratio >= 1.0
        meaningful = amplitude_r >= 0.10
        if stable_cycle and participating and meaningful:
            if prior_slope_r <= 0.0 < slope_r:
                side = "BUY"
            elif prior_slope_r >= 0.0 > slope_r:
                side = "SELL"
        setup_extreme = float(low[-1]) if side == "BUY" else (float(high[-1]) if side == "SELL" else None)
        observation.update({
            "curve_ready": valid_phase, "phase_angle_degrees": phase if valid_phase else None,
            "phase_velocity_degrees_per_bar": phase_velocity if valid_phase else None,
            "cycle_amplitude_atr": amplitude_r, "slope_r": slope_r,
            "prior_slope_r": prior_slope_r, "volume_ratio": volume_ratio,
            "stable_cycle": stable_cycle, "participating": participating,
            "meaningful_amplitude": meaningful, "curve_reversal_side": side,
        })
        self.ledger.observations.append(observation)
        if side and setup_extreme is not None:
            self.ledger.schedule(
                symbol=symbol, index=index, side=side, setup_extreme=setup_extreme,
                atr=current_atr, observation=observation,
            )
        return observation
