"""Validated market contracts. Exposure is in units of a configured symbol budget."""
from dataclasses import dataclass, asdict, replace, field
from datetime import datetime
from hashlib import sha256
import json
import math


def number(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError('expected finite real number')
    return float(x)


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, number(x)))


def digest(x):
    return sha256(json.dumps(x, sort_keys=True, allow_nan=False).encode()).hexdigest()


DROOP_LIMITS = (0.02, 0.10)


def registry_bay_settings():
    from .r8_controller_parameters import default_controller_parameters
    specs = default_controller_parameters().governor_specs
    return {b: {'droop_r': float(s.droop_r), 'capital_weight': float(s.capital_weight)}
            for b, s in specs.items()}


@dataclass(frozen=True)
class ControlConfig:
    symbol: str
    bay_id: str
    budget_fraction: float
    droop_r: float = 0.04
    bar_seconds: float = 60.0
    feature_window: int = 20
    min_history: int = 5
    kp: float = 0.8
    ki_per_second: float = 0.0005
    kd_seconds: float = 0.5
    derivative_tau_seconds: float = 0.0
    integral_limit: float = 0.5
    command_slew_per_second: float = 0.01
    market_response_tau_seconds: float = 120.0
    buy_enter: float = 0.12
    buy_release: float = 0.06
    sell_enter: float = -0.12
    sell_release: float = -0.06
    emergency: float = 0.24
    hold_bars: int = 2
    cooldown_bars: int = 1
    max_pulse_age: int = 30
    normal_exit_confirm_bars: int = 2
    # Four parallel evidence controllers. There are no weights: each source casts its
    # own BULL/FLAT/BEAR vote and the consensus interlock alone decides.
    study_droop_r: float = 0.05
    dispatch_droop_r: float = 0.05
    governor_threshold: float = 0.10
    avr_threshold: float = 0.08
    dispatch_threshold: float = 0.10
    studies_threshold: float = 0.10
    # Input scaling (full-scale points), so each controller sees its own [-1, 1] signal:
    # governor = close vs its own moving reference; AVR = candle pressure x relative volume.
    governor_price_full_scale: float = 0.005
    avr_volume_full_scale: float = 2.0
    # Integral memory of the four evidence PIDs. 0.25 keeps entry and exit response
    # symmetric (about 7-8 bars each way on a clean trend); 0.5 delayed exits to ~25 bars.
    evidence_integral_limit: float = 0.25
    # Gains of the four evidence PIDs and the MarketAVR participation PI: one place, fixed for the
    # run.  They are NOT adapted in any mode (R5 governor gains belong to a different loop).
    governor_pid_kp: float = 0.85
    governor_pid_ki_per_second: float = 0.0005
    governor_pid_kd_seconds: float = 0.25
    avr_pid_kp: float = 0.90
    avr_pid_ki_per_second: float = 0.0005
    avr_pid_kd_seconds: float = 0.20
    dispatch_pid_kp: float = 0.90
    dispatch_pid_ki_per_second: float = 0.0004
    dispatch_pid_kd_seconds: float = 0.15
    studies_pid_kp: float = 0.95
    studies_pid_ki_per_second: float = 0.0005
    studies_pid_kd_seconds: float = 0.20
    market_avr_kp: float = 0.30
    market_avr_ki: float = 0.002
    market_avr_kaw: float = 0.01                 # anti-windup tracking gain (not a derivative gain)
    market_avr_integral_limit: float = 0.50
    # A four-way BEAR while flat may open a short only when shorts are allowed.
    allow_shorts: bool = True

    def __post_init__(self):
        if not self.symbol or not self.bay_id:
            raise ValueError('missing identity')
        for key, val in asdict(self).items():
            if key not in ('symbol', 'bay_id', 'allow_shorts'):
                number(val)
        if not 0 < self.budget_fraction <= 1 or self.bar_seconds <= 0:
            raise ValueError('invalid budget or clock')
        for r_val in (self.droop_r, self.study_droop_r, self.dispatch_droop_r):
            if not DROOP_LIMITS[0] <= r_val <= DROOP_LIMITS[1]:
                raise ValueError(f'droop_r must be within commissioning limits {DROOP_LIMITS}')
        if not all(0 < t <= 1 for t in (self.governor_threshold, self.avr_threshold,
                                         self.dispatch_threshold, self.studies_threshold)):
            raise ValueError('evidence vote thresholds must be in (0, 1]')
        if self.governor_price_full_scale <= 0 or self.avr_volume_full_scale <= 0:
            raise ValueError('evidence input scales must be positive')
        if not 0 < self.evidence_integral_limit <= 1:
            raise ValueError('evidence integral limit must be in (0, 1]')
        if min(self.evidence_pid_gains()) < 0 or min(self.market_avr_kp, self.market_avr_ki, self.market_avr_kaw) < 0 \
                or self.market_avr_integral_limit <= 0:
            raise ValueError('evidence / market AVR gains must be non-negative (integral limit positive)')
        if type(self.allow_shorts) is not bool:
            raise ValueError('allow_shorts must be explicit')
        if any(type(x) is not int for x in (self.feature_window, self.min_history, self.hold_bars,
                                            self.cooldown_bars, self.max_pulse_age, self.normal_exit_confirm_bars)):
            raise ValueError('counts must be integers')
        if not 2 <= self.min_history <= self.feature_window or self.hold_bars < 1 or self.cooldown_bars < 1 or self.max_pulse_age < self.hold_bars:
            raise ValueError('invalid history or timing')
        if self.normal_exit_confirm_bars < 1:
            raise ValueError('normal exit confirmation must be positive')
        if min(self.kp, self.ki_per_second, self.kd_seconds, self.derivative_tau_seconds) < 0 or min(self.integral_limit, self.command_slew_per_second, self.market_response_tau_seconds) <= 0:
            raise ValueError('invalid tuning')
        if not -self.emergency <= self.sell_enter < self.sell_release <= 0 <= self.buy_release < self.buy_enter <= self.emergency <= 1:
            raise ValueError('invalid hysteresis')


def _evidence_pid_gains(c):
    return (c.governor_pid_kp, c.governor_pid_ki_per_second, c.governor_pid_kd_seconds,
            c.avr_pid_kp, c.avr_pid_ki_per_second, c.avr_pid_kd_seconds,
            c.dispatch_pid_kp, c.dispatch_pid_ki_per_second, c.dispatch_pid_kd_seconds,
            c.studies_pid_kp, c.studies_pid_ki_per_second, c.studies_pid_kd_seconds)


ControlConfig.evidence_pid_gains = _evidence_pid_gains


def default_configs(symbols, **overrides):
    from .r8_topology import FLEET_TOPOLOGY, SYMBOL_TO_BAY
    bays = registry_bay_settings()
    out = []
    for symbol in symbols:
        bay = SYMBOL_TO_BAY[symbol]
        out.append(ControlConfig(symbol, bay, bays[bay]['capital_weight'] / len(FLEET_TOPOLOGY[bay]),
                                 droop_r=bays[bay]['droop_r'], **overrides))
    return out


def validation_config(config):
    return replace(config, kd_seconds=0.0)


@dataclass(frozen=True)
class Bar:
    index: int
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        if type(self.index) is not int or self.index < 0:
            raise ValueError('invalid bar index')
        t = datetime.fromisoformat(self.timestamp)
        if t.utcoffset() is None:
            raise ValueError('timezone-aware bar required')
        for x in (self.open, self.high, self.low, self.close, self.volume):
            number(x)
        if min(self.open, self.low, self.close) <= 0 or self.volume < 0 or not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError('invalid OHLCV')


@dataclass(frozen=True)
class Context:
    actual_exposure: float
    study: float
    dispatch: float
    study_available: bool = True
    dispatch_available: bool = True
    increase_capacity: object = None
    execution_blocked: bool = False

    def __post_init__(self):
        number(self.actual_exposure)
        if self.increase_capacity is not None and number(self.increase_capacity) < 0:
            raise ValueError('increase capacity must be non-negative')
        if type(self.execution_blocked) is not bool:
            raise ValueError('execution state must be explicit')
        if abs(number(self.study)) > 1 or abs(number(self.dispatch)) > 1:
            raise ValueError('context signal out of range')
        if type(self.study_available) is not bool or type(self.dispatch_available) is not bool:
            raise ValueError('context availability must be explicit')


@dataclass(frozen=True)
class Features:
    bar: Bar
    pvt: float
    pressure: float
    liquidity: float
    volatility: float
    ready: bool
    degraded: bool
    gap: bool
    market_speed_pu: float
    speed_reference_price: float
    relative_volume: float
    dt_seconds: float


@dataclass(frozen=True)
class MarketVote:
    score: float
    quality: float

    def __post_init__(self):
        if abs(number(self.score)) > 1 or not 0 <= number(self.quality) <= 1:
            raise ValueError('invalid market vote')


@dataclass(frozen=True)
class GovernorOutput:
    target: float
    actual: float
    error: float
    p: float
    i: float
    d: float
    requested: float
    applied: float
    integration_frozen: bool = False
    over_budget: bool = False   # native position beyond target: error clipped for control

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key not in ('integration_frozen', 'over_budget'):
                number(value)
        if max(abs(self.target), abs(self.requested), abs(self.applied)) > 1 or type(self.integration_frozen) is not bool \
                or type(self.over_budget) is not bool:
            raise ValueError('invalid governor output')


@dataclass(frozen=True)
class Decision:
    symbol: str
    bay_id: str
    bar_index: int
    timestamp: str
    pulse: str
    reason: str
    target: float
    actual: float
    signal: float
    eligible: bool
    avr: MarketVote
    governor: GovernorOutput
    reference: float = 0.0
    action_intent: str = "HOLD_POSITION"
    consensus_reason: str = ""
    consensus_votes: dict = field(default_factory=dict)
    consensus_outputs: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.symbol or not self.bay_id or type(self.bar_index) is not int or self.bar_index < 0:
            raise ValueError('invalid decision identity')
        if self.pulse not in ('BUY', 'HOLD', 'SELL') or type(self.eligible) is not bool:
            raise ValueError('invalid decision state')
        for value in (self.target, self.actual, self.signal, self.reference):
            number(value)
        if abs(self.target) > 1 or abs(self.signal) > 1 or abs(self.reference) > 1:
            raise ValueError('invalid decision range')
        if not isinstance(self.action_intent, str) or not isinstance(self.consensus_reason, str):
            raise ValueError('invalid consensus metadata strings')
        if not isinstance(self.consensus_votes, dict) or not isinstance(self.consensus_outputs, dict):
            raise ValueError('consensus votes/outputs must be dicts')


class BayAggregateState:
    def __init__(self):
        self.symbols = {}

    def update(self, result):
        self.symbols[result.symbol] = {'target': result.target, 'actual': result.actual, 'pulse': result.pulse}

    def totals(self):
        return {'desired': sum(x['target'] for x in self.symbols.values()),
                'actual': sum(x['actual'] for x in self.symbols.values()),
                'buy': sum(x['pulse'] == 'BUY' for x in self.symbols.values()),
                'sell': sum(x['pulse'] == 'SELL' for x in self.symbols.values())}
