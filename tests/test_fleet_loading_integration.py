"""Optional fleet controller wired through real plant chain/real paper replay."""
from dataclasses import replace
import json
import pandas as pd
import pytest
from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator, FIXTURES
from revision5.fleet_loading_controller import FleetLoadingPolicy, FleetLoadingController
from revision5.plant_control import BayStatus, PlantControlError
from revision5.topology import BAY_IDS


def build(path, policy):
    path.mkdir(parents=True,exist_ok=True)
    engine, runtime, store, _, bars, warmup = build_orchestrator(path)
    engine = type(engine)(['TITAN'], registry=engine.registry,
        calibration_overrides=json.loads((FIXTURES/'trial_007_params.json').read_text()),
        starting_equity=engine.starting_equity, grid_context_provider=engine.grid_context_provider,
        real_plant_dcs=engine.real_plant_dcs, plant_control_mode='PAPER_APPLY',
        closed_loop_mode='active_paper',telemetry_mode='compact',governor_authority='full',
        combined_cycle_runtime=runtime,fleet_loading_policy=policy)
    return engine,store,bars,warmup


def sample(chain, ts, exposure, limit=1, trip=False, unavailable=None):
    statuses={bay: BayStatus(available=bay!=unavailable,tripped=False) for bay in BAY_IDS}
    return chain.evaluate(pd.Timestamp(ts),statuses,gross_exposure_fraction=exposure,
        gross_exposure_limit_fraction=limit,plant_protection_tripped=trip)


def test_disabled_policy_preserves_actual_run_ledger(tmp_path):
    base,_,store,_,bars,warmup=build_orchestrator(tmp_path)
    baseline=base.run({'TITAN':bars},warmup=warmup)
    disabled,second_store,bars,warmup=build(tmp_path/'disabled',FleetLoadingPolicy())
    actual=disabled.run({'TITAN':bars},warmup=warmup)
    assert actual['trades']==baseline['trades']
    assert all(s.fleet_loading is None for s in disabled.plant_control_snapshots)
    store.close();second_store.close()


def test_opt_in_actual_orchestrator_clock_and_feedback(tmp_path):
    engine,store,bars,warmup=build(tmp_path,FleetLoadingPolicy(enabled=True))
    report=engine.run({'TITAN':bars},warmup=warmup)
    assert report['plant_control']['fleet_loading']['timestamp'] is not None
    state=json.loads(engine.plant_control.fleet_loading.export_state())
    assert state['updates']>1
    assert any(s.fleet_loading is not None for s in engine.plant_control_snapshots)
    for s in engine.plant_control_snapshots:
        assert 0<=s.dispatch.plant_demand_reference_pu<=1
        assert s.dispatch.allocated_pu<=s.dispatch.plant_demand_reference_pu+1e-12
        assert max(s.dispatch.as_dict().values())<=engine.plant_control.dispatch_controller.merit_source.max_ceiling
    store.close()


def test_real_grid_chain_overexposure_clock_protection_and_restore(tmp_path):
    engine,store,bars,_=build(tmp_path,FleetLoadingPolicy(enabled=True,kp=.1,ki=.001,kd=.1))
    chain=engine.plant_control
    ts=bars.timestamp.iloc[100]
    chain.ecs._previous_demand=1
    first=sample(chain,ts,1.1,unavailable=BAY_IDS[0])
    assert first.fleet_loading.error_pu<0
    assert first.ecs.plant_demand_reference_pu<1
    assert first.dispatch.as_dict()[BAY_IDS[0]]==0
    state=chain.fleet_loading.export_state()
    assert sample(chain,ts,1.1,unavailable=BAY_IDS[0]) is first
    assert chain.fleet_loading.export_state()==state
    second=sample(chain,ts+pd.Timedelta(seconds=60),1.05)
    assert first.fleet_loading.dt_seconds==1.0
    assert second.fleet_loading.dt_seconds==60.0
    assert second.fleet_loading.integral_pu_seconds==pytest.approx(-.1- .05*60)
    saved=chain.export_fleet_loading_state()
    restored_engine,restored_store,_,_=build(tmp_path/'restore',chain.fleet_loading.policy)
    restored=restored_engine.plant_control
    restored.ecs._previous_demand=chain.ecs._previous_demand
    restored.restore_offline_fleet_loading_state(saved,next_timestamp=ts+pd.Timedelta(seconds=120),
        reconstructed_last_exposure_pu=1.05)
    assert restored.fleet_loading.export_state()==chain.fleet_loading.export_state()
    a=sample(chain,ts+pd.Timedelta(seconds=120),1.02)
    b=sample(restored,ts+pd.Timedelta(seconds=120),1.02)
    assert a.fleet_loading==b.fleet_loading
    before=a.fleet_loading.integral_pu_seconds
    trip=sample(chain,ts+pd.Timedelta(seconds=120),1.02,trip=True)
    assert trip.dispatch.allocated_pu==0 and trip.fleet_loading.integral_pu_seconds==before
    assert sample(chain,ts+pd.Timedelta(seconds=120),1.02).dispatch.allocated_pu==0
    with pytest.raises(PlantControlError,match='backwards'):
        sample(chain,ts,1)
    with pytest.raises(PlantControlError,match='aware'):
        sample(chain,'2024-02-13 10:00',1)
    with pytest.raises(PlantControlError,match='exposure'):
        restored.restore_offline_fleet_loading_state(saved,next_timestamp=ts+pd.Timedelta(seconds=120),
            reconstructed_last_exposure_pu=0)
    store.close();restored_store.close()


def test_journal_policy_identity_and_exact_resume_controller_state(tmp_path):
    from revision5.paper_state_journal import PaperStateJournal,checkpoint_state
    policy=FleetLoadingPolicy(enabled=True,kp=.1,ki=.001)
    baseline,baseline_store,bars,warmup=build(tmp_path/'baseline',policy)
    baseline_report=baseline.run({'TITAN':bars},warmup=warmup)
    initial,initial_store,bars,warmup=build(tmp_path/'interrupted',policy)
    journal_path=tmp_path/'replay.sqlite'
    def interrupt(journal,engine):
        if journal.cursor==2:
            raise InterruptedError('after durable portfolio checkpoint')
    journal=PaperStateJournal(journal_path,after_commit=interrupt)
    initial.paper_journal=journal
    with pytest.raises(InterruptedError):
        initial.run({'TITAN':bars},warmup=warmup)
    identity=json.loads(journal.con.execute('SELECT payload FROM identity').fetchone()[0])
    assert identity['fleet_loading_policy']==policy.to_dict()
    checkpoint=json.loads(journal.con.execute('SELECT payload FROM checkpoints ORDER BY seq DESC LIMIT 1').fetchone()[0])
    assert checkpoint['fleet_loading']==initial.plant_control.export_fleet_loading_state()
    journal.close();initial_store.close()
    changed,changed_store,bars,warmup=build(tmp_path/'changed',replace(policy,kp=.2))
    with pytest.raises(RuntimeError,match='identity mismatch'):
        changed.resume_verified_paper_replay({'TITAN':bars},journal_path=journal_path,warmup=warmup)
    changed_store.close()
    resumed,resumed_store,bars,warmup=build(tmp_path/'resumed',policy)
    report=resumed.resume_verified_paper_replay({'TITAN':bars},journal_path=journal_path,warmup=warmup)
    assert report['trades']==baseline_report['trades']
    assert resumed.plant_control.export_fleet_loading_state()==baseline.plant_control.export_fleet_loading_state()
    assert report['paper_recovery']['reconciled_checkpoints'] if 'reconciled_checkpoints' in report['paper_recovery'] else report['paper_recovery']['reconciled']
    baseline_store.close();resumed_store.close()


def test_absent_controller_preserves_report_checkpoint_shape(tmp_path):
    from revision5.paper_state_journal import checkpoint_state
    engine,_,store,_,bars,warmup=build_orchestrator(tmp_path)
    assert 'fleet_loading' not in checkpoint_state(engine)
    report=engine.run({'TITAN':bars},warmup=warmup)
    assert 'fleet_loading' not in report['plant_control']
    store.close()


def test_morning_checkpoint_retains_fleet_but_defers_unverified_measurement(tmp_path):
    from tests.test_r5_morning_startup import carry,boot,recovered_broker
    from revision5.state_recovery import StateRecoveryJournal
    first,_,store,truth=carry(tmp_path)
    policy=FleetLoadingPolicy(enabled=True)
    first.plant_control.fleet_loading=FleetLoadingController(policy)
    sample(first.plant_control,pd.Timestamp('2023-12-05 15:20',tz='Asia/Kolkata'),.001)
    first._checkpoint_morning_recovery(account_id='fixture-account',timestamp='2023-12-05 15:25')
    expected=first.plant_control.export_fleet_loading_state()
    assert StateRecoveryJournal(store.connection).load_boot()['fleet_loading']==expected
    store.close()
    second,_,store=boot(tmp_path)
    second.plant_control.fleet_loading=FleetLoadingController(policy)
    original=second.plant_control.fleet_loading.export_state()
    receipt=second._reconcile_morning_startup(account_id='fixture-account',broker=recovered_broker(truth))
    assert receipt['fleet_loading_restored'] is False
    assert second._pending_fleet_loading_recovery_state==expected
    assert second.plant_control.fleet_loading.export_state()==original
    assert receipt['admissions_allowed'] is False and second._execution_halted
    store.close()


def test_offline_restore_refuses_broker_claim_unsampled_and_wrong_next_clock(tmp_path):
    engine,store,bars,_=build(tmp_path,FleetLoadingPolicy(enabled=True))
    chain=engine.plant_control
    with pytest.raises(PlantControlError,match='broker fleet hydration is unsupported'):
        chain.restore_fleet_loading_state({},next_timestamp=bars.timestamp.iloc[100],
            validated_actual_exposure_pu=0)
    with pytest.raises(PlantControlError,match='sampled state'):
        chain.restore_offline_fleet_loading_state(chain.export_fleet_loading_state(),
            next_timestamp=bars.timestamp.iloc[100],reconstructed_last_exposure_pu=0)
    ts=bars.timestamp.iloc[100]
    sample(chain,ts,0)
    saved=chain.export_fleet_loading_state()
    next_ts=ts+pd.Timedelta(seconds=60)
    chain.restore_offline_fleet_loading_state(saved,next_timestamp=next_ts,
        reconstructed_last_exposure_pu=0)
    before=chain.fleet_loading.export_state()
    with pytest.raises(PlantControlError,match='declared next timestamp'):
        sample(chain,next_ts+pd.Timedelta(seconds=60),0)
    assert chain.fleet_loading.export_state()==before
    sample(chain,next_ts,0)
    assert chain._fleet_expected_next_timestamp is None
    store.close()
