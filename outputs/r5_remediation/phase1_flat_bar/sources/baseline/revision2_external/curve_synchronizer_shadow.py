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
from revision2_external.curve_entry_pid_shadow import CurveEntryPidShadow


def _wrapped_delta_degrees(current: float, previous: float) -> float:
    """Signed shortest angular displacement in degrees, in [-180, 180)."""
    return float((current - previous + 180.0) % 360.0 - 180.0)


class CurveSynchronizerShadow:
    """Extract causal curve state and queue conservative reversal hypotheses."""

    def __init__(
        self, ledger: StudyEntryShadowLedger, min_history: int = 64,
        phase_center_degrees: float = 0.0, phase_tolerance_degrees: float = 10.0,
        amplitude_range_atr: tuple[float, float] = (0.10, 2.00),
        phase_velocity_range: tuple[float, float] = (1.0, 20.0),
        minimum_volume_ratio: float = 1.0, admission_policy: Any | None = None,
        entry_pid: CurveEntryPidShadow | None = None, phase_setpoints: Any | None = None,
    ) -> None:
        self.ledger = ledger
        self.min_history = max(64, int(min_history))
        self.phase_center_degrees = float(phase_center_degrees)
        self.phase_tolerance_degrees = abs(float(phase_tolerance_degrees))
        self.amplitude_range_atr = tuple(map(float, amplitude_range_atr))
        self.phase_velocity_range = tuple(map(float, phase_velocity_range))
        self.minimum_volume_ratio = float(minimum_volume_ratio)
        self.admission_policy = admission_policy
        self.entry_pid = entry_pid
        self.phase_setpoints = phase_setpoints

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
        phase_error = _wrapped_delta_degrees(phase, self.phase_center_degrees)
        valid_phase = bool(np.isfinite(phase) and np.isfinite(prior_phase))

        side = None
        # Phase is a locator/telemetry variable, not an assumed universal
        # "zero means reversal" rule.  Causal slope reversal is the actuator
        # candidate; a stable phase estimate, non-noise amplitude and normal
        # or greater participation are the shadow confirmation.
        stable_cycle = valid_phase and self.phase_velocity_range[0] <= abs(phase_velocity) <= self.phase_velocity_range[1]
        phase_in_band = valid_phase and abs(phase_error) <= self.phase_tolerance_degrees
        participating = volume_ratio >= self.minimum_volume_ratio
        meaningful = self.amplitude_range_atr[0] <= amplitude_r <= self.amplitude_range_atr[1]
        admitted = self.admission_policy.allow_entry() if self.admission_policy is not None else True
        if stable_cycle and participating and meaningful and admitted:
            if prior_slope_r <= 0.0 < slope_r:
                side = "BUY"
            elif prior_slope_r >= 0.0 > slope_r:
                side = "SELL"
        newton_update = None
        if self.phase_setpoints is not None and valid_phase:
            # At this completed bar, the previous bar's turn is confirmed;
            # update from that previous phase only, with no future bar.
            if prior_slope_r <= 0.0 < slope_r:
                newton_update = self.phase_setpoints.update(symbol, "BUY", prior_phase)
            elif prior_slope_r >= 0.0 > slope_r:
                newton_update = self.phase_setpoints.update(symbol, "SELL", prior_phase)
            if side is not None:
                target = self.phase_setpoints.target(symbol, side)
                if target is not None:
                    phase_error = _wrapped_delta_degrees(phase, target)
                    phase_in_band = abs(phase_error) <= self.phase_tolerance_degrees
        setup_extreme = float(low[-1]) if side == "BUY" else (float(high[-1]) if side == "SELL" else None)
        observation.update({
            "curve_ready": valid_phase, "phase_angle_degrees": phase if valid_phase else None,
            "phase_error_degrees": phase_error if valid_phase else None,
            "phase_velocity_degrees_per_bar": phase_velocity if valid_phase else None,
            "cycle_amplitude_atr": amplitude_r, "slope_r": slope_r,
            "prior_slope_r": prior_slope_r, "volume_ratio": volume_ratio,
            "stable_cycle": stable_cycle, "participating": participating,
            "meaningful_amplitude": meaningful, "phase_in_band": phase_in_band,
            "newton_phase_update": newton_update,
            "dynamic_phase_target_degrees": (self.phase_setpoints.target(symbol, side) if self.phase_setpoints is not None and side is not None else self.phase_center_degrees),
            "daily_pnl_admission": admitted, "curve_reversal_side": side,
        })
        if self.entry_pid is not None:
            if side is not None and self.phase_setpoints is not None:
                target = self.phase_setpoints.target(symbol, side)
                if target is not None:
                    self.entry_pid.phase_center = target
            pid_state = self.entry_pid.evaluate(observation)
            observation["entry_pid"] = pid_state
            # In this shadow experiment a candidate must meet all three
            # synchronizer ranges. The PID multiplier is logged, never used
            # to size an order.
            if not pid_state["synchronized"]:
                side = None
                observation["curve_reversal_side"] = None
        elif not phase_in_band:
            side = None
            observation["curve_reversal_side"] = None
        self.ledger.observations.append(observation)
        if side and setup_extreme is not None:
            self.ledger.schedule(
                symbol=symbol, index=index, side=side, setup_extreme=setup_extreme,
                atr=current_atr, observation=observation,
            )
        return observation
