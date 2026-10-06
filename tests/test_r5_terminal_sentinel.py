"""Terminal CLOSE prices at the last processed symbol bar, excluding lookahead sentinel."""
from copy import deepcopy
import pandas as pd
from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator


def test_unprocessed_next_session_extreme_sentinel_cannot_change_terminal_close(tmp_path):
    def run(path,mutate):
        path.mkdir()
        engine,runtime,store,_,bars,warmup=build_orchestrator(path)
        # Boundary fixture: retain the actual historical signal/fill until terminal CLOSE.
        # This deliberately disables ordinary hold/exit management for this test only.
        engine._maybe_exit=lambda *args,**kwargs: None
        submitted=[]
        real_exit=engine._execute_exit
        def observe(symbol,timestamp,trade,price,reason):
            submitted.append((timestamp,price,reason))
            return real_exit(symbol,timestamp,trade,price,reason)
        engine._execute_exit=observe
        if mutate:
            bars=bars.copy()
            bars.loc[bars.index[-1],'timestamp']=pd.Timestamp(bars.iloc[-1]['timestamp'])+pd.Timedelta(days=1)
            for name in ('open','high','low','close'):
                bars.loc[bars.index[-1],name]=99999.0
        report=engine.run({'TITAN':bars},warmup=warmup)
        trade=report['trades'][0]
        assert trade['reason']=='end_of_run_reconciliation'
        assert pd.Timestamp(trade['exit_timestamp'])==pd.Timestamp(bars.iloc[-2]['timestamp'])
        assert submitted[-1][1]==float(bars.iloc[-2]['close'])
        store.close()
        return report['trades']
    assert run(tmp_path/'original',False)==run(tmp_path/'extreme_sentinel',True)


def test_terminal_close_fails_if_entry_fill_timestamp_is_later_than_last_observed_bar(tmp_path):
    import pytest
    engine,runtime,store,_,bars,warmup=build_orchestrator(tmp_path)
    last_observed=pd.Timestamp(bars.iloc[-2]['timestamp'])
    def retain_and_inject_future_entry(symbol,timestamp,*args,**kwargs):
        # Deliberate synthetic metadata fault after a real historical entry/fill.
        if symbol in engine.open_trades and pd.Timestamp(timestamp)==last_observed:
            engine.open_trades[symbol]['entry_timestamp']=str(bars.iloc[-1]['timestamp'])
    engine._maybe_exit=retain_and_inject_future_entry
    with pytest.raises(RuntimeError,match='cannot precede authoritative entry fill'):
        engine.run({'TITAN':bars},warmup=warmup)
    assert engine._execution_halted
    assert not engine.completed_trades
    assert engine.broker.get_position('TITAN')['quantity']!=0
    assert len(engine.broker.fills)==1
    store.close()


def test_final_signal_cannot_enter_an_unprocessed_same_session_sentinel(tmp_path):
    from dataclasses import replace
    from tests_external.test_audit_remediation import signal
    engine,_,store,_,full_bars,warmup=build_orchestrator(tmp_path)
    bars=full_bars.iloc[:100].copy()
    final_timestamp=pd.Timestamp(bars.iloc[-2]['timestamp'])
    real_id=engine.id_box.evaluate
    id_timestamps=[]
    def observe_id(pa,*args,**kwargs):
        id_timestamps.append(pd.Timestamp(pa.timestamp))
        return real_id(pa,*args,**kwargs)
    engine.id_box.evaluate=observe_id
    def final_only(snapshot,config):
        final=pd.Timestamp(snapshot.timestamp)==final_timestamp
        return replace(signal('TITAN',direction=1 if final else 0,confidence=.95 if final else 0),
                       timestamp=snapshot.timestamp),[]
    engine.pa.evaluate=final_only
    report=engine.run({'TITAN':bars},warmup=warmup)
    assert not engine.broker.fills
    assert not report['trades']
    assert not report['execution_halted']
    assert final_timestamp not in id_timestamps
    assert engine._controller_event_counts['TERMINAL_FORWARD_CONTEXT_NO_ENTRY']>=1
    store.close()
