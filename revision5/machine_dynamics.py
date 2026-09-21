"""
Revision-5 rotating-machine dynamics and mechanical protection.

The model is intentionally physical-equivalent rather than OEM-specific.

No Revision-2/3/4 dependencies.

Dynamic chain
-------------
speed/load reference
      |
      v
 governor droop
      |
      v
 governor lag Tg
      |
      v
 actuator lag Ta
      |
      v
 turbine lag Tt
      |
      v
 mechanical power Pm
      |
      v
 swing equation: H + D + electrical load Pe
      |
      v
 rotor frequency / acceleration
      |
      +---- feedback

All operating constants are supplied explicitly through MachineDynamicSpec
and MechanicalProtectionSpec.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Optional


def _finite(name: str, value: float) -> float:
    value = float(value)

    if not isfinite(value):
        raise ValueError(
            f"{name} must be finite"
        )

    return value


def _clamp(
    value: float,
    lower: float,
    upper: float,
) -> float:
    return max(
        float(lower),
        min(
            float(upper),
            float(value),
        ),
    )


@dataclass(frozen=True)
class MachineDynamicSpec:
    """
    Effective R5 machine dynamic envelope.

    These are model parameters, not claimed OEM generator constants.
    """

    name: str
    machine_kind: str
    response_class: str

    nominal_frequency_hz: float

    droop_fraction: float
    inertia_h_seconds: float
    damping_pu: float

    governor_time_constant_s: float
    actuator_time_constant_s: float
    turbine_time_constant_s: float

    mechanical_power_min_pu: float
    mechanical_power_max_pu: float

    ramp_up_pu_per_s: float
    ramp_down_pu_per_s: float

    speed_deadband_hz: float

    acceleration_limit_hz_per_s: float
    deceleration_limit_hz_per_s: float

    def validate(self) -> None:
        numeric = {
            name: value
            for name, value in vars(self).items()
            if name not in (
                "name",
                "machine_kind",
                "response_class",
            )
        }

        for name, value in numeric.items():
            _finite(name, value)

        if self.machine_kind not in (
            "GAS_TURBINE",
            "STEAM_TURBINE",
        ):
            raise ValueError(
                "machine_kind must be GAS_TURBINE "
                "or STEAM_TURBINE"
            )

        if self.response_class not in (
            "FAST",
            "MEDIUM",
            "SLOW",
        ):
            raise ValueError(
                "response_class must be FAST/MEDIUM/SLOW"
            )

        if self.nominal_frequency_hz <= 0.0:
            raise ValueError(
                "nominal_frequency_hz must be positive"
            )

        if not 0.0 < self.droop_fraction < 1.0:
            raise ValueError(
                "droop_fraction must be between zero and one"
            )

        if self.inertia_h_seconds <= 0.0:
            raise ValueError(
                "inertia_h_seconds must be positive"
            )

        if self.damping_pu < 0.0:
            raise ValueError(
                "damping_pu cannot be negative"
            )

        for name in (
            "governor_time_constant_s",
            "actuator_time_constant_s",
            "turbine_time_constant_s",
        ):
            if getattr(self, name) <= 0.0:
                raise ValueError(
                    f"{name} must be positive"
                )

        if (
            self.mechanical_power_min_pu
            >= self.mechanical_power_max_pu
        ):
            raise ValueError(
                "mechanical-power limits invalid"
            )

        if (
            self.ramp_up_pu_per_s <= 0.0
            or self.ramp_down_pu_per_s <= 0.0
        ):
            raise ValueError(
                "ramp rates must be positive"
            )

        if self.speed_deadband_hz < 0.0:
            raise ValueError(
                "speed_deadband_hz cannot be negative"
            )

        if (
            self.acceleration_limit_hz_per_s <= 0.0
            or self.deceleration_limit_hz_per_s <= 0.0
        ):
            raise ValueError(
                "acceleration limits must be positive"
            )


@dataclass(frozen=True)
class MachineState:
    frequency_hz: float
    acceleration_hz_per_s: float

    speed_error_hz: float

    governor_output_pu: float
    actuator_output_pu: float

    mechanical_power_pu: float
    electrical_power_pu: float
    load_reference_pu: float


class TurbineMachineModel:
    """
    Simplified but causal turbine-generator dynamic model.

    Explicit Euler integration is bounded by:
      - first-order lag saturation
      - turbine mechanical-power ramp limits
      - acceleration/deceleration limits
    """

    def __init__(
        self,
        spec: MachineDynamicSpec,
    ):
        spec.validate()
        self.spec = spec

        self.frequency_hz = (
            spec.nominal_frequency_hz
        )

        self.governor_output_pu = 0.0
        self.actuator_output_pu = 0.0
        self.mechanical_power_pu = 0.0

        self.last_acceleration_hz_per_s = 0.0

    def reset(
        self,
        *,
        frequency_hz: Optional[float] = None,
        mechanical_power_pu: float = 0.0,
    ) -> None:

        if frequency_hz is None:
            frequency_hz = (
                self.spec.nominal_frequency_hz
            )

        frequency_hz = _finite(
            "frequency_hz",
            frequency_hz,
        )

        mechanical_power_pu = _finite(
            "mechanical_power_pu",
            mechanical_power_pu,
        )

        mechanical_power_pu = _clamp(
            mechanical_power_pu,
            self.spec.mechanical_power_min_pu,
            self.spec.mechanical_power_max_pu,
        )

        self.frequency_hz = frequency_hz

        self.governor_output_pu = (
            mechanical_power_pu
        )

        self.actuator_output_pu = (
            mechanical_power_pu
        )

        self.mechanical_power_pu = (
            mechanical_power_pu
        )

        self.last_acceleration_hz_per_s = 0.0

    @staticmethod
    def _first_order(
        *,
        current: float,
        target: float,
        dt_seconds: float,
        time_constant_s: float,
    ) -> float:

        alpha = _clamp(
            dt_seconds / time_constant_s,
            0.0,
            1.0,
        )

        return (
            current
            + alpha * (target - current)
        )

    def step(
        self,
        *,
        speed_reference_hz: float,
        load_reference_pu: float,
        electrical_power_pu: float,
        dt_seconds: float,
    ) -> MachineState:

        dt = _finite(
            "dt_seconds",
            dt_seconds,
        )

        if dt <= 0.0:
            raise ValueError(
                "dt_seconds must be positive"
            )

        speed_reference_hz = _finite(
            "speed_reference_hz",
            speed_reference_hz,
        )

        load_reference_pu = _finite(
            "load_reference_pu",
            load_reference_pu,
        )

        electrical_power_pu = _finite(
            "electrical_power_pu",
            electrical_power_pu,
        )

        speed_error_hz = (
            speed_reference_hz
            - self.frequency_hz
        )

        if (
            abs(speed_error_hz)
            <= self.spec.speed_deadband_hz
        ):
            effective_speed_error_hz = 0.0
        else:
            effective_speed_error_hz = (
                speed_error_hz
            )

        speed_error_pu = (
            effective_speed_error_hz
            / self.spec.nominal_frequency_hz
        )

        #
        # Classical droop:
        #
        # load reference supplies the steady MW operating point,
        # while 1/R converts speed error into corrective demand.
        #
        governor_demand = (
            load_reference_pu
            + speed_error_pu
            / self.spec.droop_fraction
        )

        governor_demand = _clamp(
            governor_demand,
            self.spec.mechanical_power_min_pu,
            self.spec.mechanical_power_max_pu,
        )

        self.governor_output_pu = (
            self._first_order(
                current=self.governor_output_pu,
                target=governor_demand,
                dt_seconds=dt,
                time_constant_s=(
                    self.spec
                    .governor_time_constant_s
                ),
            )
        )

        self.actuator_output_pu = (
            self._first_order(
                current=self.actuator_output_pu,
                target=self.governor_output_pu,
                dt_seconds=dt,
                time_constant_s=(
                    self.spec
                    .actuator_time_constant_s
                ),
            )
        )

        turbine_target = self._first_order(
            current=self.mechanical_power_pu,
            target=self.actuator_output_pu,
            dt_seconds=dt,
            time_constant_s=(
                self.spec
                .turbine_time_constant_s
            ),
        )

        delta_power = (
            turbine_target
            - self.mechanical_power_pu
        )

        if delta_power >= 0.0:
            max_delta = (
                self.spec.ramp_up_pu_per_s
                * dt
            )
        else:
            max_delta = (
                self.spec.ramp_down_pu_per_s
                * dt
            )

        delta_power = _clamp(
            delta_power,
            -max_delta,
            max_delta,
        )

        self.mechanical_power_pu = _clamp(
            self.mechanical_power_pu
            + delta_power,
            self.spec.mechanical_power_min_pu,
            self.spec.mechanical_power_max_pu,
        )

        frequency_deviation_pu = (
            self.frequency_hz
            - self.spec.nominal_frequency_hz
        ) / self.spec.nominal_frequency_hz

        damping_power_pu = (
            self.spec.damping_pu
            * frequency_deviation_pu
        )

        power_imbalance_pu = (
            self.mechanical_power_pu
            - electrical_power_pu
            - damping_power_pu
        )

        #
        # Simplified swing-equation form:
        #
        # df/dt = f_nominal/(2H) * power imbalance
        #
        acceleration = (
            self.spec.nominal_frequency_hz
            / (
                2.0
                * self.spec.inertia_h_seconds
            )
            * power_imbalance_pu
        )

        acceleration = _clamp(
            acceleration,
            -self.spec.deceleration_limit_hz_per_s,
            self.spec.acceleration_limit_hz_per_s,
        )

        self.frequency_hz = max(
            0.0,
            self.frequency_hz
            + acceleration * dt,
        )

        self.last_acceleration_hz_per_s = (
            acceleration
        )

        return MachineState(
            frequency_hz=self.frequency_hz,
            acceleration_hz_per_s=acceleration,
            speed_error_hz=speed_error_hz,
            governor_output_pu=(
                self.governor_output_pu
            ),
            actuator_output_pu=(
                self.actuator_output_pu
            ),
            mechanical_power_pu=(
                self.mechanical_power_pu
            ),
            electrical_power_pu=(
                electrical_power_pu
            ),
            load_reference_pu=(
                load_reference_pu
            ),
        )


@dataclass(frozen=True)
class MechanicalProtectionSpec:
    machine_kind: str

    overspeed_alarm_hz: float
    overspeed_trip_hz: float

    acceleration_trip_hz_per_s: float

    vibration_alarm_mm_s: float
    vibration_trip_mm_s: float

    low_lube_oil_trip_bar: float
    low_hydraulic_pressure_trip_bar: float

    bearing_temperature_alarm_c: float
    bearing_temperature_trip_c: float

    exhaust_temperature_alarm_c: Optional[float]
    exhaust_temperature_trip_c: Optional[float]

    exhaust_spread_hold_c: Optional[float]
    exhaust_spread_trip_c: Optional[float]

    flame_loss_trip_delay_s: Optional[float]
    fuel_loss_trip_delay_s: Optional[float]

    lockout_trip_codes: tuple[str, ...]

    def validate(self) -> None:

        if self.machine_kind not in (
            "GAS_TURBINE",
            "STEAM_TURBINE",
        ):
            raise ValueError(
                "invalid mechanical-protection machine_kind"
            )

        required = (
            "overspeed_alarm_hz",
            "overspeed_trip_hz",
            "acceleration_trip_hz_per_s",
            "vibration_alarm_mm_s",
            "vibration_trip_mm_s",
            "low_lube_oil_trip_bar",
            "low_hydraulic_pressure_trip_bar",
            "bearing_temperature_alarm_c",
            "bearing_temperature_trip_c",
        )

        for name in required:
            _finite(
                name,
                getattr(self, name),
            )

        if (
            self.overspeed_alarm_hz
            >= self.overspeed_trip_hz
        ):
            raise ValueError(
                "overspeed alarm must be below trip"
            )

        if (
            self.vibration_alarm_mm_s
            >= self.vibration_trip_mm_s
        ):
            raise ValueError(
                "vibration alarm must be below trip"
            )

        if (
            self.bearing_temperature_alarm_c
            >= self.bearing_temperature_trip_c
        ):
            raise ValueError(
                "bearing temperature alarm must be below trip"
            )

        combustion_fields = (
            self.exhaust_temperature_alarm_c,
            self.exhaust_temperature_trip_c,
            self.exhaust_spread_hold_c,
            self.exhaust_spread_trip_c,
            self.flame_loss_trip_delay_s,
            self.fuel_loss_trip_delay_s,
        )

        if self.machine_kind == "GAS_TURBINE":

            if any(
                value is None
                for value in combustion_fields
            ):
                raise ValueError(
                    "GTG mechanical protection requires "
                    "combustion settings"
                )

            if (
                self.exhaust_temperature_alarm_c
                >= self.exhaust_temperature_trip_c
            ):
                raise ValueError(
                    "exhaust temperature alarm must be below trip"
                )

            if (
                self.exhaust_spread_hold_c
                >= self.exhaust_spread_trip_c
            ):
                raise ValueError(
                    "exhaust spread hold must be below trip"
                )

            if (
                self.flame_loss_trip_delay_s < 0.0
                or self.fuel_loss_trip_delay_s < 0.0
            ):
                raise ValueError(
                    "combustion trip delays cannot be negative"
                )

        else:
            if any(
                value is not None
                for value in combustion_fields
            ):
                raise ValueError(
                    "steam turbine must not have "
                    "GTG combustion protection settings"
                )


@dataclass(frozen=True)
class MechanicalMeasurements:
    frequency_hz: float
    acceleration_hz_per_s: float

    vibration_mm_s: float
    lube_oil_pressure_bar: float
    hydraulic_pressure_bar: float
    bearing_temperature_c: float

    dt_seconds: float

    flame_proven: Optional[bool] = None
    fuel_available: Optional[bool] = None

    exhaust_temperature_c: Optional[float] = None
    exhaust_spread_c: Optional[float] = None


@dataclass(frozen=True)
class MechanicalProtectionResult:
    tripped: bool
    lockout_86: bool

    trip_code: Optional[str]
    reason: str

    alarm: bool
    hold_startup: bool


class TurbineMechanicalProtection:
    """
    Independent turbine mechanical protection.

    It does not ask the governor for permission to trip.
    """

    def __init__(
        self,
        spec: MechanicalProtectionSpec,
    ):
        spec.validate()
        self.spec = spec

        self.flame_loss_seconds = 0.0
        self.fuel_loss_seconds = 0.0

    def reset(self) -> None:
        self.flame_loss_seconds = 0.0
        self.fuel_loss_seconds = 0.0

    def _trip(
        self,
        code: str,
        reason: str,
    ) -> MechanicalProtectionResult:

        return MechanicalProtectionResult(
            tripped=True,
            lockout_86=(
                code
                in self.spec.lockout_trip_codes
            ),
            trip_code=code,
            reason=reason,
            alarm=True,
            hold_startup=True,
        )

    def evaluate(
        self,
        measurement: MechanicalMeasurements,
    ) -> MechanicalProtectionResult:

        dt = _finite(
            "dt_seconds",
            measurement.dt_seconds,
        )

        if dt < 0.0:
            raise ValueError(
                "dt_seconds cannot be negative"
            )

        frequency = _finite(
            "frequency_hz",
            measurement.frequency_hz,
        )

        acceleration = _finite(
            "acceleration_hz_per_s",
            measurement.acceleration_hz_per_s,
        )

        vibration = _finite(
            "vibration_mm_s",
            measurement.vibration_mm_s,
        )

        lube = _finite(
            "lube_oil_pressure_bar",
            measurement.lube_oil_pressure_bar,
        )

        hydraulic = _finite(
            "hydraulic_pressure_bar",
            measurement.hydraulic_pressure_bar,
        )

        bearing_temp = _finite(
            "bearing_temperature_c",
            measurement.bearing_temperature_c,
        )

        if frequency >= self.spec.overspeed_trip_hz:
            return self._trip(
                "MECH_OVERSPEED",
                "mechanical overspeed trip",
            )

        if (
            abs(acceleration)
            >= self.spec.acceleration_trip_hz_per_s
        ):
            return self._trip(
                "MECH_ACCELERATION",
                "excessive rotor acceleration/deceleration",
            )

        if vibration >= self.spec.vibration_trip_mm_s:
            return self._trip(
                "MECH_VIBRATION",
                "high turbine vibration trip",
            )

        if lube <= self.spec.low_lube_oil_trip_bar:
            return self._trip(
                "MECH_LOW_LUBE_OIL",
                "low lube-oil pressure trip",
            )

        if (
            hydraulic
            <= self.spec.low_hydraulic_pressure_trip_bar
        ):
            return self._trip(
                "MECH_LOW_HYDRAULIC",
                "low hydraulic/trip-oil pressure trip",
            )

        if (
            bearing_temp
            >= self.spec.bearing_temperature_trip_c
        ):
            return self._trip(
                "MECH_BEARING_TEMP",
                "high bearing temperature trip",
            )

        hold_startup = False
        alarm = False
        reason = "MECHANICAL_PROTECTION_HEALTHY"

        if (
            frequency
            >= self.spec.overspeed_alarm_hz
        ):
            alarm = True
            reason = "OVERSPEED_ALARM"

        if (
            vibration
            >= self.spec.vibration_alarm_mm_s
        ):
            alarm = True
            reason = "VIBRATION_ALARM"

        if (
            bearing_temp
            >= self.spec.bearing_temperature_alarm_c
        ):
            alarm = True
            reason = "BEARING_TEMPERATURE_ALARM"

        if self.spec.machine_kind == "GAS_TURBINE":

            if measurement.exhaust_temperature_c is None:
                raise ValueError(
                    "GTG requires exhaust_temperature_c"
                )

            if measurement.exhaust_spread_c is None:
                raise ValueError(
                    "GTG requires exhaust_spread_c"
                )

            if measurement.flame_proven is None:
                raise ValueError(
                    "GTG requires flame_proven"
                )

            if measurement.fuel_available is None:
                raise ValueError(
                    "GTG requires fuel_available"
                )

            exhaust_temp = _finite(
                "exhaust_temperature_c",
                measurement.exhaust_temperature_c,
            )

            exhaust_spread = _finite(
                "exhaust_spread_c",
                measurement.exhaust_spread_c,
            )

            if (
                exhaust_temp
                >= self.spec.exhaust_temperature_trip_c
            ):
                return self._trip(
                    "MECH_EXHAUST_TEMP",
                    "high exhaust temperature trip",
                )

            if (
                exhaust_spread
                >= self.spec.exhaust_spread_trip_c
            ):
                return self._trip(
                    "MECH_EXHAUST_SPREAD",
                    "exhaust temperature spread trip",
                )

            if (
                exhaust_spread
                >= self.spec.exhaust_spread_hold_c
            ):
                alarm = True
                hold_startup = True
                reason = "EXHAUST_SPREAD_STARTUP_HOLD"

            elif (
                exhaust_temp
                >= self.spec.exhaust_temperature_alarm_c
            ):
                alarm = True
                reason = "EXHAUST_TEMPERATURE_ALARM"

            if measurement.flame_proven:
                self.flame_loss_seconds = 0.0
            else:
                self.flame_loss_seconds += dt

            if measurement.fuel_available:
                self.fuel_loss_seconds = 0.0
            else:
                self.fuel_loss_seconds += dt

            if (
                self.flame_loss_seconds
                >= self.spec.flame_loss_trip_delay_s
            ):
                return self._trip(
                    "MECH_FLAME_LOSS",
                    "loss of flame trip",
                )

            if (
                self.fuel_loss_seconds
                >= self.spec.fuel_loss_trip_delay_s
            ):
                return self._trip(
                    "MECH_FUEL_LOSS",
                    "fuel supply loss trip",
                )

        return MechanicalProtectionResult(
            tripped=False,
            lockout_86=False,
            trip_code=None,
            reason=reason,
            alarm=alarm,
            hold_startup=hold_startup,
        )
