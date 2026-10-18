"""Validated market contracts. Exposure is in units of a configured symbol budget."""
from dataclasses import dataclass, asdict
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
    avr_weight: float = 0.5
    study_weight: float = 0.3
    dispatch_weight: float = 0.2

    def __post_init__(self):
        if not self.symbol or not self.bay_id:
            raise ValueError('missing identity')
        for key, val in asdict(self).items():
            if key not in ('symbol', 'bay_id'):
                number(val)
        if not 0 < self.budget_fraction <= 1 or self.bar_seconds <= 0:
            raise ValueError('invalid budget or clock')
        if self.droop_r != 0.04:
            raise ValueError('R7 commissioned market-speed droop is fixed at 4%')
        if any(type(x) is not int for x in (self.feature_window, self.min_history, self.hold_bars, self.cooldown_bars, self.max_pulse_age, self.normal_exit_confirm_bars)):
            raise ValueError('counts must be integers')
        if not 2 <= self.min_history <= self.feature_window or self.hold_bars < 1 or self.cooldown_bars < 1 or self.max_pulse_age < self.hold_bars:
            raise ValueError('invalid history or timing')
        if self.normal_exit_confirm_bars < 1:
            raise ValueError('normal exit confirmation must be positive')
        if min(self.kp, self.ki_per_second, self.kd_seconds) < 0 or min(self.integral_limit, self.command_slew_per_second, self.market_response_tau_seconds) <= 0:
            raise ValueError('invalid tuning')
        if not -self.emergency <= self.sell_enter < self.sell_release <= 0 <= self.buy_release < self.buy_enter <= self.emergency <= 1:
            raise ValueError('invalid hysteresis')
        if min(self.avr_weight, self.study_weight, self.dispatch_weight) < 0 or not math.isclose(self.avr_weight + self.study_weight + self.dispatch_weight, 1):
            raise ValueError('weights must sum to one')


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

    def __post_init__(self):
        number(self.actual_exposure)
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
    market_speed_pu: float
    droop_target: float

    def __post_init__(self):
        for value in asdict(self).values():
            number(value)
        if max(abs(self.target), abs(self.requested), abs(self.applied), abs(self.droop_target)) > 1 or self.market_speed_pu <= 0:
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

    def __post_init__(self):
        if not self.symbol or not self.bay_id or type(self.bar_index) is not int or self.bar_index < 0:
            raise ValueError('invalid decision identity')
        if self.pulse not in ('BUY', 'HOLD', 'SELL') or type(self.eligible) is not bool:
            raise ValueError('invalid decision state')
        for value in (self.target, self.actual, self.signal):
            number(value)
        if abs(self.target) > 1 or abs(self.signal) > 1:
            raise ValueError('invalid decision range')


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
