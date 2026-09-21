"""
Revision-5 turbine startup and generator synchronization.

Native R5 implementation:
    25A  Automatic synchronizer
         - SPEED RAISE / LOWER pulses
         - VOLTAGE RAISE / LOWER pulses
         - breaker-closing-time phase prediction

    25   Independent synchrocheck relay
         - delta-frequency supervision
         - delta-voltage supervision
         - phase-angle supervision
         - dead-bus permissive

    52G  Generator breaker close request
         requires BOTH 25A close opportunity and 25 permissive
         on a live bus.

No Revision-2/3/4 market-proxy synchronizer is used.

All operating settings are injected. There are no production
synchronizing thresholds hidden inside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


# =====================================================================
# COMMON TYPES
# =====================================================================

class MachineKind(str, Enum):
    GAS_TURBINE = "GAS_TURBINE"
    STEAM_TURBINE = "STEAM_TURBINE"


class StartupState(str, Enum):
    OFF = "OFF"
    STARTING_MEANS = "STARTING_MEANS"
    CRANKING = "CRANKING"

    FIRING = "FIRING"
    FLAME_PROVEN = "FLAME_PROVEN"

    ACCELERATING = "ACCELERATING"

    FSNL = "FSNL"
    SYNC_READY = "SYNC_READY"

    SYNCHRONIZED = "SYNCHRONIZED"
    MINIMUM_LOAD = "MINIMUM_LOAD"
    DISPATCH_READY = "DISPATCH_READY"

    TRIPPED = "TRIPPED"


class SyncMode(str, Enum):
    LIVE_BUS = "LIVE_BUS"
    DEAD_BUS = "DEAD_BUS"


class SpeedPulse(str, Enum):
    NONE = "NONE"
    RAISE = "SPEED_RAISE"
    LOWER = "SPEED_LOWER"


class VoltagePulse(str, Enum):
    NONE = "NONE"
    RAISE = "VOLTAGE_RAISE"
    LOWER = "VOLTAGE_LOWER"


def _finite(name: str, value: float) -> float:
    value = float(value)

    if not isfinite(value):
        raise ValueError(
            f"{name} must be finite"
        )

    return value


def _exceeds_limit(
    value: float,
    limit: float,
    *,
    abs_tol: float = 1e-12,
) -> bool:
    """
    Equality with a configured boundary is treated as ON the boundary.

    abs_tol is numerical protection against binary floating-point noise;
    it is not an operating setting.
    """

    value = abs(float(value))
    limit = abs(float(limit))

    return (
        value > limit
        and abs(value - limit) > abs_tol
    )


def _wrap_phase_degrees(
    angle: float,
) -> float:
    return (
        (float(angle) + 180.0)
        % 360.0
    ) - 180.0


# =====================================================================
# DEVICE 25 — INDEPENDENT SYNCHROCHECK RELAY
# =====================================================================

@dataclass(frozen=True)
class SynchrocheckSpec:
    """
    Independent Device-25 close-supervision settings.

    Mandatory injected parameters. These are NOT given production
    defaults in source code.
    """

    nominal_frequency_hz: float

    max_delta_frequency_hz: float
    max_delta_voltage_pu: float
    max_phase_angle_deg: float

    breaker_closing_time_seconds: float

    dead_bus_voltage_pu: float

    def validate(self) -> None:
        values = (
            self.nominal_frequency_hz,
            self.max_delta_frequency_hz,
            self.max_delta_voltage_pu,
            self.max_phase_angle_deg,
            self.breaker_closing_time_seconds,
            self.dead_bus_voltage_pu,
        )

        if not all(
            isfinite(float(v))
            for v in values
        ):
            raise ValueError(
                "synchrocheck settings must be finite"
            )

        if self.nominal_frequency_hz <= 0:
            raise ValueError(
                "nominal frequency must be positive"
            )

        if self.max_delta_frequency_hz <= 0:
            raise ValueError(
                "delta-f tolerance must be positive"
            )

        if self.max_delta_voltage_pu <= 0:
            raise ValueError(
                "delta-V tolerance must be positive"
            )

        if not (
            0
            < self.max_phase_angle_deg
            <= 180
        ):
            raise ValueError(
                "phase-angle tolerance invalid"
            )

        if self.breaker_closing_time_seconds < 0:
            raise ValueError(
                "breaker closing time cannot be negative"
            )

        if self.dead_bus_voltage_pu < 0:
            raise ValueError(
                "dead-bus threshold cannot be negative"
            )


@dataclass(frozen=True)
class SynchrocheckResult:
    permitted: bool
    mode: SyncMode

    delta_frequency_hz: float
    delta_voltage_pu: float

    present_phase_angle_deg: float
    predicted_phase_at_close_deg: float

    reason: str


class SynchrocheckRelay25:
    """
    Independent ANSI/IEEE Device 25.

    This object DOES NOT issue governor or AVR commands.

    Its job is only:
        SAFE TO CLOSE
              or
        BLOCK CLOSE
    """

    def __init__(
        self,
        spec: SynchrocheckSpec,
    ):
        spec.validate()
        self.spec = spec

    def evaluate(
        self,
        *,
        generator_frequency_hz: float,
        bus_frequency_hz: float,
        generator_voltage_pu: float,
        bus_voltage_pu: float,
        generator_phase_deg: float,
        bus_phase_deg: float,
        dead_bus_close_authorized: bool = False,
    ) -> SynchrocheckResult:

        gf = _finite(
            "generator_frequency_hz",
            generator_frequency_hz,
        )

        bf = _finite(
            "bus_frequency_hz",
            bus_frequency_hz,
        )

        gv = _finite(
            "generator_voltage_pu",
            generator_voltage_pu,
        )

        bv = _finite(
            "bus_voltage_pu",
            bus_voltage_pu,
        )

        gp = _finite(
            "generator_phase_deg",
            generator_phase_deg,
        )

        bp = _finite(
            "bus_phase_deg",
            bus_phase_deg,
        )

        delta_f = gf - bf
        delta_v = abs(gv - bv)

        present_phase = _wrap_phase_degrees(
            gp - bp
        )

        predicted_phase = _wrap_phase_degrees(
            present_phase
            + 360.0
            * delta_f
            * self.spec.breaker_closing_time_seconds
        )

        #
        # DEAD BUS
        #
        if bv <= self.spec.dead_bus_voltage_pu:

            if not dead_bus_close_authorized:
                return SynchrocheckResult(
                    False,
                    SyncMode.DEAD_BUS,
                    delta_f,
                    delta_v,
                    present_phase,
                    predicted_phase,
                    "25_DEAD_BUS_CLOSE_NOT_AUTHORIZED",
                )

            nominal_error = (
                gf
                - self.spec.nominal_frequency_hz
            )

            if _exceeds_limit(
                nominal_error,
                self.spec.max_delta_frequency_hz,
            ):
                return SynchrocheckResult(
                    False,
                    SyncMode.DEAD_BUS,
                    delta_f,
                    delta_v,
                    present_phase,
                    predicted_phase,
                    "25_DEAD_BUS_FREQUENCY_NOT_READY",
                )

            if gv <= self.spec.dead_bus_voltage_pu:
                return SynchrocheckResult(
                    False,
                    SyncMode.DEAD_BUS,
                    delta_f,
                    delta_v,
                    present_phase,
                    predicted_phase,
                    "25_DEAD_BUS_GENERATOR_VOLTAGE_NOT_READY",
                )

            return SynchrocheckResult(
                True,
                SyncMode.DEAD_BUS,
                delta_f,
                delta_v,
                present_phase,
                predicted_phase,
                "25_DEAD_BUS_PERMISSIVE",
            )

        #
        # LIVE BUS
        #
        if _exceeds_limit(
            delta_f,
            self.spec.max_delta_frequency_hz,
        ):
            return SynchrocheckResult(
                False,
                SyncMode.LIVE_BUS,
                delta_f,
                delta_v,
                present_phase,
                predicted_phase,
                "25_DELTA_F_OUTSIDE_WINDOW",
            )

        if _exceeds_limit(
            delta_v,
            self.spec.max_delta_voltage_pu,
        ):
            return SynchrocheckResult(
                False,
                SyncMode.LIVE_BUS,
                delta_f,
                delta_v,
                present_phase,
                predicted_phase,
                "25_DELTA_V_OUTSIDE_WINDOW",
            )

        if _exceeds_limit(
            predicted_phase,
            self.spec.max_phase_angle_deg,
        ):
            return SynchrocheckResult(
                False,
                SyncMode.LIVE_BUS,
                delta_f,
                delta_v,
                present_phase,
                predicted_phase,
                "25_PHASE_OUTSIDE_WINDOW",
            )

        return SynchrocheckResult(
            True,
            SyncMode.LIVE_BUS,
            delta_f,
            delta_v,
            present_phase,
            predicted_phase,
            "25_SYNC_CHECK_PERMISSIVE",
        )


# Backward-compatible name for the first R5 implementation.
ANSI25Synchronizer = SynchrocheckRelay25


# =====================================================================
# DEVICE 25A — AUTOMATIC SYNCHRONIZER
# =====================================================================

@dataclass(frozen=True)
class AutoSynchronizerSpec:
    """
    Device-25A control settings.

    No production defaults are embedded.

    25A continuously drives:
        speed/frequency through governor pulses
        voltage through AVR pulses

    until the predicted breaker-closing point is acceptable.
    """

    preferred_slip_hz: float

    frequency_deadband_hz: float
    voltage_deadband_pu: float
    phase_capture_deg: float

    breaker_closing_time_seconds: float

    speed_pulse_width_seconds: float
    voltage_pulse_width_seconds: float
    pulse_interval_seconds: float

    synchronization_timeout_seconds: float
    max_control_pulses: int

    def validate(self) -> None:

        scalars = (
            self.preferred_slip_hz,
            self.frequency_deadband_hz,
            self.voltage_deadband_pu,
            self.phase_capture_deg,
            self.breaker_closing_time_seconds,
            self.speed_pulse_width_seconds,
            self.voltage_pulse_width_seconds,
            self.pulse_interval_seconds,
            self.synchronization_timeout_seconds,
        )

        if not all(
            isfinite(float(v))
            for v in scalars
        ):
            raise ValueError(
                "25A settings must be finite"
            )

        if self.frequency_deadband_hz <= 0:
            raise ValueError(
                "25A frequency deadband must be positive"
            )

        if self.voltage_deadband_pu <= 0:
            raise ValueError(
                "25A voltage deadband must be positive"
            )

        if not (
            0
            < self.phase_capture_deg
            <= 180
        ):
            raise ValueError(
                "25A phase capture invalid"
            )

        if self.breaker_closing_time_seconds < 0:
            raise ValueError(
                "breaker close time cannot be negative"
            )

        if self.speed_pulse_width_seconds <= 0:
            raise ValueError(
                "speed pulse width must be positive"
            )

        if self.voltage_pulse_width_seconds <= 0:
            raise ValueError(
                "voltage pulse width must be positive"
            )

        if self.pulse_interval_seconds <= 0:
            raise ValueError(
                "pulse interval must be positive"
            )

        if self.synchronization_timeout_seconds <= 0:
            raise ValueError(
                "synchronization timeout must be positive"
            )

        if self.max_control_pulses <= 0:
            raise ValueError(
                "max_control_pulses must be positive"
            )


@dataclass(frozen=True)
class AutoSynchronizerOutput:
    enabled: bool
    timed_out: bool

    speed_command: SpeedPulse
    voltage_command: VoltagePulse

    speed_pulse_width_seconds: float
    voltage_pulse_width_seconds: float

    delta_frequency_hz: float
    frequency_error_hz: float
    delta_voltage_pu: float

    present_phase_angle_deg: float
    predicted_phase_at_close_deg: float

    close_opportunity: bool

    pulse_count: int
    elapsed_seconds: float

    reason: str


class AutomaticSynchronizer25A:
    """
    Active automatic synchronizer.

    25A is a controller, not protection.

    It produces:
        SPEED_RAISE / SPEED_LOWER
        VOLTAGE_RAISE / VOLTAGE_LOWER

    It cannot close 52G by itself.
    """

    def __init__(
        self,
        spec: AutoSynchronizerSpec,
    ):
        spec.validate()

        self.spec = spec

        self.elapsed_seconds = 0.0
        self.seconds_since_pulse = (
            spec.pulse_interval_seconds
        )

        self.pulse_count = 0
        self.timed_out = False

    def reset(self) -> None:
        self.elapsed_seconds = 0.0

        self.seconds_since_pulse = (
            self.spec.pulse_interval_seconds
        )

        self.pulse_count = 0
        self.timed_out = False

    def step(
        self,
        *,
        dt_seconds: float,
        generator_frequency_hz: float,
        bus_frequency_hz: float,
        generator_voltage_pu: float,
        bus_voltage_pu: float,
        generator_phase_deg: float,
        bus_phase_deg: float,
        enabled: bool = True,
    ) -> AutoSynchronizerOutput:

        dt = _finite(
            "dt_seconds",
            dt_seconds,
        )

        if dt < 0:
            raise ValueError(
                "dt_seconds cannot be negative"
            )

        gf = _finite(
            "generator_frequency_hz",
            generator_frequency_hz,
        )

        bf = _finite(
            "bus_frequency_hz",
            bus_frequency_hz,
        )

        gv = _finite(
            "generator_voltage_pu",
            generator_voltage_pu,
        )

        bv = _finite(
            "bus_voltage_pu",
            bus_voltage_pu,
        )

        gp = _finite(
            "generator_phase_deg",
            generator_phase_deg,
        )

        bp = _finite(
            "bus_phase_deg",
            bus_phase_deg,
        )

        delta_f = gf - bf

        frequency_error = (
            delta_f
            - self.spec.preferred_slip_hz
        )

        delta_v_signed = gv - bv

        present_phase = _wrap_phase_degrees(
            gp - bp
        )

        predicted_phase = _wrap_phase_degrees(
            present_phase
            + 360.0
            * delta_f
            * self.spec.breaker_closing_time_seconds
        )

        if not enabled:
            return AutoSynchronizerOutput(
                False,
                False,
                SpeedPulse.NONE,
                VoltagePulse.NONE,
                0.0,
                0.0,
                delta_f,
                frequency_error,
                delta_v_signed,
                present_phase,
                predicted_phase,
                False,
                self.pulse_count,
                self.elapsed_seconds,
                "25A_DISABLED",
            )

        self.elapsed_seconds += dt
        self.seconds_since_pulse += dt

        if (
            self.elapsed_seconds
            >= self.spec.synchronization_timeout_seconds
            or self.pulse_count
            >= self.spec.max_control_pulses
        ):
            self.timed_out = True

            return AutoSynchronizerOutput(
                True,
                True,
                SpeedPulse.NONE,
                VoltagePulse.NONE,
                0.0,
                0.0,
                delta_f,
                frequency_error,
                delta_v_signed,
                present_phase,
                predicted_phase,
                False,
                self.pulse_count,
                self.elapsed_seconds,
                "25A_SYNCHRONIZATION_TIMEOUT",
            )

        frequency_aligned = not _exceeds_limit(
            frequency_error,
            self.spec.frequency_deadband_hz,
        )

        voltage_aligned = not _exceeds_limit(
            delta_v_signed,
            self.spec.voltage_deadband_pu,
        )

        phase_aligned = not _exceeds_limit(
            predicted_phase,
            self.spec.phase_capture_deg,
        )

        close_opportunity = (
            frequency_aligned
            and voltage_aligned
            and phase_aligned
        )

        speed_command = SpeedPulse.NONE
        voltage_command = VoltagePulse.NONE

        speed_width = 0.0
        voltage_width = 0.0

        pulse_due = (
            self.seconds_since_pulse
            >= self.spec.pulse_interval_seconds
        )

        if pulse_due and not close_opportunity:

            if not frequency_aligned:

                if frequency_error < 0:
                    speed_command = (
                        SpeedPulse.RAISE
                    )
                else:
                    speed_command = (
                        SpeedPulse.LOWER
                    )

                speed_width = (
                    self.spec
                    .speed_pulse_width_seconds
                )

            if not voltage_aligned:

                if delta_v_signed < 0:
                    voltage_command = (
                        VoltagePulse.RAISE
                    )
                else:
                    voltage_command = (
                        VoltagePulse.LOWER
                    )

                voltage_width = (
                    self.spec
                    .voltage_pulse_width_seconds
                )

            if (
                speed_command
                != SpeedPulse.NONE
                or voltage_command
                != VoltagePulse.NONE
            ):
                self.pulse_count += 1
                self.seconds_since_pulse = 0.0

        if close_opportunity:
            reason = "25A_CLOSE_OPPORTUNITY"

        elif (
            speed_command != SpeedPulse.NONE
            or voltage_command != VoltagePulse.NONE
        ):
            reason = "25A_CONTROL_PULSE"

        elif not phase_aligned:
            #
            # Frequency may already be inside its deadband but the
            # machine must continue to drift naturally into phase.
            #
            reason = "25A_WAITING_FOR_PHASE"

        else:
            reason = "25A_WAITING"

        return AutoSynchronizerOutput(
            True,
            False,
            speed_command,
            voltage_command,
            speed_width,
            voltage_width,
            delta_f,
            frequency_error,
            delta_v_signed,
            present_phase,
            predicted_phase,
            close_opportunity,
            self.pulse_count,
            self.elapsed_seconds,
            reason,
        )


# =====================================================================
# 25A + 25 COORDINATION
# =====================================================================

@dataclass(frozen=True)
class SynchronizingResult:
    auto: AutoSynchronizerOutput
    check: SynchrocheckResult

    breaker_close_request: bool

    reason: str


class GeneratorSynchronizingController:
    """
    Complete R5 synchronizing cubicle.

    LIVE BUS:
        52G CLOSE =
            25A close opportunity
            AND
            25 independent synchrocheck permissive

    DEAD BUS:
        Explicit dead-bus authority + Device-25 permissive is enough.
        No active frequency/phase chasing of a de-energized bus.
    """

    def __init__(
        self,
        *,
        auto_spec: AutoSynchronizerSpec,
        check_spec: SynchrocheckSpec,
    ):
        self.auto = AutomaticSynchronizer25A(
            auto_spec
        )

        self.check = SynchrocheckRelay25(
            check_spec
        )

    def reset(self) -> None:
        self.auto.reset()

    def step(
        self,
        *,
        dt_seconds: float,
        generator_frequency_hz: float,
        bus_frequency_hz: float,
        generator_voltage_pu: float,
        bus_voltage_pu: float,
        generator_phase_deg: float,
        bus_phase_deg: float,
        synchronizing_enabled: bool,
        dead_bus_close_authorized: bool = False,
    ) -> SynchronizingResult:

        check = self.check.evaluate(
            generator_frequency_hz=(
                generator_frequency_hz
            ),
            bus_frequency_hz=(
                bus_frequency_hz
            ),
            generator_voltage_pu=(
                generator_voltage_pu
            ),
            bus_voltage_pu=bus_voltage_pu,
            generator_phase_deg=(
                generator_phase_deg
            ),
            bus_phase_deg=bus_phase_deg,
            dead_bus_close_authorized=(
                dead_bus_close_authorized
            ),
        )

        bus_is_dead = (
            float(bus_voltage_pu)
            <= self.check.spec.dead_bus_voltage_pu
        )

        auto = self.auto.step(
            dt_seconds=dt_seconds,
            generator_frequency_hz=(
                generator_frequency_hz
            ),
            bus_frequency_hz=(
                bus_frequency_hz
            ),
            generator_voltage_pu=(
                generator_voltage_pu
            ),
            bus_voltage_pu=bus_voltage_pu,
            generator_phase_deg=(
                generator_phase_deg
            ),
            bus_phase_deg=bus_phase_deg,

            # 25A does not chase a dead bus.
            enabled=(
                synchronizing_enabled
                and not bus_is_dead
            ),
        )

        if not synchronizing_enabled:
            close_request = False
            reason = "SYNCHRONIZING_DISABLED"

        elif bus_is_dead:
            close_request = (
                dead_bus_close_authorized
                and check.permitted
            )

            reason = (
                "52G_DEAD_BUS_CLOSE_REQUEST"
                if close_request
                else check.reason
            )

        else:
            close_request = (
                auto.close_opportunity
                and check.permitted
            )

            if close_request:
                reason = (
                    "52G_LIVE_BUS_CLOSE_REQUEST"
                )

            elif not check.permitted:
                reason = check.reason

            else:
                reason = auto.reason

        return SynchronizingResult(
            auto=auto,
            check=check,
            breaker_close_request=(
                close_request
            ),
            reason=reason,
        )


# =====================================================================
# STARTUP SEQUENCER
# =====================================================================

@dataclass(frozen=True)
class StartupInputs:
    start_command: bool

    starting_means_ready: bool
    crank_complete: bool

    flame_proven: bool

    exhaust_spread_ok: bool
    exhaust_spread_trip: bool

    fsnl_reached: bool

    synchronizer_ready: bool

    # This must be the COMBINED 25A + 25 result produced by
    # GeneratorSynchronizingController.
    synchronizer_permissive: bool

    generator_breaker_closed: bool

    minimum_load_reached: bool
    auxiliary_services_stable: bool

    mechanical_trip: bool = False


@dataclass(frozen=True)
class StartupTransition:
    previous_state: StartupState
    current_state: StartupState

    breaker_close_requested: bool
    dispatch_enabled: bool

    reason: str


class TurbineStartupSequencer:
    """
    GTG:
      OFF
      -> starting means
      -> crank/purge
      -> firing
      -> flame proven
      -> acceleration
      -> exhaust-spread validation
      -> FSNL
      -> 25A + 25 synchronizing
      -> 52G close
      -> minimum load
      -> auxiliaries stable
      -> ECS dispatch

    STG:
      combustion-specific stages are skipped.
    """

    def __init__(
        self,
        machine_kind: MachineKind,
    ):
        self.machine_kind = machine_kind
        self.state = StartupState.OFF

    def trip(
        self,
        reason: str,
    ) -> StartupTransition:

        previous = self.state
        self.state = StartupState.TRIPPED

        return StartupTransition(
            previous,
            self.state,
            False,
            False,
            str(reason),
        )

    def reset(self) -> None:
        self.state = StartupState.OFF

    def step(
        self,
        inputs: StartupInputs,
    ) -> StartupTransition:

        previous = self.state

        if self.state == StartupState.TRIPPED:
            return StartupTransition(
                previous,
                self.state,
                False,
                False,
                "UNIT_TRIPPED",
            )

        if inputs.mechanical_trip:
            return self.trip(
                "MECHANICAL_PROTECTION_TRIP"
            )

        if (
            self.machine_kind
            == MachineKind.GAS_TURBINE
            and inputs.exhaust_spread_trip
        ):
            return self.trip(
                "EXHAUST_SPREAD_TRIP"
            )

        breaker_request = False
        dispatch_enabled = False
        reason = "NO_TRANSITION"

        if self.state == StartupState.OFF:

            if inputs.start_command:
                self.state = (
                    StartupState.STARTING_MEANS
                )
                reason = "START_COMMAND"

        elif (
            self.state
            == StartupState.STARTING_MEANS
        ):

            if inputs.starting_means_ready:
                self.state = StartupState.CRANKING
                reason = "STARTING_MEANS_READY"

        elif self.state == StartupState.CRANKING:

            if inputs.crank_complete:

                if (
                    self.machine_kind
                    == MachineKind.GAS_TURBINE
                ):
                    self.state = StartupState.FIRING
                    reason = "CRANK_COMPLETE_FIRE"

                else:
                    self.state = (
                        StartupState.ACCELERATING
                    )
                    reason = (
                        "STEAM_ADMISSION_ENABLED"
                    )

        elif self.state == StartupState.FIRING:

            if inputs.flame_proven:
                self.state = (
                    StartupState.FLAME_PROVEN
                )
                reason = "FLAME_PROVEN"

        elif (
            self.state
            == StartupState.FLAME_PROVEN
        ):

            self.state = StartupState.ACCELERATING
            reason = "ACCELERATION_ENABLED"

        elif (
            self.state
            == StartupState.ACCELERATING
        ):

            if (
                self.machine_kind
                == MachineKind.GAS_TURBINE
                and not inputs.exhaust_spread_ok
            ):
                reason = "EXHAUST_SPREAD_HOLD"

            elif inputs.fsnl_reached:
                self.state = StartupState.FSNL
                reason = "FSNL_REACHED"

        elif self.state == StartupState.FSNL:

            if inputs.synchronizer_ready:
                self.state = StartupState.SYNC_READY
                reason = "SYNCHRONIZER_ENABLED"

        elif self.state == StartupState.SYNC_READY:

            if inputs.synchronizer_permissive:
                breaker_request = True
                reason = "52G_CLOSE_REQUEST"

            if inputs.generator_breaker_closed:
                self.state = (
                    StartupState.SYNCHRONIZED
                )

                breaker_request = False
                reason = (
                    "GENERATOR_BREAKER_CLOSED"
                )

        elif (
            self.state
            == StartupState.SYNCHRONIZED
        ):

            if inputs.minimum_load_reached:
                self.state = (
                    StartupState.MINIMUM_LOAD
                )
                reason = (
                    "MINIMUM_LOAD_REACHED"
                )

        elif (
            self.state
            == StartupState.MINIMUM_LOAD
        ):

            if inputs.auxiliary_services_stable:
                self.state = (
                    StartupState.DISPATCH_READY
                )

                reason = (
                    "AUXILIARIES_STABLE_"
                    "ECS_ENABLED"
                )

        elif (
            self.state
            == StartupState.DISPATCH_READY
        ):

            dispatch_enabled = True
            reason = "ECS_DISPATCH_ACTIVE"

        if (
            self.state
            == StartupState.DISPATCH_READY
        ):
            dispatch_enabled = True

        return StartupTransition(
            previous,
            self.state,
            breaker_request,
            dispatch_enabled,
            reason,
        )
