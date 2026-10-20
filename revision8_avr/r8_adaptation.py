"""R8 runtime adaptation: the single-source R5 dynamic-parameter formulas, evaluated every bar.

Modes
-----
off     fixed parameters; nothing is evaluated (identical to R7).
shadow  every bar: build the environment, evaluate R8's dynamic formulas, check them against the
        host plant's snapshot, log every change.  Nothing is applied; decisions are identical to off.
paper   shadow, plus the evaluated profile is applied TIGHTEN-ONLY to the host engine's limits:
        safety / execution limits only fall, the dispatch ceiling only falls, entry confidence and
        minimum reward:risk only rise, minimum and maximum hold only shorten.
There is no live mode.

R8's four evidence PIDs are not adapted: R5's governor gains belong to a per-trade R-multiple loop
and, copied into a per-second vote PID, make it vote on the first bar of any small move (measured).
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass, replace
from hashlib import sha256
import json
import math

from .r8_dynamic_parameters import R5EnvironmentState, Revision5DynamicParameterController

R8_ADAPTATION_MODES = ('off', 'shadow', 'paper')
R8_ADAPTATION_MEMORY = 1000

# Strategy overlay: which direction is "tighter".  Higher entry confidence / reward:risk admits
# fewer entries; shorter min / max hold makes exits come sooner, never later.
STRATEGY_TIGHTER = (
    ('entry_confidence_threshold', max, float),
    ('min_risk_reward_ratio', max, float),
    ('min_hold_bars', min, int),
    ('max_hold_bars', min, int),
)


def flatten(value, prefix=''):
    """Every scalar of a snapshot as {dotted.path: value}; AVR registry coefficients are static and
    are left out (they never change bar to bar)."""
    out = {}
    if is_dataclass(value):
        for f in fields(value):
            if f.name == 'params':
                continue
            out.update(flatten(getattr(value, f.name), f'{prefix}.{f.name}' if prefix else f.name))
    elif isinstance(value, dict):
        for key in sorted(value):
            out.update(flatten(value[key], f'{prefix}.{key}' if prefix else str(key)))
    else:
        out[prefix] = value
    return out


def snapshot_image(snapshot):
    """The comparable content of a snapshot: every runtime value except the version counter."""
    image = flatten(snapshot)
    image.pop('version', None)
    return image


def environment_dict(environment):
    return {f.name: float(getattr(environment, f.name)) for f in fields(environment)}


class R8Adaptation:
    """Per-engine adaptive state: formulas, last snapshot, applied profile, append-only change log."""

    def __init__(self, mode, parameters, log_path=None):
        if mode not in R8_ADAPTATION_MODES:
            raise ValueError(f'r8_adaptation_mode must be one of {R8_ADAPTATION_MODES}')
        self.mode = mode
        self.controller = Revision5DynamicParameterController(parameters)
        self.vol_baseline = {}
        self.environment = None
        self.snapshot = None
        self.image = None
        self.applied = None
        self.count = 0
        self.digest = '0' * 64
        self.parity_checks = 0
        self.parity_mismatches = 0
        self.records = deque(maxlen=R8_ADAPTATION_MEMORY)
        self.log_path = log_path
        self._log = None

    @property
    def evaluates(self):
        return self.mode != 'off'

    @property
    def applies(self):
        return self.mode == 'paper'

    def evaluate(self, environment):
        snapshot = self.controller.evaluate(environment)
        previous = self.image
        self.environment, self.snapshot, self.image = environment, snapshot, snapshot_image(snapshot)
        return previous

    def check_parity(self, host_snapshot):
        """R8's formulas must reproduce the host plant's snapshot exactly (same environment, same
        registry).  Returns the differing paths."""
        self.parity_checks += 1
        host = snapshot_image(host_snapshot)
        diff = sorted(k for k in set(host) | set(self.image) if host.get(k) != self.image.get(k))
        if diff:
            self.parity_mismatches += 1
        return diff

    def record(self, timestamp, previous_image, previous_applied, parity_diff):
        changed = {k: {'old': None if previous_image is None else previous_image.get(k), 'new': v}
                   for k, v in self.image.items() if previous_image is None or previous_image.get(k) != v}
        applied_changed = self.applied != previous_applied
        if not changed and not applied_changed and not parity_diff:
            return None
        entry = {'sequence': self.count, 'timestamp': str(timestamp), 'mode': self.mode,
                 'version': self.snapshot.version, 'stress_index': self.snapshot.stress_index,
                 'risk_derate_factor': self.snapshot.risk_derate_factor,
                 'environment': environment_dict(self.environment), 'changed': changed,
                 'applied': {'old': previous_applied, 'new': self.applied} if applied_changed else None,
                 'parity_mismatch': parity_diff}
        line = json.dumps(entry, sort_keys=True, allow_nan=False)
        self.digest = sha256((self.digest + line).encode()).hexdigest()
        self.count += 1
        self.records.append(entry)
        if self.log_path is not None:
            if self._log is None:
                self._log = open(self.log_path, 'a', encoding='utf-8')
            self._log.write(line + '\n')
            self._log.flush()
        return entry

    def close(self):
        if self._log is not None:
            self._log.close()
            self._log = None

    def snapshot_state(self):
        return deepcopy({
            'mode': self.mode, 'version': self.controller._version,
            'vol_baseline': self.vol_baseline,
            'environment': None if self.environment is None else environment_dict(self.environment),
            'image': self.image, 'applied': self.applied,
            'count': self.count, 'digest': self.digest,
            'parity_checks': self.parity_checks, 'parity_mismatches': self.parity_mismatches})

    def validate_state(self, state):
        expected = {'mode', 'version', 'vol_baseline', 'environment', 'image', 'applied', 'count', 'digest',
                    'parity_checks', 'parity_mismatches'}
        if not isinstance(state, dict) or set(state) != expected:
            raise ValueError('invalid R8 adaptation checkpoint schema')
        if state['mode'] != self.mode:
            raise ValueError('R8 adaptation mode mismatch')
        for key in ('version', 'count', 'parity_checks', 'parity_mismatches'):
            if type(state[key]) is not int or state[key] < 0:
                raise ValueError(f'invalid R8 adaptation {key}')
        if not (isinstance(state['digest'], str) and len(state['digest']) == 64):
            raise ValueError('invalid R8 adaptation digest')
        if not isinstance(state['vol_baseline'], dict) or any(
                not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in state['vol_baseline'].values()):
            raise ValueError('invalid R8 volatility baseline')
        if state['environment'] is not None:
            R5EnvironmentState(**state['environment'])
        if (state['image'] is None) != (state['environment'] is None):
            raise ValueError('R8 adaptation image/environment mismatch')
        if state['applied'] is not None and not self.applies:
            raise ValueError('applied profile present outside paper mode')

    def reproduce(self, state):
        """Rebuild the checkpointed snapshot on a scratch controller; it must match exactly."""
        if state['environment'] is None:
            return None, None
        environment = R5EnvironmentState(**state['environment'])
        scratch = Revision5DynamicParameterController(self.controller.parameters)
        scratch._version = state['version'] - 1
        snapshot = scratch.evaluate(environment)
        if snapshot_image(snapshot) != state['image'] or snapshot.version != state['version']:
            raise ValueError('R8 adaptation checkpoint does not reproduce its snapshot')
        return environment, snapshot

    def restore_state(self, state):
        """Validate everything first; a rejected checkpoint leaves this object untouched."""
        self.validate_state(state)
        environment, snapshot = self.reproduce(state)
        self.controller._version = state['version']
        self.vol_baseline = dict(state['vol_baseline'])
        self.environment, self.snapshot = environment, snapshot
        self.image = deepcopy(state['image'])
        self.applied = deepcopy(state['applied'])
        self.count, self.digest = state['count'], state['digest']
        self.parity_checks, self.parity_mismatches = state['parity_checks'], state['parity_mismatches']
