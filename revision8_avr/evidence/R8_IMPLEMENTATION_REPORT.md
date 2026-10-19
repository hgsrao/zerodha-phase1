# R8 implementation report

**Branch:** `claude/funny-gauss-u3eq3t` (PR #14): commit `ec016f0` (R8 code), plus an evidence commit
(`revision8_avr/evidence/`), on top of the four-way consensus commit `3a764cc`. **R7 (`revision7_avr/`) is unchanged.** `LIVE_TRADING_ENABLED=False` throughout, and there
is no live mode.

## 1. What was done

1. **Created `revision8_avr/`** as a copy of R7 with the four-way consensus, every name renamed `r7` → `r8`.
2. **Copied the R5/R6 code R7 used into R8, in full.**
   - Only the imports were re-pointed; each copied file records its source path and SHA-256.
   - R8's runtime code no longer imports `revision5` or `revision6_avr`. A test checks every `r8_*.py` file.
3. **Built R8's own runtime adaptation** with three modes: `off` / `shadow` / `paper`. There is no live mode.
4. **Applied the audit fixes** you approved: 10, 11, 12, 13, and moving the gains into the config.
5. **Ran the tests and the 48-symbol Engine B and Engine C replays** (section 5).

## 2. What was copied from R5 / R6

| R8 file | Copied from | What it holds |
|---|---|---|
| `r8_topology.py` | `revision5/topology.py` | 48 symbols, 5 bays |
| `r8_controller_parameters.py` | `revision5/controller_parameters.py` | the complete parameter registry (299 values) and every schedule coefficient |
| `r8_dynamic_parameters.py` | `revision5/dynamic_parameters.py` | **all the dynamic-parameter formulas**: governor, AVR, unit protection, grid protection, plant, strategy, execution and safety profiles |
| `r8_governor_spec.py` | `revision5/governor.py` (spec classes only) | `BayGovernorSpec`, `PositionControlV3`: the classes the registry builds |
| `r8_machine_dynamics.py` | `revision5/machine_dynamics.py` | machine dynamics |
| `r8_machine_archetypes.py` | `revision5/machine_archetypes.py` | GTG/STG archetypes used by the turbine units |
| `r8_turbine_governor.py` | `revision6_avr/r6_turbine_governor.py` | physical turbine units |
| `r8_avr_core.py` | `revision6_avr/r6_avr_core_fixed.py` | the R6 AVR core **with fixes F1–F9** (transfer item T1) |

**Proof the formulas are copied exactly:** `test_r8_formulas_reproduce_the_r5_formulas_exactly` evaluates
R5's formulas and R8's copy in 4 market conditions and compares every field. It passes. In the replays,
R8's copy also matched the host plant's own snapshot on every bar (section 5).

**What stays in the host:** R8 still runs inside the Engine B / Engine C host (`revision2_external`,
`engine_b`, `engine_c`). The host carries:
- the venue/broker simulation;
- the order and position ledger;
- the R5 plant's own safety;
- the R5 plant's own per-bar governor, AVR and relay profiles, applied once, as before.

## 3. Adaptation modes

| Mode | What happens every bar | Decisions |
|---|---|---|
| `off` (default) | nothing is evaluated | same as R7 |
| `shadow` | R8 builds the market environment, runs its own formulas, checks the result against the host plant's snapshot, and logs every change (old → new) | identical to `off` |
| `paper` | as `shadow`, then applies the profile **tighten-only** (below) | can be stricter |

What `paper` may change, and in which direction only:

| Value | Direction |
|---|---|
| max gross exposure, max per-symbol exposure, max positions | only lower |
| drawdown halt, daily-loss halt | only lower |
| slippage tolerance, market-data age, order timeout | only lower |
| dispatch ceiling | only lower |
| entry confidence threshold, minimum reward:risk | only higher |
| minimum hold, maximum hold | only shorter (R5's calm-trend 60 → 68 is capped at 60) |

Never changed in any mode:
- the gains and thresholds of the four voting PIDs, and the MarketAVR gains;
- the host's own profile consumption, which stays refused.

If R8's formulas ever disagree with the host plant, `paper` stops the run.

## 4. Tests

| Suite | Result |
|---|---|
| R8 complete (`revision8_avr/`) | **310 passed** (39 new for adaptation and the audit fixes) |
| R7 (`revision7_avr/`) | unchanged |

## 5. Replay results: 48 symbols, 200 bars, synthetic data

| Engine | Run | Decisions | Pulses (BUY / HOLD / SELL) | Trades | Net P&L | Decision digest | Adaptation |
|---|---|---|---|---|---|---|---|
| B | R7 | 9600 | 319 / 681 / 0 | 5 | 970.39 | `779229d934f3806e` | — |
| B | R8 off | 9600 | 319 / 681 / 0 | 5 | 970.39 | `fff29c104a0e724f` | not evaluated |
| B | R8 shadow | 9600 | 319 / 681 / 0 | 5 | 970.39 | `fff29c104a0e724f` | 140 bars, parity 140/140, 140 changes logged |
| B | R8 paper | 9600 | 319 / 681 / 0 | 5 | 901.62 | `e10d9def03ef73cd` | 140 bars, parity 140/140, 140 changes logged, checkpoint round trip OK |
| C | R7 | 9600 | 319 / 681 / 0 | 5 | 741.45 | `39890e0c1aa2bd3c` | — |
| C | R8 off | 9600 | 319 / 681 / 0 | 5 | 741.45 | `9ff3d6138d681eb5` | not evaluated |
| C | R8 shadow | 9600 | 319 / 681 / 0 | 5 | 741.45 | `9ff3d6138d681eb5` | 140 bars, parity 140/140, 140 changes logged |
| C | R8 paper | 9600 | 319 / 681 / 0 | 5 | 788.90 | `df127386d2701a6f` | 140 bars, parity 140/140, 140 changes logged, checkpoint round trip OK |

- Engine B: R8 `off` vs R7, last 1000 decision records with MarketAVR telemetry left out: **identical**. R8 `shadow` vs `off` decision digest: **identical**.
- Engine C: R8 `off` vs R7, last 1000 decision records with MarketAVR telemetry left out: **identical**. R8 `shadow` vs `off` decision digest: **identical**.

**About `paper`:** the same 5 entries and the same pulses as `off`, but net P&L changed (Engine B 970.39 → 901.62,
Engine C 741.45 → 788.90). The tighter limits acted on the trades, for example per-symbol exposure
0.15 → 0.131 (sizing), max positions 5 → 4 and max hold 60 → 59. I did not record a per-trade breakdown.
No SELL pulses appear because the synthetic data trends upward; SELL behaviour was verified earlier on
the balanced test stream.

**About "R8 `off` = R7":** the decision fingerprint is not byte-identical. The MarketAVR telemetry score
differs in the 17th decimal place (largest measured difference 5.6×10⁻¹⁷), because R8 uses R6's fixed
anti-windup (F1). MarketAVR is telemetry only, not one of the four votes. Every other decision field is
identical, as is every pulse, trade and P&L figure.

**Not yet run:** full-session replays (390 bars) and replays on real frozen market data. These replays used
synthetic data. The raw results are in `revision8_avr/evidence/`.

## 6. The audits: what was applied

### ChatGPT

| # | Finding | Applied? | What was done |
|---|---|---|---|
| 4 | No adaptation option in the replay runner | **Done** | `run_r8_replay.py --adaptation off\|shadow\|paper` (code 7.10) |
| 5 | Tests contradict adaptation | **Done** | The guard test now checks only that the *host's* switch is refused; 39 new tests |
| 6 | Governor/AVR/protection values only logged | **No, by design** | The host plant already applies them every bar; applying them again would apply them twice |
| 7 | PID gains hard-coded | **Done** (moved to config, not adapted) | code 7.6–7.8 |
| 8 | MarketAVR gains hard-coded | **Done** (moved to config) | code 7.9. ChatGPT called the third gain `kd`; it is the anti-windup gain `kaw` |
| 9 | Shadow requires the paper plant | **No** | That plant mode is what the B/C runners always use; shadow needs it for the market input and the parity check |
| 10 | Status flag ambiguous | **Done** | `r8_adaptation_enabled` = shadow or paper; `r8_adaptation_applies` = paper (code 7.3) |
| 11 | Restore not atomic | **Done** | The saved profile must equal what R8 derives from the saved snapshot; everything is validated and built before any assignment (code 7.4, 7.5) |
| 12 | No identity in the checkpoint | **Done** | Registry identity, config hash and R8 parameter fingerprint are saved and checked (code 7.5) |
| 13 | Parity misses the AVR coefficients | **Done** | At startup R8's whole parameter image, AVR coefficients included, must equal the host's (code 7.2, 7.3) |
| 14 | BUY can flip straight to SELL | **No** | That is your approved four-way consensus with the 1-bar cooldown |
| 15 | No runtime evidence | **Done** | sections 4 and 5 |

### Gemini

| Finding | Applied? | Why |
|---|---|---|
| "Crash on the first paper bar: `_decision_config_key` never set" | **No, not a bug** | The host sets it (`revision2_external/orchestrator.py:323`). Checked on a fresh engine, and `paper` ran every replay bar without error |
| "Logged to `decisions.jsonl` with `r8_decision_digest`" | Correction | Adaptation has its own log (`r8_adaptation.jsonl`) and its own digest, so the decision stream is untouched |
| Its 8 passed points | Agree | except "restore is atomic", which was not fully true and is fixed now (ChatGPT 11) |

## 7. The code

### 7.1 New file `r8_adaptation.py` (complete)

```python
"""R8 runtime adaptation: R8's own copy of the dynamic-parameter formulas, evaluated every bar.

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
```

### 7.2 `r8_engine.py`: parameter fingerprint (audit fix 12/13)

```python
# revision8_avr/r8_engine.py:25
def r8_parameter_fingerprint(parameters):
    """SHA-256 of the complete controller-parameter image: every registry value (governor specs,
    schedules, AVR coefficients, protection, dispatcher, HRSG, profile schedules), the profile bases
    and the position-control V3 values.  Works on the host's image and on R8's copy alike."""
    payload = {'values': dict(parameters.values), 'profile_bases': dict(parameters.profile_bases),
               'v3_values': dict(parameters.v3_values)}
    return sha256(json.dumps(payload, sort_keys=True, allow_nan=False, default=str).encode()).hexdigest()
```

### 7.3 `r8_engine.py`: constructor additions (modes, flags, host refusal, startup identity)

```python
# revision8_avr/r8_engine.py:53
    def __init__(self, *args, r8_configs, r8_dispatch_scale=0.01,
                 r8_allow_native_shorts=True, r8_decision_log_path=None,
                 r8_decision_memory=5000, r8_adaptation_mode='off', r8_adaptation_log_path=None,
                 **kwargs):
        if r8_adaptation_mode not in R8_ADAPTATION_MODES:
            raise ValueError(f'r8_adaptation_mode must be one of {R8_ADAPTATION_MODES} (there is no live mode)')
```
```python
# revision8_avr/r8_engine.py:107
        self.r8_adaptation_mode = r8_adaptation_mode
        self.r8_adaptation_enabled = r8_adaptation_mode in ('shadow', 'paper')   # evaluates and logs
        self.r8_adaptation_applies = r8_adaptation_mode == 'paper'                # installs tighten-only limits
```
```python
# revision8_avr/r8_engine.py:113
        # R8 owns adaptation: the host engine's own profile consumption stays off so a profile can
        # never be applied twice or by a path R8 does not log and checkpoint.
        if getattr(self, 'full_adaptation', False) or getattr(self, 'dynamic_profile_mode', 'off') != 'off':
            raise ValueError('R8 owns adaptation: use r8_adaptation_mode, host dynamic_profile_mode must stay off')
        if r8_adaptation_mode != 'off' and not getattr(self, '_plant_wired', False):
            raise ValueError('r8_adaptation_mode shadow/paper requires the PAPER_APPLY plant')
        # R8's own registry image, read from the run's EffectiveConfig exactly as the host reads its own.
        if hasattr(self, 'config') and hasattr(self, 'safety_contract'):
            self.r8_parameters = R5ControllerParameters.from_config(
                self.config, safety_bases=dict(self.safety_contract.values))
        else:                                    # minimal test hosts; off mode never evaluates
            self.r8_parameters = R5ControllerParameters.defaults()
        self.r8_parameter_fingerprint = r8_parameter_fingerprint(self.r8_parameters)
        host_parameters = getattr(self, 'controller_parameters', None)
        if host_parameters is not None and r8_parameter_fingerprint(host_parameters) != self.r8_parameter_fingerprint:
            raise ValueError('R8 parameter image differs from the host parameter image')
        self.r8_adaptation = R8Adaptation(r8_adaptation_mode, self.r8_parameters, r8_adaptation_log_path)
```

### 7.4 `r8_engine.py`: per-bar adaptation (environment, hook, tighten-only values, install)

```python
# revision8_avr/r8_engine.py:332
    def _r8_environment(self, tick_events):
        """Causal supervisory environment for this timestamp: volatility against each symbol's own
        EMA baseline (R8 keeps its own baseline), HMM stress posterior, trend share, fleet drawdown
        and mean governor error.  Same formula as the host plant's environment."""
        alpha = 2.0 / (self.governor_config.z_window_bars + 1.0)
        ratios, stress, trending, measured = [], [], 0, 0
        baselines = self.r8_adaptation.vol_baseline
        for event in tick_events:
            telemetry = self._governor_telemetry.get(event.symbol)
            if telemetry is None or not telemetry.available or not telemetry.atr_fraction:
                continue
            measured += 1
            fraction = float(telemetry.atr_fraction)
            baseline = baselines.get(event.symbol)
            if baseline is not None and baseline > 0.0:
                ratios.append(fraction / baseline)
            baselines[event.symbol] = fraction if baseline is None else baseline + alpha * (fraction - baseline)
            trending += int(abs(float(telemetry.z_score)) >= 1.0)
            observation = self.id_box.latest_regime_observation(event.symbol)
            if observation.get('available') and observation.get('stress_probability') is not None:
                stress.append(float(observation['stress_probability']))
        turbulent = min(1.0, max(0.0, sum(stress) / len(stress))) if stress else 1.0 / 3.0
        trend_share = trending / measured if measured else 0.5
        ratios.sort()
        governors = [bay.governor for bay in self.real_plant_dcs.bays.values()]
        return R5EnvironmentState(
            normalized_vol=ratios[len(ratios) // 2] if ratios else 1.0,
            trend_probability=(1.0 - turbulent) * trend_share,
            chop_probability=(1.0 - turbulent) * (1.0 - trend_share),
            turbulent_probability=turbulent,
            fleet_drawdown_fraction=self._current_drawdown(),
            control_error_delta=sum(g.last_error for g in governors) / len(governors),
        )

# revision8_avr/r8_engine.py:366
    def _plant_bar_step(self, timestamp, tick_events, symbol_bars):
        adaptation = self.r8_adaptation
        if not adaptation.evaluates or not self._plant_wired:
            return super()._plant_bar_step(timestamp, tick_events, symbol_bars)
        # Read the environment before the host steps: the inputs are the same ones the host reads.
        environment = self._r8_environment(tick_events)
        super()._plant_bar_step(timestamp, tick_events, symbol_bars)
        previous_image, previous_applied = adaptation.image, deepcopy(adaptation.applied)
        adaptation.evaluate(environment)
        parity = adaptation.check_parity(self.real_plant_dcs.dynamic_snapshot)
        if parity and adaptation.applies:
            adaptation.record(timestamp, previous_image, previous_applied, parity)
            raise RuntimeError(f'R8 dynamic formulas disagree with the host plant: {parity[:5]}')
        if adaptation.applies:
            profile = self._r8_profile_values(adaptation.snapshot)
            self._r8_install_profile(profile)
            adaptation.applied = profile
        adaptation.record(timestamp, previous_image, previous_applied, parity)
        self.r8_gate_counts['ADAPTATION_BARS'] += 1

# revision8_avr/r8_engine.py:386
    def _r8_profile_values(self, snapshot):
        """Pure: the tighten-only operating values for this snapshot.  Strategy values move only in
        the tighter direction from the run's own base (and stay inside registry bounds); safety and
        execution limits are min(contract, profile); the dispatch ceiling only falls."""
        strategy, safety, execution = snapshot.strategy, snapshot.safety, snapshot.execution
        overlay = {}
        for name, tighter, kind in STRATEGY_TIGHTER:
            spec = self.registry.params[name]
            value = min(max(float(getattr(strategy, name)), spec.minimum), spec.maximum)
            overlay[name] = kind(tighter(value, float(self.config.require(name))))
        c = self.safety_contract.values
        gate = self._base_gate_config
        tightened = {
            'drawdown_halt_threshold': min(float(c['safety_drawdown_halt_threshold']), float(safety.drawdown_halt_threshold)),
            'daily_loss_halt_threshold': float(c['max_daily_loss_rupees']) * min(1.0, float(safety.daily_loss_limit_multiplier)),
            'max_concurrent_positions': min(int(c['max_concurrent_positions']), int(safety.max_concurrent_positions)),
            'max_gross_exposure_fraction': min(float(c['max_gross_exposure_fraction']), float(safety.max_gross_exposure_fraction)),
            'max_exposure_per_symbol_fraction': min(float(c['max_exposure_per_symbol_fraction']),
                                                    float(safety.max_symbol_exposure_fraction)),
            'slippage_tolerance_percent': min(float(gate.slippage_tolerance_percent), float(execution.slippage_tolerance_fraction)),
            'max_market_data_age_seconds': min(int(gate.max_market_data_age_seconds), int(execution.max_market_data_age_seconds)),
            'order_timeout_seconds': min(int(gate.order_timeout_seconds), int(execution.order_timeout_seconds)),
        }
        merit = self.plant_control.dispatch_controller.merit_source
        ceiling = min(float(merit.max_ceiling), float(snapshot.plant.dispatcher_max_ceiling))
        floor = min(float(snapshot.plant.dispatcher_min_floor), ceiling)
        return {'version': snapshot.version, 'strategy': overlay, 'safety_and_execution': tightened,
                'dispatch_ceiling': ceiling, 'dispatch_floor': floor}

# revision8_avr/r8_engine.py:415
    def _r8_prepare_profile(self, profile):
        """Validate a profile and build everything installing it needs, without changing the engine."""
        if not isinstance(profile, dict) or set(profile) != {'version', 'strategy', 'safety_and_execution',
                                                             'dispatch_ceiling', 'dispatch_floor'}:
            raise ValueError('invalid R8 profile schema')
        if set(profile['strategy']) != {name for name, _, _ in STRATEGY_TIGHTER}:
            raise ValueError('invalid R8 strategy profile')
        key = tuple(sorted(profile['strategy'].items()))
        decision_config = None
        if key != self._decision_config_key:
            values = self.config.as_dict()
            values.update(profile['strategy'])
            decision_config = type(self.config).build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        gate_config = replace(self._base_gate_config, **profile['safety_and_execution'])
        entry_engine = None
        if gate_config != self.entry_decision_engine.config:
            entry_engine = type(self.entry_decision_engine)(config=gate_config, logger=self.entry_decision_engine.logger)
        return {'profile': deepcopy(profile), 'key': key, 'decision_config': decision_config, 'entry_engine': entry_engine}

# revision8_avr/r8_engine.py:434
    def _r8_commit_profile(self, prepared):
        """Assign a prepared profile to the host consumers (plain assignments; cannot fail midway)."""
        profile = prepared['profile']
        if prepared['decision_config'] is not None:
            self._decision_config, self._decision_config_key = prepared['decision_config'], prepared['key']
        if prepared['entry_engine'] is not None:
            self.entry_decision_engine = prepared['entry_engine']
        if getattr(self, '_run', None) is not None:
            self._run.max_concurrent = profile['safety_and_execution']['max_concurrent_positions']
            self._run.max_gross_fraction = profile['safety_and_execution']['max_gross_exposure_fraction']
        merit = self.plant_control.dispatch_controller.merit_source
        merit.runtime_ceiling_cap = profile['dispatch_ceiling']
        merit.runtime_floor = profile['dispatch_floor']
        self._runtime_profile = deepcopy(profile)

# revision8_avr/r8_engine.py:449
    def _r8_install_profile(self, profile):
        """Hand a tighten-only profile to the host's existing consumers (PA / ID / Box 6 config,
        safety gate, run limits, sector dispatch, drawdown halt and per-symbol sizing)."""
        self._r8_commit_profile(self._r8_prepare_profile(profile))

# revision8_avr/r8_engine.py:454
    def r8_adaptation_report(self):
        a = self.r8_adaptation
        return {'mode': a.mode, 'bars': self.r8_gate_counts.get('ADAPTATION_BARS', 0),
                'version': a.controller._version, 'changes_logged': a.count, 'digest': a.digest,
                'parity_checks': a.parity_checks, 'parity_mismatches': a.parity_mismatches,
                'stress_index': None if a.snapshot is None else a.snapshot.stress_index,
                'applied': deepcopy(a.applied)}
```

### 7.5 `r8_engine.py`: checkpoint with identity, atomic restore (audit fixes 11/12)

```python
# revision8_avr/r8_engine.py:669
    def r8_snapshot(self):
        if self._r8_observed or self._r8_batch or self._r8_deferred_exits:
            raise RuntimeError('snapshot requires a completed control handoff')
        return deepcopy({'schema': 3, 'adaptation': self.r8_adaptation_mode, 'identity': self._r8_identity(),
            'adaptation_state': self.r8_adaptation.snapshot_state(), 'dispatch_scale': self.r8_dispatch_scale,
            'allow_shorts': self.r8_allow_native_shorts, 'topology': self.r8_topology_report,
            'controllers': {s: c.snapshot() for s, c in self.r8_symbol_controllers.items()},
            'physical': {b: x.snapshot() for b, x in self.r8_physical_units.items()},
            'bay_state': {b: x.symbols for b, x in self.r8_bay_state.items()},
            'normal_exit_state': self.r8_normal_exit_state,
            'counts': dict(self.r8_gate_counts),
            'decision_count': self.r8_decision_count, 'decision_digest': self.r8_decision_digest})

# revision8_avr/r8_engine.py:682
    def _r8_identity(self):
        registry = getattr(self, 'registry', None)
        config = getattr(self, 'config', None)
        return {'registry_identity_sha256': getattr(registry, 'FROZEN_IDENTITY_SHA256', None),
                'config_hash': getattr(config, 'config_hash', None),
                'r8_parameter_fingerprint': self.r8_parameter_fingerprint}

# revision8_avr/r8_engine.py:689
    def r8_restore(self, payload):
        """Restore R8 control state only, alongside a separately restored native ledger."""
        json.dumps(payload, allow_nan=False)
        if (payload['schema'], payload['adaptation'], payload['dispatch_scale'], payload['allow_shorts'], payload['topology']) != (3, self.r8_adaptation_mode, self.r8_dispatch_scale, self.r8_allow_native_shorts, self.r8_topology_report):
            raise ValueError('R8 checkpoint identity/configuration mismatch')
        if payload.get('identity') != self._r8_identity():
            raise ValueError('R8 checkpoint registry / configuration / parameter identity mismatch')
        adaptation_state = payload['adaptation_state']
        self.r8_adaptation.validate_state(adaptation_state)
        _, restored_snapshot = self.r8_adaptation.reproduce(adaptation_state)
        prepared_profile = None
        if adaptation_state['applied'] is not None:
            # The saved profile must be exactly what this engine derives from the saved snapshot.
            if restored_snapshot is None or adaptation_state['applied'] != self._r8_profile_values(restored_snapshot):
                raise ValueError('R8 checkpoint applied profile does not follow from its snapshot')
            prepared_profile = self._r8_prepare_profile(adaptation_state['applied'])
        if set(payload['controllers']) != set(self.r8_symbol_controllers) or set(payload['physical']) != set(BAY_IDS) or set(payload['bay_state']) != set(BAY_IDS):
            raise ValueError('R8 checkpoint universe mismatch')
        new = {s: SymbolController(c) for s, c in self.r8_configs.items()}
        for s, controller in new.items():
            controller.restore(payload['controllers'][s])
        physical = {b: TurbineGovernorUnit(c, p_mech_pu=c.power_min_pu) for b, c in repository_defaults().items()}
        for b, unit in physical.items():
            unit.restore(payload['physical'][b])
        bays = {b: BayAggregateState() for b in BAY_IDS}
        for s, ctrl in new.items():
            if ctrl.last_result is not None:
                d = ctrl.last_result
                f = self.r8_configs[s].budget_fraction
                bays[d.bay_id].update(type(d)(**{**vars(d), 'target': d.target * f, 'actual': d.actual * f}))
        if {b: x.symbols for b, x in bays.items()} != payload['bay_state']:
            raise ValueError('R8 aggregate state mismatch')
        if any(type(v) is not int or v < 0 for v in payload['counts'].values()):
            raise ValueError('invalid checkpoint counters')
        for s, state in payload['normal_exit_state'].items():
            if s not in new or set(state) != {'position_id', 'last_bar', 'count'} or type(state['count']) is not int or state['count'] < 0 or type(state['last_bar']) is not int or state['last_bar'] < 0:
                raise ValueError('invalid normal-exit checkpoint')
        # Validate every remaining field before the first assignment so a rejected payload
        # leaves the engine untouched.
        if type(payload['decision_count']) is not int or payload['decision_count'] < 0 or not (
                isinstance(payload['decision_digest'], str) and len(payload['decision_digest']) == 64):
            raise ValueError('invalid decision telemetry checkpoint')
        self.r8_symbol_controllers, self.r8_physical_units, self.r8_bay_state = new, physical, bays
        self.r8_gate_counts = Counter(payload['counts'])
        self.r8_decisions.clear()
        self.r8_decision_count, self.r8_decision_digest = payload['decision_count'], payload['decision_digest']
        self.r8_normal_exit_state = deepcopy(payload['normal_exit_state'])
        self.r8_adaptation.restore_state(adaptation_state)      # already validated: assignments only
        if prepared_profile is not None:
            self._r8_commit_profile(prepared_profile)
```

### 7.6 `r8_state.py`: gains moved into `ControlConfig` (values unchanged, never adapted)

```python
# revision8_avr/r8_state.py:73
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
```
```python
# revision8_avr/r8_state.py:112
        if min(self.evidence_pid_gains()) < 0 or min(self.market_avr_kp, self.market_avr_ki, self.market_avr_kaw) < 0 \
                or self.market_avr_integral_limit <= 0:
            raise ValueError('evidence / market AVR gains must be non-negative (integral limit positive)')
```
```python
# revision8_avr/r8_state.py:130
def _evidence_pid_gains(c):
    return (c.governor_pid_kp, c.governor_pid_ki_per_second, c.governor_pid_kd_seconds,
            c.avr_pid_kp, c.avr_pid_ki_per_second, c.avr_pid_kd_seconds,
            c.dispatch_pid_kp, c.dispatch_pid_ki_per_second, c.dispatch_pid_kd_seconds,
            c.studies_pid_kp, c.studies_pid_ki_per_second, c.studies_pid_kd_seconds)
```

### 7.7 `r8_market_governor.py`: the four PIDs take their gains from the config

```python
# revision8_avr/r8_market_governor.py:110
def _config_gains(prefix, kp, ki_per_second, kd_seconds):
    """Gains passed by SymbolController from ControlConfig; a standalone PID uses the ControlConfig
    defaults, so every gain has exactly one source."""
    from .r8_state import ControlConfig
    defaults = ControlConfig.__dataclass_fields__
    names = (f'{prefix}_pid_kp', f'{prefix}_pid_ki_per_second', f'{prefix}_pid_kd_seconds')
    return tuple(defaults[n].default if v is None else v for n, v in zip(names, (kp, ki_per_second, kd_seconds)))
```
```python
# revision8_avr/r8_market_governor.py:119
class TrendGovernorPID(BaseParallelDroopPID):
    """1. Governor PID: price only - close vs its own moving reference (no volume)."""
    def __init__(self, droop_r=0.04, enter_threshold=0.10, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('governor', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)

# revision8_avr/r8_market_governor.py:152
class AvrParallelPID(BaseParallelDroopPID):
    """2. AVR PID: participation - signed candle pressure x relative volume (no price trend)."""
    def __init__(self, droop_r=0.04, enter_threshold=0.08, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('avr', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)

# revision8_avr/r8_market_governor.py:184
class DispatchParallelPID(BaseParallelDroopPID):
    """3. Dispatch PID: Measures NIFTY Macro Grid Deviation."""
    def __init__(self, droop_r=0.05, enter_threshold=0.10, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('dispatch', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)

# revision8_avr/r8_market_governor.py:215
class StudiesParallelPID(BaseParallelDroopPID):
    """4. Studies PID: Measures Technical Projection Confidence."""
    def __init__(self, droop_r=0.05, enter_threshold=0.10, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('studies', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)
```

### 7.8 `r8_coordinated_controller.py`: PIDs and MarketAVR built from the config

```python
        self.avr = MarketAVR(config)
...
# revision8_avr/r8_coordinated_controller.py:135
        c = config
        self.pid_gov = TrendGovernorPID(kp=c.governor_pid_kp, ki_per_second=c.governor_pid_ki_per_second,
                                        kd_seconds=c.governor_pid_kd_seconds, droop_r=c.droop_r,
                                        enter_threshold=c.governor_threshold, integral_limit=limit)
        self.pid_avr = AvrParallelPID(kp=c.avr_pid_kp, ki_per_second=c.avr_pid_ki_per_second,
                                      kd_seconds=c.avr_pid_kd_seconds, droop_r=c.droop_r,
                                      enter_threshold=c.avr_threshold, integral_limit=limit)
        self.pid_dispatch = DispatchParallelPID(kp=c.dispatch_pid_kp, ki_per_second=c.dispatch_pid_ki_per_second,
                                                kd_seconds=c.dispatch_pid_kd_seconds, droop_r=c.dispatch_droop_r,
                                                enter_threshold=c.dispatch_threshold, integral_limit=limit)
        self.pid_study = StudiesParallelPID(kp=c.studies_pid_kp, ki_per_second=c.studies_pid_ki_per_second,
                                            kd_seconds=c.studies_pid_kd_seconds, droop_r=c.study_droop_r,
                                            enter_threshold=c.studies_threshold, integral_limit=limit)
```

### 7.9 `r8_market_avr.py`: MarketAVR gains from the config

```python
# revision8_avr/r8_market_avr.py:7
class MarketAVR:
    def __init__(self, config=None):
        # Gains have explicit seconds units; provisional strategy tuning owned by ControlConfig.
        if config is None:
            from .r8_state import ControlConfig
            gains = {f: ControlConfig.__dataclass_fields__[f].default
                     for f in ('market_avr_kp', 'market_avr_ki', 'market_avr_kaw', 'market_avr_integral_limit')}
        else:
            gains = {f: getattr(config, f)
                     for f in ('market_avr_kp', 'market_avr_ki', 'market_avr_kaw', 'market_avr_integral_limit')}
        self.pi = PositionalPI(PIConfig(gains['market_avr_kp'], gains['market_avr_ki'],
                                        gains['market_avr_kaw'], gains['market_avr_integral_limit']))
        self.level = 0.0

    def step(self, features, dt_seconds):
        if features.degraded or features.gap or not features.ready:
            self.pi.i_term *= exp(-dt_seconds / 120)
            self.level *= exp(-dt_seconds / 120)
            return MarketVote(self.level, 0.0)
        quality = features.liquidity * (1 - 0.5 * features.volatility)
        desired = clamp((0.6 * features.pvt + 0.4 * features.pressure) * quality)
        command, _telemetry = self.pi.step(error=desired - self.level, dt=dt_seconds,
                                          bias=desired, low=-1, high=1)
        # An explicit internal first-order participation model closes this loop.
        self.level += (1 - exp(-dt_seconds / 60)) * (command - self.level)
        return MarketVote(clamp(self.level), quality)
```

### 7.10 `run_r8_replay.py`: `--adaptation` option

```python
# revision8_avr/run_r8_replay.py:213
    p.add_argument("--adaptation", choices=["off", "shadow", "paper"], default="off",
                   help="R8 runtime adaptation (there is no live mode)")
...
# revision8_avr/run_r8_replay.py:246
            if a.adaptation != "off":
                extra.update(r8_adaptation_mode=a.adaptation,
                             r8_adaptation_log_path=str(out / "r8_adaptation.jsonl"))
```

### 7.11 Copied R5/R6 files: header and re-pointed imports

**`r8_topology.py`** (134 lines)
```python
"""R8 vendored copy of revision5/topology.py (source commit 3a764cc, sha256 40f099fb297202cc).
Copied in full so R8 carries these formulas itself; only the imports were re-pointed to R8.
```
**`r8_controller_parameters.py`** (935 lines)
```python
"""R8 vendored copy of revision5/controller_parameters.py (source commit 3a764cc, sha256 d919d4eeeed4f4e0).
Copied in full so R8 carries these formulas itself; only the imports were re-pointed to R8.
# R8-local imports:
36: from .r8_topology import (
832:         from .r8_governor_spec import BayGovernorSpec
877:         from .r8_governor_spec import PositionControlV3
```
**`r8_dynamic_parameters.py`** (959 lines)
```python
"""R8 vendored copy of revision5/dynamic_parameters.py (source commit 3a764cc, sha256 521c960ebac9f939).
Copied in full so R8 carries these formulas itself; only the imports were re-pointed to R8.
# R8-local imports:
26: from .r8_controller_parameters import (
```
**`r8_governor_spec.py`** (112 lines)
```python
"""R8 vendored copy of revision5/governor.py (source commit 3a764cc, sha256 70c53223efb50267).
Copied: BayGovernorSpec, _InnerLoopState and PositionControlV3, the classes the parameter registry
# R8-local imports:
9: from .r8_controller_parameters import GOVERNOR_COMMON_DEFAULTS as _GOV_DEFAULTS
10: from .r8_topology import BAY_IDS
```
**`r8_machine_dynamics.py`** (929 lines)
```python
"""R8 vendored copy of revision5/machine_dynamics.py (source commit 3a764cc, sha256 d69637fad496fce4).
Copied in full; R8 carries these formulas itself.
```
**`r8_machine_archetypes.py`** (471 lines)
```python
"""R8 vendored copy of revision5/machine_archetypes.py (source commit 3a764cc, sha256 0028c23617a3ec0a).
Copied in full; only the import was re-pointed to R8.
# R8-local imports:
32: from .r8_machine_dynamics import (
```
**`r8_turbine_governor.py`** (305 lines)
```python
"""R8 vendored copy of revision6_avr/r6_turbine_governor.py (source commit 3a764cc, sha256 2a6c3c89eb3a7461).
Copied in full so R8 carries these formulas itself; only the imports were re-pointed to R8.
# R8-local imports:
152:     from .r8_machine_archetypes import MACHINE_ARCHETYPES
```
**`r8_avr_core.py`** (708 lines)
```python
"""R8 vendored copy of revision6_avr/r6_avr_core_fixed.py (source commit 3a764cc, sha256 b29cde0e1001bdc2).
Copied in full so R8 carries these formulas itself; only the imports were re-pointed to R8.
```

### 7.12 New tests `test_r8_adaptation.py` (complete)

```python
"""R8 runtime adaptation: copied formulas, modes, tighten-only application, checkpoint."""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from . import Revision8Engine
from .r8_state import ControlConfig
from .r8_adaptation import R8_ADAPTATION_MODES, R8Adaptation, snapshot_image
from .r8_controller_parameters import R5ControllerParameters
from .r8_dynamic_parameters import R5EnvironmentState
from .test_r8_adaptation_guard import plant_kwargs

HERE = Path(__file__).resolve().parent
CFG = ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)
CALM_TREND = R5EnvironmentState(1.0, 0.9, 0.05, 0.05, 0.0, 0.0)
NEUTRAL = R5EnvironmentState()
STRESSED = R5EnvironmentState(2.0, 0.05, 0.15, 0.8, 0.05, 0.5)
ENVIRONMENTS = [CALM_TREND, NEUTRAL, STRESSED, R5EnvironmentState(0.4, 0.2, 0.7, 0.1, 0.15, -1.2)]
VENDORED = ('r8_topology.py', 'r8_controller_parameters.py', 'r8_dynamic_parameters.py', 'r8_governor_spec.py',
            'r8_machine_dynamics.py', 'r8_machine_archetypes.py', 'r8_turbine_governor.py', 'r8_avr_core.py')


def build(mode='off'):
    return Revision8Engine(['INFY'], r8_configs=[CFG], r8_adaptation_mode=mode, **plant_kwargs())


# --- the formulas live in R8 ---------------------------------------------------------------------

def test_r8_runtime_code_does_not_import_revision5_or_revision6():
    for path in HERE.glob('r8_*.py'):
        imports = [l for l in path.read_text().splitlines() if re.match(r'\s*(from|import)\s', l)]
        assert not [l for l in imports if 'revision5' in l or 'revision6' in l], path.name


@pytest.mark.parametrize('name', VENDORED)
def test_vendored_modules_record_their_source(name):
    head = (HERE / name).read_text().splitlines()[0]
    assert head.startswith('"""R8 vendored copy of revision') and 'sha256' in head


@pytest.mark.parametrize('env', ENVIRONMENTS)
def test_r8_formulas_reproduce_the_r5_formulas_exactly(env):
    """Proof of the copy: same environment, same registry -> identical snapshot, field by field."""
    from revision5.dynamic_parameters import R5EnvironmentState as R5Env, Revision5DynamicParameterController as R5Ctl
    from .r8_dynamic_parameters import Revision5DynamicParameterController as R8Ctl
    r5 = R5Ctl().evaluate(R5Env(**{k: getattr(env, k) for k in env.__dataclass_fields__}))
    r8 = R8Ctl().evaluate(env)
    assert snapshot_image(r8) == snapshot_image(r5) and r8.version == r5.version
    assert len(snapshot_image(r8)) > 100


# --- modes ---------------------------------------------------------------------------------------

@pytest.mark.parametrize('mode', ['live', 'LIVE', 'consume', 'on', '', None, True])
def test_only_off_shadow_paper_exist(mode):
    with pytest.raises(ValueError):
        build(mode)


def test_host_profile_consumption_is_refused_in_every_mode():
    for mode in R8_ADAPTATION_MODES:
        with pytest.raises(ValueError, match='R8 owns adaptation'):
            Revision8Engine(['INFY'], r8_configs=[CFG], r8_adaptation_mode=mode, dynamic_profile_mode='consume',
                            **plant_kwargs())


def test_shadow_and_paper_require_the_paper_apply_plant():
    kwargs = {k: v for k, v in plant_kwargs().items() if k != 'plant_control_mode'}
    for mode in ('shadow', 'paper'):
        with pytest.raises(ValueError, match='PAPER_APPLY'):
            Revision8Engine(['INFY'], r8_configs=[CFG], r8_adaptation_mode=mode, **kwargs)


def test_off_evaluates_nothing():
    e = build('off')
    assert e.r8_adaptation.evaluates is False and e.r8_adaptation.applies is False
    assert e.r8_adaptation_report()['version'] == 0 and e._runtime_profile is None


def test_r8_parameters_come_from_the_run_config():
    e = build('shadow')
    expected = R5ControllerParameters.from_config(e.config, safety_bases=dict(e.safety_contract.values))
    assert e.r8_parameters.values == expected.values


# --- tighten-only application ------------------------------------------------------------------------

@pytest.mark.parametrize('env', ENVIRONMENTS)
def test_paper_profile_never_widens_any_limit(env):
    e = build('paper')
    e.r8_adaptation.evaluate(env)
    p = e._r8_profile_values(e.r8_adaptation.snapshot)
    c, gate, t, s = e.safety_contract.values, e._base_gate_config, p['safety_and_execution'], p['strategy']
    assert t['max_gross_exposure_fraction'] <= c['max_gross_exposure_fraction']
    assert t['max_exposure_per_symbol_fraction'] <= c['max_exposure_per_symbol_fraction']
    assert t['max_concurrent_positions'] <= c['max_concurrent_positions']
    assert t['drawdown_halt_threshold'] <= c['safety_drawdown_halt_threshold']
    assert t['daily_loss_halt_threshold'] <= c['max_daily_loss_rupees']
    assert t['slippage_tolerance_percent'] <= gate.slippage_tolerance_percent
    assert t['max_market_data_age_seconds'] <= gate.max_market_data_age_seconds
    assert t['order_timeout_seconds'] <= gate.order_timeout_seconds
    merit = e.plant_control.dispatch_controller.merit_source
    assert p['dispatch_floor'] <= p['dispatch_ceiling'] <= merit.max_ceiling
    assert s['entry_confidence_threshold'] >= e.config.require('entry_confidence_threshold')
    assert s['min_risk_reward_ratio'] >= e.config.require('min_risk_reward_ratio')
    assert s['min_hold_bars'] <= e.config.require('min_hold_bars')
    assert s['max_hold_bars'] <= e.config.require('max_hold_bars')
    assert type(t['max_concurrent_positions']) is int and type(s['max_hold_bars']) is int


def test_calm_trend_max_hold_extension_is_capped():
    """R5 schedules max hold 60 -> 68 in a calm trend; R8 never lets an exit come later."""
    e = build('paper')
    e.r8_adaptation.evaluate(CALM_TREND)
    assert e.r8_adaptation.snapshot.strategy.max_hold_bars > e.config.require('max_hold_bars')
    assert e._r8_profile_values(e.r8_adaptation.snapshot)['strategy']['max_hold_bars'] == e.config.require('max_hold_bars')


def test_install_reaches_the_host_consumers():
    e = build('paper')
    e.r8_adaptation.evaluate(STRESSED)
    profile = e._r8_profile_values(e.r8_adaptation.snapshot)
    e._r8_install_profile(profile)
    assert e._runtime_profile == profile
    assert e._decision_cfg().require('entry_confidence_threshold') == profile['strategy']['entry_confidence_threshold']
    assert e.entry_decision_engine.config.max_concurrent_positions == profile['safety_and_execution']['max_concurrent_positions']
    assert e.plant_control.dispatch_controller.merit_source.runtime_ceiling_cap == profile['dispatch_ceiling']


def test_paper_never_changes_the_four_evidence_pids():
    e = build('paper')
    gains = lambda: {s: [(p.kp, p.ki_per_second, p.kd_seconds, p.droop_r, p.integral_limit, p.enter_threshold)
                         for p in (c.pid_gov, c.pid_avr, c.pid_dispatch, c.pid_study)]
                     for s, c in e.r8_symbol_controllers.items()}
    before = gains()
    e.r8_adaptation.evaluate(STRESSED)
    e._r8_install_profile(e._r8_profile_values(e.r8_adaptation.snapshot))
    assert gains() == before


# --- telemetry and checkpoint --------------------------------------------------------------------------

def test_change_log_records_old_and_new_values_with_a_rolling_digest(tmp_path):
    a = R8Adaptation('shadow', R5ControllerParameters.defaults(), log_path=tmp_path / 'a.jsonl')
    a.evaluate(NEUTRAL); first = a.record('t1', None, None, [])
    prev = a.image; a.evaluate(STRESSED); second = a.record('t2', prev, None, [])
    prev = a.image; a.evaluate(STRESSED); a.close()
    assert first['changed'] and second['changed']['stress_index']['old'] < second['changed']['stress_index']['new']
    lines = [json.loads(x) for x in (tmp_path / 'a.jsonl').read_text().splitlines()]
    assert [x['sequence'] for x in lines] == [0, 1] and a.count == 2 and a.digest != '0' * 64


def test_paper_checkpoint_round_trip_reinstalls_the_profile():
    e = build('paper')
    e.r8_adaptation.evaluate(STRESSED)
    profile = e._r8_profile_values(e.r8_adaptation.snapshot)
    e._r8_install_profile(profile); e.r8_adaptation.applied = profile
    e.r8_adaptation.record('t', None, None, [])
    snap = json.loads(json.dumps(e.r8_snapshot(), allow_nan=False))
    fresh = build('paper')
    fresh.r8_restore(snap)
    assert fresh.r8_snapshot() == snap
    assert fresh._runtime_profile == profile and fresh.r8_adaptation.snapshot.version == 1


@pytest.mark.parametrize('field, value', [('image', {'stress_index': 0.0}), ('version', -1), ('mode', 'shadow'),
                                          ('digest', 'x'), ('vol_baseline', {'INFY': float('nan')})])
def test_tampered_adaptation_checkpoint_is_rejected_and_leaves_the_engine_untouched(field, value):
    e = build('paper')
    e.r8_adaptation.evaluate(NEUTRAL)
    e.r8_adaptation.record('t', None, None, [])
    good = json.loads(json.dumps(e.r8_snapshot(), allow_nan=False))
    bad = json.loads(json.dumps(good))
    bad['adaptation_state'][field] = value
    target = build('paper')
    before = target.r8_snapshot()
    with pytest.raises(ValueError):
        target.r8_restore(bad)
    assert target.r8_snapshot() == before


def test_checkpoint_mode_must_match():
    e = build('shadow')
    snap = json.loads(json.dumps(e.r8_snapshot()))
    with pytest.raises(ValueError):
        build('paper').r8_restore(snap)


# --- audit fixes: status flags, identity, atomic restore, gains in config --------------------------

@pytest.mark.parametrize('mode, enabled, applies', [('off', False, False), ('shadow', True, False), ('paper', True, True)])
def test_status_flags_say_what_each_mode_does(mode, enabled, applies):
    e = build(mode)
    assert (e.r8_adaptation_enabled, e.r8_adaptation_applies) == (enabled, applies)


def test_r8_parameter_image_equals_the_host_image_at_startup():
    from .r8_engine import r8_parameter_fingerprint
    e = build('shadow')
    assert r8_parameter_fingerprint(e.controller_parameters) == e.r8_parameter_fingerprint
    assert any(name.startswith('avr_') for name in e.r8_parameters.values)       # AVR coefficients included


def test_startup_refuses_a_different_parameter_image(monkeypatch):
    from . import r8_engine
    real = r8_engine.r8_parameter_fingerprint
    monkeypatch.setattr(r8_engine, 'r8_parameter_fingerprint',
                        lambda p: real(p) if p.__class__.__module__.startswith('revision8') else 'host-differs')
    with pytest.raises(ValueError, match='differs from the host'):
        build('off')


@pytest.mark.parametrize('field', ['registry_identity_sha256', 'config_hash', 'r8_parameter_fingerprint'])
def test_checkpoint_identity_mismatch_is_rejected(field):
    e = build('paper')
    snap = json.loads(json.dumps(e.r8_snapshot()))
    assert snap['identity'][field] is not None
    snap['identity'][field] = '0' * 64
    target = build('paper'); before = target.r8_snapshot()
    with pytest.raises(ValueError, match='identity'):
        target.r8_restore(snap)
    assert target.r8_snapshot() == before


@pytest.mark.parametrize('tamper', [
    lambda a: a['safety_and_execution'].__setitem__('max_concurrent_positions', 99),   # wider than derived
    lambda a: a['strategy'].pop('max_hold_bars'),                                     # broken schema
    lambda a: a.__setitem__('dispatch_ceiling', 1.0)])
def test_tampered_applied_profile_is_rejected_before_anything_changes(tamper):
    e = build('paper')
    e.r8_adaptation.evaluate(STRESSED)
    profile = e._r8_profile_values(e.r8_adaptation.snapshot)
    e._r8_install_profile(profile); e.r8_adaptation.applied = profile
    snap = json.loads(json.dumps(e.r8_snapshot()))
    tamper(snap['adaptation_state']['applied'])
    target = build('paper'); before = target.r8_snapshot(); limits = target._runtime_profile
    with pytest.raises(ValueError):
        target.r8_restore(snap)
    assert target.r8_snapshot() == before and target._runtime_profile == limits


def test_evidence_and_market_avr_gains_come_from_control_config():
    from .r8_coordinated_controller import SymbolController
    c = SymbolController(CFG)
    assert (c.pid_gov.kp, c.pid_gov.ki_per_second, c.pid_gov.kd_seconds) == (0.85, 0.0005, 0.25)
    assert (c.pid_avr.kp, c.pid_dispatch.ki_per_second, c.pid_study.kp) == (0.90, 0.0004, 0.95)
    assert (c.avr.pi.cfg.kp, c.avr.pi.cfg.ki, c.avr.pi.cfg.kaw, c.avr.pi.cfg.integral_limit) == (0.30, 0.002, 0.01, 0.50)
    tuned = SymbolController(replace(CFG, governor_pid_kp=0.5, studies_pid_kd_seconds=0.1, market_avr_ki=0.001))
    assert (tuned.pid_gov.kp, tuned.pid_study.kd_seconds, tuned.avr.pi.cfg.ki) == (0.5, 0.1, 0.001)
    with pytest.raises(ValueError):
        replace(CFG, avr_pid_kp=-0.1)
```
