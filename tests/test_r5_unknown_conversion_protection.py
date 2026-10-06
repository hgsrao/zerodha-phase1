"""Withheld paper conversion receipts: no promotion/retry; protection stays active."""
import pytest
from test_r5_d01_product_reconciliation import SYMBOL, QTY, build, open_registered


def pending_position(tmp_path, monkeypatch, actual_product):
    orch, runtime, store = build(tmp_path)
    trade = open_registered(orch)
    request = runtime.handoff.request(trade['trade_id'], QTY, '2024-02-13 15:10',
                                      current_r=1.0, mfe_r=1.0, trend_aligned=True)
    assert request is not None
    if actual_product == 'CNC':
        assert orch.broker.request_product_conversion(request.request_id, SYMBOL, QTY,
                                                      timestamp='2024-02-13 15:10')['status'] == 'ACKNOWLEDGED'
    monkeypatch.setattr(orch.broker, 'conversion_receipt', lambda _: None)
    return orch, runtime, store, trade, request


def bar(price):
    return dict(open=price, high=price + .2, low=price - .2, close=price)


@pytest.mark.parametrize('product', ['MIS', 'CNC'])
def test_unknown_receipt_preserves_owner_and_protection_without_resubmit(tmp_path, monkeypatch, product):
    orch, runtime, store, trade, request = pending_position(tmp_path, monkeypatch, product)
    before_fills, before_costs = len(orch.broker.fills), orch.broker.booked_costs

    def forbid_resubmit(*args, **kwargs):
        pytest.fail('pending conversion was blindly resubmitted')

    monkeypatch.setattr(orch.broker, 'request_product_conversion', forbid_resubmit)
    assert runtime.handle_bar(orch, SYMBOL, '2024-02-13 15:11', bar(101), False)
    snap = store.load(trade['trade_id'])
    assert snap.record.lifecycle_state == 'TRANSFER_REQUESTED'
    assert snap.record.owner_engine == 'ENGINE_A' and snap.record.product == 'MIS'
    assert orch._execution_halted
    protection = next(o for o in orch.broker.snapshot()['orders']
                      if o['order_id'] == snap.protection['protective_order_id'])
    assert protection['product'] == product
    assert protection['pending_quantity'] == QTY
    assert protection['trigger_price'] == snap.record.current_stop_price
    assert (len(orch.broker.fills), orch.broker.booked_costs) == (before_fills, before_costs)


@pytest.mark.parametrize('product', ['MIS', 'CNC'])
@pytest.mark.parametrize('disposition', ['stop', 'cutoff'])
def test_unknown_conversion_still_allows_stop_and_a_squareoff(tmp_path, monkeypatch, product, disposition):
    orch, runtime, store, trade, request = pending_position(tmp_path, monkeypatch, product)
    price = trade['stop_price'] - 1 if disposition == 'stop' else 101
    timestamp = ('2024-02-13 15:11' if disposition == 'stop'
                 else '2024-02-13 ' + orch.entry_decision_engine.config.force_close_time)
    assert runtime.handle_bar(orch, SYMBOL, timestamp, bar(price), False)
    assert orch.broker.get_position(SYMBOL)['quantity'] == 0
    assert orch.open_trades == {}
    assert store.load(trade['trade_id']).record.lifecycle_state == 'CLOSED'
    assert len(orch.completed_trades) == 1
    assert orch.completed_trades[0]['reason'] == ('stop_gap' if disposition == 'stop' else 'force_close_time')


def test_timeout_does_not_imply_rejection_and_correlated_ack_can_resolve(tmp_path):
    orch, runtime, store = build(tmp_path)
    trade = open_registered(orch)
    request = runtime.handoff.request(trade['trade_id'], QTY, '2024-02-13 15:10',
                                      current_r=1, mfe_r=1, trend_aligned=True)
    before = store.load(trade['trade_id'])
    pending = runtime.handoff.resolve(request.request_id, 'TIMEOUT')
    assert pending.record.lifecycle_state == 'TRANSFER_REQUESTED'
    assert pending.revision == before.revision
    acknowledged = runtime.handoff.resolve(request.request_id, 'ACKNOWLEDGED', 'CNC', QTY,
                                           '2024-02-13 15:12')
    assert acknowledged.record.lifecycle_state == 'B_OPEN'


@pytest.mark.parametrize('status', ['unknown', 'TIMEOUT'])
def test_unresolved_transport_receipt_preserves_stop_and_correlation(tmp_path, monkeypatch, status):
    orch, runtime, store, trade, request = pending_position(tmp_path, monkeypatch, 'CNC')
    monkeypatch.setattr(orch.broker, 'conversion_receipt',
                        lambda _: dict(request_id=request.request_id, status=status))
    runtime.handle_bar(orch, SYMBOL, '2024-02-13 15:11', bar(101), False)
    assert store.requests()[0]['status'] == 'PENDING'
    assert store.requests()[0]['last_observation'] == status.upper()
    assert store.load(trade['trade_id']).record.lifecycle_state == 'TRANSFER_REQUESTED'


def test_actual_quantity_mismatch_freezes_risk_without_another_fill(tmp_path, monkeypatch):
    from revision5.combined_cycle_runtime import CombinedCycleReconciliationError
    orch, runtime, store, trade, request = pending_position(tmp_path, monkeypatch, 'MIS')
    assert orch.broker.place_order(SYMBOL, 'SELL', 4, 'MARKET', 101,
                                   orch.safety_contract.as_dict(), orch.registry)['passed']
    before = len(orch.broker.fills)
    with pytest.raises(CombinedCycleReconciliationError, match='reconcilable paper broker truth'):
        runtime.handle_bar(orch, SYMBOL, '2024-02-13 15:11', bar(90), False)
    assert orch._execution_halted
    assert len(orch.broker.fills) == before


def test_correlated_ack_resolves_pending_owner_without_retry_or_automatic_unhalt(tmp_path, monkeypatch):
    orch, runtime, store, trade, request = pending_position(tmp_path, monkeypatch, 'CNC')
    runtime.handle_bar(orch, SYMBOL, '2024-02-13 15:11', bar(101), False)
    actual = orch.broker._conversion_receipts[request.request_id]
    monkeypatch.setattr(orch.broker, 'conversion_receipt', lambda _: dict(actual))
    runtime.handle_bar(orch, SYMBOL, '2024-02-13 15:12', bar(101), False)
    snap = store.load(trade['trade_id'])
    assert snap.record.lifecycle_state == 'B_OPEN'
    assert 'transfer_unresolved' not in snap.protection
    assert len(orch.broker._conversion_receipts) == 1
    assert len(orch.broker.fills) == 1 and orch._execution_halted
