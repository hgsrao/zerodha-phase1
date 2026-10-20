"""R8 consensus governor with independent evidence PIDs and explicit consensus."""
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import json
import math

def finite(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("expected finite number")
    return float(x)

def bounded(x):
    return max(-1.0, min(1.0, finite(x)))

class Vote(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"

@dataclass(frozen=True)
class R8Config:
    threshold: float = .12
    kp: float = .8
    ki_per_second: float = .0005
    kd_seconds: float = .5
    integral_limit: float = .5
    slew_per_second: float = .01
    response_tau_seconds: float = 120.0
    buy_enter: float = .12
    buy_release: float = .06
    sell_enter: float = -.12
    sell_release: float = -.06
    emergency: float = .24
    hold_bars: int = 2
    cooldown_bars: int = 1
    max_pulse_age: int = 30

    def __post_init__(self):
        for name, value in asdict(self).items():
            finite(value)
        if not 0 < self.threshold <= 1 or min(self.kp, self.ki_per_second, self.kd_seconds) < 0:
            raise ValueError("invalid PID parameters")
        if min(self.integral_limit, self.slew_per_second, self.response_tau_seconds) <= 0:
            raise ValueError("invalid safety limits")
        if not self.sell_enter <= self.sell_release <= 0 <= self.buy_release <= self.buy_enter <= self.emergency <= 1:
            raise ValueError("invalid hysteresis parameters")
        if any(type(x) is not int or x < 1 for x in (self.hold_bars, self.cooldown_bars, self.max_pulse_age)):
            raise ValueError("invalid FSM parameters")

@dataclass(frozen=True)
class Evidence:
    governor: float
    avr: float
    dispatch: float
    studies: float
    actual_exposure: float = 0.0
    eligible: bool = True
    def __post_init__(self):
        for name in ("governor", "avr", "dispatch", "studies", "actual_exposure"):
            value = finite(getattr(self, name))
            if name != "actual_exposure" and abs(value) > 1: raise ValueError(f"{name} outside [-1, 1]")
        if type(self.eligible) is not bool: raise ValueError("eligible must be bool")
    def votes(self, threshold=.12):
        threshold = finite(threshold)
        if not 0 < threshold <= 1: raise ValueError("threshold outside (0, 1]")
        def vote(x): return Vote.BUY if x >= threshold else Vote.SELL if x <= -threshold else Vote.HOLD
        return {name: vote(getattr(self, name)) for name in ("governor", "avr", "dispatch", "studies")}

@dataclass(frozen=True)
class PIDOutput:
    measurement: float
    error: float
    p: float
    i: float
    d: float
    output: float
    integration_frozen: bool

class EvidencePID:
    def __init__(self, kp, ki, kd, integral_limit):
        self.kp, self.ki, self.kd = finite(kp), finite(ki), finite(kd)
        self.integral_limit = finite(integral_limit)
        if min(self.kp, self.ki, self.kd) < 0 or self.integral_limit <= 0: raise ValueError("invalid PID tuning")
        self.integral, self.previous, self.output = 0.0, None, 0.0
    def step(self, measurement, dt, enabled=True):
        measurement, dt = bounded(measurement), finite(dt)
        if dt <= 0 or type(enabled) is not bool: raise ValueError("invalid PID step")
        d = 0.0 if self.previous is None else self.kd * (measurement - self.previous) / dt
        trial = max(-self.integral_limit, min(self.integral_limit, self.integral + self.ki * measurement * dt))
        raw = self.kp * measurement + trial + d
        frozen = not enabled or (abs(raw) > 1 and raw * measurement > 0)
        if enabled and not frozen: self.integral = trial
        elif not enabled: self.integral *= math.exp(-dt / 120.0)
        self.previous, self.output = measurement, bounded(raw)
        return PIDOutput(measurement, measurement, self.kp * measurement, self.integral, d, self.output, frozen)
    def snapshot(self): return {"integral": self.integral, "previous": self.previous, "output": self.output}
    def restore(self, state):
        if set(state) != {"integral", "previous", "output"}: raise ValueError("invalid PID checkpoint")
        self.integral = finite(state["integral"]); self.previous = None if state["previous"] is None else bounded(state["previous"]); self.output = bounded(state["output"])
        if abs(self.integral) > self.integral_limit: raise ValueError("PID integral exceeds limit")

@dataclass(frozen=True)
class Decision:
    direction: Vote
    reason: str
    action_intent: str
    votes: dict[str, Vote]
    evidence: Evidence
    execution_target: float
    execution_command: float
    pid: dict[str, PIDOutput] = field(default_factory=dict)
    def as_dict(self):
        result = asdict(self); result["direction"] = self.direction.value
        result["votes"] = {k: v.value for k, v in self.votes.items()}; result["pid"] = {k: asdict(v) for k, v in self.pid.items()}
        return result

class R8Controller:
    def __init__(self, config=None, **overrides):
        values = asdict(config or R8Config()); values.update(overrides); self.config = R8Config(**values)
        c = self.config; self.execution_command = 0.0; self.sequence = 0
        self.pids = {n: EvidencePID(c.kp, c.ki_per_second, c.kd_seconds, c.integral_limit) for n in ("governor", "avr", "dispatch", "studies")}
    def decide(self, evidence: Evidence, dt_seconds=60.0):
        dt_seconds = finite(dt_seconds)
        if dt_seconds <= 0: raise ValueError("dt_seconds must be positive")
        votes = evidence.votes(self.config.threshold); pid = {n: p.step(getattr(evidence, n), dt_seconds, evidence.eligible) for n, p in self.pids.items()}; values = tuple(votes.values())
        long, short = evidence.actual_exposure > 0, evidence.actual_exposure < 0
        if not evidence.eligible: direction, reason = Vote.HOLD, "INELIGIBLE_INPUT"
        elif all(v is Vote.BUY for v in values): direction, reason = Vote.BUY, "FOUR_WAY_BUY_CONSENSUS"
        elif all(v is Vote.SELL for v in values): direction, reason = Vote.SELL, "FOUR_WAY_SELL_CONSENSUS"
        elif long and votes["governor"] is Vote.SELL and votes["studies"] is Vote.SELL: direction, reason = Vote.SELL, "LONG_EXIT_GOVERNOR_STUDY_CONSENSUS"
        elif short and votes["governor"] is Vote.BUY and votes["studies"] is Vote.BUY: direction, reason = Vote.BUY, "SHORT_EXIT_GOVERNOR_STUDY_CONSENSUS"
        else: direction, reason = Vote.HOLD, "NO_DIRECTIONAL_CONSENSUS"
        if direction is Vote.BUY: intent, target = (("EXIT_SHORT", 0.0) if short else ("ENTER_OR_MAINTAIN_LONG", 1.0))
        elif direction is Vote.SELL: intent, target = (("REDUCE_OR_EXIT_LONG", 0.0) if long else ("ENTER_SHORT_CANDIDATE", -1.0))
        else: intent, target = (("MAINTAIN_POSITION", max(-1.0, min(1.0, evidence.actual_exposure))) if long or short else ("STAY_FLAT", 0.0))
        command = self.ramp(target, evidence.actual_exposure, dt_seconds); self.sequence += 1
        return Decision(direction, reason, intent, votes, evidence, target, command, pid)
    def ramp(self, target, actual, dt_seconds):
        target, actual, dt_seconds = bounded(target), finite(actual), finite(dt_seconds)
        if dt_seconds <= 0: raise ValueError("dt_seconds must be positive")
        step = self.config.slew_per_second * dt_seconds; self.execution_command = max(self.execution_command - step, min(self.execution_command + step, target)); return self.execution_command
    def snapshot(self):
        payload = {"schema": 1, "config": asdict(self.config), "sequence": self.sequence, "execution_command": self.execution_command, "pids": {k: v.snapshot() for k, v in self.pids.items()}}
        payload["sha256"] = sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest(); return payload
    def restore(self, payload):
        if not isinstance(payload, dict) or payload.get("schema") != 1: raise ValueError("invalid R8 checkpoint schema")
        saved = dict(payload); actual = saved.pop("sha256", None); expected = sha256(json.dumps(saved, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if actual != expected or saved["config"] != asdict(self.config): raise ValueError("R8 checkpoint integrity/configuration mismatch")
        self.execution_command = bounded(saved["execution_command"]); self.sequence = saved["sequence"]
        if type(self.sequence) is not int or self.sequence < 0 or set(saved["pids"]) != set(self.pids): raise ValueError("invalid R8 checkpoint state")
        for name, state in saved["pids"].items(): self.pids[name].restore(state)
