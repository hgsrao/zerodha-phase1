"""Opt-in positional management; prototype policy, not sealed calibration.

Hard stop/target and opening-gap execution remain the protective executor's duty.
A stop proposed from a completed bar becomes effective on NEXT_BAR only.
"""
from dataclasses import dataclass, asdict
from math import isfinite
from copy import deepcopy
import pandas as pd
from revision5.position_lifecycle import ENGINE_B, B_OPEN, PositionLifecycleRecord

@dataclass(frozen=True)
class EngineBPolicy:
    enabled: bool = False
    max_sessions: int = 3
    structural_window: int = 20
    reversal_confirmation_bars: int = 3
    trail_mode: str = 'R'
    trail_distance_r: float = 2.0
    atr_multiple: float = 3.0
    trail_activation_r: float = 1.0

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (self.max_sessions, self.structural_window, self.reversal_confirmation_bars)):
            raise ValueError('policy windows must be positive integers')
        if self.trail_mode not in ('R', 'ATR', 'NONE'):
            raise ValueError('trail_mode must be R, ATR or NONE')
        if any(not isfinite(v) or v < 0 for v in (self.trail_distance_r, self.atr_multiple, self.trail_activation_r)):
            raise ValueError('trail parameters must be finite and nonnegative')
        if self.trail_mode == 'R' and self.trail_distance_r == 0 or self.trail_mode == 'ATR' and self.atr_multiple == 0:
            raise ValueError('trailing distance must be positive')

@dataclass(frozen=True)
class EngineBDecision:
    action: str
    reason: str
    proposed_stop_price: float
    effective_from: str = 'NEXT_BAR'
    current_r: float = 0.0
    structural_reference: float | None = None
    reversal_count: int = 0

class EngineBController:
    def __init__(self, policy: EngineBPolicy):
        self.policy = policy
        self._states = {}

    def evict(self, position_id):
        """Forget positional management state after the caller durably closes it.

        This is not a feedback receipt or an ownership transition. Runtime callers
        must persist CLOSED before invoking it; a failed save retains this state.
        """
        self._states.pop(position_id, None)

    def export_state(self):
        return {'version': 1, 'policy': asdict(self.policy), 'positions': deepcopy(self._states)}

    def restore_state(self, receipt):
        if not isinstance(receipt, dict) or receipt.get('version') != 1 or receipt.get('policy') != asdict(self.policy):
            raise ValueError('incompatible Engine B state')
        states = {}
        try:
            for key, value in receipt['positions'].items():
                if not isinstance(key, str) or not key or not isinstance(value, dict):
                    raise ValueError('invalid Engine B position identity')
                if set(value) != {'last_timestamp', 'high_water', 'reversal_count',
                                  'sessions_elapsed', 'session_last_bar', 'decision'}:
                    raise ValueError('invalid Engine B state schema')
                if pd.isna(pd.Timestamp(value['last_timestamp'])):
                    raise ValueError('invalid Engine B timestamp')
                count, sessions = value['reversal_count'], value['sessions_elapsed']
                if (type(count) is not int or count < 0 or type(sessions) is not int or sessions < 1
                        or type(value['session_last_bar']) is not bool):
                    raise ValueError('invalid Engine B session/reversal metadata')
                decision = EngineBDecision(**value['decision'])
                numbers = [value['high_water'], decision.proposed_stop_price, decision.current_r]
                if decision.structural_reference is not None:
                    numbers.append(decision.structural_reference)
                if any(type(n) not in (int, float) or not isfinite(n) for n in numbers):
                    raise ValueError('invalid Engine B numeric state')
                if (value['high_water'] <= 0 or decision.proposed_stop_price <= 0
                        or decision.structural_reference is not None and decision.structural_reference <= 0):
                    raise ValueError('invalid Engine B price state')
                if (type(decision.reversal_count) is not int or decision.reversal_count != count
                        or decision.effective_from != 'NEXT_BAR'):
                    raise ValueError('invalid Engine B cached decision metadata')
                expected = ('HOLD', 'ENGINE_B_HOLD')
                if sessions > self.policy.max_sessions or (sessions == self.policy.max_sessions and value['session_last_bar']):
                    expected = ('EXIT', 'ENGINE_B_MAX_SESSIONS')
                elif count >= self.policy.reversal_confirmation_bars:
                    expected = ('EXIT', 'ENGINE_B_STRUCTURAL_REVERSAL')
                if (decision.action, decision.reason) != expected:
                    raise ValueError('cached Engine B action contradicts restored state')
                states[key] = deepcopy(value)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('invalid Engine B state schema') from exc
        self._states = states

    def evaluate(self, record: PositionLifecycleRecord, completed_bars: pd.DataFrame, timestamp, sessions_elapsed: int, *, session_last_bar: bool = False):
        if not self.policy.enabled:
            raise ValueError('Engine B management requires explicit opt-in')
        if record.owner_engine != ENGINE_B or record.lifecycle_state != B_OPEN or record.direction != 'BUY':
            raise ValueError('only acknowledged BUY Engine B positions are supported')
        if isinstance(sessions_elapsed, bool) or not isinstance(sessions_elapsed, int) or sessions_elapsed < 1:
            raise ValueError('sessions_elapsed counts the entry session as one')
        timestamp = pd.Timestamp(timestamp)
        if completed_bars.empty or not completed_bars.index.is_monotonic_increasing or completed_bars.index.has_duplicates:
            raise ValueError('completed bars must be nonempty and chronological')
        if pd.Timestamp(completed_bars.index[-1]) != timestamp:
            raise ValueError('timestamp must identify the last completed bar; future bars prohibited')
        values = completed_bars[['high', 'low', 'close']].astype(float)
        if not all(isfinite(v) and v > 0 for v in values.to_numpy().flat):
            raise ValueError('invalid bar prices')
        if (values.high < values.low).any() or (values.close > values.high).any() or (values.close < values.low).any():
            raise ValueError('invalid OHLC geometry')
        state = self._states.get(record.position_id)
        if state and timestamp < pd.Timestamp(state['last_timestamp']):
            raise ValueError('cannot evaluate older bars')
        if state and timestamp == pd.Timestamp(state['last_timestamp']):
            if state.get('sessions_elapsed') != sessions_elapsed or state.get('session_last_bar') != session_last_bar:
                raise ValueError('conflicting session metadata for duplicate bar')
            return EngineBDecision(**state['decision'])
        close = float(values.close.iloc[-1])
        current_r = (close-record.anchor_price)/record.initial_risk_r
        high_water = max(record.anchor_price, float(values.high.iloc[-1]), state['high_water'] if state else record.anchor_price)
        # Prior completed bars establish reference; current bar never sets its own hurdle.
        prior = values.close.iloc[:-1].tail(self.policy.structural_window)
        reference = float(prior.mean()) if len(prior) == self.policy.structural_window else None
        count = (state['reversal_count'] if state else 0)+1 if reference is not None and close < reference else 0
        stop = record.current_stop_price
        if (high_water-record.anchor_price)/record.initial_risk_r >= self.policy.trail_activation_r:
            distance = None
            if self.policy.trail_mode == 'R':
                distance = self.policy.trail_distance_r*record.initial_risk_r
            elif self.policy.trail_mode == 'ATR' and len(values) >= self.policy.structural_window+1:
                previous = values.close.shift(1)
                true_range = pd.concat([values.high-values.low, (values.high-previous).abs(), (values.low-previous).abs()],axis=1).max(axis=1)
                distance = float(true_range.tail(self.policy.structural_window).mean())*self.policy.atr_multiple
            if distance is not None:
                stop = max(stop, high_water-distance)
        reason = 'ENGINE_B_HOLD'
        action = 'HOLD'
        if sessions_elapsed > self.policy.max_sessions or (sessions_elapsed == self.policy.max_sessions and session_last_bar):
            action,reason = 'EXIT','ENGINE_B_MAX_SESSIONS'
        elif count >= self.policy.reversal_confirmation_bars:
            action,reason = 'EXIT','ENGINE_B_STRUCTURAL_REVERSAL'
        decision = EngineBDecision(action,reason,stop,current_r=current_r,structural_reference=reference,reversal_count=count)
        self._states[record.position_id] = {'sessions_elapsed':sessions_elapsed,'session_last_bar':session_last_bar,'last_timestamp':timestamp.isoformat(),'high_water':high_water,'reversal_count':count,'decision':asdict(decision)}
        return decision
