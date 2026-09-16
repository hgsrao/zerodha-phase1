"""Causal curve-synchronizer input for an entry PID, shadow-only.

Phase has a chosen centre and angular tolerance.  Curve voltage (amplitude)
and frequency (absolute phase velocity) are normalized to the symbol's own
rolling completed-bar medians, so a ±10% band has the same meaning during a
quiet and an active SUNPHARMA interval.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict

import numpy as np
from simple_pid import PID


class CurveEntryPidShadow:
    def __init__(self, phase_center: float = 0.0, phase_tolerance: float = 10.0, relative_tolerance: float = 0.10, baseline_bars: int = 60) -> None:
        self.phase_center = float(phase_center)
        self.phase_tolerance = abs(float(phase_tolerance))
        self.relative_tolerance = abs(float(relative_tolerance))
        self._amplitudes: deque[float] = deque(maxlen=int(baseline_bars))
        self._frequencies: deque[float] = deque(maxlen=int(baseline_bars))
        # simple_pid error = setpoint - measurement. A poor measurement thus
        # produces a positive derate.  We subtract it below, making this a
        # brake only: it can never create an above-baseline multiplier.
        self._pid = PID(Kp=0.35, Ki=0.02, Kd=0.05, setpoint=1.0, sample_time=None, output_limits=(0.0, 0.75))

    @staticmethod
    def _wrap(angle: float) -> float:
        return (angle + 180.0) % 360.0 - 180.0

    def evaluate(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        if not observation.get("curve_ready"):
            return {"entry_pid_ready": False, "entry_timing_multiplier": 0.25, "synchronized": False}
        amplitude = float(observation["cycle_amplitude_atr"])
        frequency = abs(float(observation["phase_velocity_degrees_per_bar"]))
        phase_error = abs(self._wrap(float(observation["phase_angle_degrees"]) - self.phase_center))
        amp_reference = float(np.median(self._amplitudes)) if self._amplitudes else None
        freq_reference = float(np.median(self._frequencies)) if self._frequencies else None
        ready = amp_reference is not None and freq_reference is not None and amp_reference > 0 and freq_reference > 0
        phase_ok = phase_error <= self.phase_tolerance
        amp_error = abs(amplitude / amp_reference - 1.0) if ready else None
        freq_error = abs(frequency / freq_reference - 1.0) if ready else None
        voltage_ok = ready and amp_error <= self.relative_tolerance
        frequency_ok = ready and freq_error <= self.relative_tolerance
        # The PID sees a smooth quality signal; the separate synchronized flag
        # retains the user's explicit three-range condition.
        phase_quality = max(0.0, 1.0 - phase_error / max(self.phase_tolerance, 1e-9))
        voltage_quality = max(0.0, 1.0 - (amp_error or 1.0) / max(self.relative_tolerance, 1e-9)) if ready else 0.0
        frequency_quality = max(0.0, 1.0 - (freq_error or 1.0) / max(self.relative_tolerance, 1e-9)) if ready else 0.0
        measurement = min(phase_quality, voltage_quality, frequency_quality)
        derate = float(self._pid(measurement, dt=1)) if ready else 0.75
        result = {
            "entry_pid_ready": ready, "phase_center_degrees": self.phase_center,
            "phase_tolerance_degrees": self.phase_tolerance, "voltage_relative_tolerance": self.relative_tolerance,
            "frequency_relative_tolerance": self.relative_tolerance, "voltage_reference_atr": amp_reference,
            "frequency_reference_degrees_per_bar": freq_reference, "phase_error_degrees": phase_error,
            "voltage_relative_error": amp_error, "frequency_relative_error": freq_error,
            "phase_in_range": phase_ok, "voltage_in_range": voltage_ok,
            "frequency_in_range": frequency_ok, "entry_pid_measurement": measurement,
            "entry_pid_derate": derate, "entry_timing_multiplier": 1.0 - derate,
            "synchronized": bool(phase_ok and voltage_ok and frequency_ok),
        }
        # Append only after evaluating; current bar is never in its own target.
        self._amplitudes.append(amplitude)
        self._frequencies.append(frequency)
        return result
