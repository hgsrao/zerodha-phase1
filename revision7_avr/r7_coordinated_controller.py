"""One symbol's market control pipeline, 4-way parallel consensus interlock, and pulse FSM."""
from dataclasses import asdict, replace
from copy import deepcopy
import json
from .r7_state import Bar, Context, ControlConfig, Decision, MarketVote, GovernorOutput, digest, clamp, number
from .r7_features import SymbolFeatureExtractor
from .r7_market_avr import MarketAVR
from .r7_market_governor import (
    MarketGovernor,
    PulseState,
    TrendGovernorPID,
    AvrParallelPID,
    DispatchParallelPID,
    StudiesParallelPID,
)
from .r7_allocator import approve_target


class ConsensusInterlock:
    @staticmethod
    def evaluate(gov_vote: str, avr_vote: str, dispatch_vote: str, study_vote: str,
                 actual_exposure: float, allow_shorts: bool = True) -> tuple[str, str, str]:
        votes = (gov_vote, avr_vote, dispatch_vote, study_vote)
        actual = number(actual_exposure)
        in_long = actual > 1e-9
        in_short = actual < -1e-9

        if all(v == PulseState.BULL for v in votes):
            if in_short:
                return "BUY", "REDUCE_OR_EXIT_SHORT", "FOUR_WAY_BULL_CONSENSUS_EXIT_SHORT"
            if in_long:
                return "BUY", "HOLD_OR_SCALE_LONG", "FOUR_WAY_BULL_CONSENSUS_CONTINUE"
            return "BUY", "ENTER_LONG", "FOUR_WAY_BULL_CONSENSUS"

        if all(v == PulseState.BEAR for v in votes):
            if in_long:
                return "SELL", "REDUCE_OR_EXIT_LONG", "FOUR_WAY_BEAR_CONSENSUS_EXIT_LONG"
            if in_short:
                return "SELL", "HOLD_OR_SCALE_SHORT", "FOUR_WAY_BEAR_CONSENSUS_CONTINUE"
            if not allow_shorts:
                # SELL is reserved for exiting longs; flat stays flat.
                return "HOLD", "STAY_FLAT", "FOUR_WAY_BEAR_CONSENSUS_SHORT_BLOCKED_BY_POLICY"
            return "SELL", "SHORT_ENTRY_CANDIDATE", "FOUR_WAY_BEAR_CONSENSUS"

        if in_long or in_short:
            expected = PulseState.BULL if in_long else PulseState.BEAR
            if study_vote == expected:
                if avr_vote != expected:
                    return "HOLD", "HOLD_POSITION", "HOLD_STUDIES_PROJECT_SUPPORT_AVR_DROOPED"
                if dispatch_vote != expected:
                    return "HOLD", "HOLD_POSITION", "HOLD_STUDIES_PROJECT_SUPPORT_DISPATCH_DROOPED"
                return "HOLD", "HOLD_POSITION", "HOLD_STUDIES_PROJECTION_ACTIVE"
            return "HOLD", "HOLD_POSITION", "POSITION_HELD_WITHOUT_EXIT_CONSENSUS"

        if study_vote == PulseState.FLAT:
            return "HOLD", "STAY_FLAT", "NO_STUDY_PROJECTION_SETUP"
        if dispatch_vote != study_vote:
            return "HOLD", "STAY_FLAT", "ENTRY_BLOCKED_BY_DISPATCH_NIFTY"
        if avr_vote != study_vote:
            return "HOLD", "STAY_FLAT", "ENTRY_BLOCKED_BY_AVR_LIQUIDITY"
        return "HOLD", "STAY_FLAT", "NO_FOUR_WAY_ENTRY_CONSENSUS"


class PulseFSM:
    """BUY/HOLD/SELL pulse state. bind() is the live path; step() is the legacy Schmitt trigger."""
    def __init__(self, config):
        self.config = config
        self.pulse = 'HOLD'
        self.active_age = 0
        self.hold_age = config.cooldown_bars
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
                self.pulse, self.active_age, self.hold_age, self.emergency_age = 'HOLD', 0, min(1, c.cooldown_bars), 0
            else:
                self.active_age = min(c.max_pulse_age, self.active_age + 1)
        return self.pulse, f'{previous}_TO_{self.pulse}'

    def bind(self, direction, eligible=True):
        """The pulse IS this bar's four-way consensus (R3/R4 at the output). The only
        anti-churn left: after a release, a NEW entry waits cooldown_bars bars."""
        if direction not in ('BUY', 'HOLD', 'SELL') or type(eligible) is not bool:
            raise ValueError('invalid consensus direction')
        if not eligible:
            self.pulse, self.active_age, self.hold_age, self.emergency_age = 'HOLD', 0, 0, 0
            return self.pulse, 'INPUT_NOT_ELIGIBLE'
        previous = self.pulse
        if direction == 'HOLD':
            if previous != 'HOLD':
                self.pulse, self.active_age, self.hold_age = 'HOLD', 0, 0
            self.hold_age = min(self.config.cooldown_bars, self.hold_age + 1)
            return self.pulse, f'{previous}_TO_HOLD'
        if previous == 'HOLD' and self.hold_age < self.config.cooldown_bars:
            self.hold_age += 1
            return self.pulse, 'COOLDOWN_BLOCKS_ENTRY'
        self.active_age = min(self.config.max_pulse_age, self.active_age + 1) if previous == direction else 1
        self.pulse, self.hold_age, self.emergency_age = direction, 0, 0
        return self.pulse, f'{previous}_TO_{direction}'


class SymbolController:
    def __init__(self, config):
        self.config = config
        self.features = SymbolFeatureExtractor(config)
        self.avr = MarketAVR()
        self.exec_governor = MarketGovernor(config)
        self.governor = self.exec_governor
        self.fsm = PulseFSM(config)

        limit = config.evidence_integral_limit
        self.pid_gov = TrendGovernorPID(droop_r=config.droop_r, enter_threshold=config.governor_threshold, integral_limit=limit)
        self.pid_avr = AvrParallelPID(droop_r=config.droop_r, enter_threshold=config.avr_threshold, integral_limit=limit)
        self.pid_dispatch = DispatchParallelPID(droop_r=config.dispatch_droop_r, enter_threshold=config.dispatch_threshold,
                                                integral_limit=limit)
        self.pid_study = StudiesParallelPID(droop_r=config.study_droop_r, enter_threshold=config.studies_threshold,
                                            integral_limit=limit)
        self.last_consensus = None

        self.last_input = None
        self.last_result = None
        self._pending = None
        self.steps = 0

    def step(self, bar, context):
        if self.last_input is not None and bar.index == self.last_input[0].index:
            if (bar, context) != self.last_input:
                raise ValueError('conflicting same-bar input; integrators not replayed')
            return self.last_result
        self.prepare(bar, context)
        return self.commit(context.increase_capacity, context.execution_blocked)

    def prepare(self, bar, context):
        if self._pending is not None:
            raise RuntimeError('previous bar prepared but not committed')
        if self.last_input is not None and bar.index <= self.last_input[0].index:
            raise ValueError('bar must advance before prepare')
        f = self.features.extract(bar)
        market_vote = self.avr.step(f, self.config.bar_seconds)
        vote = MarketVote(
            score=clamp(market_vote.score),
            quality=clamp(market_vote.quality),
        )
        c = self.config
        eligible = f.ready and not f.degraded and not f.gap and context.study_available and context.dispatch_available

        # Governor: price only - this close against its own moving reference (no volume).
        gov_input = clamp((f.bar.close / f.speed_reference_price - 1.0) / c.governor_price_full_scale)
        # AVR: participation - candle buying/selling pressure scaled by the volume behind it.
        # Signed, so AVR can oppose a move as well as support it (unsigned volume never could).
        avr_input = clamp(f.pressure * min(f.relative_volume, c.avr_volume_full_scale) / c.avr_volume_full_scale)

        self.pid_gov.set_context(price_moved=abs(gov_input) > 0.005)
        self.pid_avr.set_context(participation=avr_input)
        self.pid_dispatch.set_context(grid_deviation=context.dispatch)
        self.pid_study.set_context(confidence=abs(context.study))

        gov_vote = self.pid_gov.step(gov_input, f.dt_seconds, enabled=eligible)
        avr_vote = self.pid_avr.step(avr_input, f.dt_seconds, enabled=eligible)
        dispatch_vote = self.pid_dispatch.step(context.dispatch, f.dt_seconds, enabled=eligible and context.dispatch_available)
        study_vote = self.pid_study.step(context.study, f.dt_seconds, enabled=eligible and context.study_available)

        consensus_dir, action_intent, consensus_reason = ConsensusInterlock.evaluate(
            gov_vote,
            avr_vote,
            dispatch_vote,
            study_vote,
            context.actual_exposure,
            c.allow_shorts,
        )

        self.last_consensus = {
            "direction": consensus_dir,
            "action_intent": action_intent,
            "reason": consensus_reason,
            "votes": {
                "governor": gov_vote,
                "avr": avr_vote,
                "dispatch": dispatch_vote,
                "study": study_vote,
            },
            "inputs": {"governor": gov_input, "avr": avr_input, "dispatch": context.dispatch, "study": context.study},
            "outputs": {
                "governor": self.pid_gov.output,
                "avr": self.pid_avr.output,
                "dispatch": self.pid_dispatch.output,
                "study": self.pid_study.output,
            },
        }

        if consensus_dir == "BUY":
            reference = 1.0
        elif consensus_dir == "SELL":
            reference = 0.0 if context.actual_exposure > 0 else -1.0
        else:
            reference = clamp(context.actual_exposure)
        reference = clamp(reference, -1.0, 1.0)

        self._pending = (bar, context, f, vote, reference, eligible, deepcopy(self.last_consensus))
        return reference, eligible

    def commit(self, increase_capacity, execution_blocked):
        if self._pending is None:
            raise RuntimeError('commit without prepare')
        bar, context, f, vote, reference, eligible, consensus = self._pending
        context = replace(context, increase_capacity=increase_capacity, execution_blocked=execution_blocked)
        c = self.config

        reference = clamp(reference, -1.0, 1.0)
        target = approve_target(
            reference,
            context.actual_exposure,
            context.increase_capacity,
        )

        execution = self.exec_governor.step(
            target,
            context.actual_exposure,
            f.dt_seconds,
            enabled=eligible,
            execution_blocked=context.execution_blocked,
        )

        # The pulse follows this bar's four-way consensus; FSM adds only the entry cooldown.
        final_pulse, fsm_reason = self.fsm.bind(consensus["direction"], eligible)
        full_reason = f"{consensus['reason']} | FSM: {fsm_reason}"

        out = Decision(
            symbol=c.symbol,
            bay_id=c.bay_id,
            bar_index=bar.index,
            timestamp=bar.timestamp,
            pulse=final_pulse,
            reason=full_reason,
            target=target,
            actual=context.actual_exposure,
            signal=execution.applied,
            eligible=eligible,
            avr=vote,
            governor=execution,
            reference=reference,
            action_intent=consensus["action_intent"],
            consensus_reason=consensus["reason"],
            consensus_votes=dict(consensus["votes"]),
            consensus_outputs={"inputs": dict(consensus["inputs"]), "outputs": dict(consensus["outputs"])},
        )
        self.last_input, self.last_result, self._pending = (bar, context), out, None
        self.steps += 1
        return out

    def snapshot(self):
        if self._pending is not None:
            raise RuntimeError('snapshot requires a completed bar')
        f, g, p = self.features, self.exec_governor, self.fsm
        gov_state = {k: v for k, v in vars(g).items() if k != 'config'}
        pulse_state = {k: v for k, v in vars(p).items() if k != 'config'}
        return deepcopy({
            'schema': 3,
            'config': asdict(self.config),
            'volumes': list(f.volumes),
            'ranges': list(f.ranges),
            'pvt': f.pvt,
            'speed_reference': f.speed_reference,
            'features': {
                'volumes': list(f.volumes),
                'ranges': list(f.ranges),
                'pvt': f.pvt,
                'speed_reference': f.speed_reference,
            },
            'avr': {'i': self.avr.pi.i_term, 'level': self.avr.level},
            'parallel_pids': {
                'governor': self.pid_gov.snapshot_state(),
                'avr': self.pid_avr.snapshot_state(),
                'dispatch': self.pid_dispatch.snapshot_state(),
                'study': self.pid_study.snapshot_state(),
            },
            'governor': gov_state,
            'execution_governor': gov_state,
            'pulse': pulse_state,
            'last_bar': asdict(self.last_input[0]) if self.last_input else None,
            'last_context': asdict(self.last_input[1]) if self.last_input else None,
            'last_result': asdict(self.last_result) if self.last_input else None,
            'last_consensus': deepcopy(self.last_consensus),
            'steps': self.steps,
        })

    def restore(self, payload):
        json.dumps(payload, allow_nan=False)
        if payload.get('schema') not in (2, 3) or payload.get('config') != asdict(self.config):
            raise ValueError('checkpoint configuration mismatch')
        new = type(self)(self.config)
        feat = payload.get('features') or {}
        volumes = payload['volumes'] if 'volumes' in payload else feat.get('volumes', [])
        ranges = payload['ranges'] if 'ranges' in payload else feat.get('ranges', [])
        pvt = payload['pvt'] if 'pvt' in payload else feat.get('pvt', 0.0)
        speed_ref = payload['speed_reference'] if 'speed_reference' in payload else feat.get('speed_reference')
        if len(volumes) > self.config.feature_window or len(ranges) != len(volumes):
            raise ValueError('invalid feature history')
        for x in volumes + ranges:
            if number(x) < 0:
                raise ValueError('negative feature history')
        new.features.volumes.extend(volumes)
        new.features.ranges.extend(ranges)
        new.features.pvt = clamp(pvt)
        new.features.speed_reference = speed_ref
        if new.features.speed_reference is not None and number(new.features.speed_reference) <= 0:
            raise ValueError('invalid speed reference')
        new.avr.pi.i_term, new.avr.level = number(payload['avr']['i']), clamp(payload['avr']['level'])
        if abs(new.avr.pi.i_term) > new.avr.pi.cfg.integral_limit:
            raise ValueError('invalid AVR integral')

        if 'parallel_pids' in payload:
            pp = payload['parallel_pids']
            if not isinstance(pp, dict) or set(pp) != {'governor', 'avr', 'dispatch', 'study'}:
                raise ValueError('invalid parallel_pids checkpoint schema')
            new.pid_gov.restore_state(pp['governor'])
            new.pid_avr.restore_state(pp['avr'])
            new.pid_dispatch.restore_state(pp['dispatch'])
            new.pid_study.restore_state(pp['study'])

        gov_payload = payload['governor'] if 'governor' in payload else payload.get('execution_governor')
        expected_g = set(vars(new.exec_governor)) - {'config'}
        if not isinstance(gov_payload, dict) or set(gov_payload) != expected_g:
            raise ValueError('invalid governor schema')
        for key, val in gov_payload.items():
            if val is not None:
                number(val)
            elif key != 'previous_actual':
                raise ValueError('missing governor state')
            setattr(new.exec_governor, key, val)
        if abs(new.exec_governor.integral) > self.config.integral_limit or max(abs(new.exec_governor.command), abs(new.exec_governor.output)) > 1:
            raise ValueError('invalid bounded governor state')

        ps = dict(payload['pulse'])
        ps.pop('config', None)
        if set(ps) not in ({'pulse', 'active_age', 'hold_age'}, {'pulse', 'active_age', 'hold_age', 'emergency_age'}) or ps['pulse'] not in ('BUY', 'SELL', 'HOLD'):
            raise ValueError('invalid pulse state')
        ps.setdefault('emergency_age', 0)
        if any(type(ps[k]) is not int or ps[k] < 0 for k in ('active_age', 'hold_age', 'emergency_age')) or ps['active_age'] > self.config.max_pulse_age or ps['hold_age'] > self.config.cooldown_bars:
            raise ValueError('invalid pulse clock')
        if ps['emergency_age'] > 1:
            raise ValueError('invalid emergency streak')
        if (ps['pulse'] == 'HOLD') != (ps['active_age'] == 0):
            raise ValueError('pulse clock inconsistent with pulse state')
        new.fsm.__dict__.update(ps)
        if type(payload['steps']) is not int or payload['steps'] < 0:
            raise ValueError('invalid step count')
        new.steps = payload['steps']
        new.last_consensus = deepcopy(payload.get('last_consensus'))
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
