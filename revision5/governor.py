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

        # Fast inner governor loop: active only while a position exists.
        self.position_active = False
        self.inner_integral_error = 0.0
        self.inner_last_error = 0.0
        self.inner_last_control_u = 0.0

        # Monotonic protective ratchet. It may advance only.
        self.protected_r_floor = -1.0

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

    def configure_synchronizing_speed_reference(
        self,
        *,
        initial_reference_hz: float,
        minimum_reference_hz: float,
        maximum_reference_hz: float,
        reference_rate_hz_per_second: float,
    ) -> None:
        """
        Install the generator synchronizing speed-reference channel.

        The numerical limits/rate are supplied by configuration.
        No synchronizing operating constants live in this class.
        """
        from math import isfinite

        values = (
            initial_reference_hz,
            minimum_reference_hz,
            maximum_reference_hz,
            reference_rate_hz_per_second,
        )

        if not all(isfinite(float(v)) for v in values):
            raise ValueError(
                "synchronizing governor parameters must be finite"
            )

        if not (
            float(minimum_reference_hz)
            < float(initial_reference_hz)
            < float(maximum_reference_hz)
        ):
            raise ValueError(
                "initial speed reference must lie inside its limits"
            )

        if float(reference_rate_hz_per_second) <= 0.0:
            raise ValueError(
                "speed reference rate must be positive"
            )

        self.sync_speed_reference_hz = float(
            initial_reference_hz
        )

        self.sync_speed_reference_min_hz = float(
            minimum_reference_hz
        )

        self.sync_speed_reference_max_hz = float(
            maximum_reference_hz
        )

        self.sync_speed_reference_rate_hz_per_second = float(
            reference_rate_hz_per_second
        )

    def apply_synchronizing_speed_pulse(
        self,
        *,
        command,
        pulse_width_seconds: float,
    ) -> float:
        """
        Apply one physical-equivalent 25A SPEED RAISE/LOWER pulse.

        Pulse width times configured reference-rate determines the
        reference movement. The machine dynamic model will later turn
        this reference movement into rotor acceleration through
        governor/actuator/turbine/inertia dynamics.
        """
        from math import isfinite

        required = (
            "sync_speed_reference_hz",
            "sync_speed_reference_min_hz",
            "sync_speed_reference_max_hz",
            "sync_speed_reference_rate_hz_per_second",
        )

        if not all(hasattr(self, name) for name in required):
            raise RuntimeError(
                "synchronizing governor channel not configured"
            )

        width = float(pulse_width_seconds)

        if not isfinite(width) or width < 0.0:
            raise ValueError(
                "pulse_width_seconds must be finite and non-negative"
            )

        normalized = str(
            getattr(command, "value", command)
        ).upper()

        delta = (
            self.sync_speed_reference_rate_hz_per_second
            * width
        )

        reference = self.sync_speed_reference_hz

        if normalized in (
            "RAISE",
            "SPEED_RAISE",
        ):
            reference += delta

        elif normalized in (
            "LOWER",
            "SPEED_LOWER",
        ):
            reference -= delta

        elif normalized in (
            "NONE",
            "HOLD",
            "SPEED_NONE",
        ):
            pass

        else:
            raise ValueError(
                f"unsupported synchronizing speed command: "
                f"{normalized!r}"
            )

        reference = max(
            self.sync_speed_reference_min_hz,
            min(
                self.sync_speed_reference_max_hz,
                reference,
            ),
        )

        self.sync_speed_reference_hz = float(reference)

        return self.sync_speed_reference_hz

    def synchronizing_speed_error(
        self,
        measured_frequency_hz: float,
    ) -> float:
        """
        Comparator error used by the coming machine-dynamic model.
        """
        if not hasattr(
            self,
            "sync_speed_reference_hz",
        ):
            raise RuntimeError(
                "synchronizing governor channel not configured"
            )

        return (
            float(self.sync_speed_reference_hz)
            - float(measured_frequency_hz)
        )

    def evaluate_entry_request(
        self,
        *,
        z_score: float,
        grid_return_fraction: float = 0.0,
    ) -> dict:
        """
        Final bay-governor ENTRY authority.

        Upstream boxes provide the requested operating condition.
        The governor owns the final strategic ENTRY/NO_ACTION state.
        """
        if not isfinite(z_score):
            raise ValueError(
                "z_score must be finite"
            )

        threshold = self.dynamic_z(
            grid_return_fraction
        )

        admitted = float(z_score) <= threshold

        return {
            "action": (
                "ENTRY"
                if admitted
                else "NO_ACTION"
            ),
            "reason": (
                "GOVERNOR_ENTRY"
                if admitted
                else "GOVERNOR_ENTRY_NOT_REACHED"
            ),
            "z_score": float(z_score),
            "dynamic_z": float(threshold),
        }

    def begin_position(
        self,
        *,
        hard_stop_r: float = -1.0,
    ) -> None:
        if not isfinite(hard_stop_r):
            raise ValueError(
                "hard_stop_r must be finite"
            )

        self.position_active = True

        self.inner_integral_error = 0.0
        self.inner_last_error = 0.0
        self.inner_last_control_u = 0.0

        self.protected_r_floor = float(
            hard_stop_r
        )

    def confirm_position_closed(self) -> None:
        self.position_active = False
        self.inner_integral_error = 0.0
        self.inner_last_error = 0.0
        self.inner_last_control_u = 0.0

    def evaluate_position_control(
        self,
        *,
        measured_r: float,
        reference_r: float,
        max_favorable_r: float,
        elapsed_bars: int,
        min_hold_bars: int,
        max_hold_bars: int,
        hard_stop_r: float,
        trade_target_r: float,
    ) -> dict:
        """
        Fast closed-loop governor.

        output -> comparator -> PID -> HOLD/EXIT -> output feedback

        protected_r_floor is the one-way ratchet. PID error may change
        sign, but the secured protective floor never moves backwards.
        """
        values = (
            measured_r,
            reference_r,
            max_favorable_r,
            hard_stop_r,
            trade_target_r,
        )

        if not all(
            isfinite(float(v))
            for v in values
        ):
            raise ValueError(
                "governor position feedback "
                "must be finite"
            )

        if elapsed_bars < 0:
            raise ValueError(
                "elapsed_bars must be non-negative"
            )

        if min_hold_bars < 0:
            raise ValueError(
                "min_hold_bars must be non-negative"
            )

        if max_hold_bars <= 0:
            raise ValueError(
                "max_hold_bars must be positive"
            )

        if max_hold_bars < min_hold_bars:
            raise ValueError(
                "max_hold_bars cannot be below "
                "min_hold_bars"
            )

        if not self.position_active:
            self.begin_position(
                hard_stop_r=float(hard_stop_r)
            )

        measured_r = float(measured_r)
        reference_r = float(reference_r)
        max_favorable_r = float(
            max_favorable_r
        )

        error = reference_r - measured_r

        self.inner_integral_error = max(
            -self.runtime_integral_clamp,
            min(
                self.runtime_integral_clamp,
                self.inner_integral_error
                + error,
            ),
        )

        derivative = (
            error
            - self.inner_last_error
        )

        self.inner_last_error = error

        control_u = (
            self.runtime_kp * error
            + self.runtime_ki
            * self.inner_integral_error
            + self.runtime_kd * derivative
        )

        self.inner_last_control_u = (
            float(control_u)
        )

        #
        # One-direction protective ratchet.
        #
        # Step and gap are derived from current dynamic governor values,
        # not from a separate fixed trading constant.
        #
        ratchet_step = (
            abs(self.runtime_target_r)
            / max(
                self.runtime_outcome_window,
                1,
            )
        )

        trailing_gap = max(
            abs(
                self.runtime_dynamic_offset_min
            ),
            abs(self.runtime_target_r),
        )

        desired_floor = max(
            float(hard_stop_r),
            max_favorable_r
            - trailing_gap,
        )

        if desired_floor > self.protected_r_floor:
            self.protected_r_floor = min(
                desired_floor,
                self.protected_r_floor
                + ratchet_step,
            )

        #
        # FSNL-equivalent target condition.
        #
        if measured_r >= float(trade_target_r):
            action = "EXIT"
            reason = "GOVERNOR_TARGET_REACHED"

        elif measured_r <= float(hard_stop_r):
            action = "EXIT"
            reason = "GOVERNOR_HARD_STOP"

        elif (
            elapsed_bars >= min_hold_bars
            and measured_r
            <= self.protected_r_floor
            and self.protected_r_floor
            > float(hard_stop_r)
        ):
            action = "EXIT"
            reason = "GOVERNOR_RATCHET_FLOOR"

        elif elapsed_bars >= max_hold_bars:
            action = "EXIT"
            reason = "GOVERNOR_MAX_HOLD"

        else:
            #
            # Comparator/PID path deviation protection.
            # These thresholds are themselves derived from the active,
            # dynamically scheduled governor parameters.
            #
            exit_error = max(
                abs(self.runtime_target_r),
                abs(
                    self.runtime_dynamic_offset_max
                ),
            )

            exit_control = max(
                abs(self.runtime_target_r),
                abs(
                    self.runtime_ki
                    * self.runtime_integral_clamp
                ),
            )

            if (
                elapsed_bars >= min_hold_bars
                and error >= exit_error
                and control_u >= exit_control
            ):
                action = "EXIT"
                reason = "GOVERNOR_PATH_ERROR"
            else:
                action = "HOLD"
                reason = "GOVERNOR_TRACKING"

        return {
            "action": action,
            "reason": reason,
            "measured_r": measured_r,
            "reference_r": reference_r,
            "error": float(error),
            "integral_error": float(
                self.inner_integral_error
            ),
            "derivative": float(derivative),
            "control_u": float(control_u),
            "protected_r_floor": float(
                self.protected_r_floor
            ),
            "max_favorable_r": (
                max_favorable_r
            ),
            "elapsed_bars": int(
                elapsed_bars
            ),
        }

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
