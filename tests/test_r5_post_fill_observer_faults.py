"""Real replay fills remain owned and protected after injected observer errors."""
import pytest
from test_r5_d01_orchestrator_run_integration import build_orchestrator


@pytest.mark.parametrize('observer', ['bridge', 'post_fill', 'expectancy', 'dynamics'])
def test_non_authority_failure_after_fill_preserves_books_and_protection(tmp_path, monkeypatch, observer):
    orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)
    targets = {
        'bridge': (orch.supervisory_bridge, 'snapshot_bb09_bb10'),
        'post_fill': (orch.entry_decision_engine, 'evaluate_post_fill'),
        'expectancy': (orch.entry_expectancy_ledger, 'observe_fill'),
        'dynamics': (orch.closed_loop, 'entry_snapshot'),
    }

    def observer_failure(*args, **kwargs):
        raise RuntimeError('injected ' + observer + ' failure')

    owner, method = targets[observer]
    monkeypatch.setattr(owner, method, observer_failure)
    with pytest.raises(RuntimeError, match='injected ' + observer + ' failure'):
        orch.run({'TITAN': titan}, warmup=warmup)
    assert len(orch.broker.fills) == 1
    trade = orch.open_trades['TITAN']
    assert trade['quantity'] == abs(orch.broker.get_position('TITAN')['quantity'])
    assert 'TITAN' in orch._exit_controller_states
    assert store.load(trade['trade_id']).record.lifecycle_state == 'A_OPEN'
    runtime.reconcile(orch, store.load(trade['trade_id']))
    assert orch._execution_halted
    # A halted entry stream still permits the real protective close path.
    orch._execute_exit('TITAN', trade['entry_timestamp'], trade,
                       trade['entry_price'], 'fault_fixture_close')
    assert orch.broker.get_position('TITAN')['quantity'] == 0
    assert orch.open_trades == {}
    assert len(orch.completed_trades) == 1
