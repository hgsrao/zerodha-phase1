import pytest
from revision2_external.paper_execution import CostedPaperBrokerAdapter
from revision2_external.broker_reconciliation import BrokerReconciliationService
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def filled():
    broker = CostedPaperBrokerAdapter()
    engine = Revision2ExternalEngineOrchestrator(['TITAN'])
    broker.test_contract = engine.safety_contract.as_dict()
    assert broker.place_order('TITAN', 'BUY', 10, 'MARKET', 100, broker.test_contract)['passed']
    return broker


def test_conversion_is_not_a_fill_and_duplicate_is_idempotent():
    broker = filled()
    costs, fills = broker.booked_costs, len(broker.fills)
    receipt = broker.request_product_conversion('conversion-1', 'TITAN', 10, timestamp='2024-02-13 15:10')
    assert receipt['status'] == 'ACKNOWLEDGED'
    assert broker.get_position('TITAN')['product'] == 'CNC'
    assert broker.request_product_conversion('conversion-1', 'TITAN', 10) == receipt
    assert (broker.booked_costs, len(broker.fills)) == (costs, fills)
    with pytest.raises(ValueError):
        broker.request_product_conversion('conversion-1', 'TITAN', 9)


def test_restart_preserves_costs_conversion_and_contingent_protection():
    broker = filled()
    broker.request_product_conversion('conversion-1', 'TITAN', 10)
    oid = broker.ensure_protection('TITAN', 98, 10, 'CNC')
    recovered = CostedPaperBrokerAdapter()
    recovered.restore_snapshot(broker.snapshot())
    assert recovered.snapshot() == broker.snapshot()
    assert recovered.conversion_receipt('conversion-1')['status'] == 'ACKNOWLEDGED'
    receipt = BrokerReconciliationService().reconcile([
        {'symbol': 'TITAN', 'quantity': 10, 'product': 'CNC', 'protective_order_id': oid}], recovered.snapshot())
    assert receipt.new_risk_allowed
    # Exit closes the actual simulated position, incurs exactly one more cost,
    # and removes protection from the active broker view.
    assert recovered.place_order('TITAN', 'SELL', 10, 'MARKET', 99, broker.test_contract)['passed']
    assert not any(o['order_id'] == oid for o in recovered.snapshot()['orders'])
    assert len(recovered.cost_ledger) == 2


def test_conversion_rejection_does_not_change_position():
    broker = filled()
    assert broker.request_product_conversion('bad', 'TITAN', 9)['status'] == 'REJECTED'
    assert broker.get_position('TITAN').get('product', 'MIS') == 'MIS'
    with pytest.raises(ValueError):
        broker.ensure_protection('TITAN', 98, 9)
    with pytest.raises(ValueError):
        broker.restore_snapshot(broker.snapshot())
