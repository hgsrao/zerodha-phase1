from datetime import datetime, timezone
import pytest
from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter
from revision2_external.live_execution_boundary import LiveExecutionBoundary, AdmissionBlocked

NOW=datetime(2026,10,6,4,0,tzinfo=timezone.utc)
REQ=dict(symbol='TITAN',side='BUY',quantity=4,order_type='MARKET',product='MIS')
class Client:
    VARIETY_REGULAR='regular'
    def __init__(self): self.book=[]; self.writes=0; self.fail=False; self.inventory=[]
    def profile(self): return {'user_id':'A'}
    def orders(self): return self.book
    def positions(self): return {'net':self.inventory}
    def holdings(self): return []
    def margins(self): return {}
    def place_order(self,**kw):
        self.writes+=1
        self.book.append(dict(kw,order_id='1',status='OPEN',filled_quantity=0,average_price=0))
        if self.fail: raise TimeoutError('uncertain')
        return '1'
    def order_history(self,oid): return [self.book[0].copy()]

def setup(tmp_path,mode='enabled',cursor=True):
    client=Client(); adapter=KiteConnectBrokerAdapter(account_id='A',client=client)
    boundary=LiveExecutionBoundary(adapter,tmp_path/'execution.sqlite3',account_id='A',mode=mode,clock=lambda:NOW,ownership_reader=lambda:dict(account_id='A',checkpoint_id='durable1',records=[]),cursor_reader=(lambda:dict(account_id='A',checkpoint_id='durable1',timestamp=NOW.isoformat())) if cursor else None)
    return client,boundary

def test_acceptance_partial_complete_and_restart_no_resubmit(tmp_path):
    client,b=setup(tmp_path)
    result=b.submit('entry1',REQ)
    assert result['accepted'] and not result['filled']
    client.book[0].update(filled_quantity=2,average_price=100)
    client.inventory=[dict(exchange='NSE',tradingsymbol='TITAN',product='MIS',quantity=2)]
    assert b.reconcile('entry1')['fill_delta']==2
    assert b.reconcile('entry1')['fill_delta']==0
    client.book[0].update(status='COMPLETE',filled_quantity=4)
    assert b.reconcile('entry1')['filled']
    b.close()
    b=LiveExecutionBoundary(KiteConnectBrokerAdapter(account_id='A',client=client),tmp_path/'execution.sqlite3',account_id='A')
    assert b.submit('entry1',REQ)['filled']
    assert client.writes==1
    b.close()

def test_unknown_intent_never_blind_retry(tmp_path):
    client,b=setup(tmp_path); client.fail=True
    b.submit('entry1',REQ)
    b.submit('entry1',REQ)
    assert client.writes==1
    assert b.reconcile('entry1')['status']=='OPEN'
    client.book=[]
    assert b.reconcile('entry1')['status']=='UNKNOWN'
    b.close()

@pytest.mark.parametrize('mode,cursor',[('offline',True),('enabled',False)])
def test_missing_prerequisites_no_actuation(tmp_path,mode,cursor):
    client,b=setup(tmp_path,mode,cursor)
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    assert client.writes==0
    b.close()

@pytest.mark.parametrize('patch',[{'quantity':True},{'quantity':0},{'product':'NRML'},{'order_type':'LIMIT','limit_price':float('nan')}])
def test_invalid_geometry(tmp_path,patch):
    client,b=setup(tmp_path)
    with pytest.raises(ValueError): b.submit('entry1',dict(REQ,**patch))
    assert client.writes==0
    b.close()

def test_stale_cursor_and_unprotected_truth_block(tmp_path):
    client,b=setup(tmp_path)
    b.cursor_reader=lambda:dict(account_id='A',checkpoint_id='x',timestamp='2026-10-05T04:00:00+00:00')
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    b.cursor_reader=lambda:dict(account_id='A',checkpoint_id='x',timestamp=NOW.isoformat())
    client.inventory=[dict(exchange='NSE',tradingsymbol='TITAN',product='MIS',quantity=2)]
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    assert client.writes==0
    b.close()

def test_collision_and_fill_regression(tmp_path):
    client,b=setup(tmp_path); b.submit('entry1',REQ)
    with pytest.raises(ValueError): b.submit('entry1',dict(REQ,quantity=5))
    client.book[0].update(filled_quantity=2,average_price=100)
    b.reconcile('entry1')
    client.book[0]['filled_quantity']=1
    with pytest.raises(AdmissionBlocked): b.reconcile('entry1')
    b.close()

def test_delivery_sell_no_boolean_authorization(tmp_path):
    client,b=setup(tmp_path)
    with pytest.raises(AdmissionBlocked): b.submit('sell1',dict(REQ,side='SELL',product='CNC'))
    assert client.writes==0
    b.close()

def test_cancelled_partial_preserved_and_unknown_blocks_new_risk(tmp_path):
    client,b=setup(tmp_path); b.submit('entry1',REQ)
    client.book=[]
    assert b.reconcile('entry1')['status']=='UNKNOWN'
    with pytest.raises(AdmissionBlocked): b.submit('entry2',REQ)
    assert client.writes==1
    client.book=[dict(tradingsymbol='TITAN',transaction_type='BUY',quantity=4,order_type='MARKET',product='MIS',exchange='NSE',tag='entry1',order_id='1',status='CANCELLED',filled_quantity=2,average_price=100)]
    result=b.reconcile('entry1')
    assert result['status']=='CANCELLED' and result['filled_quantity']==2 and not result['filled']
    b.close()

def test_delivery_error_forwarded_and_durably_retained(tmp_path):
    from revision2_external.protection_policy import DeliveryAuthorisationTrip
    client,b=setup(tmp_path)
    client.holdings=lambda:[dict(exchange='NSE',tradingsymbol='TITAN',product='CNC',quantity=4,authorised_quantity=4,authorised_date=NOW.date().isoformat())]
    client.book=[dict(exchange='NSE',tradingsymbol='TITAN',product='CNC',transaction_type='SELL',quantity=4,pending_quantity=4,filled_quantity=0,order_type='SL-M',trigger_price=90,status='TRIGGER PENDING',order_id='stop')]
    def rejected(**kw):
        client.writes+=1
        return dict(passed=False,status='REJECTED',trip_code='PLANT_TRIP_DELIVERY_UNAUTHORIZED')
    b.adapter.place_order=rejected
    records=[dict(exchange='NSE',tradingsymbol='TITAN',product='CNC',quantity=4,protective_order_id='stop',current_stop_price=90)]
    b.ownership_reader=lambda:dict(account_id='A',checkpoint_id='durable1',records=records)
    with pytest.raises(DeliveryAuthorisationTrip): b.submit('sell1',dict(REQ,side='SELL',product='CNC'),runtime_records=records)
    with pytest.raises(DeliveryAuthorisationTrip): b.submit('sell1',dict(REQ,side='SELL',product='CNC'))
    assert client.writes==1
    b.close()

def test_omitted_or_forged_ownership_inventory_blocked(tmp_path):
    client,b=setup(tmp_path)
    b.ownership_reader=None
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    b.ownership_reader=lambda:dict(account_id='A',checkpoint_id='durable1',records=[dict(symbol='TITAN',product='MIS',quantity=4)])
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ,runtime_records=[])
    assert client.writes==0
    b.close()

def test_incomplete_broker_snapshot_and_stale_ownership_blocked(tmp_path):
    client,b=setup(tmp_path)
    b.adapter.snapshot=lambda:dict(passed=True)
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    b.ownership_reader=lambda:dict(account_id='A',checkpoint_id='old',records=[])
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    assert client.writes==0
    b.close()

def test_missing_terminal_readback_durably_blocks_new_risk(tmp_path):
    client,b=setup(tmp_path); b.submit('entry1',REQ)
    client.book[0].update(status='COMPLETE',filled_quantity=4,average_price=100)
    b.reconcile('entry1')
    client.book=[]
    assert b.reconcile('entry1')['status']=='UNKNOWN'
    with pytest.raises(AdmissionBlocked): b.submit('entry2',REQ)
    assert client.writes==1
    b.close()

def test_cursor_expires_during_snapshot(tmp_path):
    from datetime import timedelta
    client,b=setup(tmp_path)
    times=iter([NOW,NOW+timedelta(seconds=6)])
    b.clock=lambda:next(times)
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    assert client.writes==0
    b.close()

@pytest.mark.parametrize('cursor',[dict(account_id='A',checkpoint_id='durable1'),dict(account_id='A',checkpoint_id='durable1',timestamp='broken'),dict(account_id='A',checkpoint_id='durable1',timestamp=None)])
def test_missing_malformed_cursor_timestamp_is_admission_blocked(tmp_path,cursor):
    client,b=setup(tmp_path); b.cursor_reader=lambda:cursor
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    assert client.writes==0
    b.close()

class ConnectionProxy:
    def __init__(self,connection,hook): self.connection=connection; self.hook=hook
    @property
    def in_transaction(self): return self.connection.in_transaction
    def execute(self,sql,*args): self.hook(sql); return self.connection.execute(sql,*args)
    def close(self): self.connection.close()

def test_begin_failure_preserves_original_error_without_rollback(tmp_path):
    import sqlite3
    client,b=setup(tmp_path); seen=[]
    def hook(sql):
        seen.append(sql)
        if sql=='BEGIN IMMEDIATE': raise sqlite3.OperationalError('original begin failure')
    b.db=ConnectionProxy(b.db,hook)
    with pytest.raises(sqlite3.OperationalError,match='original begin failure'): b.submit('entry1',REQ)
    assert 'ROLLBACK' not in seen and client.writes==0
    b.close()

def test_slow_intent_commit_rechecks_cursor_before_actuation(tmp_path):
    from datetime import timedelta
    client,b=setup(tmp_path); advanced=[False]
    def hook(sql):
        if sql=='COMMIT': advanced[0]=True
    b.db=ConnectionProxy(b.db,hook)
    b.clock=lambda: NOW+timedelta(seconds=6 if advanced[0] else 0)
    with pytest.raises(AdmissionBlocked): b.submit('entry1',REQ)
    assert client.writes==0
    assert b.submit('entry1',REQ)['status']=='NOT_SUBMITTED'
    assert b.reconcile('entry1')['status']=='NOT_SUBMITTED'
    with pytest.raises(AdmissionBlocked): b.submit('entry2',REQ)
    assert client.writes==0
    b.close()
