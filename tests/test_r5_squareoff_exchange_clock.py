"""Equivalent exchange/UTC instants must have identical square-off disposition."""
from types import SimpleNamespace
import pandas as pd
import pytest
from test_r5_d01_product_reconciliation import SYMBOL, QTY, build, open_registered


@pytest.mark.parametrize('utc', [False, True])
@pytest.mark.parametrize('pending', [False, True])
def test_a_squareoff_uses_exchange_clock_for_ordinary_and_pending_positions(tmp_path, monkeypatch, utc, pending):
    orch, runtime, store = build(tmp_path)
    trade = open_registered(orch)
    if pending:
        runtime.handoff.request(trade['trade_id'], QTY, '2024-02-13 15:10',
                                current_r=1, mfe_r=1, trend_aligned=True)
        monkeypatch.setattr(orch.broker, 'conversion_receipt', lambda _: None)
    local = pd.Timestamp('2024-02-13 ' + orch.entry_decision_engine.config.force_close_time,
                         tz='Asia/Kolkata')
    stamp = local.tz_convert('UTC') if utc else local
    bar = dict(open=101, high=101.2, low=100.8, close=101)
    signal = SimpleNamespace(exit_confidence=.8)
    orch._maybe_exit(SYMBOL, stamp, bar, signal, 2, False, .8)
    assert SYMBOL not in orch.open_trades
    assert orch.completed_trades[0]['reason'] == 'force_close_time'
    assert orch.broker.get_position(SYMBOL)['quantity'] == 0
