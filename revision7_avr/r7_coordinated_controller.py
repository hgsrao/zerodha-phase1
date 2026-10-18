"""One symbol's market control pipeline and hysteretic pulse state machine."""
from dataclasses import asdict, replace
from copy import deepcopy
import json
from .r7_state import Bar, Context, ControlConfig, Decision, MarketVote, GovernorOutput, digest, clamp
from .r7_features import SymbolFeatureExtractor
from .r7_market_avr import MarketAVR
from .r7_market_governor import MarketGovernor
from .r7_allocator import approve_target


class PulseFSM:
    """BUY/HOLD/SELL Schmitt trigger with explicit completed-bar counting.

    * active_age is the number of completed bars the current pulse has been shown,
      including its entry bar. A pulse may release only when active_age >= hold_bars,
      evaluated BEFORE the current bar is counted, so a pulse is visible for at least
      exactly hold_bars bars.
    * hold_age is the number of completed HOLD bars since a release, including the
      release bar, capped at cooldown_bars. A new pulse may enter only when
      hold_age >= cooldown_bars. An ineligible bar resets hold_age to 0.
    * An emergency reversal needs two consecutive opposing emergency samples
      (emergency_age >= 2); one sample never flips the pulse. Once a pulse has been shown
      for hold_bars, the first emergency sample releases to HOLD instead, and the opposite
      pulse can enter after the cooldown. It is a pulse state only: positions still change
      only through the native admission and exit paths.
    """
    def __init__(self, config):
        self.config = config
        self.pulse = 'HOLD'
        self.active_age = 0
        self.hold_age = config.cooldown_bars
        # A single opposing emergency sample is not enough to reverse a pulse.
        # Native protective exits are evaluated outside this FSM and remain immediate.
        self.emergency_age = 0

    def step(self, signal, eligible=True):
        c = self.config
        previous = self.pulse
        if not eligible:
            self.pulse, self.active_age, self.hold_age, self.emergency_age = 'HOLD', 0, 0, 0
            return self.pulse, 'INPUT_NOT_ELIGIBLE'
        if previous == 'HOLD':
            if self.hold_age >= c.cooldown_bars:
                self.pulse = 'BUY' if signal >= c.buy_enter else 'SELL' if signal <= c.sell_enter else 'HOLD'
            if self.pulse != 'HOLD':
                self.active_age, self.hold_age, self.emergency_age = 1, 0, 0
            else:
                self.hold_age = min(c.cooldown_bars, self.hold_age + 1)
        else:
            emergency = signal <= -c.emergency if previous == 'BUY' else signal >= c.emergency
            if emergency:
                self.emergency_age += 1
            else:
                self.emergency_age = 0
            release = signal < c.buy_release if previous == 'BUY' else signal > c.sell_release
            unsupported = signal < c.buy_enter if previous == 'BUY' else signal > c.sell_enter
            if emergency and self.emergency_age >= 2:
                self.pulse = 'SELL' if previous == 'BUY' else 'BUY'
                self.active_age, self.hold_age, self.emergency_age = 1, 0, 0
            elif (release and self.active_age >= c.hold_bars) or (unsupported and self.active_age >= c.max_pulse_age):
                # The release bar is the first completed HOLD bar of the cooldown.
                self.pulse, self.active_age, self.hold_age, self.emergency_age = 'HOLD', 0, min(1, c.cooldown_bars), 0
            else:
                self.active_age = min(c.max_pulse_age, self.active_age + 1)
        return self.pulse, f'{previous}_TO_{self.pulse}'


class SymbolController:
    def __init__(self, config):
        self.config = config
        self.features = SymbolFeatureExtractor(config)
        self.avr = MarketAVR()
        self.governor = MarketGovernor(config)
        self.fsm = PulseFSM(config)
        self.last_input = None
        self.last_result = None
        self._pending = None
        self.steps = 0

    def step(self, bar, context):
        """Single-symbol step: prepare and commit with the context's own capacity."""
        if self.last_input is not None and bar.index == self.last_input[0].index:
            if (bar, context) != self.last_input:
                raise ValueError('conflicting same-bar input; integrators not replayed')
            return self.last_result
        self.prepare(bar, context)
        return self.commit(context.increase_capacity, context.execution_blocked)

    def prepare(self, bar, context):
        """Advance features and AVR once and form the strategy reference E_ref.

        No governor or FSM state moves here, so a portfolio allocator can gather every
        symbol's demand at a timestamp before any control state is committed."""
        if self._pending is not None:
            raise RuntimeError('previous bar prepared but not committed')
        if self.last_input is not None and bar.index <= self.last_input[0].index:
            raise ValueError('bar must advance before prepare')
        f = self.features.extract(bar)
        vote = self.avr.step(f, self.config.bar_seconds)
        c = self.config
        # Parameter 1: three-source signed strategy reference E_ref. Market evidence is
        # counted once through the AVR; studies and grid direction enter once here.
        reference = clamp(c.avr_weight * vote.score + c.study_weight * context.study + c.dispatch_weight * context.dispatch)
        eligible = f.ready and not f.degraded and not f.gap and context.study_available and context.dispatch_available
        self._pending = (bar, context, f, vote, reference, eligible)
        return reference, eligible

    def commit(self, increase_capacity, execution_blocked):
        """Apply the allocated capacity and advance governor and FSM exactly once."""
        if self._pending is None:
            raise RuntimeError('commit without prepare')
        bar, context, f, vote, reference, eligible = self._pending
        context = replace(context, increase_capacity=increase_capacity, execution_blocked=execution_blocked)
        c = self.config
        # Parameters 2/3: portfolio-capacity allocation result for this symbol.
        target = approve_target(reference, context.actual_exposure, context.increase_capacity)
        gov = self.governor.step(target, context.actual_exposure, f.dt_seconds,
                                 enabled=eligible, execution_blocked=context.execution_blocked)
        pulse, reason = self.fsm.step(gov.applied, eligible)
        out = Decision(c.symbol, c.bay_id, bar.index, bar.timestamp, pulse, reason,
                       target, context.actual_exposure, gov.applied, eligible, vote, gov, reference)
        self.last_input, self.last_result, self._pending = (bar, context), out, None
        self.steps += 1
        return out

    def snapshot(self):
        if self._pending is not None:
            raise RuntimeError('snapshot requires a committed bar')
        f, g, p = self.features, self.governor, self.fsm
        return deepcopy({'schema': 2, 'config': asdict(self.config),
            'volumes': list(f.volumes), 'ranges': list(f.ranges), 'pvt': f.pvt,
            'speed_reference': f.speed_reference,
            'avr': {'i': self.avr.pi.i_term, 'level': self.avr.level},
            'governor': {k: v for k, v in vars(g).items() if k != 'config'},
            'pulse': {k: v for k, v in vars(p).items() if k != 'config'},
            'last_bar': asdict(self.last_input[0]) if self.last_input else None,
            'last_context': asdict(self.last_input[1]) if self.last_input else None,
            'last_result': asdict(self.last_result) if self.last_result else None, 'steps': self.steps})

    def restore(self, payload):
        # Reconstruct into a temporary object; failed validation cannot partially mutate self.
        json.dumps(payload, allow_nan=False)
        if payload['schema'] != 2 or payload['config'] != asdict(self.config):
            raise ValueError('checkpoint configuration mismatch')
        new = type(self)(self.config)
        if len(payload['volumes']) > self.config.feature_window or len(payload['ranges']) != len(payload['volumes']):
            raise ValueError('invalid feature history')
        from .r7_state import number
        for x in payload['volumes'] + payload['ranges']:
            if number(x) < 0:
                raise ValueError('negative feature history')
        new.features.volumes.extend(payload['volumes'])
        new.features.ranges.extend(payload['ranges'])
        new.features.pvt = clamp(payload['pvt'])
        new.features.speed_reference = payload['speed_reference']
        if new.features.speed_reference is not None and number(new.features.speed_reference) <= 0:
            raise ValueError('invalid speed reference')
        new.avr.pi.i_term, new.avr.level = number(payload['avr']['i']), clamp(payload['avr']['level'])
        if abs(new.avr.pi.i_term) > new.avr.pi.cfg.integral_limit:
            raise ValueError('invalid AVR integral')
        expected_g = set(vars(new.governor)) - {'config'}
        if set(payload['governor']) != expected_g:
            raise ValueError('invalid governor schema')
        for key, val in payload['governor'].items():
            if val is not None:
                number(val)
            elif key != 'previous_actual':
                raise ValueError('missing governor state')
            setattr(new.governor, key, val)
        if abs(new.governor.integral) > self.config.integral_limit or max(abs(new.governor.command), abs(new.governor.output)) > 1:
            raise ValueError('invalid bounded governor state')
        ps = payload['pulse']
        if set(ps) not in ({'pulse', 'active_age', 'hold_age'}, {'pulse', 'active_age', 'hold_age', 'emergency_age'}) or ps['pulse'] not in ('BUY', 'SELL', 'HOLD'):
            raise ValueError('invalid pulse state')
        ps.setdefault('emergency_age', 0)
        if any(type(ps[k]) is not int or ps[k] < 0 for k in ('active_age', 'hold_age', 'emergency_age')) or ps['active_age'] > self.config.max_pulse_age or ps['hold_age'] > self.config.cooldown_bars:
            raise ValueError('invalid pulse clock')
        # A streak of 2 reverses and resets, so a stored streak is only ever 0 or 1.
        if ps['emergency_age'] > 1:
            raise ValueError('invalid emergency streak')
        if (ps['pulse'] == 'HOLD') != (ps['active_age'] == 0):
            raise ValueError('pulse clock inconsistent with pulse state')
        new.fsm.__dict__.update(ps)
        if type(payload['steps']) is not int or payload['steps'] < 0:
            raise ValueError('invalid step count')
        new.steps = payload['steps']
        if payload['last_bar'] is not None:
            bar, context = Bar(**payload['last_bar']), Context(**payload['last_context'])
            data = deepcopy(payload['last_result'])
            data['avr'], data['governor'] = MarketVote(**data['avr']), GovernorOutput(**data['governor'])
            result = Decision(**data)
            if (result.symbol, result.bay_id, result.bar_index, result.timestamp) != (self.config.symbol, self.config.bay_id, bar.index, bar.timestamp) or result.pulse != new.fsm.pulse or result.actual != context.actual_exposure:
                raise ValueError('checkpoint clock/identity mismatch')
            new.last_input, new.last_result = (bar, context), result
            new.features.last_bar = bar
        elif new.steps or payload['last_context'] is not None or payload['last_result'] is not None:
            raise ValueError('inconsistent empty checkpoint')
        self.__dict__.update(new.__dict__)
