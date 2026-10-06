from types import SimpleNamespace as NS
from scripts.diagnostics.r5_block1_acceptance import attach_passive_counters

def test_wrappers_call_once_preserve_identity_and_close_accounting():
    calls=[]
    id_result=(NS(approved=False,reason='RR_LOW'),object())
    gov_result=dict(action='NO_ACTION',reason='ELASTICITY')
    pre_result=dict(passed=False,gate='Gate12StrategySignals',reason='confidence',decisions=[NS(gate_name='Gate12StrategySignals',passed=False,reason='confidence')])
    def identity(name,result):
        def call(*args,**kwargs): calls.append(name); return result
        return call
    orch=NS(_native_bar_index=0,id_box=NS(evaluate=identity('id',id_result)),_governor_entry=identity('governor',gov_result),entry_decision_engine=NS(evaluate_pre_submit=identity('pre',pre_result)))
    counters=attach_passive_counters(orch)
    assert orch.id_box.evaluate(NS(direction=-1)) is id_result
    assert orch._governor_entry('TITAN','ts',1,'SELL') is gov_result
    assert orch.entry_decision_engine.evaluate_pre_submit(signal=NS(entry_price=100,profit_target_price=90)) is pre_result
    assert calls==['id','governor','pre']
    result=counters.report()
    assert all(r['accounting_closed'] for stages in result.values() for r in stages.values())
    assert result['ID']['SELL']['rejects']=={'RR_LOW':1}
    assert result['GATE12_REACHED']['SELL']['input']==1

def test_gate12_not_reached_is_not_counted_and_warmup_separate():
    orch=NS(_native_bar_index=-1,id_box=NS(evaluate=lambda *a,**k:(NS(approved=True,reason='OK'),None)),_governor_entry=lambda *a,**k:dict(action='ENTRY',reason='OK'),entry_decision_engine=NS(evaluate_pre_submit=lambda *a,**k:dict(passed=False,gate='Gate01KillSwitch',reason='kill',decisions=[])))
    c=attach_passive_counters(orch)
    orch.id_box.evaluate(NS(direction=1))
    orch.entry_decision_engine.evaluate_pre_submit(signal=NS(entry_price=100,profit_target_price=110))
    report=c.report()
    assert report['GATE12_REACHED']['BUY']['input']==0
    assert report['GATE12_REACHED']['SELL']['input']==0
    assert report['ID_WARMUP']['BUY']['passed']==1

def test_actual_factory_real_fixture_ledger_parity(tmp_path):
    import json
    from pathlib import Path
    from tests.test_r5_d01_orchestrator_run_integration import load_inputs, build_orchestrator, FIXTURES, PROTOCOL
    from scripts.diagnostics.r5_block1_acceptance import execute_block
    titan,nifty,vix=load_inputs()
    protocol=json.loads(PROTOCOL.read_text())
    params=json.loads((FIXTURES/'trial_007_params.json').read_text())
    baseline_dir=tmp_path/'baseline'; baseline_dir.mkdir()
    orch,runtime,store,observed,frame,warmup=build_orchestrator(baseline_dir)
    try: baseline=orch.run({'TITAN':frame},warmup=warmup)
    finally: store.close()
    def prepare(root,protocol,block):
        return {'TITAN':titan},{'NIFTY_50_15MIN':nifty,'INDIA_VIX_15MIN':vix},{'slice_sha256':{'fixture':'derived-real'}}
    result=execute_block(Path('.'),protocol,{'block':1,'sessions':['2024-02-13']},params,
        combined_cycle_state_path=tmp_path/'runtime.sqlite3',output_path=tmp_path/'acceptance',prepare=prepare)
    assert result['trades']==baseline['trades']
    assert len(result['trades'])==1
    assert all(r['accounting_closed'] for stage in result['passive_rejection_accounting'].values() for r in stage.values())
    assert result['runtime_safety_contract']['min_signal_confidence']>0
    assert result['native_accounting_reconciliation']['passed']
    assert all(side in stages for stages in result['passive_rejection_accounting'].values() for side in ('BUY','SELL'))
    receipt=json.loads((tmp_path/'acceptance/runtime_receipts.json').read_text())
    assert len(receipt['positions'])==1


def test_independent_native_reconciliation_detects_missing_observation():
    from scripts.diagnostics.r5_block1_acceptance import PassiveCounters,reconcile_native_accounting
    c=PassiveCounters()
    c.observe('ID','BUY',True,'OK')
    c.observe('GOVERNOR','BUY',True,'OK')
    c.observe('PRE_SUBMIT','BUY',False,'Gate01:kill')
    report=dict(id_approvals=1,id_rejections=0,gates_evaluated=1,gates_passed=0,gates_rejected=1,
                governor_authority=dict(entry_decisions={'ENTRY:OK':1}))
    result=reconcile_native_accounting(c.report(),report)
    assert result['passed']
    assert result['checks']['GATE12_REACHED_BUY']['not_reached']==1
    report['id_rejections']=1
    result=reconcile_native_accounting(c.report(),report)
    assert not result['passed']
    assert not result['checks']['ID_INPUT']['passed']
    assert not result['checks']['ID_REJECT']['passed']


def test_gate12_reached_cannot_exceed_pre_submit_candidates():
    from scripts.diagnostics.r5_block1_acceptance import PassiveCounters,reconcile_native_accounting
    c=PassiveCounters();c.observe('GATE12_REACHED','SELL',False,'LOW')
    report=dict(id_approvals=0,id_rejections=0,gates_evaluated=0,gates_passed=0,gates_rejected=0,
                governor_authority=dict(entry_decisions={}))
    result=reconcile_native_accounting(c.report(),report)
    assert not result['passed']
    assert not result['checks']['GATE12_REACHED_SELL']['passed']


def test_actual_opt_in_factory_fleet_and_compaction_accounting(tmp_path):
    import json
    from pathlib import Path
    from tests.test_r5_d01_orchestrator_run_integration import load_inputs,FIXTURES,PROTOCOL
    from scripts.diagnostics.r5_block1_acceptance import execute_block
    from revision5.fleet_loading_controller import FleetLoadingPolicy
    titan,nifty,vix=load_inputs()
    protocol=json.loads(PROTOCOL.read_text())
    params=json.loads((FIXTURES/'trial_007_params.json').read_text())
    def prepare(root,protocol,block):
        return {'TITAN':titan},{'NIFTY_50_15MIN':nifty,'INDIA_VIX_15MIN':vix},{'slice_sha256':{'fixture':'derived-real'}}
    result=execute_block(Path('.'),protocol,{'block':1,'sessions':['2024-02-13']},params,
        combined_cycle_state_path=tmp_path/'runtime.sqlite3',output_path=tmp_path/'acceptance',prepare=prepare,
        fleet_loading_policy=FleetLoadingPolicy(enabled=True),feedback_compaction=True)
    assert result['native_accounting_reconciliation']['passed']
    assert json.loads(result['plant_control']['fleet_loading']['controller'])['policy']['enabled']
    assert result['broker_fill_counts']['total']==2*len(result['trades'])
    assert result['paper_journal_statistics']['retained_memory_receipts']==0
    import sqlite3
    with sqlite3.connect(tmp_path/'acceptance/paper_replay.sqlite3') as con:
        assert con.execute('SELECT COUNT(*) FROM feedback_epochs').fetchone()==(1,)
        assert con.execute('SELECT COUNT(*) FROM applied_feedback').fetchone()[0]==len(result['trades'])
