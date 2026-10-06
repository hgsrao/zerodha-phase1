"""Durable CLOSED receipts reflect actual broker close and the engine ledger."""
import pytest
from test_r5_d01_product_reconciliation import SYMBOL, build, open_registered


def test_durable_closed_record_contains_post_close_broker_and_completed_pnl(tmp_path):
    orch, runtime, store = build(tmp_path)
    trade = open_registered(orch)
    orch._execute_exit(SYMBOL, '2024-02-13 10:00', trade, 105.0, 'test_close')
    closed = store.load(trade['trade_id'])
    assert closed.record.lifecycle_state == 'CLOSED'
    snapshot = closed.protection['broker_snapshot']
    position = next(p for p in snapshot['positions'] if p['tradingsymbol'] == SYMBOL)
    assert position['quantity'] == 0
    assert closed.protection['completed_trade'] == orch.completed_trades[0]
    assert closed.protection['close_feedback_receipts'] == orch._close_feedback_receipts
    assert snapshot['paper_state']['realized_pnl'] == pytest.approx(orch.completed_trades[0]['pnl'])
    assert not any(o['order_id'] == closed.protection['protective_order_id'] for o in snapshot['orders'])


def test_real_replay_closed_receipt_includes_completed_feedback(tmp_path):
    from test_r5_d01_orchestrator_run_integration import build_orchestrator
    orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)
    report = orch.run({'TITAN': titan}, warmup=warmup)
    trade = report['trades'][0]
    closed = store.load(trade['trade_id'])
    assert closed.protection['completed_trade'] == trade
    key = 'trade_id:' + trade['trade_id']
    assert closed.protection['close_feedback_receipts'][key] == 'DONE'
