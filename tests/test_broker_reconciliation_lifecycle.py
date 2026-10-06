from unittest.mock import Mock
from kiteconnect.exceptions import NetworkException
from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter
from revision2_external.broker_reconciliation import BrokerReconciliationService

def client():
    c=Mock(); c.VARIETY_REGULAR='regular'; return c

def order(**extra):
    o=dict(order_id='1',exchange='NSE',tradingsymbol='TITAN',transaction_type='BUY',quantity=10,order_type='MARKET',product='MIS',tag='abc',status='OPEN',filled_quantity=3)
    o.update(extra); return o

def test_network_after_accept_reconciles_partial_without_duplicate():
    c=client(); c.orders.side_effect=[[],[order()]]; c.place_order.side_effect=NetworkException('timeout')
    r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',10,'MARKET',correlation_id='abc')
    assert r['accepted'] and not r['filled'] and r['filled_quantity']==3
    assert c.place_order.call_count==1 and not r['retry_allowed']

def test_ambiguous_without_receipt_never_retries():
    c=client(); c.orders.return_value=[]; c.place_order.side_effect=NetworkException('timeout')
    r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',10,'MARKET',correlation_id='abc')
    assert r['ambiguous'] and not r['retry_allowed'] and c.place_order.call_count==1

def test_duplicate_receipts_block():
    c=client(); c.orders.return_value=[order(),order(order_id='2')]
    r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',10,'MARKET',correlation_id='abc')
    assert not r['passed']; c.place_order.assert_not_called()

def test_conversion_requires_product_quantity_confirmation():
    c=client(); c.convert_position.return_value=True
    old=dict(exchange='NSE',tradingsymbol='TITAN',product='MIS',quantity=10)
    new=dict(old,product='CNC')
    c.positions.side_effect=[{'net':[old]},{'net':[new]}]
    assert KiteConnectBrokerAdapter(client=c).convert_position('TITAN',10)['product_confirmed']
    c.positions.side_effect=[{'net':[old]},{'net':[old]}]
    assert not KiteConnectBrokerAdapter(client=c).convert_position('TITAN',10)['passed']

def test_restart_expired_day_stop_blocks_risk():
    p=dict(symbol='TITAN',exchange='NSE',product='CNC',quantity=10,protective_order_id='stop')
    stop=order(order_id='stop',product='CNC',transaction_type='SELL',order_type='SL',status='CANCELLED',filled_quantity=0)
    s=dict(passed=True,positions={'net':[dict(p,tradingsymbol='TITAN')]},orders=[stop])
    assert not BrokerReconciliationService().reconcile([p],s).new_risk_allowed
    stop.update(status='TRIGGER PENDING',pending_quantity=10)
    assert BrokerReconciliationService().reconcile([p],s).passed
    s['positions']['net'][0]['quantity']=3
    assert not BrokerReconciliationService().reconcile([p],s).passed

def test_unknown_broker_position_blocks_risk():
    s=dict(passed=True,positions={'net':[dict(exchange='NSE',tradingsymbol='X',product='CNC',quantity=1)]},orders=[])
    assert not BrokerReconciliationService().reconcile([],s).passed

def test_settled_holdings_are_reconciled_and_unknown_holdings_block():
    h=dict(exchange='NSE',tradingsymbol='TITAN',product='CNC',quantity=10,t1_quantity=0,used_quantity=0)
    p=dict(h,protection_required=False)
    s=dict(passed=True,positions={'net':[]},holdings=[h],orders=[])
    assert BrokerReconciliationService().reconcile([p],s).passed
    assert not BrokerReconciliationService().reconcile([],s).passed

def test_stop_trigger_and_pending_qty_must_cover_without_reversal():
    p=dict(symbol='TITAN',exchange='NSE',product='CNC',quantity=10,protective_order_id='stop',current_stop_price=100)
    stop=order(order_id='stop',product='CNC',transaction_type='SELL',order_type='SL',status='TRIGGER PENDING',filled_quantity=0,pending_quantity=10,trigger_price=100,price=99)
    s=dict(passed=True,positions={'net':[dict(p,tradingsymbol='TITAN')]},orders=[stop])
    svc=BrokerReconciliationService()
    assert svc.reconcile([p],s).passed
    for change in ({'pending_quantity':11},{'pending_quantity':9},{'trigger_price':98},{'price':101},{'transaction_type':'BUY'}):
        previous=dict(stop);stop.update(change)
        assert not svc.reconcile([p],s).passed
        stop.clear();stop.update(previous)

def test_network_conversion_ack_confirms_without_retry():
    c=client(); old=dict(exchange='NSE',tradingsymbol='TITAN',product='MIS',quantity=10)
    c.positions.side_effect=[{'net':[old]},{'net':[dict(old,product='CNC')]}]
    c.convert_position.side_effect=NetworkException('accepted but response lost')
    r=KiteConnectBrokerAdapter(client=c).convert_position('TITAN',10,correlation_id='transfer1')
    assert r['product_confirmed'] and r['confirmed_quantity']==10 and c.convert_position.call_count==1

def test_mixed_holdings_and_net_cannot_be_guessed():
    h=dict(exchange='NSE',tradingsymbol='TITAN',product='CNC',quantity=10)
    s=dict(passed=True,positions={'net':[h]},holdings=[h],orders=[])
    assert not BrokerReconciliationService().reconcile([dict(h,protection_required=False)],s).passed

def test_float_order_quantity_rejected_without_submission():
    c=client();r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',1.9,'MARKET')
    assert not r['passed'];c.place_order.assert_not_called()
