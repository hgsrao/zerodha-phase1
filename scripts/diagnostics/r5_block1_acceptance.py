"""Experimental native R5 execution factory with instance-local passive accounting.

Does not alter the sealed worker or its identity/holdout guard. The experimental
runner owns the manifest/Stage-B selection and calls this executor explicitly.
"""
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
import json

class PassiveCounters:
    def __init__(self):
        self.rows = {}
    def observe(self, stage, side, passed, reason):
        row=self.rows.setdefault((stage,side),dict(input=0,passed=0,rejects=Counter()))
        row['input']+=1
        if passed: row['passed']+=1
        else: row['rejects'][str(reason)]+=1
    def report(self):
        result={}
        rows=dict(self.rows)
        for stage in ('ID','ID_WARMUP','GOVERNOR','PRE_SUBMIT','GATE12_REACHED'):
            for side in ('BUY','SELL'):
                rows.setdefault((stage,side),dict(input=0,passed=0,rejects=Counter()))
        for (stage,side),row in sorted(rows.items()):
            rejects=dict(sorted(row['rejects'].items()))
            result.setdefault(stage,{})[side]=dict(input=row['input'],passed=row['passed'],rejects=rejects,
                accounting_closed=row['input']==row['passed']+sum(rejects.values()))
        return result

def attach_passive_counters(orch):
    """Each instance wrapper calls its captured bound original exactly once."""
    counters=PassiveCounters()
    original_id=orch.id_box.evaluate
    def evaluate_id(signal,*args,**kwargs):
        result=original_id(signal,*args,**kwargs)
        decision=result[0]
        side='BUY' if signal.direction>0 else 'SELL' if signal.direction<0 else 'NEUTRAL'
        stage='ID' if orch._native_bar_index>=0 else 'ID_WARMUP'
        counters.observe(stage,side,bool(decision.approved),decision.reason)
        return result
    orch.id_box.evaluate=evaluate_id
    original_governor=orch._governor_entry
    def evaluate_governor(*args,**kwargs):
        result=original_governor(*args,**kwargs)
        side=kwargs.get('side',args[3] if len(args)>3 else 'UNKNOWN')
        counters.observe('GOVERNOR',side,result['action']=='ENTRY',result.get('reason','UNSPECIFIED'))
        return result
    orch._governor_entry=evaluate_governor
    original_pre=orch.entry_decision_engine.evaluate_pre_submit
    def evaluate_pre(*args,**kwargs):
        result=original_pre(*args,**kwargs)
        signal=kwargs.get('signal')
        side='BUY' if signal.profit_target_price>signal.entry_price else 'SELL'
        counters.observe('PRE_SUBMIT',side,bool(result['passed']),f"{result.get('gate','UNKNOWN')}:{result.get('reason','UNSPECIFIED')}")
        for decision in result.get('decisions',[]):
            if decision.gate_name=='Gate12StrategySignals':
                counters.observe('GATE12_REACHED',side,bool(decision.passed),decision.reason)
                break
        return result
    orch.entry_decision_engine.evaluate_pre_submit=evaluate_pre
    return counters

def reconcile_native_accounting(accounting, report):
    """Independent native funnel counters must agree with passive observations."""
    def total(stage,name):
        rows=accounting.get(stage,{})
        return sum(sum(row['rejects'].values()) if name=='rejected' else row[name] for row in rows.values())
    comparisons={
        'ID_INPUT':(total('ID','input'),report['id_approvals']+report['id_rejections']),
        'ID_PASS':(total('ID','passed'),report['id_approvals']),
        'ID_REJECT':(total('ID','rejected'),report['id_rejections']),
        'PRE_SUBMIT_INPUT':(total('PRE_SUBMIT','input'),report['gates_evaluated']),
        'PRE_SUBMIT_PASS':(total('PRE_SUBMIT','passed'),report['gates_passed']),
        'PRE_SUBMIT_REJECT':(total('PRE_SUBMIT','rejected'),report['gates_rejected']),
        'GOVERNOR_INPUT':(total('GOVERNOR','input'),sum(report['governor_authority']['entry_decisions'].values())),
    }
    checks={key:dict(passive=values[0],native=values[1],passed=values[0]==values[1]) for key,values in comparisons.items()}
    for side in ('BUY','SELL'):
        reached=accounting['GATE12_REACHED'][side]['input']
        candidates=accounting['PRE_SUBMIT'][side]['input']
        checks['GATE12_REACHED_'+side]=dict(reached=reached,pre_submit=candidates,
                                                not_reached=candidates-reached,passed=0<=reached<=candidates)
    checks['ALL_STAGE_ACCOUNTING']=dict(passed=all(row['accounting_closed'] for rows in accounting.values() for row in rows.values()))
    passed=all(row['passed'] for row in checks.values())
    return dict(passed=passed,checks=checks,
                warmup='ID_WARMUP is reported separately and excluded from native scored ID totals',
                gate12='Not reached counts exclude candidates rejected before Gate12; zero is explicit',
                governor='NO_ACTION observations are actual vetoes only under full governor authority')

def _json(path,value):
    Path(path).write_text(json.dumps(value,sort_keys=True,indent=2,default=lambda x:asdict(x) if is_dataclass(x) else str(x),allow_nan=False)+'\n')

def execute_block(root,protocol,block,params,*,combined_cycle_state_path=None,
                  output_path=None,fleet_loading_policy=None,feedback_compaction=False,
                  prepare=None, progress_callback=None):
    """Worker-compatible result plus full native report/accounting/receipts.

    Optional policy is a FleetLoadingPolicy instance supplied by the integrated
    C02 runtime. Unsupported source rejects the option instead of ignoring it.
    """
    from scripts import run_r5_step5_candidate as worker
    from canonical_parameter_registry import CanonicalParameterRegistry
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    from revision5.paper_state_journal import PaperStateJournal
    import inspect
    if int(block['block'])!=1:
        raise ValueError('C05 acceptance factory is Block 1 only')
    if output_path is None:
        raise ValueError('Explicit isolated acceptance output path required')
    output=Path(output_path); output.mkdir(parents=True,exist_ok=True)
    frames,feeds,audit=(prepare or worker.prepare_block)(Path(root),protocol,block)
    registry=CanonicalParameterRegistry(); registry.verify_frozen_identity()
    errors=registry.validate_calibration_payload(params,engine='EXTERNAL')
    if errors: raise ValueError('; '.join(errors))
    provider=SealedGridContextProvider(feeds['NIFTY_50_15MIN'],feeds['INDIA_VIX_15MIN'])
    equity=float(protocol['block_execution_contract']['starting_equity_per_block'])
    plant=CentralPlantMasterDCS(total_capital=equity,db_path=':memory:')
    runtime=None; journal=None
    try:
        if combined_cycle_state_path is not None:
            runtime=worker.build_paper_combined_cycle_runtime(Path(combined_cycle_state_path))
        extra={}
        if fleet_loading_policy is not None:
            from revision5.fleet_loading_controller import FleetLoadingPolicy
            if not isinstance(fleet_loading_policy,FleetLoadingPolicy): raise TypeError('FleetLoadingPolicy required')
            if 'fleet_loading_policy' not in inspect.signature(Revision2ExternalEngineOrchestrator).parameters:
                raise RuntimeError('Fleet policy not integrated in loaded orchestrator')
            extra['fleet_loading_policy']=fleet_loading_policy
        if feedback_compaction:
            if 'feedback_compaction' not in inspect.signature(PaperStateJournal).parameters:
                raise RuntimeError('Feedback compaction not integrated in loaded journal')
            def progress(journal, engine):
                if progress_callback is not None:
                    progress_callback(journal,engine)
                elif journal.cursor % 250 == 0:
                    print(f'C05 PAPER CHECKPOINT cursor={journal.cursor} timestamp={engine._native_timestamp}',flush=True)
            journal=PaperStateJournal(output/'paper_replay.sqlite3',feedback_compaction=True,after_commit=progress)
            extra['paper_journal']=journal
        orch=Revision2ExternalEngineOrchestrator(sorted(frames),registry,calibration_overrides=params,
            starting_equity=equity,grid_context_provider=provider,real_plant_dcs=plant,
            plant_control_mode='PAPER_APPLY',closed_loop_mode='active_paper',telemetry_mode='compact',
            governor_authority=protocol['engine'].get('governor_authority','advisory'),
            combined_cycle_runtime=runtime,**extra)
        counters=attach_passive_counters(orch)
        safety=dict(orch.safety_contract.values)
        report=orch.run(frames,warmup=int(protocol['block_execution_contract']['stock_warmup_bars_per_symbol']))
        accounting=counters.report()
        reconciliation=reconcile_native_accounting(accounting,report)
        _json(output/'native_accounting_reconciliation.json',reconciliation)
        if not reconciliation['passed']:
            _json(output/'native_report.json',report)
            _json(output/'passive_rejection_accounting.json',accounting)
            raise RuntimeError('Acceptance accounting does not reconcile with independent native funnel counters')
        receipts=None
        if runtime is not None:
            receipts=dict(positions=[json.loads(r[0]) for r in runtime.store.connection.execute('SELECT payload FROM positions')],
                          conversions=runtime.store.requests(),handoff=asdict(runtime.handoff.config),
                          engine_b=asdict(runtime.engine_b.policy),runtime=asdict(runtime.config))
        _json(output/'native_report.json',report)
        _json(output/'passive_rejection_accounting.json',accounting)
        _json(output/'runtime_safety_contract.json',safety)
        _json(output/'runtime_receipts.json',receipts)
        broker_fill_counts=dict(total=len(orch.broker.fills),
            by_side={side:sum(fill['side']==side for fill in orch.broker.fills) for side in ('BUY','SELL')},
            native_entry_fills=report['fills'],classification='BROKER_BOTH_ENTRY_AND_EXIT_LEGS')
        journal_stats=None if journal is None else dict(
            checkpoints=journal.con.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0],
            feedback_epochs=journal.con.execute('SELECT COUNT(*) FROM feedback_epochs').fetchone()[0],
            current_epoch_applied=journal.con.execute('SELECT COUNT(*) FROM applied_feedback WHERE epoch=?',(journal.feedback_epoch,)).fetchone()[0],
            retained_memory_receipts=len(orch._close_feedback_receipts),
            policy='OPT_IN_VERIFIED_CLOSED_CURRENT_EPOCH_COMPACTION')
        _json(output/'paper_journal_statistics.json',journal_stats)
        _json(output/'broker_fill_counts.json',broker_fill_counts)
        metrics=worker.metrics(report)
        result=dict(audit=audit,metrics=metrics,plant_control=report['plant_control'],
            governor_authority=report['governor_authority'],micom=report['micom'],trades=report.get('trades',[]),
            passive_rejection_accounting=accounting,native_accounting_reconciliation=reconciliation,
            runtime_safety_contract=safety,
            broker_fill_counts=broker_fill_counts,paper_journal_statistics=journal_stats,
            native_report_path=str(output/'native_report.json'),runtime_receipts_path=str(output/'runtime_receipts.json'))
        if runtime is not None:
            result['combined_cycle_runtime']=dict(environment='paper',classification='EXPERIMENTAL_COMBINED_CYCLE',
                live_admissions=False,handoff=receipts['handoff'],engine_b=receipts['engine_b'],runtime=receipts['runtime'])
        canonical=dict(block=int(block['block']),sessions=block['sessions'],params=params,
            metrics=metrics,plant_control=report['plant_control'],governor_authority=report['governor_authority'],
            micom=report['micom'],trades=report.get('trades',[]),slice_sha256=audit['slice_sha256'])
        if runtime is not None: canonical['combined_cycle_runtime']=result['combined_cycle_runtime']
        result['block_fingerprint']=worker.canonical_json_hash(canonical)
        _json(output/'acceptance_result.json',result)
        return result
    finally:
        if journal is not None: journal.close()
        if runtime is not None: runtime.store.close()
