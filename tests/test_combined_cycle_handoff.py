import pandas as pd
import pytest
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.handoff_manager import HandoffManager, HandoffConfig
from revision5.position_lifecycle import open_position, close_position, tighten_stop, B_OPEN, TRANSFER_REQUESTED, A_OPEN, CLOSED


def setup(path):
    store=CombinedCycleStore(path)
    record=open_position(position_id='p1',symbol='TITAN',direction='BUY',initial_risk_r=10,anchor_price=100,initial_stop_price=90,created_bar_timestamp=pd.Timestamp('2023-12-05 14:00'))
    store.save(record,{'qty':10,'entry_time':pd.Timestamp('2023-12-05 14:00')},{'stop':90},expected_revision=0)
    return store,HandoffManager(store,HandoffConfig(enabled=True))


def request(manager):
    return manager.request('p1',10,pd.Timestamp('2023-12-05 15:10'),current_r=.8,mfe_r=1,trend_aligned=True)


def test_restart_duplicate_receipt_and_snapshot(tmp_path):
    path=tmp_path/'cycle.db'
    store,manager=setup(path)
    receipt=request(manager)
    assert request(manager)==receipt
    store.close()
    store=CombinedCycleStore(path); manager=HandoffManager(store,HandoffConfig(enabled=True))
    assert store.load('p1').record.lifecycle_state==TRANSFER_REQUESTED
    result=manager.resolve(receipt.request_id,'ACKNOWLEDGED','CNC',10,pd.Timestamp('2023-12-05 15:11'))
    assert result.record.lifecycle_state==B_OPEN
    assert result.trade['entry_time']==pd.Timestamp('2023-12-05 14:00')
    duplicate=manager.resolve(receipt.request_id,'ACKNOWLEDGED','CNC',10,pd.Timestamp('2023-12-05 15:11'))
    assert duplicate.revision==result.revision


def test_invalid_ack_rolls_back_unknown_and_rejection(tmp_path):
    store,manager=setup(tmp_path/'cycle.db'); receipt=request(manager)
    with pytest.raises(ValueError):
        manager.resolve(receipt.request_id,'ACKNOWLEDGED','CNC',9,pd.Timestamp('2023-12-05 15:11'))
    assert store.requests()[0]['status']=='PENDING'
    assert manager.resolve(receipt.request_id,'UNKNOWN').record.lifecycle_state==TRANSFER_REQUESTED
    assert manager.resolve(receipt.request_id,'REJECTED').record.lifecycle_state==A_OPEN


def test_closed_race_never_resurrects(tmp_path):
    store,manager=setup(tmp_path/'cycle.db'); receipt=request(manager)
    snap=store.load('p1'); store.save(close_position(snap.record),snap.trade,snap.protection,snap.revision)
    assert manager.resolve(receipt.request_id,'ACKNOWLEDGED','CNC',10,pd.Timestamp('2023-12-05 15:11')).record.lifecycle_state==CLOSED
    assert store.requests()[0]['status']=='CLOSED_RACE'


def test_stop_revision_and_serialization_faults(tmp_path):
    store,manager=setup(tmp_path/'cycle.db'); snap=store.load('p1')
    store.save(tighten_stop(snap.record,95),snap.trade,{'stop':95},snap.revision)
    with pytest.raises(ValueError): store.save(snap.record,snap.trade,snap.protection)
    with pytest.raises(ValueError): store.save(tighten_stop(snap.record,96),snap.trade,{},snap.revision)
    latest=store.load('p1')
    with pytest.raises(TypeError): store.save(latest.record,{'bad':object()}, {},latest.revision)
    assert store.load('p1').revision==latest.revision


@pytest.mark.parametrize('time,r,mfe,aligned,completed',[('15:09',1,1,True,True),('15:15',1,1,True,True),('15:10',.74,1,True,True),('15:10',1,.74,True,True),('15:10',1,1,False,True),('15:10',1,1,True,False)])
def test_qualification_gate(tmp_path,time,r,mfe,aligned,completed):
    store,manager=setup(tmp_path/'cycle.db')
    assert not manager.qualifies(store.load('p1').record,pd.Timestamp('2023-12-05 '+time),r,mfe,aligned,completed)


def test_late_ack_requires_reconciliation(tmp_path):
    store,manager=setup(tmp_path/'cycle.db'); receipt=request(manager)
    with pytest.raises(ValueError,match='late'):
        manager.resolve(receipt.request_id,'ACKNOWLEDGED','CNC',10,pd.Timestamp('2023-12-05 15:15'))
    assert store.load('p1').record.lifecycle_state==TRANSFER_REQUESTED
    assert store.requests()[0]['status']=='PENDING'
