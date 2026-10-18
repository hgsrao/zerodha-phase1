"""R6 standalone AVR controllers and governor/AVR arbiter.

Research/offline component. NO broker clients, network I/O or global adaptation.
No claim of compatibility with the existing R6 orchestrator until an adapter is
verified against its actual data structures and tests.

There are two deliberately DIFFERENT feedback models:
  * VoltageAVR: electrical-equivalent voltage -> exciter cascade. Only external
    measured or explicitly simulated voltage/Q/field feedback is accepted.
  * ExposureAVR: paper trading feedback from FILL and ORDER-ACK ledgers. It
    regulates utilized / committed notional, never calls it voltage or MVAR.

Both produce the same immutable AVR vote accepted by UnitMaster along with an
unmodified Mark V governor vote and the existing independent safety permit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from datetime import datetime
from hashlib import sha256
from math import ceil, exp, floor, isfinite
from typing import Optional
import json


class Action(str, Enum):
    ENTRY = "ENTRY"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    TRIP = "TRIP"
    BLOCK = "BLOCK"


def finite(*values: object) -> bool:
    return all(isinstance(x, (int, float)) and not isinstance(x, bool)
               and isfinite(float(x)) for x in values)


def bound(value: float, low: float, high: float) -> float:
    if low > high:
        raise ValueError("invalid bounds")
    return min(high, max(low, value))


def _check_identity(bar: int, timestamp: str, bay_id: str) -> None:
    if isinstance(bar, bool) or not isinstance(bar, int) or bar < 0:
        raise ValueError("invalid bar index")
    if not timestamp or not isinstance(timestamp, str) or not bay_id:
        raise ValueError("missing causal identity")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone aware")


@dataclass(frozen=True)
class PIConfig:
    kp: float
    ki: float
    kaw: float
    integral_limit: float

    def __post_init__(self):
        if not finite(self.kp, self.ki, self.kaw, self.integral_limit):
            raise ValueError("non-finite PI config")
        if min(self.kp, self.ki, self.kaw) < 0 or self.integral_limit <= 0:
            raise ValueError("invalid PI tuning")


@dataclass
class PositionalPI:
    """Positional PI, back-calculation anti-windup tracks ACTUALLY applied output.

    This is not the existing R6 incremental controller that adds a full PI
    expression to the previous command on every bar.
    """
    cfg: PIConfig
    i_term: float = 0.0

    def step(self, *, error: float, dt: float, bias: float,
             low: float, high: float, previous_applied: Optional[float] = None,
             rate_per_second: Optional[float] = None) -> tuple[float, dict]:
        if not finite(error, dt, bias, low, high) or dt <= 0 or low > high:
            raise ValueError("invalid PI inputs")
        if not (low <= bias <= high):
            raise ValueError("PI bias outside output bounds")
        raw = bias + self.cfg.kp * error + self.i_term
        applied = bound(raw, low, high)
        if rate_per_second is not None:
            if (previous_applied is None or not finite(rate_per_second, previous_applied)
                    or rate_per_second <= 0):
                raise ValueError("invalid applied actuator/rate")
            applied = bound(applied,
                            max(low, previous_applied - rate_per_second * dt),
                            min(high, previous_applied + rate_per_second * dt))
        tracking_error = applied - raw
        # i_term already contains the integral CONTRIBUTION in output units.
        # FIX F1: exact back-calculation (never removes more than the tracking error at any dt).
        aw = (1.0 - exp(-self.cfg.kaw * dt)) * tracking_error
        self.i_term = bound(self.i_term + dt * self.cfg.ki * error + aw,
                            -self.cfg.integral_limit, self.cfg.integral_limit)
        return applied, {
            "error": error, "p_term": self.cfg.kp * error,
            "i_term_after": self.i_term, "requested": raw, "applied": applied,
            "tracking_error": tracking_error,
            "saturated": abs(tracking_error) > 1e-12,
        }


@dataclass(frozen=True)
class AVRConfig:
    bay_id: str
    # Experimental physical reactive droop, on an explicitly defined Q pu base.
    reactive_droop: float
    outer: PIConfig = field(default_factory=lambda: PIConfig(1.0, 0.06, 0.8, 0.30))
    inner: PIConfig = field(default_factory=lambda: PIConfig(2.0, 0.12, 0.8, 0.35))
    vref_pu: float = 1.0
    field_nominal_pu: float = 1.0
    field_min_pu: float = 0.10
    field_max_pu: float = 1.20
    field_slew_pu_per_second: float = 0.06
    ansi27_trip_pu: float = 0.90
    ansi59_trip_pu: float = 1.10
    q_operating_pu: float = 0.80
    q_trip_pu: float = 0.95
    entry_voltage_tolerance_pu: float = 0.025
    field_tracking_tolerance_pu: float = 0.05

    def __post_init__(self):
        nums = [self.reactive_droop, self.vref_pu, self.field_nominal_pu,
                self.field_min_pu, self.field_max_pu,
                self.field_slew_pu_per_second, self.ansi27_trip_pu,
                self.ansi59_trip_pu, self.q_operating_pu, self.q_trip_pu,
                self.entry_voltage_tolerance_pu, self.field_tracking_tolerance_pu]
        if not self.bay_id or not finite(*nums):
            raise ValueError("invalid AVR configuration")
        if not (0 <= self.reactive_droop < 1
                and 0 < self.field_min_pu < self.field_nominal_pu < self.field_max_pu
                and self.field_slew_pu_per_second > 0
                and self.ansi27_trip_pu < self.vref_pu < self.ansi59_trip_pu
                and 0 < self.q_operating_pu < self.q_trip_pu
                and self.entry_voltage_tolerance_pu > 0
                and self.field_tracking_tolerance_pu > 0):
            raise ValueError("inconsistent AVR limits")

    @classmethod
    def experimental(cls, bay_id: str) -> "AVRConfig":
        # THESE ARE STUDY VALUES, NOT ACCEPTED PRODUCTION SETTINGS.
        if bay_id.startswith("GTG"):
            droop = 0.075
        elif bay_id.startswith(("CSTG", "BPSTG")):
            droop = 0.10
        else:
            raise ValueError("unrecognized bay type")
        return cls(bay_id=bay_id, reactive_droop=droop)


@dataclass(frozen=True)
class CausalID:
    bar_index: int
    timestamp: str
    bay_id: str
    symbol: str
    position_id: str = "FLAT"

    def __post_init__(self):
        _check_identity(self.bar_index, self.timestamp, self.bay_id)
        if not self.symbol or not self.position_id:
            raise ValueError("symbol and position id required")


@dataclass(frozen=True)
class Vote:
    identity: CausalID
    source: str                    # AVR or GOVERNOR
    action: Action
    max_total_quantity: int         # max total HELD shares of THIS symbol
    reason: str
    telemetry: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.source not in ("AVR", "GOVERNOR") or not isinstance(self.action, Action):
            raise ValueError("invalid vote source/action")
        if (isinstance(self.max_total_quantity, bool)
                or not isinstance(self.max_total_quantity, int)
                or self.max_total_quantity < 0 or not self.reason):
            raise ValueError("invalid vote capacity/reason")


@dataclass(frozen=True)
class VoltageSample:
    bar_index: int
    timestamp: str
    bay_id: str
    terminal_voltage_pu: Optional[float]
    field_feedback_pu: Optional[float]
    reactive_power_pu: Optional[float]
    source: str                     # BENCH_SIMULATED or EXTERNAL_MEASURED


@dataclass(frozen=True)
class VoltageState:
    bar_index: int
    timestamp: str
    bay_id: str
    vref_effective_pu: float
    voltage_error: Optional[float]
    field_demand_pu: float
    excitation_command_pu: float
    field_feedback_pu: Optional[float]
    outer: dict
    inner: dict
    limiter: str
    trip: str
    source: str


class VoltageAVR:
    """Two independent PI loops: terminal voltage -> field demand -> measured field.

    The field/exciter and voltage process must be a SEPARATE measured or
    simulated plant; never feed this class OHLC or market-churn-as-MVAR.
    """
    def __init__(self, config: AVRConfig):
        self.config = config
        self.outer = PositionalPI(config.outer)
        self.inner = PositionalPI(config.inner)
        self.command = config.field_nominal_pu
        self.last_bar = -1
        self.last_timestamp: Optional[str] = None
        self.last_state: Optional[VoltageState] = None
        self.trip_latch: Optional[str] = None
        self.integrity_fault = ""
        self.limiter_state = None   # FIX F9: persisted limiter state

    def step(self, sample: VoltageSample, dt: float) -> VoltageState:
        c = self.config
        _check_identity(sample.bar_index, sample.timestamp, sample.bay_id)
        if (sample.bay_id != c.bay_id or sample.bar_index <= self.last_bar
                or (self.last_timestamp is not None
                    and datetime.fromisoformat(sample.timestamp)
                        <= datetime.fromisoformat(self.last_timestamp))):
            raise ValueError("AVR bay/causality violation")
        if not finite(dt) or dt <= 0:
            raise ValueError("invalid sample period")
        self.last_bar = sample.bar_index
        self.last_timestamp = sample.timestamp
        v, field_actual, q = (sample.terminal_voltage_pu,
                              sample.field_feedback_pu, sample.reactive_power_pu)
        reason = self.trip_latch or ""
        # FIX F2: measurement-integrity failure blocks new exposure and holds the actuator; it is
        # not latched, does not de-excite and does not liquidate (owner precedence #2).
        integrity = ""
        if not reason:
            if sample.source not in ("BENCH_SIMULATED", "EXTERNAL_MEASURED"):
                integrity = "INVALID_MEASUREMENT_PROVENANCE"
            elif not finite(v, field_actual, q) or field_actual < 0:
                integrity = "AVR_MEASUREMENT_UNAVAILABLE"
        if integrity:
            state = VoltageState(sample.bar_index, sample.timestamp, c.bay_id, c.vref_pu, None,
                                 self.command, self.command, None, {}, {}, "MEASUREMENT_HOLD", "", sample.source)
            self.last_state = state
            self.limiter_state = state.limiter
            self.integrity_fault = integrity
            return state
        self.integrity_fault = ""
        if not reason:
            if v <= c.ansi27_trip_pu:
                reason = "ANSI_27_UNDERVOLTAGE"
            elif v >= c.ansi59_trip_pu:
                reason = "ANSI_59_OVERVOLTAGE"
            elif abs(q) >= c.q_trip_pu:
                reason = "REACTIVE_CAPABILITY_TRIP"
        if reason:
            # TRIP latches persist across day boundaries and require manual reset.
            self.trip_latch = reason
            self.limiter_state = "TRIPPED"
            self.command = 0.0
            state = VoltageState(sample.bar_index, sample.timestamp, c.bay_id,
                                 c.vref_pu, None, 0.0, 0.0, field_actual,
                                 {}, {}, "TRIPPED", reason, sample.source)
            self.last_state = state
            return state

        effective_vref = c.vref_pu - c.reactive_droop * q
        error_v = effective_vref - v
        # Both loops must respect directional reactive limits, not only the
        # outer demand. The inner physical actuator must not push Q farther
        # into a violated capability boundary.
        low, high = c.field_min_pu, c.field_max_pu
        limiter = "NORMAL"
        if q > c.q_operating_pu:
            high = min(high, max(low, field_actual))
            limiter = "OEL_REACTIVE_LIMITING"
        elif q < -c.q_operating_pu:
            low = max(low, min(high, field_actual))
            limiter = "UEL_REACTIVE_LIMITING"
        demand, outer = self.outer.step(error=error_v, dt=dt,
                                        bias=bound(c.field_nominal_pu, low, high),
                                        low=low, high=high)
        field_error = demand - field_actual
        command, inner = self.inner.step(
            error=field_error, dt=dt,
            bias=bound(c.field_nominal_pu, low, high),
            low=low, high=high,
            # Limiter bounds have priority over ramping when a limit tightens.
            previous_applied=bound(self.command, low, high),
            rate_per_second=c.field_slew_pu_per_second)
        self.command = command
        self.limiter_state = limiter
        state = VoltageState(sample.bar_index, sample.timestamp, c.bay_id,
                             effective_vref, error_v, demand, command,
                             field_actual, outer, inner, limiter, "", sample.source)
        self.last_state = state
        return state

    def reset_latch(self, *, operator_authorized: bool, healthy: VoltageSample) -> None:
        c = self.config
        if not operator_authorized:
            raise PermissionError("manual trip reset required")
        # FIX F3: reset needs an active latch and a NEWER causal sample; bumpless transfer.
        if self.trip_latch is None:
            raise ValueError("no active trip latch")
        if healthy.bar_index <= self.last_bar or (self.last_timestamp is not None and
                datetime.fromisoformat(healthy.timestamp) <= datetime.fromisoformat(self.last_timestamp)):
            raise ValueError("reset evidence must be a newer causal sample")
        if (healthy.bay_id != c.bay_id
                or (isinstance(healthy.field_feedback_pu, (int, float)) and healthy.field_feedback_pu < 0)
                or healthy.source not in ("BENCH_SIMULATED", "EXTERNAL_MEASURED")
                or not finite(healthy.terminal_voltage_pu,
                              healthy.field_feedback_pu,
                              healthy.reactive_power_pu)
                or not (c.ansi27_trip_pu < healthy.terminal_voltage_pu
                        < c.ansi59_trip_pu)
                or abs(healthy.reactive_power_pu) >= c.q_operating_pu):
            raise ValueError("healthy reset prerequisites not met")
        self.trip_latch = None
        f = bound(healthy.field_feedback_pu, c.field_min_pu, c.field_max_pu)
        # bumpless: integrators preloaded so both loops output the measured field
        self.outer.i_term = bound(f - c.field_nominal_pu, -c.outer.integral_limit, c.outer.integral_limit)
        self.inner.i_term = bound(f - c.field_nominal_pu, -c.inner.integral_limit, c.inner.integral_limit)
        self.command = f
        self.last_bar, self.last_timestamp = healthy.bar_index, healthy.timestamp
        self.last_state = None

    def vote(self, identity: CausalID, *, held_quantity: int,
             max_total_quantity: int, voltage_tolerance: Optional[float] = None) -> Vote:
        s = self.last_state
        if s is None or (s.bar_index, s.timestamp, s.bay_id) != (
                identity.bar_index, identity.timestamp, identity.bay_id):
            # FIX F2: stale/no current measurement blocks entry, holds a position (no liquidation)
            return Vote(identity, "AVR", Action.HOLD if held_quantity else Action.BLOCK,
                        held_quantity, "AVR_NO_CURRENT_BAR_MEASUREMENT")
        if s.trip:
            return Vote(identity, "AVR", Action.TRIP if held_quantity else Action.BLOCK,
                        0, s.trip)
        if s.limiter == "MEASUREMENT_HOLD":   # FIX F2: no invented liquidation
            return Vote(identity, "AVR", Action.HOLD if held_quantity else Action.BLOCK,
                        held_quantity, getattr(self, "integrity_fault", "") or "AVR_MEASUREMENT_UNAVAILABLE")
        if held_quantity < 0 or max_total_quantity < 0:
            raise ValueError("invalid quantities")
        if max_total_quantity < held_quantity:
            return Vote(identity, "AVR", Action.REDUCE, max_total_quantity,
                        "AVR_CAPACITY_DERATE")
        tol = voltage_tolerance if voltage_tolerance is not None else self.config.entry_voltage_tolerance_pu
        tracked = abs(s.field_demand_pu - s.field_feedback_pu) <= self.config.field_tracking_tolerance_pu
        regulated = abs(s.voltage_error) <= tol and tracked
        if held_quantity:
            return Vote(identity, "AVR", Action.HOLD, max_total_quantity,
                        "AVR_HELD_MONITORED", {"regulated": regulated})
        if not regulated or s.limiter != "NORMAL":
            return Vote(identity, "AVR", Action.BLOCK, 0,
                        "AVR_NOT_READY_FOR_ENTRY", {"regulated": regulated, "limiter": s.limiter})
        return Vote(identity, "AVR", Action.ENTRY, max_total_quantity,
                    "AVR_ENTRY_READY")

    def snapshot(self) -> dict:
        return {"schema": 1, "config_hash": _config_hash(self.config),
                "last_bar": self.last_bar, "last_timestamp": self.last_timestamp,
                "command": self.command,
                "outer_i": self.outer.i_term, "inner_i": self.inner.i_term,
                "trip_latch": self.trip_latch,
                "integrity_fault": self.integrity_fault,                       # FIX F9
                "limiter": self.limiter_state}

    def restore(self, state: dict) -> None:
        if state.get("schema") != 1 or state.get("config_hash") != _config_hash(self.config):
            raise ValueError("incompatible AVR checkpoint")
        # FIX F4: a restore may not clear a live protective latch nor rewind causal time.
        if self.trip_latch and not state.get("trip_latch"):
            raise ValueError("restore would clear an active protective latch")
        if self.last_bar >= 0 and int(state["last_bar"]) < self.last_bar:
            raise ValueError("restore would rewind causal time")
        vals = [state[k] for k in ("command", "outer_i", "inner_i")]
        if not finite(*vals):
            raise ValueError("invalid checkpoint state")
        self.last_bar = int(state["last_bar"])
        self.last_timestamp = state["last_timestamp"]
        if self.last_timestamp is not None:
            _check_identity(self.last_bar, self.last_timestamp, self.config.bay_id)
        self.command = float(state["command"])
        self.outer.i_term = float(state["outer_i"])
        self.inner.i_term = float(state["inner_i"])
        self.trip_latch = state["trip_latch"]
        self.integrity_fault = state.get("integrity_fault", "")
        self.limiter_state = state.get("limiter")
        self.last_state = None  # Fresh causal sample required after restore.


def _config_hash(config: object) -> str:
    return sha256(json.dumps(asdict(config), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ExposureConfig:
    bay_id: str
    max_utilization: float            # fixed maximum, approved by risk/safety owner
    target_utilization: float         # independently authorized operating setpoint
    outer: PIConfig = field(default_factory=lambda: PIConfig(0.3, 0.03, 0.8, 0.10))
    inner: PIConfig = field(default_factory=lambda: PIConfig(0.3, 0.03, 0.8, 0.10))
    capacity_slew_fraction_per_bar: float = 0.10

    def __post_init__(self):
        if not self.bay_id or not finite(self.max_utilization,
                                        self.target_utilization,
                                        self.capacity_slew_fraction_per_bar):
            raise ValueError("invalid exposure config")
        if not (0 < self.max_utilization <= 1
                and 0 <= self.target_utilization <= self.max_utilization
                and self.capacity_slew_fraction_per_bar > 0):
            raise ValueError("invalid exposure operating limits")


@dataclass(frozen=True)
class ExposureSample:
    identity: CausalID
    bay_capital: float
    filled_notional: float
    acknowledged_pending_entry_notional: float
    symbol_held_quantity: int
    price: float
    filled_source: str = "FILL_LEDGER"
    committed_source: str = "ORDER_ACK_LEDGER"


class ExposureAVR:
    """R6 paper-mode cascaded capital-utilization controller.

    Outer feedback: real filled bay utilization from fill ledger.
    Inner feedback: actual committed bay utilization (fills + accepted pending
    entries) from order acknowledgment ledger. Does NOT synthesize voltage/Q.
    No order submission or synthetic fills. No commercial min inside controller.
    """
    def __init__(self, config: ExposureConfig):
        self.config = config
        self.outer = PositionalPI(config.outer)
        self.inner = PositionalPI(config.inner)
        self.previous_capacity = config.target_utilization
        self.last_bar = -1
        self.last_timestamp: Optional[str] = None
        self.last_vote: Optional[Vote] = None

    def step(self, s: ExposureSample) -> Vote:
        c, ident = self.config, s.identity
        if (ident.bay_id != c.bay_id or ident.bar_index <= self.last_bar
                or (self.last_timestamp is not None
                    and datetime.fromisoformat(ident.timestamp)
                        <= datetime.fromisoformat(self.last_timestamp))):
            raise ValueError("exposure controller identity/causal violation")
        self.last_bar = ident.bar_index
        self.last_timestamp = ident.timestamp
        good = (s.filled_source == "FILL_LEDGER"
                and s.committed_source == "ORDER_ACK_LEDGER"
                and finite(s.bay_capital, s.filled_notional,
                           s.acknowledged_pending_entry_notional, s.price)
                and s.bay_capital > 0 and s.price > 0
                and s.filled_notional >= 0
                and s.acknowledged_pending_entry_notional >= 0
                and isinstance(s.symbol_held_quantity, int)
                and not isinstance(s.symbol_held_quantity, bool)
                and s.symbol_held_quantity >= 0)
        if not good:
            vote = Vote(ident, "AVR", Action.TRIP if s.symbol_held_quantity else Action.BLOCK,
                        0, "EXPOSURE_MEASUREMENT_INVALID")
            self.last_vote = vote
            return vote
        filled_util = s.filled_notional / s.bay_capital
        committed_notional = s.filled_notional + s.acknowledged_pending_entry_notional
        committed_util = committed_notional / s.bay_capital
        target = c.target_utilization
        if target == 0:
            vote = Vote(ident, "AVR", Action.EXIT if s.symbol_held_quantity else Action.BLOCK,
                        0, "AVR_TARGET_ZERO")
            self.last_vote = vote
            return vote

        # Outer loop regulates ACTUAL filled utilization. Inner loop regulates
        # recognized commitments, not self-issued instructions.
        desired, outer = self.outer.step(
            error=target - filled_util, dt=1.0, bias=target,
            low=0.0, high=c.max_utilization)
        capacity, inner = self.inner.step(
            error=desired - committed_util, dt=1.0, bias=desired,
            low=0.0, high=c.max_utilization,
            previous_applied=self.previous_capacity,
            rate_per_second=c.capacity_slew_fraction_per_bar)
        self.previous_capacity = capacity
        allowed_notional = capacity * s.bay_capital
        available_for_new = max(0.0, allowed_notional - committed_notional)
        # All other symbols' occupied and outstanding capacity is already
        # subtracted from this bay's common budget.
        additional_qty = floor(available_for_new / s.price)
        max_total = s.symbol_held_quantity + additional_qty
        telem = {"filled_util": filled_util, "committed_util": committed_util,
                 "target_util": target, "desired_util": desired,
                 "allowed_util": capacity, "additional_qty": additional_qty,
                 "outer": outer, "inner": inner,
                 "feedback_sources": [s.filled_source, s.committed_source]}

        # If filled positions ALREADY exceed the bounded operating capacity,
        # request proportional reduction; do not invent a safe new allocation.
        if s.filled_notional > allowed_notional and s.symbol_held_quantity:
            excess = s.filled_notional - allowed_notional
            to_close = min(s.symbol_held_quantity, ceil(excess / s.price))
            vote = Vote(ident, "AVR", Action.REDUCE,
                        s.symbol_held_quantity - to_close,
                        "AVR_BAY_CAPACITY_OVERHANG", telem)
        elif s.symbol_held_quantity:
            vote = Vote(ident, "AVR", Action.HOLD,
                        s.symbol_held_quantity, "AVR_HOLD_MONITORED", telem)
        elif additional_qty > 0:
            vote = Vote(ident, "AVR", Action.ENTRY,
                        additional_qty, "AVR_ENTRY_CAPACITY", telem)
        else:
            vote = Vote(ident, "AVR", Action.BLOCK,
                        0, "AVR_NO_SAFE_HEADROOM", telem)
        self.last_vote = vote
        return vote

    def snapshot(self) -> dict:
        return {"schema": 1, "config_hash": _config_hash(self.config),
                "last_bar": self.last_bar,
                "last_timestamp": self.last_timestamp,
                "outer_i": self.outer.i_term, "inner_i": self.inner.i_term,
                "previous_capacity": self.previous_capacity}

    def restore(self, s: dict) -> None:
        if s.get("schema") != 1 or s.get("config_hash") != _config_hash(self.config):
            raise ValueError("incompatible exposure AVR checkpoint")
        values = [s[k] for k in ("outer_i", "inner_i", "previous_capacity")]
        if not finite(*values) or not (0 <= values[2] <= self.config.max_utilization):
            raise ValueError("invalid exposure AVR checkpoint")
        self.last_bar = int(s["last_bar"])
        self.last_timestamp = s["last_timestamp"]
        if self.last_timestamp is not None:
            _check_identity(self.last_bar, self.last_timestamp, self.config.bay_id)
        self.outer.i_term, self.inner.i_term = float(values[0]), float(values[1])
        self.previous_capacity = float(values[2])
        self.last_vote = None


@dataclass(frozen=True)
class SafetyPermit:
    entry_allowed: bool
    max_total_quantity: int
    mandatory_trip_reason: str = ""
    identity: Optional["CausalID"] = None   # FIX F7: optional causal binding

    def __post_init__(self):
        if (not isinstance(self.entry_allowed, bool)
                or isinstance(self.max_total_quantity, bool)
                or not isinstance(self.max_total_quantity, int)
                or self.max_total_quantity < 0):
            raise ValueError("invalid safety permit")


@dataclass(frozen=True)
class Intent:
    identity: CausalID
    action: Action
    order_quantity: int              # ENTRY shares to buy / REDUCE/EXIT shares to close
    reason: str
    event_id: str
    controlling_authority: str


class UnitMaster:
    """Pure arbitration. Only external execution ledger enforces deduplication.

    This function DOES NOT submit, accept, fill, cancel or execute an order.
    Quantity caps in BOTH votes are TOTAL held shares of the identity's symbol.
    """
    @staticmethod
    def decide(*, governor: Vote, avr: Vote, safety: SafetyPermit,
               held_quantity: int, requested_entry_quantity: int,
               market_price: float, minimum_order_notional: float,
               outstanding_order: bool = False) -> Intent:
        if governor.source != "GOVERNOR" or avr.source != "AVR":
            raise ValueError("unexpected controller vote source")
        if governor.identity != avr.identity:
            raise ValueError("governor and AVR snapshots differ")
        if (not isinstance(held_quantity, int) or isinstance(held_quantity, bool)
                or not isinstance(requested_entry_quantity, int)
                or isinstance(requested_entry_quantity, bool)
                or min(held_quantity, requested_entry_quantity) < 0
                or not finite(market_price, minimum_order_notional)
                or market_price <= 0 or minimum_order_notional < 0
                or not isinstance(outstanding_order, bool)):
            raise ValueError("invalid master input")
        ident = governor.identity
        # FIX F6: FLAT identity with shares held (or a position id with none) is inconsistent.
        if (ident.position_id == "FLAT") != (held_quantity == 0):
            raise ValueError("held quantity inconsistent with position identity")
        if safety.identity is not None and safety.identity != ident:   # FIX F7
            raise ValueError("safety permit is for another snapshot")

        def make(action: Action, qty: int, reason: str, authority: str) -> Intent:
            # FIX F5: EXIT/TRIP/REDUCE ids are position-scoped so a repeated closing decision on a
            # later bar carries the SAME id and the execution ledger can deduplicate it.
            if action in (Action.EXIT, Action.TRIP, Action.REDUCE) and held_quantity:
                seed = f"CLOSE|{ident.bay_id}|{ident.symbol}|{ident.position_id}|{action.value}|{qty}"
            else:
                seed = (f"{ident.bar_index}|{ident.timestamp}|{ident.bay_id}|"
                        f"{ident.symbol}|{ident.position_id}|{action.value}|{qty}|{reason}")
            return Intent(ident, action, qty, reason,
                          sha256(seed.encode()).hexdigest(), authority)

        if safety.mandatory_trip_reason:   # FIX F8: keep simultaneous controller trip reasons too
            extra = [v.reason for v in (governor, avr) if v.action == Action.TRIP]
            return make(Action.TRIP if held_quantity else Action.BLOCK,
                        held_quantity, ";".join([safety.mandatory_trip_reason] + extra), "SAFETY")
        trips = [v for v in (governor, avr) if v.action == Action.TRIP]
        if trips:
            return make(Action.TRIP if held_quantity else Action.BLOCK,
                        held_quantity, ";".join(v.reason for v in trips), "PROTECTION")
        exits = [v for v in (governor, avr) if v.action == Action.EXIT]
        if exits:
            return make(Action.EXIT if held_quantity else Action.BLOCK,
                        held_quantity, ";".join(v.reason for v in exits),
                        "+".join(v.source for v in exits))

        cap = min(governor.max_total_quantity, avr.max_total_quantity,
                  safety.max_total_quantity)
        if held_quantity:
            if governor.action == Action.BLOCK or avr.action == Action.BLOCK:
                # HOLD authority uncertain: conservatively ask for an exit.
                return make(Action.EXIT, held_quantity, "HOLD_AUTHORITY_UNAVAILABLE", "UNIT_MASTER")
            if governor.action not in (Action.HOLD, Action.REDUCE) or avr.action not in (Action.HOLD, Action.REDUCE):
                return make(Action.EXIT, held_quantity, "INVALID_HELD_POSITION_VOTE", "UNIT_MASTER")
            if cap < held_quantity:
                return make(Action.REDUCE, held_quantity - cap,
                            "MOST_RESTRICTIVE_CAP", "UNIT_MASTER")
            return make(Action.HOLD, 0, "BOTH_CONTROLLERS_HOLD", "UNIT_MASTER")

        # An outstanding order is not another opportunity to submit the same
        # signal. Protective exits above are not suppressed by this guard.
        if outstanding_order:
            return make(Action.BLOCK, 0, "OUTSTANDING_ORDER", "EXECUTION")
        if not safety.entry_allowed:
            return make(Action.BLOCK, 0, "SAFETY_ENTRY_DENIED", "SAFETY")
        if governor.action != Action.ENTRY or avr.action != Action.ENTRY:
            return make(Action.BLOCK, 0, "ENTRY_NOT_UNANIMOUS", "UNIT_MASTER")
        qty = min(requested_entry_quantity, cap)
        if qty <= 0:
            return make(Action.BLOCK, 0, "ZERO_SAFE_QUANTITY", "UNIT_MASTER")
        # Apply commercial minimum ONCE, AFTER all approved safe caps.
        if qty * market_price < minimum_order_notional:
            return make(Action.BLOCK, 0, "BELOW_COMMERCIAL_MINIMUM", "COMMERCIAL")
        return make(Action.ENTRY, qty, "GOVERNOR_AVR_SAFETY_PERMITTED", "UNIT_MASTER")


@dataclass
class BenchExciterPlant:
    """Deterministic physical-EQUIVALENT bench, never live financial telemetry.

    Demonstrates two genuine feedback paths:
      command -> separate field state -> measured field (inner feedback)
      field -> separate voltage/Q state -> measured voltage/Q (outer feedback)
    """
    bay_id: str
    field_pu: float = 1.0
    voltage_pu: float = 1.0
    q_pu: float = 0.0
    field_tau_s: float = 3.0
    voltage_tau_s: float = 2.0
    q_tau_s: float = 4.0

    def advance(self, *, bar_index: int, timestamp: str, command: float,
                bus_voltage_pu: float, load_pu: float, dt: float) -> VoltageSample:
        if not finite(command, bus_voltage_pu, load_pu, dt) or dt <= 0:
            raise ValueError("invalid bench input")
        if min(self.field_tau_s, self.voltage_tau_s, self.q_tau_s) <= 0:
            raise ValueError("invalid bench dynamics")
        self.field_pu += (1 - exp(-dt / self.field_tau_s)) * (command - self.field_pu)
        v_goal = bus_voltage_pu + 0.55 * (self.field_pu - 1) - 0.08 * load_pu
        self.voltage_pu += (1 - exp(-dt / self.voltage_tau_s)) * (v_goal - self.voltage_pu)
        q_goal = 0.40 * (self.field_pu - 1) + 0.15 * load_pu
        self.q_pu += (1 - exp(-dt / self.q_tau_s)) * (q_goal - self.q_pu)
        return VoltageSample(bar_index, timestamp, self.bay_id,
                             self.voltage_pu, self.field_pu, self.q_pu,
                             "BENCH_SIMULATED")
