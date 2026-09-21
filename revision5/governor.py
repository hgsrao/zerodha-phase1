"""
Revision 5 frozen five-bay governor configuration.

All closed-loop trade feedback uses R-multiples:
    +1.0 = +1R
    -1.0 = -1R

The +0.30R target is a fixed Revision-5 design setpoint supplied for this
architecture. It is NOT claimed to be calibrated or profitability-proven.

PID coefficients never self-modify during certified replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import fmean
from typing import Dict

from revision5.topology import (
    BAY_IDS,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)


@dataclass(frozen=True)
class BayGovernorSpec:
    bay_id: str
    droop_r: float

    kp: float
    ki: float
    kd: float

    base_z: float
    z_exit: float

    atr_barrier: float
    target_m: float
    capital_weight: float

    target_r: float = 0.30

    outcome_window: int = 6
    integral_clamp: float = 3.0

    dynamic_offset_min: float = -0.45
    dynamic_offset_max: float = 0.15

    grid_droop_gain: float = 0.05
    grid_droop_max: float = 0.25


BAY_GOVERNOR_SPECS: Dict[str, BayGovernorSpec] = {

    GTG1_HEAVY_INDUSTRY: BayGovernorSpec(
        bay_id=GTG1_HEAVY_INDUSTRY,
        droop_r=0.040,
        kp=1.20,
        ki=0.05,
        kd=0.30,
        base_z=-1.90,
        z_exit=0.35,
        atr_barrier=1.80,
        target_m=3.0,
        capital_weight=0.25,
    ),

    GTG2_TECH_TELECOM: BayGovernorSpec(
        bay_id=GTG2_TECH_TELECOM,
        droop_r=0.040,
        kp=1.50,
        ki=0.08,
        kd=0.60,
        base_z=-2.00,
        z_exit=0.50,
        atr_barrier=2.10,
        target_m=3.2,
        capital_weight=0.20,
    ),

    CSTG1_BFSI: BayGovernorSpec(
        bay_id=CSTG1_BFSI,
        droop_r=0.055,
        kp=0.90,
        ki=0.12,
        kd=0.15,
        base_z=-1.85,
        z_exit=0.30,
        atr_barrier=1.50,
        target_m=2.8,
        capital_weight=0.25,
    ),

    CSTG2_CONSUMER_AUTO: BayGovernorSpec(
        bay_id=CSTG2_CONSUMER_AUTO,
        droop_r=0.055,
        kp=1.00,
        ki=0.07,
        kd=0.25,
        base_z=-1.95,
        z_exit=0.40,
        atr_barrier=1.65,
        target_m=2.9,
        capital_weight=0.18,
    ),

    BPSTG_HEALTHCARE: BayGovernorSpec(
        bay_id=BPSTG_HEALTHCARE,
        droop_r=0.075,
        kp=0.80,
        ki=0.04,
        kd=0.20,
        base_z=-1.80,
        z_exit=0.40,
        atr_barrier=1.40,
        target_m=2.5,
        capital_weight=0.12,
    ),
}


if set(BAY_GOVERNOR_SPECS) != set(BAY_IDS):
    raise RuntimeError(
        "Governor specifications do not exactly match Revision-5 bays"
    )

if abs(
    sum(x.capital_weight for x in BAY_GOVERNOR_SPECS.values()) - 1.0
) > 1e-12:
    raise RuntimeError("Revision-5 initial capital weights must sum to 1.0")


class BayTurbineClosedLoopGovernor:
    """
    Frozen Mark-V-style closed-loop governor.

    Configuration remains immutable.
    Only controller state evolves from realized R feedback.
    """

    def __init__(self, spec: BayGovernorSpec):
        self.spec = spec

        # Native Revision-5 runtime operating values.
        #
        # spec remains the immutable certified/base specification.
        # These members are the values actually consumed by the
        # governor PID/droop equations.
        self.runtime_target_r = float(spec.target_r)
        self.runtime_outcome_window = int(
            spec.outcome_window
        )
        self.runtime_integral_clamp = float(
            spec.integral_clamp
        )

        self.runtime_kp = float(spec.kp)
        self.runtime_ki = float(spec.ki)
        self.runtime_kd = float(spec.kd)

        self.runtime_base_z = float(spec.base_z)
        self.runtime_droop_r = float(spec.droop_r)

        self.runtime_dynamic_offset_min = float(
            spec.dynamic_offset_min
        )
        self.runtime_dynamic_offset_max = float(
            spec.dynamic_offset_max
        )

        self.runtime_grid_droop_gain = float(
            spec.grid_droop_gain
        )
        self.runtime_grid_droop_max = float(
            spec.grid_droop_max
        )

        self.integral_error = 0.0
        self.last_error = 0.0
        self.last_control_u = 0.0

        self.history_r: list[float] = []

    def register_trade(self, realized_r: float) -> float:
        if not isfinite(realized_r):
            raise ValueError("realized_r must be finite")

        self.history_r.append(float(realized_r))

        if len(self.history_r) > self.runtime_outcome_window:
            self.history_r.pop(0)

        process_r = fmean(self.history_r)
        error = self.runtime_target_r - process_r

        self.integral_error = max(
            -self.runtime_integral_clamp,
            min(
                self.runtime_integral_clamp,
                self.integral_error + error,
            ),
        )

        derivative = error - self.last_error
        self.last_error = error

        self.last_control_u = (
            self.runtime_kp * error
            + self.runtime_ki * self.integral_error
            + self.runtime_kd * derivative
        )

        return self.last_control_u

    def apply_runtime_profile(self, profile) -> None:
        """
        Apply the current Revision-5 governor operating profile.

        The immutable BayGovernorSpec is never mutated.
        """
        values = (
            profile.target_r,
            profile.integral_clamp,
            profile.grid_droop_gain,
            profile.grid_droop_max,
            profile.kp,
            profile.ki,
            profile.kd,
            profile.base_z,
            profile.droop_r,
            profile.dynamic_offset_min,
            profile.dynamic_offset_max,
        )

        if not all(isfinite(float(v)) for v in values):
            raise ValueError(
                "governor runtime profile contains "
                "non-finite value"
            )

        if int(profile.outcome_window) <= 0:
            raise ValueError(
                "outcome_window must be positive"
            )

        if float(profile.integral_clamp) <= 0.0:
            raise ValueError(
                "integral_clamp must be positive"
            )

        if float(profile.droop_r) <= 0.0:
            raise ValueError(
                "droop_r must be positive"
            )

        self.runtime_target_r = float(
            profile.target_r
        )
        self.runtime_outcome_window = int(
            profile.outcome_window
        )
        self.runtime_integral_clamp = float(
            profile.integral_clamp
        )

        self.runtime_kp = float(profile.kp)
        self.runtime_ki = float(profile.ki)
        self.runtime_kd = float(profile.kd)

        self.runtime_base_z = float(profile.base_z)
        self.runtime_droop_r = float(profile.droop_r)

        self.runtime_dynamic_offset_min = float(
            profile.dynamic_offset_min
        )
        self.runtime_dynamic_offset_max = float(
            profile.dynamic_offset_max
        )

        self.runtime_grid_droop_gain = float(
            profile.grid_droop_gain
        )
        self.runtime_grid_droop_max = float(
            profile.grid_droop_max
        )

        # If a new dynamic window is shorter than retained history,
        # trim immediately rather than allowing stale outcomes to
        # continue influencing the controller.
        while (
            len(self.history_r)
            > self.runtime_outcome_window
        ):
            self.history_r.pop(0)

        # Re-clamp existing integrator state to the new runtime limit.
        self.integral_error = max(
            -self.runtime_integral_clamp,
            min(
                self.runtime_integral_clamp,
                self.integral_error,
            ),
        )

    def dynamic_z(
        self,
        grid_return_fraction: float = 0.0,
    ) -> float:
        if not isfinite(grid_return_fraction):
            raise ValueError(
                "grid_return_fraction must be finite"
            )

        feedback_offset = max(
            self.runtime_dynamic_offset_min,
            min(
                self.runtime_dynamic_offset_max,
                -0.25 * self.last_control_u,
            ),
        )

        adverse_grid = max(
            0.0,
            -float(grid_return_fraction),
        )

        droop_penalty = (
            adverse_grid
            / self.runtime_droop_r
            * self.runtime_grid_droop_gain
        )

        droop_penalty = min(
            droop_penalty,
            self.runtime_grid_droop_max,
        )

        return (
            self.runtime_base_z
            + feedback_offset
            - droop_penalty
        )

    def snapshot(self) -> dict:
        return {
            "bay_id": self.spec.bay_id,
            "target_r": self.runtime_target_r,
            "droop_r": self.runtime_droop_r,
            "kp": self.runtime_kp,
            "ki": self.runtime_ki,
            "kd": self.runtime_kd,
            "base_z": self.runtime_base_z,
            "integral_error": self.integral_error,
            "last_error": self.last_error,
            "last_control_u": self.last_control_u,
            "history_r": tuple(self.history_r),
        }


def build_fleet_governors():
    return {
        bay_id: BayTurbineClosedLoopGovernor(spec)
        for bay_id, spec in BAY_GOVERNOR_SPECS.items()
    }
