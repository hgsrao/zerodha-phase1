"""Correctness regressions on the diagnostic/V3 lineage; no market data needed."""
from dataclasses import replace
import math

import numpy as np
import pandas as pd
import pytest

from gates_framework import EntrySignal, Gate12StrategySignals, SafetyGateConfig
from revision2_external.bb05_bb06_parameters import default_config
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5.governor_authority import (
    GovernorAuthorityConfig, position_decision,
)
from revision5.topology import BAY_IDS
from tests_external.test_audit_remediation import signal


def entry_signal(ratio=1.5, confidence=.8):
    return EntrySignal('INFY', 1000., 999., 1001.5, confidence, 10, 10000., ratio)


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('entry', [103.7, 987.35, 1643.6])
def test_gate12_accepts_minimum_bracket_after_price_rounding(side, entry):
    sign = 1 if side == 'BUY' else -1
    stop, target = entry - sign * .07, entry + sign * 1.5 * .07
    ratio = abs(target - entry) / abs(entry - stop)
    assert Gate12StrategySignals().evaluate(entry_signal(ratio)).passed


@pytest.mark.parametrize('ratio,passed', [
    (math.nextafter(1.5, 0.), True), (1.5, True), (1.50001, True),
    (1.5 - 5e-10, True), (1.5 - 9.9e-10, True),  # inside the absolute 1e-9 tolerance
    (1.5 - 1.1e-9, False), (1.5 - 2e-9, False),  # just outside it (no relative allowance)
    (1.5 - 1e-7, False), (1.49, False), (0., False),
    (float('nan'), False), (float('inf'), False), (-float('inf'), False),
])
def test_gate12_tolerance_does_not_admit_material_shortfalls(ratio, passed):
    assert Gate12StrategySignals().evaluate(entry_signal(ratio)).passed is passed


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
@pytest.mark.parametrize('field', ['confidence', 'ratio', 'min_confidence', 'min_ratio'])
def test_gate12_rejects_each_nonfinite_input(value, field):
    signal_ = entry_signal(confidence=value) if field == 'confidence' else (
        entry_signal(ratio=value) if field == 'ratio' else entry_signal())
    config = SafetyGateConfig(**({'min_signal_confidence': value} if field == 'min_confidence' else
                                 {'min_risk_reward_ratio': value} if field == 'min_ratio' else {}))
    decision = Gate12StrategySignals(config).evaluate(signal_)
    assert not decision.passed and decision.reason == 'non-finite strategy signal or threshold'


def test_hmm_observation_reads_do_not_double_append_equal_price_bars():
    box = HMMIntelligentDiscriminationBox()
    box.calibrate('INFY', pd.DataFrame({'close': []}))
    for minute in range(5):
        box.observe_bar('INFY', 100., timestamp=f'2024-03-01 10:{minute:02d}')
        box.observe_bar('INFY', 100., timestamp=f'2024-03-01 10:{minute:02d}')
        box.evaluate(signal(), box.config, latest_close=None)
        box.latest_regime_observation('INFY')
    assert list(box._bar_history['INFY']) == [100.] * 5
    assert box.latest_regime('INFY') == 'unknown'


def test_hmm_recalibration_clears_prior_symbol_model_and_event_cache():
    box = HMMIntelligentDiscriminationBox()
    box.observe_bar('INFY', 100., timestamp='2024-03-01 10:00')
    box._cached_posterior['INFY'] = np.array([.9, .1])
    box.calibrate('INFY', pd.DataFrame({'close': [101.]}))
    box.observe_bar('INFY', 102., timestamp='2024-03-01 10:00')
    assert list(box._bar_history['INFY']) == [101., 102.]
    assert 'INFY' not in box._cached_posterior


def test_hmm_rejects_out_of_order_observations_and_returns_defensive_telemetry():
    box = HMMIntelligentDiscriminationBox()
    box.observe_bar('INFY', 100., timestamp='2024-03-01 10:00')
    with pytest.raises(ValueError, match='causal order'):
        box.observe_bar('INFY', 101., timestamp='2024-03-01 09:59')
    assert list(box._bar_history['INFY']) == [100.]
    box._last_regime_observation['INFY'] = {'posterior': [.8, .2]}
    box.latest_regime_observation('INFY')['posterior'][0] = 0.
    assert box.latest_regime_observation('INFY')['posterior'] == [.8, .2]


def _seeded_box(n=80, seed=3):
    box = HMMIntelligentDiscriminationBox()
    box.calibrate('INFY', pd.DataFrame({'close': []}))
    closes = 1000. * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, .004, n)))
    for minute, close in enumerate(closes):
        box.observe_bar('INFY', float(close), timestamp=str(pd.Timestamp('2024-03-01 09:15') + pd.Timedelta(minutes=minute)))
    return box, pd.Timestamp('2024-03-01 09:15') + pd.Timedelta(minutes=n - 1)


def _snapshot(box):
    posterior = box._cached_posterior.get('INFY')
    return (list(box._bar_history['INFY']), box._bars_since_refit.get('INFY'),
            None if posterior is None else posterior.tolist(), box.latest_regime('INFY'),
            box._last_observed_bar.get('INFY'), box.latest_regime_observation('INFY'))


def test_valid_duplicate_timestamp_is_idempotent_and_ignores_a_changed_close():
    box, last = _seeded_box()
    assert box._cached_posterior.get('INFY') is not None  # past min history: a real posterior
    before = _snapshot(box)
    assert box.observe_bar('INFY', 1234.5, timestamp=str(last)) == before[3]  # changed valid close ignored
    box.observe_bar('INFY', before[0][-1], timestamp=str(last))
    assert _snapshot(box) == before  # no history append, no refit-counter step, no filter step


@pytest.mark.parametrize('close', [float('nan'), float('inf'), -float('inf'), 0., -1.])
def test_invalid_close_on_a_duplicate_timestamp_raises_without_changing_state(close):
    box, last = _seeded_box()
    before = _snapshot(box)
    with pytest.raises(ValueError, match='finite positive close'):
        box.observe_bar('INFY', close, timestamp=str(last))
    with pytest.raises(ValueError, match='finite positive close'):
        box.observe_bar('INFY', close, timestamp=str(last + pd.Timedelta(minutes=1)))
    assert _snapshot(box) == before


def decide(mode, **overrides):
    cfg = GovernorAuthorityConfig.from_config(default_config())
    kwargs = dict(position_id='INFY', measured_r=.3, reference_r=.2, max_favorable_r=.3,
                  elapsed_bars=2, min_hold_bars=8, max_hold_bars=60, trade_target_r=3.,
                  conviction=0., drawdown=0., velocity=.2, session_bar=10000, path_noise_r=.8,
                  )
    custom_cfg = overrides.pop('cfg', cfg)
    kwargs.update(overrides)
    governor = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[BAY_IDS[0]])
    return position_decision(governor, custom_cfg, **kwargs)


@pytest.mark.parametrize('mode', ['legacy'])
@pytest.mark.parametrize('fuel', [None, True])
@pytest.mark.parametrize('elapsed', [0, 7, 8, 9])
def test_advisory_and_sustained_conviction_defer_only_before_boundary(mode, fuel, elapsed):
    result = decide(mode, elapsed_bars=elapsed, fuel_cut_confirmed=fuel)
    assert result['action'] == ('HOLD' if elapsed < 8 else 'EXIT')
    if elapsed < 8:
        assert result['conviction_exit_deferred'] is True


@pytest.mark.parametrize('mode', ['legacy'])
@pytest.mark.parametrize('fuel', [None, False, True])
@pytest.mark.parametrize('limiter', ['FSRT', 'FSRA', 'FSRS', 'FSRM'])
def test_every_independent_protection_exits_even_when_fsrn_is_lower(mode, fuel, limiter):
    cfg = GovernorAuthorityConfig.from_config(default_config())
    overrides = {'cfg': cfg, 'fuel_cut_confirmed': fuel}
    if limiter == 'FSRT':
        overrides['drawdown'] = cfg.fsrt_drawdown_span * 2. / cfg.fsrt_slope
    elif limiter == 'FSRA':
        overrides['velocity'] = (cfg.fsra_base - .1) / cfg.fsra_slope
    elif limiter == 'FSRS':
        overrides.update(cfg=replace(cfg, fsrs_floor=.1), session_bar=0)
    else:
        overrides['cfg'] = replace(cfg, fsrm_manual_limit=.1)
    result = decide(mode, **overrides)
    assert result['action'] == 'EXIT'
    assert result['reason'] == f'FSR_BELOW_EXIT:{limiter}'


@pytest.mark.parametrize('mode', ['legacy'])
@pytest.mark.parametrize('fuel', [None, True])
@pytest.mark.parametrize('overrides,reason', [
    ({'measured_r': -1.5, 'max_favorable_r': 0.}, 'GOVERNOR_HARD_STOP'),
    ({'measured_r': 3.2, 'max_favorable_r': 3.2}, 'GOVERNOR_TARGET_REACHED'),
    ({'elapsed_bars': 60, 'min_hold_bars': 60}, 'GOVERNOR_MAX_HOLD'),
])
def test_inner_safety_exits_survive_conviction_collapse(mode, fuel, overrides, reason):
    result = decide(mode, fuel_cut_confirmed=fuel, **overrides)
    assert result['action'] == 'EXIT' and result['reason'] == reason


@pytest.mark.parametrize('mode', ['legacy'])
def test_exhaust_and_invalid_input_are_not_deferred(mode):
    cfg = GovernorAuthorityConfig.from_config(default_config())
    assert decide(mode, bay_exhaust_spread=cfg.exhaust_spread_trip)['reason'] == 'EXHAUST_SPREAD_TRIP'
    assert decide(mode, velocity=None)['action'] == 'EXIT'
    assert decide(mode, conviction=float('nan'))['action'] == 'EXIT'
