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
