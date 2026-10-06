"""Fault injection at durable close: actual broker fill and normal engine books."""
import pytest
from test_r5_d01_product_reconciliation import SYMBOL, build, open_registered


def test_durable_close_failure_does_not_leave_flat_position_open(tmp_path, monkeypatch):
    orch, runtime, store = build(tmp_path)
    trade = open_registered(orch)

    def disk_failure(*args, **kwargs):
        raise OSError('injected durable close failure')

    monkeypatch.setattr(runtime, 'close', disk_failure)
    with pytest.raises(OSError, match='injected durable close failure'):
        orch._execute_exit(SYMBOL, '2024-02-13 10:00', trade, 105.0, 'test_close')
    assert orch.broker.get_position(SYMBOL)['quantity'] == 0
    assert SYMBOL not in orch.open_trades
    assert SYMBOL not in orch._exit_controller_states
    assert len(orch.completed_trades) == 1
    assert orch._position_lifecycle[trade['trade_id']].lifecycle_state == 'CLOSED'
    assert orch.broker.realized_pnl == pytest.approx(orch.completed_trades[0]['pnl'])
    # A failed persistence boundary remains visible; it is not silently certified.
    assert store.load(trade['trade_id']).record.lifecycle_state == 'A_OPEN'
    assert orch._execution_halted
