import json
from dataclasses import asdict
from pathlib import Path
import pytest
from revision5.governor_position_policy import GovernorPositionPolicy, load_bay_position_policies
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5.topology import BAY_IDS, FLEET_TOPOLOGY, SYMBOL_TO_BAY

ROOT = Path(__file__).resolve().parents[1]


def test_exact_user_map_preserves_previous_policy_numbers():
    payload = json.loads((ROOT/'experiments/governor_position_policy.json').read_text())
    policies = load_bay_position_policies(payload)
    assert set(policies) == set(BAY_IDS)
    for policy in policies.values():
        assert asdict(policy) == asdict(GovernorPositionPolicy())
    assert load_bay_position_policies(asdict(GovernorPositionPolicy())) == policies


@pytest.mark.parametrize('bay', BAY_IDS)
def test_all_bays_policy_and_mapping(bay):
    policies = load_bay_position_policies(json.loads((ROOT/'experiments/governor_position_policy.json').read_text()))
    g = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[bay])
    gains = (g.runtime_kp,g.runtime_ki,g.runtime_kd,g.runtime_droop_r)
    g.position_policy = policies[bay]
    assert all(SYMBOL_TO_BAY[s] == bay for s in FLEET_TOPOLOGY[bay])
    args=dict(measured_r=.1, reference_r=.8, elapsed_bars=1, min_hold_bars=0,
              max_hold_bars=30, hard_stop_r=-1., trade_target_r=3., position_id='x')
    out = g.evaluate_position_control(max_favorable_r=.29,**args)
    assert out['protected_r_floor'] == -1.
    out = g.evaluate_position_control(max_favorable_r=.4,**args)
    assert out['trailing_active'] and out['effective_gap_r'] < .45
    assert gains == (g.runtime_kp,g.runtime_ki,g.runtime_kd,g.runtime_droop_r)


def test_bad_schema_rejected():
    with pytest.raises(ValueError):
        load_bay_position_policies({'CSTG1_BFSI': {'pid_clip_max':2}})
    with pytest.raises(ValueError):
        load_bay_position_policies({'pid_clip_max':2, 'pid_u_max':2})
    with pytest.raises(TypeError):
        load_bay_position_policies({'wrong_field':2})


def test_comparison_all_bays_mfe_and_parity(tmp_path):
    from scripts.r5_compare_governor_trace import compare
    metadata = dict(protocol_id='fixture',params={},runs=[])
    for bay in BAY_IDS:
        metadata['runs'].append(dict(block=1,bay=bay,authority='full',sessions=['fixture'],symbols=[FLEET_TOPOLOGY[bay][0]],slice_sha256='same'))
        for mode in ('before','after'):
            directory=tmp_path/mode/'block1'/bay/'full'
            directory.mkdir(parents=True)
            (directory/'summary.json').write_text(json.dumps(dict(metrics={'completed_trades':1,'net_pnl':0},bars_held={'mean':2},exit_reason_detail={'stop':1})))
            (directory/'trades.json').write_text(json.dumps([{'mfe_r':.4}]))
            (directory/'inner.jsonl').write_text('')
            (directory/'events.jsonl').write_text('')
    for mode in ('before','after'):
        (tmp_path/mode/'overview.json').write_text(json.dumps(metadata))
    result=compare(tmp_path/'before',tmp_path/'after',require_parity=True)
    assert set(result)==set(BAY_IDS)
    assert all(r['after']['mfe_r']['mean']==.4 for r in result.values())
    (tmp_path/'after'/'block1'/BAY_IDS[0]/'full'/'trades.json').write_text('[{"mfe_r": 0.5}]')
    with pytest.raises(ValueError,match='parity failed'):
        compare(tmp_path/'before',tmp_path/'after',require_parity=True)


def test_distinct_bay_override_is_not_broadcast():
    payload=json.loads((ROOT/'experiments/governor_position_policy.json').read_text())
    payload['BPSTG_HEALTHCARE']['pid_tighten_gain']=.07
    policies=load_bay_position_policies(payload)
    assert policies['BPSTG_HEALTHCARE'].pid_alpha_r==.07
    assert policies['CSTG1_BFSI'].pid_alpha_r==.10
