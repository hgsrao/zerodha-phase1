"""R8 vendored copy of revision6_avr/r6_turbine_governor.py (source commit 3a764cc, sha256 2a6c3c89eb3a7461).
Copied in full so R8 carries these formulas itself; only the imports were re-pointed to R8.
"""
from __future__ import annotations
"""R6 PHYSICAL-EQUIVALENT turbine governor: constant settable droop R, rotor inertia M/J/H.

Isolated simulation component.  It is NOT the Mark V strategic governor (revision5/governor.py,
market-return R-feedback PID, dynamic_z, FSR gate, trailing floor) and is not called by the
trading path: market bars carry no causal rotor-speed / electrical-power measurement.

Conventions (per unit on the machine base S_base = rated_mva, speed base = synchronous speed):
    speed_error_pu = speed_pu - 1
    droop          P_request = P_ref - speed_error_pu / R ;  P_cmd = clamp(P_request, Pmin, Pmax)
                   (R = 0.04: a 4 % speed drop drives a 1.0 pu power increase)
    turbine lag    tau * dPm/dt = P_cmd - Pm         (exact ZOH step; separate from rotor inertia)
    swing          M * dspeed_pu/dt = Pm - Pe - D*(speed_pu - 1),  M = 2H  [s]
    inertia        omega_mech = 2*pi*f_nom / pole_pairs ;  H = J*omega_mech**2/(2*S_base_VA)
                   J = M*S_base_VA/omega_mech**2   (needs rated_mva and pole_pairs, NOT in repo)
Physical dt in seconds, internally sub-stepped (dt_max_s); a trading-bar duration is never used.

Correction of the Gemini reference (repo R5 governor_patch.py):
  * its `raw_pid - error/R` mixes an undefined error with droop -> replaced by the explicit
    speed-error droop above (no PID inside this module; Mark V PID untouched);
  * its alpha = dt/(M+dt) filter is a first-order lag, not rotor inertia -> modelled as the
    distinct turbine lag tau, and inertia as the swing equation with M;
  * R = 0, M <= 0, nonfinite inputs and dt <= 0 rejected; output saturated; no integrator, so no
    windup; J/H/machine base added; gas/steam bays configured individually.
"""

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from math import exp, isfinite, pi
from typing import Dict, Optional
import json

BAYS = ("GTG1_HEAVY_INDUSTRY", "GTG2_TECH_TELECOM", "CSTG1_BFSI", "CSTG2_CONSUMER_AUTO", "BPSTG_HEALTHCARE")
GAS_BAYS = frozenset(BAYS[:2])
PROVISIONAL = "PROVISIONAL_R6_MODEL_NOT_OEM_NOT_FOR_COMMISSIONING"


def _num(name, value, *, positive=False, nonneg=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if positive and value <= 0:
        raise ValueError(f"{name} must be > 0")
    if nonneg and value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _pole_pairs(p):
    if isinstance(p, bool) or not isinstance(p, int) or p < 1:
        raise ValueError("pole_pairs must be a positive integer")
    return p


def omega_mechanical(nominal_frequency_hz: float, pole_pairs: int) -> float:
    return 2.0 * pi * _num("nominal_frequency_hz", nominal_frequency_hz, positive=True) / _pole_pairs(pole_pairs)


def inertia_constant_h(*, j_kg_m2: float, s_base_va: float, f_nominal_hz: float, pole_pairs: int) -> float:
    w = omega_mechanical(f_nominal_hz, pole_pairs)
    return _num("J", j_kg_m2, positive=True) * w * w / (2.0 * _num("S_base_VA", s_base_va, positive=True))


def moment_from_m(*, inertia_m: float, rated_mva: float, nominal_frequency_hz: float, pole_pairs: int) -> float:
    w = omega_mechanical(nominal_frequency_hz, pole_pairs)
    return _num("M", inertia_m, positive=True) * _num("rated_mva", rated_mva, positive=True) * 1e6 / (w * w)


@dataclass(frozen=True)
class TurbinePhysicalConfig:
    """Immutable operating configuration (operator-set before a run; never adapted)."""
    bay_id: str
    machine_kind: str                         # GAS_TURBINE | STEAM_TURBINE  (alias: turbine_type)
    droop_r: float = 0.04
    inertia_m: float = 8.0                    # swing coefficient M = 2H [s] on the machine base
    inertia_j: Optional[float] = None         # kg m^2 (requires rated_mva and pole_pairs)
    rated_mva: Optional[float] = None
    nominal_frequency_hz: float = 50.0
    pole_pairs: Optional[int] = None
    damping_d: float = 0.0
    turbine_response_tau: float = 0.3
    power_min_pu: float = 0.0
    power_max_pu: float = 1.0
    provenance: str = PROVISIONAL

    def __post_init__(self):
        if self.bay_id not in BAYS:
            raise ValueError("unknown bay")
        if self.machine_kind not in ("GAS_TURBINE", "STEAM_TURBINE"):
            raise ValueError("machine_kind")
        r = _num("droop_r", self.droop_r, positive=True)
        if r >= 1.0:
            raise ValueError("droop_r must be < 1 pu")
        _num("inertia_m", self.inertia_m, positive=True)
        _num("damping_d", self.damping_d, nonneg=True)
        _num("turbine_response_tau", self.turbine_response_tau, positive=True)
        _num("nominal_frequency_hz", self.nominal_frequency_hz, positive=True)
        if not _num("power_min_pu", self.power_min_pu) < _num("power_max_pu", self.power_max_pu):
            raise ValueError("power_min_pu < power_max_pu required")
        if self.rated_mva is not None or self.pole_pairs is not None:
            if self.rated_mva is None or self.pole_pairs is None:
                raise ValueError("rated_mva and pole_pairs must be supplied together")
            derived = moment_from_m(inertia_m=self.inertia_m, rated_mva=self.rated_mva,
                                    nominal_frequency_hz=self.nominal_frequency_hz, pole_pairs=self.pole_pairs)
            if self.inertia_j is not None:
                j = _num("inertia_j", self.inertia_j, positive=True)
                if abs(derived - j) > max(1e-9, derived * 1e-9):
                    raise ValueError("inconsistent M and J")
        elif self.inertia_j is not None:
            raise ValueError("inertia_j requires rated_mva and pole_pairs (machine base)")

    @property
    def turbine_type(self) -> str:
        return self.machine_kind

    @property
    def m_s(self) -> float:
        return self.inertia_m

    @property
    def inertia_h_s(self) -> float:
        return self.inertia_m / 2.0

    @property
    def derived_j(self) -> Optional[float]:
        if self.rated_mva is None:
            return None
        return moment_from_m(inertia_m=self.inertia_m, rated_mva=self.rated_mva,
                             nominal_frequency_hz=self.nominal_frequency_hz, pole_pairs=self.pole_pairs)

    def config_hash(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_inertia(cls, *, j_kg_m2, s_base_va, pole_pairs, **kw):
        h = inertia_constant_h(j_kg_m2=j_kg_m2, s_base_va=s_base_va,
                               f_nominal_hz=kw.get("nominal_frequency_hz", 50.0), pole_pairs=pole_pairs)
        kw.update(inertia_m=2.0 * h, inertia_j=j_kg_m2, rated_mva=s_base_va / 1e6, pole_pairs=pole_pairs)
        return cls(**kw)


TurbineGovernorConfig = TurbinePhysicalConfig        # backward-compatible name


def repository_defaults() -> Dict[str, TurbinePhysicalConfig]:
    """Requested five-bay operating configuration: R = 0.040 for all bays; provisional M = 8.0 s
    (gas) and 5.0 s (steam).  Turbine lag tau and damping D come from revision5 machine_archetypes
    (R5_ENGINEERING_CANDIDATE_NOT_OEM).  No MVA / pole pairs / J exist in the repository."""
    from .r8_machine_archetypes import MACHINE_ARCHETYPES
    out = {}
    for bay in BAYS:
        d = MACHINE_ARCHETYPES[bay].dynamics
        out[bay] = TurbinePhysicalConfig(
            bay_id=bay, machine_kind="GAS_TURBINE" if bay in GAS_BAYS else "STEAM_TURBINE",
            droop_r=0.04, inertia_m=8.0 if bay in GAS_BAYS else 5.0,
            nominal_frequency_hz=d.nominal_frequency_hz, damping_d=d.damping_pu,
            turbine_response_tau=d.turbine_time_constant_s,
            power_min_pu=d.mechanical_power_min_pu, power_max_pu=d.mechanical_power_max_pu)
    return out


def speed_droop_command(*, p_ref_pu, droop_r, p_min_pu, p_max_pu, speed_pu=None, omega_pu=None):
    speed = _num("speed_pu", speed_pu if speed_pu is not None else omega_pu, positive=True)
    request = _num("p_ref_pu", p_ref_pu) - (speed - 1.0) / _num("droop_r", droop_r, positive=True)
    return min(_num("p_max_pu", p_max_pu), max(_num("p_min_pu", p_min_pu), request))


@dataclass
class TurbineRuntimeState:
    speed_pu: float = 1.0
    mechanical_power_pu: float = 0.0
    electrical_power_pu: float = 0.0
    commanded_power_pu: float = 0.0
    time_s: float = 0.0
    steps: int = 0
    operating_state: str = "RUNNING"          # RUNNING | REJECTED_INPUT (last call)


class TurbineGovernorUnit:
    """One bay.  Config immutable; runtime state mutable and private to this instance."""

    def __init__(self, config: TurbinePhysicalConfig, *, speed_pu=1.0, omega_pu=None,
                 mechanical_power_pu=None, p_mech_pu=None):
        if not isinstance(config, TurbinePhysicalConfig):
            raise TypeError("config must be TurbinePhysicalConfig")
        self._config = config
        pm0 = mechanical_power_pu if mechanical_power_pu is not None else p_mech_pu
        self.state = TurbineRuntimeState(
            speed_pu=_num("speed0", speed_pu if omega_pu is None else omega_pu, positive=True),
            mechanical_power_pu=_num("mechanical_power0", config.power_min_pu if pm0 is None else pm0))
        self.last_timestamp_s: Optional[float] = None

    # config is read-only: there is no setter, and dataclass(frozen) blocks field mutation
    @property
    def config(self) -> TurbinePhysicalConfig:
        return self._config

    @property
    def speed_pu(self):
        return self.state.speed_pu

    omega_pu = speed_pu

    @property
    def mechanical_power_pu(self):
        return self.state.mechanical_power_pu

    p_mech_pu = mechanical_power_pu

    @property
    def time_s(self):
        return self.state.time_s

    @property
    def steps(self):
        return self.state.steps

    def _reject(self, reason: str) -> dict:
        self.state.operating_state = "REJECTED_INPUT"
        return {"accepted": False, "reason": reason, "bay_id": self.config.bay_id, "speed_pu": self.state.speed_pu,
                "mechanical_power_pu": self.state.mechanical_power_pu, "droop_r": self.config.droop_r}

    def evaluate(self, electrical_power_pu, reference_power_pu, dt_seconds, *, timestamp_s=None, dt_max_s=0.01) -> dict:
        """Contract step: never raises on bad input; returns accepted=False with the reason and
        leaves the state unchanged."""
        try:
            pe = _num("electrical_power_pu", electrical_power_pu)
            pref = _num("reference_power_pu", reference_power_pu)
            dt = _num("dt_seconds", dt_seconds, positive=True)
            h_max = _num("dt_max_s", dt_max_s, positive=True)
            if timestamp_s is not None:
                t = _num("timestamp_s", timestamp_s)
                if self.last_timestamp_s is not None and abs(t - (self.last_timestamp_s + dt)) > 1e-9:
                    raise ValueError("timestamp not consistent with last timestamp + dt (non-monotonic or gap)")
        except ValueError as exc:
            return self._reject(str(exc))
        c, s = self.config, self.state
        n = max(1, int(-(-dt // h_max)))
        h = dt / n
        speed, pm = s.speed_pu, s.mechanical_power_pu
        for _ in range(n):
            cmd = speed_droop_command(p_ref_pu=pref, speed_pu=speed, droop_r=c.droop_r,
                                      p_min_pu=c.power_min_pu, p_max_pu=c.power_max_pu)
            pm = cmd + (pm - cmd) * exp(-h / c.turbine_response_tau)                    # turbine lag
            speed += h * (pm - pe - c.damping_d * (speed - 1.0)) / c.inertia_m            # swing
            if not (isfinite(speed) and speed > 0):
                return self._reject("speed state diverged")
        request = pref - (speed - 1.0) / c.droop_r
        cmd = min(c.power_max_pu, max(c.power_min_pu, request))
        s.speed_pu, s.mechanical_power_pu, s.electrical_power_pu, s.commanded_power_pu = speed, pm, pe, cmd
        s.time_s += dt
        s.steps += 1
        s.operating_state = "RUNNING"
        if timestamp_s is not None:
            self.last_timestamp_s = float(timestamp_s)
        dspeed = (pm - pe - c.damping_d * (speed - 1.0)) / c.inertia_m
        return {"accepted": True, "reason": "", "bay_id": c.bay_id, "turbine_type": c.turbine_type,
                "t_s": s.time_s, "speed_pu": speed, "omega_pu": speed, "frequency_hz": speed * c.nominal_frequency_hz,
                "dspeed_dt_pu_per_s": dspeed, "requested_power_pu": request, "p_command_pu": cmd,
                "mechanical_power_pu": pm, "p_mech_pu": pm, "p_electrical_pu": pe,
                "droop_r": c.droop_r, "inertia_m": c.inertia_m, "inertia_h_s": c.inertia_h_s,
                "inertia_j": c.derived_j, "power_limits_pu": [c.power_min_pu, c.power_max_pu],
                "saturated": "HIGH" if request > c.power_max_pu else "LOW" if request < c.power_min_pu else ""}

    def step(self, *, dt_s, p_ref_pu, p_electrical_pu=None, p_elec_pu=None, dt_max_s=0.01) -> dict:
        """Strict variant used by earlier callers: raises ValueError on invalid input."""
        r = self.evaluate(p_electrical_pu if p_electrical_pu is not None else p_elec_pu, p_ref_pu, dt_s, dt_max_s=dt_max_s)
        if not r["accepted"]:
            if "diverged" in r["reason"]:
                raise FloatingPointError(r["reason"])
            raise ValueError(r["reason"])
        return r

    def snapshot(self) -> dict:
        return {"schema": 3, "config_hash": self.config.config_hash(), **asdict(self.state),
                "last_timestamp_s": self.last_timestamp_s}

    def restore(self, snap: dict) -> None:
        """Restores runtime state only; the config can never be changed by a restore."""
        if snap.get("schema") != 3 or snap.get("config_hash") != self.config.config_hash():
            raise ValueError("incompatible governor checkpoint (schema or configuration differs)")
        self.state = TurbineRuntimeState(
            speed_pu=_num("speed", snap["speed_pu"], positive=True),
            mechanical_power_pu=_num("mechanical_power", snap["mechanical_power_pu"]),
            electrical_power_pu=_num("electrical_power", snap["electrical_power_pu"]),
            commanded_power_pu=_num("commanded_power", snap["commanded_power_pu"]),
            time_s=_num("time", snap["time_s"], nonneg=True), steps=int(snap["steps"]),
            operating_state=str(snap["operating_state"]))
        self.last_timestamp_s = snap.get("last_timestamp_s")

    def reset(self, *, speed_pu=1.0, mechanical_power_pu=None) -> None:
        """Operator reset of the RUNTIME state; the droop configuration is untouched."""
        self.state = TurbineRuntimeState(speed_pu=_num("speed", speed_pu, positive=True),
                                         mechanical_power_pu=_num("pm", self.config.power_min_pu
                                                                  if mechanical_power_pu is None else mechanical_power_pu))
        self.last_timestamp_s = None


def steady_state(config, *, p_ref_pu, p_elec_pu):
    dw = (p_ref_pu - p_elec_pu) / (1.0 / config.droop_r + config.damping_d)
    pm = p_elec_pu + config.damping_d * dw
    return {"speed_pu": 1 + dw, "omega_pu": 1 + dw, "mechanical_power_pu": pm, "p_mech_pu": pm}
