"""R6 standalone three-phase measurement + generator-equivalent AVR test bench.

NOT FOR BROKER EXECUTION OR LIVE PLANT CONTROL.

This module supplements, without modifying, the supplied r6_avr_core.py.
It never imports or touches ExposureAVR, governor code, or native execution.

Sign convention for balanced positive-sequence THREE-PHASE generator export:
    P = sqrt(3) * V_LL * I_L * cos(phi)
    Q = sqrt(3) * V_LL * I_L * sin(phi)
    phi = angle(V_phase) - angle(I_phase), wrapped into (-pi, pi]
    positive P: active power exported; positive Q: lagging current/reactive
    power exported, under the selected generator-export sign convention.

V_LL in kV and I_L in kA yield apparent MVA, active MW, reactive MVAR.
V_pu=V_LL/rated_voltage_ll_kv; Q_pu=Q_mvar/rated_mva.
Reactive droop coefficient R_Q scales THIS q_pu; it does not define the
manufacturer's % droop independently of this chosen normalization.

The simulated generator forms synthetic P/Q from a simplified field/load
response, derives synthetic current and phi, then reconstructs P/Q from
V/I/phi through the SAME adapter used for independent measurement testing.
That is a consistency check, NOT physical model identification.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from math import acos, atan2, cos, exp, hypot, isfinite, pi, sin, sqrt
import json
from typing import Optional

from r6_avr_core import AVRConfig, CausalID, VoltageAVR, VoltageSample

SOURCE_BENCH = "BENCH_SIMULATED"
SOURCE_EXTERNAL = "EXTERNAL_MEASURED"
_SQRT3 = sqrt(3.0)
_EPS = 1e-12


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def _check_finite(**kwargs: object) -> None:
    for k, v in kwargs.items():
        if not _number(v):
            raise ValueError(f"{k} must be a finite real number")


def _wrap_angle(angle: float) -> float:
    """Signed principal phase angle (-pi, pi]."""
    return atan2(sin(angle), cos(angle))


@dataclass(frozen=True)
class GeneratorRating:
    bay_id: str
    rated_voltage_ll_kv: float
    rated_mva: float

    def __post_init__(self):
        if not isinstance(self.bay_id, str) or not self.bay_id:
            raise ValueError("generator bay_id required")
        _check_finite(rated_voltage_ll_kv=self.rated_voltage_ll_kv, rated_mva=self.rated_mva)
        if self.rated_voltage_ll_kv <= 0 or self.rated_mva <= 0:
            raise ValueError("generator rated voltage and MVA must be positive")

    @property
    def rated_current_ka(self) -> float:
        return self.rated_mva / (_SQRT3 * self.rated_voltage_ll_kv)


@dataclass(frozen=True)
class ThreePhaseRMS:
    """One synchronized, BALANCED, line-to-line RMS voltage/current sample.

    ``phi_rad`` must be independently measured or explicitly BENCH_SIMULATED;
    do not infer it from OHLC/market data. At zero current, phi must be zero
    and PF will be reported as undefined.

    Optional metered P/Q values are cross-checked against phasor-derived P/Q.
    """
    identity: CausalID
    voltage_ll_kv: float
    current_line_ka: float
    phi_rad: float
    source: str
    meter_p_mw: Optional[float] = None
    meter_q_mvar: Optional[float] = None

    def __post_init__(self):
        _check_finite(voltage_ll_kv=self.voltage_ll_kv,
                      current_line_ka=self.current_line_ka,
                      phi_rad=self.phi_rad)
        if self.voltage_ll_kv <= 0 or self.current_line_ka < 0:
            raise ValueError("invalid electrical voltage/current")
        if not (-pi <= self.phi_rad <= pi):
            raise ValueError("signed phase angle must be in [-pi, pi]")
        if self.source not in (SOURCE_BENCH, SOURCE_EXTERNAL):
            raise ValueError("unverified electrical measurement provenance")
        if self.current_line_ka == 0 and self.phi_rad != 0:
            raise ValueError("phase angle undefined at zero current: require zero sentinel")
        for k in ("meter_p_mw", "meter_q_mvar"):
            value = getattr(self, k)
            if value is not None and not _number(value):
                raise ValueError(f"invalid {k}")


@dataclass(frozen=True)
class PhaseAnglesRMS:
    """Alternative input retaining separate voltage and current phasor angles."""
    identity: CausalID
    voltage_ll_kv: float
    current_line_ka: float
    voltage_phase_angle_rad: float
    current_phase_angle_rad: float
    source: str

    def rms(self) -> ThreePhaseRMS:
        _check_finite(voltage_phase_angle_rad=self.voltage_phase_angle_rad,
                      current_phase_angle_rad=self.current_phase_angle_rad)
        phi = _wrap_angle(self.voltage_phase_angle_rad - self.current_phase_angle_rad)
        if self.current_line_ka == 0:
            phi = 0.0
        return ThreePhaseRMS(self.identity, self.voltage_ll_kv,
                             self.current_line_ka, phi, self.source)


@dataclass(frozen=True)
class ElectricalPower:
    identity: CausalID
    source: str
    voltage_ll_kv: float
    current_line_ka: float
    voltage_pu: float
    phi_rad: Optional[float]             # undefined if I=0
    cos_phi: Optional[float]             # signed P/S, undefined if S=0
    power_factor_magnitude: Optional[float]
    power_factor_status: str            # LAGGING, LEADING, UNITY, UNDEFINED
    apparent_mva: float
    active_mw: float
    reactive_mvar: float
    p_pu: float
    q_pu: float
    overload_relative_to_rating: bool   # diagnostic only; not a protection trip


def measure_three_phase(
    rms: ThreePhaseRMS,
    rating: GeneratorRating,
    *,
    meter_abs_tolerance: float = 1e-6,
    meter_relative_tolerance: float = 1e-6,
) -> ElectricalPower:
    """Calculate P/Q/S/PF from synchronized balanced three-phase RMS measurements.

    Independent metered P/Q, if supplied, must agree with the phasor values.
    This is measurement validation only; protective thresholds remain owned
    by the existing AVR and safety systems.
    """
    if rms.identity.bay_id != rating.bay_id:
        raise ValueError("electrical measurement bay/rating mismatch")
    _check_finite(meter_abs_tolerance=meter_abs_tolerance,
                  meter_relative_tolerance=meter_relative_tolerance)
    if meter_abs_tolerance < 0 or meter_relative_tolerance < 0:
        raise ValueError("invalid meter tolerances")

    apparent = _SQRT3 * rms.voltage_ll_kv * rms.current_line_ka
    phi = rms.phi_rad if rms.current_line_ka else None
    active = apparent * cos(rms.phi_rad)
    reactive = apparent * sin(rms.phi_rad)
    if not all(isfinite(x) for x in (apparent, active, reactive)):
        raise ValueError("electrical power calculation overflow")

    for label, reported, computed in (
        ("MW", rms.meter_p_mw, active),
        ("MVAR", rms.meter_q_mvar, reactive),
    ):
        if reported is not None:
            tolerance = meter_abs_tolerance + meter_relative_tolerance * max(abs(reported), abs(computed))
            if abs(reported - computed) > tolerance:
                raise ValueError(f"inconsistent independent meter {label}")

    if apparent <= _EPS:
        cosphi = None
        pf = None
        status = "UNDEFINED"
    else:
        cosphi = max(-1.0, min(1.0, active / apparent))
        pf = abs(cosphi)
        status = "UNITY" if abs(reactive) <= 1e-10 * max(apparent, 1.0) else (
            "LAGGING" if reactive > 0 else "LEADING"
        )

    return ElectricalPower(
        identity=rms.identity, source=rms.source,
        voltage_ll_kv=rms.voltage_ll_kv,
        current_line_ka=rms.current_line_ka,
        voltage_pu=rms.voltage_ll_kv / rating.rated_voltage_ll_kv,
        phi_rad=phi, cos_phi=cosphi,
        power_factor_magnitude=pf, power_factor_status=status,
        apparent_mva=apparent, active_mw=active, reactive_mvar=reactive,
        p_pu=active / rating.rated_mva,
        q_pu=reactive / rating.rated_mva,
        overload_relative_to_rating=apparent > rating.rated_mva + 1e-10,
    )


def to_voltage_sample(power: ElectricalPower, *, field_feedback_pu: float) -> VoltageSample:
    """VoltageAVR measurement adapter; never touches ExposureAVR or order flow."""
    _check_finite(field_feedback_pu=field_feedback_pu)
    if field_feedback_pu < 0:
        raise ValueError("negative excitation feedback")
    return VoltageSample(
        bar_index=power.identity.bar_index,
        timestamp=power.identity.timestamp,
        bay_id=power.identity.bay_id,
        terminal_voltage_pu=power.voltage_pu,
        field_feedback_pu=field_feedback_pu,
        reactive_power_pu=power.q_pu,
        source=power.source,
    )


def reactive_droop_calibration(*, reactive_droop: float, q_pu: float) -> float:
    """Expected per-unit shift in voltage reference under the chosen Q/Sbase convention."""
    _check_finite(reactive_droop=reactive_droop, q_pu=q_pu)
    if reactive_droop < 0 or reactive_droop >= 1:
        raise ValueError("invalid reactive droop")
    return -reactive_droop * q_pu


@dataclass(frozen=True)
class BenchGeneratorConfig:
    rating: GeneratorRating
    field_tau_s: float = 3.0
    voltage_tau_s: float = 2.0
    reactive_tau_s: float = 4.0
    field_to_voltage_gain: float = 0.55
    real_power_voltage_droop_gain: float = 0.04
    reactive_voltage_droop_gain: float = 0.015
    field_to_reactive_gain: float = 0.40
    power_to_reactive_gain: float = 0.08

    def __post_init__(self):
        numeric_fields = (
            self.field_tau_s, self.voltage_tau_s, self.reactive_tau_s,
            self.field_to_voltage_gain, self.real_power_voltage_droop_gain,
            self.reactive_voltage_droop_gain, self.field_to_reactive_gain,
            self.power_to_reactive_gain,
        )
        if not all(_number(x) for x in numeric_fields):
            raise ValueError("invalid bench configuration")
        if min(self.field_tau_s, self.voltage_tau_s, self.reactive_tau_s) <= 0:
            raise ValueError("invalid bench time constants")
        if self.field_to_voltage_gain <= 0 or self.field_to_reactive_gain <= 0:
            raise ValueError("bench must respond to excitation")


@dataclass(frozen=True)
class GeneratorBenchOutput:
    electrical: ElectricalPower
    avr_sample: VoltageSample
    field_actual_pu: float
    model_active_mw: float
    model_reactive_mvar: float


class ThreePhaseGeneratorBench:
    """Synthetic balanced synchronous-generator equivalent ONLY.

    Dynamics are illustrative, not an identified synchronous machine.
    Prime mover power (MW) is an EXTERNAL input; governor dynamics are not
    simulated here, and this bench NEVER creates brokerage orders.

    Causal update order:
      1. Apply prior command to exciter and advance separate field state.
      2. Advance voltage and reactive response from field, bus and P input.
      3. Independently synthesize RMS line current and voltage/current angle
         from modeled P and Q using complex-power identities.
      4. Reconstruct P/Q through measure_three_phase and expose a VoltageSample.
      5. Controller uses that sample to command the NEXT step.
    """
    def __init__(self, config: BenchGeneratorConfig):
        self.config = config
        self.field_pu = 1.0
        self.voltage_pu = 1.0
        self.q_pu = 0.0
        self.last_bar = -1
        self.last_timestamp: Optional[str] = None

    def advance(
        self,
        *,
        bar_index: int,
        timestamp: str,
        previous_excitation_command_pu: float,
        bus_voltage_pu: float,
        mechanical_active_power_mw: float,
        reactive_disturbance_mvar: float = 0.0,
        dt: float = 1.0,
    ) -> GeneratorBenchOutput:
        c = self.config
        ident = CausalID(bar_index, timestamp, c.rating.bay_id, "GENERATOR_BENCH")
        _check_finite(previous_excitation_command_pu=previous_excitation_command_pu,
                      bus_voltage_pu=bus_voltage_pu,
                      mechanical_active_power_mw=mechanical_active_power_mw,
                      reactive_disturbance_mvar=reactive_disturbance_mvar, dt=dt)
        if (bar_index <= self.last_bar or dt <= 0 or bus_voltage_pu <= 0
                or previous_excitation_command_pu < 0):
            raise ValueError("invalid or replayed generator bench observation")
        if self.last_timestamp is not None and (
            datetime.fromisoformat(timestamp) <= datetime.fromisoformat(self.last_timestamp)
        ):
            raise ValueError("generator bench timestamp not increasing")

        # No future controller command enters these state transitions.
        alpha_e = 1.0 - exp(-dt / c.field_tau_s)
        alpha_v = 1.0 - exp(-dt / c.voltage_tau_s)
        alpha_q = 1.0 - exp(-dt / c.reactive_tau_s)
        self.field_pu += alpha_e * (previous_excitation_command_pu - self.field_pu)
        p_pu = mechanical_active_power_mw / c.rating.rated_mva
        q_target_pu = (c.field_to_reactive_gain * (self.field_pu - 1.0)
                       + c.power_to_reactive_gain * p_pu
                       + reactive_disturbance_mvar / c.rating.rated_mva)
        self.q_pu += alpha_q * (q_target_pu - self.q_pu)
        voltage_target = (bus_voltage_pu
                          + c.field_to_voltage_gain * (self.field_pu - 1.0)
                          - c.real_power_voltage_droop_gain * p_pu
                          - c.reactive_voltage_droop_gain * self.q_pu)
        self.voltage_pu += alpha_v * (voltage_target - self.voltage_pu)
        if not all(isfinite(x) for x in (self.field_pu, self.q_pu, self.voltage_pu)):
            raise ValueError("generator bench numerical overflow")
        if self.voltage_pu <= 0:
            raise ValueError("bench voltage nonpositive")

        q_mvar = self.q_pu * c.rating.rated_mva
        p_mw = mechanical_active_power_mw
        apparent = hypot(p_mw, q_mvar)
        v_kv = self.voltage_pu * c.rating.rated_voltage_ll_kv
        current_ka = apparent / (_SQRT3 * v_kv)
        phi = atan2(q_mvar, p_mw) if apparent > _EPS else 0.0
        rms = ThreePhaseRMS(ident, v_kv, current_ka, phi, SOURCE_BENCH,
                            meter_p_mw=p_mw, meter_q_mvar=q_mvar)
        electrical = measure_three_phase(rms, c.rating)
        sample = to_voltage_sample(electrical, field_feedback_pu=self.field_pu)
        self.last_bar = bar_index
        self.last_timestamp = timestamp
        return GeneratorBenchOutput(electrical, sample, self.field_pu, p_mw, q_mvar)

    def snapshot(self) -> dict:
        return {
            "schema": 1,
            "config_hash": sha256(json.dumps(asdict(self.config), sort_keys=True).encode()).hexdigest(),
            "field_pu": self.field_pu,
            "voltage_pu": self.voltage_pu,
            "q_pu": self.q_pu,
            "last_bar": self.last_bar,
            "last_timestamp": self.last_timestamp,
        }

    def restore(self, snap: dict) -> None:
        if (snap.get("schema") != 1 or
                snap.get("config_hash") != sha256(json.dumps(asdict(self.config), sort_keys=True).encode()).hexdigest()):
            raise ValueError("generator bench configuration mismatch")
        vals = [snap.get(k) for k in ("field_pu", "voltage_pu", "q_pu")]
        if not all(_number(x) for x in vals) or vals[0] < 0 or vals[1] <= 0:
            raise ValueError("invalid generator bench checkpoint")
        bar = snap.get("last_bar")
        stamp = snap.get("last_timestamp")
        if not isinstance(bar, int) or isinstance(bar, bool) or bar < -1:
            raise ValueError("invalid generator bench bar")
        if bar == -1:
            if stamp is not None:
                raise ValueError("unexpected timestamp for new bench")
        else:
            CausalID(bar, stamp, self.config.rating.bay_id, "GENERATOR_BENCH")
        self.field_pu, self.voltage_pu, self.q_pu = map(float, vals)
        self.last_bar, self.last_timestamp = bar, stamp


def build_bench_controller(rating: GeneratorRating, reactive_droop: float) -> VoltageAVR:
    """Preserve the supplied AVR control law; only configure its study droop."""
    from dataclasses import replace
    c = replace(AVRConfig.experimental(rating.bay_id), reactive_droop=reactive_droop)
    return VoltageAVR(c)
