"""Opt-in epoch-local durable dedup, with actual paper replay and CLOSED evidence."""
from copy import deepcopy
import json
import sqlite3
import pytest
from revision5.paper_state_journal import PaperStateJournal, canonical, checkpoint_state
from revision5.state_recovery import StateRecoveryJournal
from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator


def attached(tmp_path, *, enabled=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine, runtime, store, _, bars, warmup=build_orchestrator(tmp_path)
    journal=PaperStateJournal(tmp_path/'paper.sqlite',feedback_compaction=enabled)
    engine.paper_journal=journal
    return engine,runtime,store,journal,bars,warmup


def run_and_capture(tmp_path, *, enabled=True):
    engine,runtime,store,journal,bars,warmup=attached(tmp_path,enabled=enabled)
    calls=[]
    real=engine._register_realized_r_close_feedback
    def observe(**kwargs):
        calls.append(deepcopy(kwargs))
        return real(**kwargs)
    engine._register_realized_r_close_feedback=observe
    report=engine.run({'TITAN':bars},warmup=warmup)
    assert len(calls)==1
    return engine,runtime,store,journal,bars,warmup,report,calls[0]


def test_actual_close_compacts_and_duplicate_does_not_mutate_consumers(tmp_path):
    engine,runtime,store,journal,_,_,report,kwargs=run_and_capture(tmp_path)
    assert len(report['trades'])==1
    key='trade_id:'+kwargs['trade']['trade_id']
    assert key not in engine._close_feedback_receipts
    assert kwargs['trade']['trade_id'] not in engine._position_lifecycle
    before=canonical(checkpoint_state(engine))
    engine._register_realized_r_close_feedback(**kwargs)
    assert canonical(checkpoint_state(engine))==before
    assert journal.con.execute('SELECT COUNT(*) FROM applied_feedback WHERE epoch=?',(journal.feedback_epoch,)).fetchone()==(1,)
    assert runtime.store.load(kwargs['trade']['trade_id']).record.lifecycle_state=='CLOSED'
    journal.close();store.close()


def test_conflicting_duplicate_event_fails_closed_before_controller_mutation(tmp_path):
    engine,_,store,journal,_,_,_,kwargs=run_and_capture(tmp_path)
    merit=deepcopy(engine.plant_control.dispatch_controller.merit_source.trade_history_r)
    kwargs['realized_r']+=1
    with pytest.raises(RuntimeError,match='conflicting'):
        engine._register_realized_r_close_feedback(**kwargs)
    assert engine._execution_halted
    assert engine.plant_control.dispatch_controller.merit_source.trade_history_r==merit
    journal.close();store.close()


@pytest.mark.parametrize('target',['identity','epoch','applied'])
def test_corrupted_binding_or_consumption_fails_closed(tmp_path,target):
    engine,_,store,journal,_,_,_,kwargs=run_and_capture(tmp_path)
    if target=='identity':
        journal.con.execute("UPDATE identity SET payload='corrupt'")
    elif target=='epoch':
        journal.con.execute('DROP TRIGGER epoch_no_update')
        journal.con.execute("UPDATE feedback_epochs SET identity_checksum='corrupt'")
    else:
        journal.con.execute('DROP TRIGGER applied_no_update')
        journal.con.execute("UPDATE applied_feedback SET state_checksum='corrupt'")
    before=deepcopy(engine.plant_control.dispatch_controller.merit_source.trade_history_r)
    with pytest.raises(RuntimeError):
        engine._register_realized_r_close_feedback(**kwargs)
    assert engine._execution_halted
    assert engine.plant_control.dispatch_controller.merit_source.trade_history_r==before
    journal.close();store.close()


def test_fresh_epoch_reconstructs_once_and_preserves_ledger_and_state(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second'
    engine,_,store,journal,bars,warmup,report,kwargs=run_and_capture(first)
    epoch=journal.feedback_epoch
    before=canonical(checkpoint_state(engine))
    journal.close();store.close()
    second.mkdir()
    fresh,_,fresh_store,_,fresh_bars,fresh_warmup=build_orchestrator(second)
    replay=PaperStateJournal(first/'paper.sqlite',feedback_compaction=True)
    fresh.paper_journal=replay
    replay_report=fresh.run({'TITAN':fresh_bars},warmup=fresh_warmup)
    assert replay.feedback_epoch!=epoch
    assert canonical(replay_report['trades'])==canonical(report['trades'])
    assert canonical(checkpoint_state(fresh))==before
    assert replay.reconciled_checkpoints==replay.prefix_length
    assert replay.con.execute('SELECT COUNT(DISTINCT epoch) FROM applied_feedback').fetchone()==(2,)
    state=canonical(checkpoint_state(fresh))
    fresh._register_realized_r_close_feedback(**kwargs)
    assert canonical(checkpoint_state(fresh))==state
    replay.close();fresh_store.close()


def test_atomic_epoch_consumption_rolls_back_with_failed_transaction(tmp_path):
    engine,_,store,journal,bars,warmup=attached(tmp_path)
    journal.bind(engine,{'TITAN':bars},warmup)
    journal.con.execute("CREATE TRIGGER fail_applied BEFORE INSERT ON applied_feedback BEGIN SELECT RAISE(ABORT,'injected epoch write failure'); END")
    from revision5.topology import bay_for_symbol
    bay=bay_for_symbol('TITAN')
    kwargs=dict(symbol='TITAN',trade={'trade_id':'atomic-test'},bay_id=bay,realized_r=-1.,reason='STOP')
    before=deepcopy(engine.plant_control.dispatch_controller.merit_source.trade_history_r)
    with pytest.raises(sqlite3.IntegrityError,match='epoch write failure'):
        engine._register_realized_r_close_feedback(**kwargs)
    assert engine.plant_control.dispatch_controller.merit_source.trade_history_r==before
    assert engine._close_feedback_receipts=={}
    assert journal.con.execute('SELECT COUNT(*) FROM applied_feedback').fetchone()==(0,)
    assert journal.con.execute('SELECT COUNT(*) FROM fleet_state').fetchone()==(0,)
    assert journal.con.execute('SELECT COUNT(*) FROM receipts').fetchone()==(0,)
    journal.close();store.close()


def test_failed_closed_persistence_retains_done_guard(tmp_path,monkeypatch):
    engine,runtime,store,journal,bars,warmup=attached(tmp_path)
    save=store.save
    count=0
    def fail_final_closed(record,*args,**kwargs):
        nonlocal count
        if record.lifecycle_state=='CLOSED':
            count+=1
            if count==2:  # First close precedes feedback; second carries DONE.
                raise RuntimeError('final CLOSED save failed')
        return save(record,*args,**kwargs)
    monkeypatch.setattr(store,'save',fail_final_closed)
    with pytest.raises(RuntimeError,match='final CLOSED save failed'):
        engine.run({'TITAN':bars},warmup=warmup)
    assert list(engine._close_feedback_receipts.values())==['DONE']
    assert engine._compact_close_feedback_receipts(list(engine._close_feedback_receipts))==0
    assert engine._execution_halted
    journal.close();store.close()


def test_unresolved_and_unstable_receipts_never_compact(tmp_path):
    engine,_,store,journal,bars,warmup=attached(tmp_path)
    journal.bind(engine,{'TITAN':bars},warmup)
    engine._close_feedback_receipts={'trade_id:pending':'PENDING','symbol_object:TITAN:1':'DONE'}
    assert engine._compact_close_feedback_receipts(list(engine._close_feedback_receipts))==0
    assert len(engine._close_feedback_receipts)==2
    journal.close();store.close()


def test_verified_interrupted_resume_uses_fresh_epoch_and_matches_ledger(tmp_path):
    baseline=tmp_path/'baseline'
    base,_,base_store,base_journal,_,_,report,_=run_and_capture(baseline)
    base_state=canonical(checkpoint_state(base))
    base_journal.close();base_store.close()
    interrupted=tmp_path/'interrupted'
    engine,_,store,journal,bars,warmup=attached(interrupted)
    def stop_after_close(journal,engine):
        if engine.completed_trades:
            raise RuntimeError('interrupt after compacted durable close')
    journal.after_commit=stop_after_close
    with pytest.raises(RuntimeError,match='interrupt after compacted durable close'):
        engine.run({'TITAN':bars},warmup=warmup)
    assert engine._close_feedback_receipts=={}
    old_epoch=journal.feedback_epoch
    journal.close();store.close()
    resumed_path=tmp_path/'resumed';resumed_path.mkdir()
    resumed,_,resumed_store,_,resumed_bars,resumed_warmup=build_orchestrator(resumed_path)
    resumed_report=resumed.resume_verified_paper_replay({'TITAN':resumed_bars},
                    journal_path=interrupted/'paper.sqlite',warmup=resumed_warmup,feedback_compaction=True)
    assert canonical(resumed_report['trades'])==canonical(report['trades'])
    assert canonical(checkpoint_state(resumed))==base_state
    assert resumed_report['paper_recovery']['admissions_resumed']
    with sqlite3.connect(interrupted/'paper.sqlite') as con:
        assert con.execute('SELECT COUNT(DISTINCT epoch) FROM applied_feedback').fetchone()==(2,)
    resumed_store.close()


def test_applied_consumption_is_append_only_and_bound_to_actual_engine(tmp_path):
    engine,_,store,journal,_,_,_,kwargs=run_and_capture(tmp_path/'first')
    with pytest.raises(sqlite3.IntegrityError,match='append-only'):
        journal.con.execute('DELETE FROM applied_feedback')
    with pytest.raises(sqlite3.IntegrityError,match='append-only'):
        journal.con.execute('DELETE FROM feedback_epochs')
    other_path=tmp_path/'other';other_path.mkdir()
    other,_,other_store,*_=build_orchestrator(other_path)
    other.paper_journal=journal
    with pytest.raises(RuntimeError,match='attached bound'):
        other._register_realized_r_close_feedback(**kwargs)
    assert not other.plant_control.dispatch_controller.merit_source.trade_history_r[kwargs['bay_id']]
    journal.close();store.close();other_store.close()


def test_compaction_changes_only_receipt_cache_not_economic_ledger_or_controllers(tmp_path):
    compact=run_and_capture(tmp_path/'compact',enabled=True)
    legacy=run_and_capture(tmp_path/'legacy',enabled=False)
    assert canonical(compact[6]['trades'])==canonical(legacy[6]['trades'])
    compact_state=checkpoint_state(compact[0]);legacy_state=checkpoint_state(legacy[0])
    assert compact_state.pop('close_receipts')=={}
    assert list(legacy_state.pop('close_receipts').values())==['DONE']
    assert canonical(compact_state)==canonical(legacy_state)
    for engine,runtime,store,journal,*_ in (compact,legacy):
        journal.close();store.close()


def test_real_process_crash_after_compaction_preserves_resume_ledger(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    def run(mode):
        return subprocess.run([sys.executable,'-m','tests.test_r5_feedback_compaction',str(tmp_path),mode],
                              capture_output=True,text=True,timeout=90)
    baseline=run('baseline')
    assert baseline.returncode==0,baseline.stderr
    crashed=run('crash')
    assert crashed.returncode==73,crashed.stderr
    resumed=run('resume')
    assert resumed.returncode==0,resumed.stderr
    baseline_result=json.loads((tmp_path/'baseline.result.json').read_text())
    resumed_result=json.loads((tmp_path/'resume.result.json').read_text())
    assert baseline_result==resumed_result
    with sqlite3.connect(tmp_path/'interrupted.sqlite') as con:
        assert con.execute('SELECT COUNT(DISTINCT epoch) FROM applied_feedback').fetchone()==(2,)


def test_opt_in_bind_rejects_preseeded_feedback_guard(tmp_path):
    engine,_,store,journal,bars,warmup=attached(tmp_path)
    engine._close_feedback_receipts['trade_id:old']='DONE'
    with pytest.raises(RuntimeError,match='fresh offline engine'):
        journal.bind(engine,{'TITAN':bars},warmup)
    journal.close();store.close()


def test_opt_in_memory_done_without_epoch_consumption_fails_closed(tmp_path):
    engine,_,store,journal,bars,warmup=attached(tmp_path)
    journal.bind(engine,{'TITAN':bars},warmup)
    from revision5.topology import bay_for_symbol
    engine._close_feedback_receipts['trade_id:old']='DONE'
    with pytest.raises(RuntimeError,match='current-epoch durable consumption'):
        engine._register_realized_r_close_feedback(symbol='TITAN',trade={'trade_id':'old'},
                         bay_id=bay_for_symbol('TITAN'),realized_r=-1.0)
    assert engine._execution_halted
    journal.close();store.close()


if __name__=='__main__':
    import os
    import sys
    from pathlib import Path
    root=Path(sys.argv[1]);mode=sys.argv[2]
    runtime_dir=root/mode;runtime_dir.mkdir(parents=True,exist_ok=True)
    engine,_,store,_,bars,warmup=build_orchestrator(runtime_dir)
    path=root/('baseline.sqlite' if mode=='baseline' else 'interrupted.sqlite')
    if mode=='resume':
        report=engine.resume_verified_paper_replay({'TITAN':bars},journal_path=path,
                                                  warmup=warmup,feedback_compaction=True)
    else:
        def after_commit(journal,engine):
            if mode=='crash' and engine.completed_trades:
                assert not engine._close_feedback_receipts
                os._exit(73)
        journal=PaperStateJournal(path,feedback_compaction=True,after_commit=after_commit)
        engine.paper_journal=journal
        report=engine.run({'TITAN':bars},warmup=warmup)
        journal.close()
    (root/(mode+'.result.json')).write_text(canonical(dict(trades=report['trades'],state=checkpoint_state(engine))))
    store.close()
