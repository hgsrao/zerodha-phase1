"""R7 governor adaptation from the native PAPER_APPLY plant's per-bar R5 snapshot."""
from dataclasses import asdict, replace
import json
import pandas as pd
import pytest

from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.dynamic_parameters import R5EnvironmentState, Revision5DynamicParameterController
from revision2_external.grid_context import SealedGridContextProvider
from tests.test_r5_parameter_authority import registry_with
from . import Revision7Engine
from .r7_state import ControlConfig
from .r7_r5_dynamic_adapter import (R7_ADAPTED_GOVERNOR_PARAMETERS, R7_GOVERNOR_PARAMETERS,
                                    adapted_governor_config, governor_scales)
from .conftest import make_bar

CFG = ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)
STRESSED = R5EnvironmentState(normalized_vol=2.5, trend_probability=0.1, chop_probability=0.2,
                              turbulent_probability=0.7, fleet_drawdown_fraction=0.03, control_error_delta=0.5)


def plant_kwargs():
    # A rising Nifty from before the first bar, so the grid direction is available and
    # bullish and the governor runs enabled (its PID gains act).
    times = pd.date_range("2026-08-09 00:00", "2026-08-13 12:00", freq="15min", tz="Asia/Kolkata")
    close = 21000.0 + pd.Series(range(len(times))) * 5.0
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": close}),
                                         pd.DataFrame({"timestamp": times, "close": 15.0}),
                                         minimum_aligned_bars=3)
    return dict(registry=registry_with(), real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                plant_control_mode="PAPER_APPLY", grid_context_provider=provider)


def frames(n=120):
    return {'INFY': pd.DataFrame([{k: v for k, v in asdict(make_bar(i)).items() if k != 'index'} for i in range(n)])}


def run(mode, n=120):
    e = Revision7Engine(['INFY'], r7_configs=[CFG], r7_adaptation_mode=mode, **plant_kwargs())
    e.run(frames(n), warmup=60)
    return e


def specs():
    return Revision5DynamicParameterController().parameters.governor_specs


def snapshot(env=R5EnvironmentState()):
    return Revision5DynamicParameterController().evaluate(env)


def test_scales_are_r5_ratios_and_limits_tighten_only():
    s = snapshot(STRESSED)
    scales = governor_scales(s, specs(), CFG.bay_id)
    profile, spec = s.governor_by_bay[CFG.bay_id], specs()[CFG.bay_id]
    assert scales['kp'] == pytest.approx(profile.kp / spec.kp)
    assert scales['ki_per_second'] == pytest.approx(profile.ki / spec.ki)
    assert scales['kd_seconds'] == pytest.approx(profile.kd / spec.kd)
    assert 0 < scales['integral_limit'] <= 1 and 0 < scales['command_slew_per_second'] <= 1
    adapted = adapted_governor_config(CFG, scales)
    assert adapted.integral_limit <= CFG.integral_limit
    assert adapted.command_slew_per_second <= CFG.command_slew_per_second
    for name in set(R7_GOVERNOR_PARAMETERS) - set(R7_ADAPTED_GOVERNOR_PARAMETERS):
        assert getattr(adapted, name) == getattr(CFG, name)
    assert (adapted.symbol, adapted.bay_id, adapted.budget_fraction) == (CFG.symbol, CFG.bay_id, CFG.budget_fraction)


def test_invalid_snapshot_scale_is_rejected():
    s = snapshot()
    broken = replace(s, risk_derate_factor=0.0)
    with pytest.raises(ValueError):
        governor_scales(broken, specs(), CFG.bay_id)


def test_adaptation_requires_the_paper_apply_plant():
    with pytest.raises(ValueError, match='PAPER_APPLY'):
        Revision7Engine(['INFY'], r7_configs=[CFG], r7_adaptation_mode='paper')
    with pytest.raises(ValueError):
        Revision7Engine(['INFY'], r7_configs=[CFG], r7_adaptation_mode='consume', **plant_kwargs())


def test_native_profile_consumption_stays_refused_with_governor_adaptation():
    with pytest.raises(ValueError, match='adaptation'):
        Revision7Engine(['INFY'], r7_configs=[CFG], r7_adaptation_mode='paper',
                        dynamic_profile_mode='consume', **plant_kwargs())


def test_paper_mode_consumes_a_fresh_snapshot_every_bar_in_the_native_loop():
    e = run('paper')
    bars = e.r7_gate_counts['BARS_PROCESSED']
    warm = e.r7_gate_counts['ADAPTATION_WARMUP']
    assert bars == 119 and warm == 60 and e.r7_gate_counts['ADAPTATION_APPLIED'] == bars - warm
    assert e.r7_gate_counts['ADAPTATION_FAIL_CLOSED'] == 0
    live = [d for d in e.r7_decisions if d['adaptation']['status'] != 'WARMUP']
    assert all(d['adaptation']['status'] == 'APPLIED' for d in live)
    versions = [d['adaptation']['snapshot_version'] for d in live]
    assert len(set(versions)) == len(versions)                        # one snapshot per bar, never reused
    last = e.r7_decisions[-1]['adaptation']
    assert last['active_parameters'] == last['proposed_parameters']
    assert last['parameter_hash'] != last['base_parameter_hash']
    gov = e.r7_symbol_controllers['INFY'].governor.config
    assert gov != CFG and gov.kp == pytest.approx(last['active_parameters']['kp'])
    assert e.r7_configs['INFY'] == CFG                                # commissioned config untouched


def test_paper_changes_governor_output_and_shadow_only_records():
    paper, shadow, off = run('paper'), run('shadow'), run('off')
    assert shadow.r7_gate_counts['ADAPTATION_SHADOW'] == 59 and shadow.r7_gate_counts['ADAPTATION_FAIL_CLOSED'] == 0
    s_last = shadow.r7_decisions[-1]['adaptation']
    assert s_last['active_parameters'] != s_last['proposed_parameters']
    assert s_last['parameter_hash'] == s_last['base_parameter_hash']
    eligible = [i for i, d in enumerate(off.r7_decisions) if d['eligible']]
    assert eligible, 'test needs eligible bars for the PID gains to act'
    gov = lambda e: [(d['governor']['p'], d['governor']['i'], d['governor']['requested'], d['governor']['applied']) for d in e.r7_decisions]
    # Shadow is decision-identical to off; paper's governor terms differ.
    assert gov(shadow) == gov(off)
    assert gov(paper) != gov(off)
    assert 'adaptation' not in off.r7_decisions[-1]


def test_stale_snapshot_fails_closed_and_blocks_increases_only():
    e = run('paper', n=80)
    ctrl = e.r7_symbol_controllers['INFY']
    before = e.r7_gate_counts['ADAPTATION_FAIL_CLOSED']
    nxt = asdict(make_bar(80))
    e._r7_step('INFY', 80, nxt, 0.9, True)                          # no plant step: snapshot reused
    d = e.r7_decisions[-1]
    assert e.r7_gate_counts['ADAPTATION_FAIL_CLOSED'] == before + 1
    assert d['adaptation']['status'] == 'FAIL_CLOSED' and d['adaptation']['reason'] == 'R5_SNAPSHOT_STALE'
    assert d['allocation']['increase_capacity_pu'] == 0.0
    assert d['adaptation']['parameter_hash'] == d['adaptation']['base_parameter_hash']
    assert ctrl.governor.config == CFG


def test_adaptive_checkpoint_round_trip_and_identity():
    e = run('paper', n=80)
    snap = json.loads(json.dumps(e.r7_snapshot()))
    assert snap['adaptation'] == 'paper' and snap['adaptation_state']['last_snapshot_version'] is not None
    e.r7_restore(snap)
    assert e._r7_adaptation_version == snap['adaptation_state']['last_snapshot_version']
    for bad in ({**snap, 'adaptation': 'off'}, {k: v for k, v in snap.items() if k != 'adaptation_state'},
                {**snap, 'adaptation_state': {'last_snapshot_version': -1}}):
        with pytest.raises(ValueError):
            e.r7_restore(bad)
    off = run('off', n=80)
    with pytest.raises(ValueError):
        off.r7_restore(snap)


def test_invalid_adapted_config_fails_closed_for_that_symbol(monkeypatch):
    import revision7_avr.r7_engine as eng
    e = run('paper', n=80)
    def bad(base, scales):
        raise ValueError('invalid tuning')
    monkeypatch.setattr(eng, 'adapted_governor_config', bad)
    e.real_plant_dcs.dynamic_snapshot = replace(e.real_plant_dcs.dynamic_snapshot, version=10**6)
    e._r7_step('INFY', 80, asdict(make_bar(80)), 0.9, True)
    d = e.r7_decisions[-1]
    assert d['adaptation']['status'] == 'FAIL_CLOSED' and d['adaptation']['reason'].startswith('R7_CONFIG_INVALID')
    assert d['allocation']['increase_capacity_pu'] == 0.0
    assert e.r7_symbol_controllers['INFY'].governor.config == CFG
