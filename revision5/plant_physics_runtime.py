"""Revision-5 plant physics runtime: the turbine machines, their protection, unit startup and
synchronization, and the bay relays, stepped once per completed replay bar.

Before this module the native plant built these objects (``TurbineMachineModel`` and
``TurbineMechanicalProtection`` are installed in every bay at plant start) but the trading loop
never ran them.  This runtime runs all of them, every bar, from measurements the engine already
makes causally.  It adds no market signal of its own.

Modes
-----
off       not run (the engine behaves exactly as before).
observe   run on a SEPARATE shadow plant fed the same inputs.  Every trip, startup and
          synchronization is recorded; nothing touches the live plant, so trading is unchanged.
enforce   run on the LIVE plant.  A mechanical or relay trip opens the bay's unit breaker (52G,
          with ANSI-86 lockout where the protection says so), which the existing plant gate already
          treats as "bay unavailable" for new entries.  At each session start every bay goes
          through startup and must synchronize (25A + 25) before its breaker closes.

Market -> machine mapping (all causal, completed bar t only)
-----------------------------------------------------------
electrical power Pe       bay booked notional / bay effective allocation (bay loading, pu)
load reference            previous bar's Pe (one-bar dispatch lag): a large fill is a load step,
                          closing a large position at once is a load rejection
vibration (mm/s)          VIBRATION_BASE + VIBRATION_GAIN x worst member wick/ATR "vibration"
                          (the governor's own telemetry)
exhaust spread (C, GT)    trip temperature x bay exhaust spread / governor exhaust-spread trip:
                          the turbine trips exactly where the governor's spread trip sits
exhaust temperature (GT)  EXHAUST_BASE + (trip - base) x drawdown / FSRT drawdown span
bearing temperature       first-order heating from loading: ambient + rise x Pe^2
lube oil / hydraulic      execution-infrastructure health (the revision-3 safety-panel
                          convention): nominal while the paper/venue port is serviceable
flame / fuel (GT)         a completed bar arrived for the bay / the bay has allocation
bus for synchronization   nominal 50 Hz, 1.0 pu; the generator side is the machine model itself

Time scaling
------------
The machine model integrates a swing equation with explicit Euler; a 60 s step is unstable.
Each bar holds its inputs constant while the machine runs ``SCAN_STEPS`` steps of
``SCAN_DT_SECONDS`` (``PLANT_SECONDS_PER_BAR`` of plant time per bar).  Relay timing uses the
same plant seconds.

Settings
--------
The synchronizer and relay settings below are ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED.  The
R5 synchronizer classes deliberately carry no production defaults ("mandatory injected
parameters"); these are the injected values, kept here in one place and reported at startup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Callable, Dict, List, Optional

from revision5.relay_coordination import (
    Breaker86Lockout,
    DifferentialSetting,
    DistanceProtectionRelay,
    DistanceZone,
    DualStageIDMTRelay,
    GeneratorDifferentialRelay,
    HighSetStage,
    IDMTLowStage,
    IECInverseCurve,
)
from revision5.startup_synchronization import (
    AutoSynchronizerSpec,
    AutomaticSynchronizer25A,
    StartupState,
    SynchrocheckRelay25,
    SynchrocheckSpec,
)

PHYSICS_MODES = ("off", "observe", "enforce")

# --- time scaling ----------------------------------------------------------------------------
SCAN_DT_SECONDS = 0.1
SCAN_STEPS = 50
PLANT_SECONDS_PER_BAR = SCAN_DT_SECONDS * SCAN_STEPS
SYNC_STEPS_PER_BAR = 50

# --- market -> machine mapping constants (ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED) ----------
NOMINAL_HZ = 50.0
VIBRATION_BASE_MM_S = 2.0
VIBRATION_GAIN_MM_S = 4.0          # wick/ATR of 2.0 -> 10 mm/s, the vibration trip
EXHAUST_BASE_C = 450.0
BEARING_AMBIENT_C = 60.0
BEARING_RISE_C = 40.0              # full load -> 100 C (alarm 95, trip 115 only on overload)
BEARING_TIME_CONSTANT_BARS = 10.0
LUBE_OIL_NOMINAL_BAR = 2.5
HYDRAULIC_NOMINAL_BAR = 3.0
FSNL_TOLERANCE_HZ = 0.25

# --- synchronizer settings (IEEE C50.12 / C37.118-practice values) ----------------------------
SYNCHROCHECK_25 = dict(nominal_frequency_hz=NOMINAL_HZ, max_delta_frequency_hz=0.10,
                       max_delta_voltage_pu=0.05, max_phase_angle_deg=10.0,
                       breaker_closing_time_seconds=0.10, dead_bus_voltage_pu=0.20)
AUTO_SYNCHRONIZER_25A = dict(preferred_slip_hz=0.05, frequency_deadband_hz=0.02,
                             voltage_deadband_pu=0.01, phase_capture_deg=5.0,
                             breaker_closing_time_seconds=0.10, speed_pulse_width_seconds=0.20,
                             voltage_pulse_width_seconds=0.20, pulse_interval_seconds=1.0,
                             synchronization_timeout_seconds=600.0, max_control_pulses=600)
SYNC_SPEED_REFERENCE = dict(initial_speed_reference_hz=NOMINAL_HZ, minimum_speed_reference_hz=49.0,
                            maximum_speed_reference_hz=51.0, speed_reference_rate_hz_per_second=0.10)
SYNC_VOLTAGE_REFERENCE = dict(initial_voltage_reference_pu=1.0, minimum_voltage_reference_pu=0.90,
                              maximum_voltage_reference_pu=1.10, voltage_reference_rate_pu_per_second=0.05)

# --- bay relay settings ------------------------------------------------------------------------
IDMT_51 = dict(low=IDMTLowStage(pickup_multiple=1.0, tms=0.10, curve=IECInverseCurve.STANDARD_INVERSE),
               high=HighSetStage(pickup_multiple=1.5, delay_seconds=0.0))
DIFFERENTIAL_87G = DifferentialSetting(minimum_pickup_pu=0.05, slope=0.25, high_set_pu=0.50)
DISTANCE_21 = (DistanceZone(name="Z1", reach_ratio=0.25, delay_seconds=0.0),
               DistanceZone(name="Z2", reach_ratio=0.50, delay_seconds=PLANT_SECONDS_PER_BAR * 2))


def settings_report() -> Dict[str, Any]:
    """Every injected physics setting, for the engine's startup report."""
    return {"status": "ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED",
            "scan_dt_seconds": SCAN_DT_SECONDS, "scan_steps": SCAN_STEPS,
            "plant_seconds_per_bar": PLANT_SECONDS_PER_BAR,
            "mapping": {"vibration_base_mm_s": VIBRATION_BASE_MM_S, "vibration_gain_mm_s": VIBRATION_GAIN_MM_S,
                        "exhaust_base_c": EXHAUST_BASE_C, "bearing_ambient_c": BEARING_AMBIENT_C,
                        "bearing_rise_c": BEARING_RISE_C, "bearing_time_constant_bars": BEARING_TIME_CONSTANT_BARS,
                        "lube_oil_nominal_bar": LUBE_OIL_NOMINAL_BAR,
                        "hydraulic_nominal_bar": HYDRAULIC_NOMINAL_BAR},
            "synchrocheck_25": dict(SYNCHROCHECK_25), "auto_synchronizer_25a": dict(AUTO_SYNCHRONIZER_25A),
            "idmt_51": {"low_pickup": IDMT_51["low"].pickup_multiple, "tms": IDMT_51["low"].tms,
                        "curve": IDMT_51["low"].curve.value, "high_pickup": IDMT_51["high"].pickup_multiple},
            "differential_87g": vars(DIFFERENTIAL_87G) if hasattr(DIFFERENTIAL_87G, "__dict__") else str(DIFFERENTIAL_87G),
            "distance_21": [vars(z) if hasattr(z, "__dict__") else str(z) for z in DISTANCE_21]}


@dataclass
class BayInputs:
    """One bay's causal inputs for the completed bar."""
    bar_present: bool
    vibration_ratio: Optional[float]      # worst member wick/ATR (governor telemetry)
    velocity: Optional[float]             # worst member |move|/ATR (governor telemetry)
    exhaust_spread: Optional[float]       # governor bay exhaust spread
    broker_notional: float                # broker-reported notional of the bay's open positions


@dataclass
class _BayRuntime:
    previous_pe: float = 0.0
    bearing_c: float = BEARING_AMBIENT_C
    phase_deg: float = 0.0
    relay_51: Any = None
    relay_87g: Any = None
    relay_21: Any = None
    lockout_86: Any = None
    events: List[str] = field(default_factory=list)


def _finite_or(value, fallback: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return value if isfinite(value) else fallback


class PlantPhysicsRuntime:
    """Steps every bay's machine, protection, startup/synchronization and relays once per bar."""

    def __init__(self, plant, *, mode: str, exhaust_spread_trip: float, fsrt_drawdown_span: float,
                 record: Optional[Callable[[str, Any, str, Dict[str, Any]], None]] = None,
                 load_plant=None):
        """``plant`` is where the physics acts (the live plant in enforce, a shadow plant in
        observe).  ``load_plant`` is where bay loading is read (always the live plant)."""
        if mode not in PHYSICS_MODES or mode == "off":
            raise ValueError(f"plant physics mode must be one of {PHYSICS_MODES[1:]} to build a runtime")
        if not (exhaust_spread_trip > 0 and fsrt_drawdown_span > 0):
            raise ValueError("exhaust_spread_trip and fsrt_drawdown_span must be positive")
        self.mode = mode
        self.plant = plant
        self.load_plant = load_plant if load_plant is not None else plant
        self.exhaust_spread_trip = float(exhaust_spread_trip)
        self.fsrt_drawdown_span = float(fsrt_drawdown_span)
        self._record = record or (lambda *a, **k: None)
        self.counts: Dict[str, int] = {}
        self.last_bar_trips: List[Dict[str, Any]] = []   # trips raised by the latest step_bar
        self.bays: Dict[str, _BayRuntime] = {}
        for bay_id in plant.bays:
            plant.configure_unit_synchronizer(
                bay_id=bay_id,
                auto_synchronizer=AutomaticSynchronizer25A(AutoSynchronizerSpec(**AUTO_SYNCHRONIZER_25A)),
                synchrocheck_relay=SynchrocheckRelay25(SynchrocheckSpec(**SYNCHROCHECK_25)),
                **SYNC_SPEED_REFERENCE, **SYNC_VOLTAGE_REFERENCE)
            self.bays[bay_id] = _BayRuntime(
                relay_51=DualStageIDMTRelay(element="51", **IDMT_51),
                relay_87g=GeneratorDifferentialRelay(DIFFERENTIAL_87G),
                relay_21=DistanceProtectionRelay(DISTANCE_21),
                lockout_86=Breaker86Lockout())

    # ---- helpers ------------------------------------------------------------------------------
    def _count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def _trip(self, bay_id: str, timestamp, code: str, reason: str, source: str, lockout: bool) -> None:
        rt = self.bays[bay_id]
        rt.lockout_86.trip(f"{code}:{reason}") if lockout else None
        self._count(f"TRIP:{code}")
        self.last_bar_trips.append({"bay_id": bay_id, "code": code, "reason": reason, "lockout_86": lockout})
        self.plant.trip_unit_breaker(bay_id=bay_id, reason=f"{code}:{reason}", source=source, lockout=lockout)
        self._record("PLANT_PHYSICS_TRIP", timestamp, bay_id, {
            "mode": self.mode, "code": code, "reason": reason, "source": source, "lockout_86": lockout})

    def _allocation(self, bay_id: str) -> float:
        return float(getattr(self.load_plant.bays[bay_id], "effective_allocation", 0.0) or 0.0)

    def _pe(self, bay_id: str) -> float:
        allocation = self._allocation(bay_id)
        if allocation <= 0:
            return 0.0
        return max(0.0, self.load_plant.bay_bound_notional(bay_id) / allocation)

    # ---- session start: every unit starts, accelerates to FSNL and synchronizes --------------
    def begin_session(self, timestamp) -> None:
        for bay_id, rt in self.bays.items():
            network = self.plant.electrical_network
            if network.unit_breaker(bay_id).lockout_86 or rt.lockout_86.breaker_open:
                # Operator reset of the previous session's lockout before the morning start.
                self.plant.reset_unit_breaker(bay_id=bay_id)
                rt.lockout_86.reset()
                self._count("LOCKOUT_86_SESSION_RESET")
            bay = self.plant.bays[bay_id]
            if bay.tripped_offline:
                continue                       # the bay's own trip state is cleared by its session reset
            self.plant.prepare_unit_startup(bay_id=bay_id)
            rt.phase_deg = 37.0                # an arbitrary non-zero phase the 25A must close out
            self._count("UNIT_STARTUP_PREPARED")
            self._record("PLANT_UNIT_STARTUP", timestamp, bay_id, {"mode": self.mode})

    def _advance_startup(self, bay_id: str, machine_hz: float, inputs: BayInputs, spread_c: float,
                         spread_trip_c: Optional[float]) -> None:
        bay = self.plant.bays[bay_id]
        if not hasattr(bay, "startup_sequencer"):
            return
        state = bay.startup_sequencer.state
        if state in (StartupState.SYNC_READY, StartupState.DISPATCH_READY, StartupState.TRIPPED, StartupState.OFF) \
                and state != StartupState.OFF:
            return
        gas = bay_id.startswith("GTG")
        hold_c = 0.0 if spread_trip_c is None else spread_trip_c * (40.0 / 70.0)
        for _ in range(12):                     # walk the sequence; each call moves at most one state
            before = bay.startup_sequencer.state
            if before in (StartupState.SYNC_READY, StartupState.DISPATCH_READY, StartupState.TRIPPED):
                break
            self.plant.advance_unit_startup(
                bay_id=bay_id, start_command=True, starting_means_ready=True, crank_complete=True,
                flame_proven=(inputs.bar_present if gas else True),
                exhaust_spread_ok=(spread_c < hold_c) if gas and spread_trip_c else True,
                exhaust_spread_trip=(spread_c >= spread_trip_c) if gas and spread_trip_c else False,
                fsnl_reached=abs(machine_hz - NOMINAL_HZ) <= FSNL_TOLERANCE_HZ,
                synchronizer_ready=True, synchronizer_permissive=False, generator_breaker_closed=False,
                minimum_load_reached=False, auxiliary_services_stable=True, mechanical_trip=False)
            if bay.startup_sequencer.state == before:
                break

    def _synchronize(self, bay_id: str, timestamp) -> None:
        bay = self.plant.bays[bay_id]
        rt = self.bays[bay_id]
        if not hasattr(bay, "startup_sequencer") or bay.startup_sequencer.state != StartupState.SYNC_READY:
            return
        machine = bay.machine_model
        for _ in range(SYNC_STEPS_PER_BAR):
            speed_ref = float(bay.governor.sync_speed_reference_hz)
            machine.step(speed_reference_hz=speed_ref, load_reference_pu=0.0, electrical_power_pu=0.0,
                         dt_seconds=SCAN_DT_SECONDS)
            rt.phase_deg = (rt.phase_deg + 360.0 * (machine.frequency_hz - NOMINAL_HZ) * SCAN_DT_SECONDS) % 360.0
            phase = rt.phase_deg - 360.0 if rt.phase_deg > 180.0 else rt.phase_deg
            result = self.plant.run_unit_synchronization(
                bay_id=bay_id, dt_seconds=SCAN_DT_SECONDS, generator_frequency_hz=machine.frequency_hz,
                bus_frequency_hz=NOMINAL_HZ, generator_voltage_pu=float(bay.avr.sync_voltage_reference_pu),
                bus_voltage_pu=1.0, generator_phase_deg=phase, bus_phase_deg=0.0)
            self._count("SYNC_STEP")
            if result.get("breaker_closed"):
                self._count("52G_CLOSED_SYNCHRONIZED")
                for _ in range(3):              # SYNC_READY -> SYNCHRONIZED -> MINIMUM_LOAD -> DISPATCH_READY
                    if bay.startup_sequencer.state == StartupState.DISPATCH_READY:
                        break
                    bay.advance_startup(minimum_load_reached=True, auxiliary_services_stable=True,
                                        generator_breaker_closed=True)
                self._record("PLANT_UNIT_SYNCHRONIZED", timestamp, bay_id, {
                    "mode": self.mode, "generator_hz": machine.frequency_hz, "phase_deg": phase})
                return

    # ---- per bar ------------------------------------------------------------------------------
    def step_bar(self, timestamp, inputs_by_bay: Dict[str, BayInputs], drawdown: float) -> Dict[str, Any]:
        network = self.plant.electrical_network
        drawdown = max(0.0, _finite_or(drawdown, 0.0))
        self.last_bar_trips = []
        out: Dict[str, Any] = {}
        for bay_id, rt in self.bays.items():
            bay = self.plant.bays[bay_id]
            inputs = inputs_by_bay.get(bay_id) or BayInputs(False, None, None, None, 0.0)
            protection = getattr(bay, "mechanical_protection", None)
            spec = getattr(protection, "spec", None)
            gas = bay_id.startswith("GTG")
            spread_trip_c = getattr(spec, "exhaust_spread_trip_c", None) if spec is not None else None
            temp_trip_c = getattr(spec, "exhaust_temperature_trip_c", None) if spec is not None else None
            spread = _finite_or(inputs.exhaust_spread, 0.0)
            spread_c = (spread_trip_c * spread / self.exhaust_spread_trip) if spread_trip_c else 0.0
            exhaust_c = (EXHAUST_BASE_C + (temp_trip_c - EXHAUST_BASE_C) * min(1.5, drawdown / self.fsrt_drawdown_span)
                         if temp_trip_c else None)
            vibration = VIBRATION_BASE_MM_S + VIBRATION_GAIN_MM_S * max(0.0, _finite_or(inputs.vibration_ratio, 0.0))
            pe = self._pe(bay_id)
            rt.bearing_c += (BEARING_AMBIENT_C + BEARING_RISE_C * pe * pe - rt.bearing_c) / BEARING_TIME_CONSTANT_BARS
            breaker_closed = network.unit_available(bay_id)

            state = getattr(getattr(bay, "startup_sequencer", None), "state", None)
            if state is not None and state not in (StartupState.DISPATCH_READY, StartupState.SYNCHRONIZED,
                                                   StartupState.MINIMUM_LOAD) and not breaker_closed:
                self._advance_startup(bay_id, bay.machine_model.frequency_hz, inputs, spread_c, spread_trip_c)
                self._synchronize(bay_id, timestamp)
                breaker_closed = network.unit_available(bay_id)

            # Machine dynamics + mechanical protection (only while the unit is on line).
            scan = None
            if breaker_closed and not bay.tripped_offline:
                for _ in range(SCAN_STEPS):
                    scan = self.plant.run_unit_machine_scan(
                        bay_id=bay_id, dt_seconds=SCAN_DT_SECONDS, load_reference_pu=rt.previous_pe,
                        electrical_power_pu=pe, vibration_mm_s=vibration,
                        lube_oil_pressure_bar=LUBE_OIL_NOMINAL_BAR, hydraulic_pressure_bar=HYDRAULIC_NOMINAL_BAR,
                        bearing_temperature_c=rt.bearing_c, speed_reference_hz=NOMINAL_HZ,
                        flame_proven=(inputs.bar_present if gas else None),
                        fuel_available=((self._allocation(bay_id) > 0.0) if gas else None),
                        exhaust_temperature_c=exhaust_c if gas else None,
                        exhaust_spread_c=spread_c if gas else None)
                    self._count("MACHINE_SCAN_STEP")
                    mech = scan["mechanical_protection"]
                    if mech.tripped:
                        rt.lockout_86.trip(f"{mech.trip_code}:{mech.reason}") if mech.lockout_86 else None
                        self._count(f"TRIP:{mech.trip_code}")
                        self._record("PLANT_PHYSICS_TRIP", timestamp, bay_id, {
                            "mode": self.mode, "code": mech.trip_code, "reason": mech.reason,
                            "source": "TURBINE_MECHANICAL_PROTECTION", "lockout_86": bool(mech.lockout_86)})
                        break
            rt.previous_pe = pe

            # Bay relays (51, 87G, 21) on the unit while it is on line.
            relays: Dict[str, Any] = {}
            if network.unit_available(bay_id) and not bay.tripped_offline:
                allocation = self._allocation(bay_id)
                booked = self.load_plant.bay_bound_notional(bay_id)
                broker = max(0.0, _finite_or(inputs.broker_notional, booked))
                diff = abs(broker - booked) / allocation if allocation > 0 else 0.0
                restraint = (broker + booked) / 2.0 / allocation if allocation > 0 else 0.0
                velocity = max(1e-9, _finite_or(inputs.velocity, 0.0))
                for code, relay, kwargs, lockout in (
                        ("51", rt.relay_51, dict(measured_multiple=pe, dt_seconds=PLANT_SECONDS_PER_BAR), False),
                        ("87G", rt.relay_87g, dict(differential_current_pu=diff, restraint_current_pu=restraint), True),
                        ("21", rt.relay_21, dict(apparent_impedance_ratio=1.0 / velocity,
                                                 dt_seconds=PLANT_SECONDS_PER_BAR), False)):
                    trip = relay.evaluate(**kwargs)
                    self._count(f"RELAY_EVAL:{code}")
                    relays[code] = bool(getattr(trip, "tripped", False))
                    if relays[code]:
                        self._trip(bay_id, timestamp, f"ANSI_{code}", str(getattr(trip, "reason", code)),
                                   "R5_BAY_RELAY", lockout)
                        break
            out[bay_id] = {"pe": pe, "breaker_closed": network.unit_available(bay_id),
                           "frequency_hz": float(bay.machine_model.frequency_hz),
                           "startup_state": getattr(getattr(getattr(bay, "startup_sequencer", None), "state", None),
                                                    "value", None),
                           "vibration_mm_s": vibration, "bearing_c": rt.bearing_c,
                           "exhaust_spread_c": spread_c, "exhaust_c": exhaust_c,
                           "lockout_86": bool(rt.lockout_86.breaker_open), "relays": relays}
        self._count("BAR")
        return out
