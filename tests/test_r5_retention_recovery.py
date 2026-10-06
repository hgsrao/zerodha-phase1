"""Terminal cache eviction; conservative feedback guards remain replay-safe."""
from copy import deepcopy
import pytest
from revision5.position_lifecycle import close_position
from revision5.topology import bay_for_symbol
from test_r5_d01_product_reconciliation import build, open_registered, SYMBOL
from test_r5_d01_orchestrator_run_integration import build_orchestrator


def test_closed_save_evicts_only_terminal_management_state(tmp_path):
    engine, runtime, store = build(tmp_path)
    trade = open_registered(engine)
    runtime.engine_b._states = {'trade-1': {'cache': 1}, 'still-active': {'cache': 2}}
    runtime._history = {'trade-1': [1], 'still-active': [2]}
    record = close_position(store.load('trade-1').record)
    runtime.close(record, trade)
    assert store.load('trade-1').record.lifecycle_state == 'CLOSED'
    assert runtime.engine_b._states == {'still-active': {'cache': 2}}
    assert runtime._history == {'still-active': [2]}
    assert 'trade-1' not in store.load('trade-1').protection['engine_b_state']['positions']
    assert 'history' not in store.load('trade-1').protection
    store.close()


def test_failed_close_save_retains_positional_state(tmp_path, monkeypatch):
    engine, runtime, store = build(tmp_path)
    trade = open_registered(engine)
    runtime.engine_b._states['trade-1'] = {'cache': 1}
    runtime._history['trade-1'] = [1]
    before = deepcopy((runtime.engine_b.export_state(), runtime._history))
    def fail(*args, **kwargs):
        raise RuntimeError('durable save failed')
    monkeypatch.setattr(store, 'save', fail)
    with pytest.raises(RuntimeError, match='durable save failed'):
        runtime.close(close_position(store.load('trade-1').record), trade)
    assert (runtime.engine_b.export_state(), runtime._history) == before
    assert store.load('trade-1').record.lifecycle_state == 'A_OPEN'
    store.close()


def test_close_rejects_nonterminal_record_without_eviction(tmp_path):
    engine, runtime, store = build(tmp_path)
    trade = open_registered(engine)
    runtime.engine_b._states['trade-1'] = {'cache': 1}
    with pytest.raises(ValueError, match='CLOSED'):
        runtime.close(store.load('trade-1').record, trade)
    assert 'trade-1' in runtime.engine_b._states
    store.close()


def test_restore_filters_old_aggregate_closed_state_and_history(tmp_path):
    engine, runtime, store = build(tmp_path)
    open_registered(engine)
    snapshot = store.load('trade-1')
    protection = deepcopy(snapshot.protection)
    # Historical aggregate in a surviving position refers to a no-longer-open ID.
    protection['engine_b_state']['positions']['old-closed'] = {'obsolete': True}
    store.save(snapshot.record, snapshot.trade, protection, snapshot.revision)
    runtime._history['old-closed'] = [1]
    runtime.engine_b._states['old-closed'] = {'obsolete': True}
    runtime.restore(engine)
    assert 'old-closed' not in runtime.engine_b._states
    assert 'old-closed' not in runtime._history
    assert engine.open_trades[SYMBOL]['trade_id'] == 'trade-1'
    assert runtime._history['trade-1'] == []
    store.close()


def test_restore_empty_store_does_not_resurrect_terminal_cache(tmp_path):
    engine, runtime, store = build(tmp_path)
    trade = open_registered(engine)
    runtime.close(close_position(store.load('trade-1').record), trade)
    runtime.engine_b._states['trade-1'] = {'old': True}
    runtime._history['trade-1'] = [1]
    runtime.restore(engine)
    assert runtime.engine_b.export_state()['positions'] == {}
    assert runtime._history == {}
    store.close()


@pytest.mark.parametrize('journal_present', [False, True])
def test_compaction_never_removes_done_guard_or_double_applies(tmp_path, journal_present):
    engine, _, store, *_ = build_orchestrator(tmp_path)
    if journal_present:
        class ReplayJournal:
            def commit_feedback(self, engine, key):
                pass
            def is_durably_closed(self, key):
                return True  # Closure alone must not authorize compaction.
        engine.paper_journal = ReplayJournal()
    bay_id = bay_for_symbol('TITAN')
    kwargs = dict(symbol='TITAN', trade={'trade_id': 'dedup-1'}, bay_id=bay_id,
                  realized_r=-1.0, reason='STOP')
    engine._register_realized_r_close_feedback(**kwargs)
    assert engine._compact_close_feedback_receipts(['trade_id:dedup-1']) == 0
    engine._register_realized_r_close_feedback(**kwargs)
    assert engine._close_feedback_receipts['trade_id:dedup-1'] == 'DONE'
    assert engine.plant_control.dispatch_controller.merit_source.trade_history_r[bay_id] == [-1.0]
    assert engine.real_plant_dcs.bays[bay_id].governor.history_r == [-1.0]
    store.close()


def test_morning_hydration_filters_terminal_but_keeps_active_b_state(tmp_path):
    from test_r5_morning_startup import carry, boot, recovered_broker
    import pandas as pd
    engine, runtime, store, truth = carry(tmp_path)
    active = deepcopy(runtime.engine_b._states['trade-1'])
    runtime.engine_b._states['old-closed'] = deepcopy(active)
    engine._checkpoint_morning_recovery(account_id='fixture-account',
                                      timestamp=pd.Timestamp('2023-12-05 15:25'))
    store.close()
    fresh, fresh_runtime, fresh_store = boot(tmp_path)
    fresh_runtime._history['old-closed'] = [1]
    fresh._reconcile_morning_startup(account_id='fixture-account', broker=recovered_broker(truth))
    assert fresh_runtime.engine_b._states == {'trade-1': active}
    assert 'old-closed' not in fresh_runtime._history
    assert fresh.open_trades['TITAN']['trade_id'] == 'trade-1'
    assert fresh._execution_halted  # retention changes never authorize live resume
    fresh_store.close()
