"""5% geometry, authorisation trips, and sealed real-data paper-prefix recovery."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
import sqlite3
import pandas as pd
import pytest
from kiteconnect.exceptions import InputException, OrderException
from revision2_external.protection_policy import gtt_sell_limit, DeliveryAuthorisationTrip, DELIVERY_UNAUTHORIZED
from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter
from revision5.state_recovery import StateRecoveryJournal
from revision5.paper_state_journal import PaperStateJournal
from tests.test_r5_morning_startup import carry, boot, recovered_broker, gtt_client
from tests.test_r5_paper_apply import engine
from tests_external.test_audit_remediation import open_position
from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator


def test_five_percent_limit_is_tick_aligned_and_is_not_a_gap_guarantee():
    assert gtt_sell_limit(95,0.05)==90.25
    assert gtt_sell_limit(90,0.05)==85.5
    assert gtt_sell_limit(95.01,0.01)==90.25
    # A SELL limit at 90.25 cannot fill against a best bid of 90.
    assert 90 < gtt_sell_limit(95,0.05)
    with pytest.raises(ValueError,match='tick size'):
        gtt_sell_limit(95.01,0.05)


@pytest.mark.parametrize('error', [InputException,OrderException])
def test_cnc_exit_authorisation_failure_trips_without_inventing_a_close(error):
    o,t=engine()
    trade=open_position(o)
    assert o.broker.request_product_conversion('authorisation-fixture','INFY',10)['status']=='ACKNOWLEDGED'
    o.broker.place_order=Mock(side_effect=error('Holdings not authorised. Please authorise using CDSL TPIN.'))
    with pytest.raises(DeliveryAuthorisationTrip,match=DELIVERY_UNAUTHORIZED):
        o._execute_exit('INFY',t,trade,99,'stop')
    assert o._execution_halted and not o.completed_trades
    assert o.open_trades['INFY'] is trade and o.broker.get_position('INFY')['quantity']==10
    assert o._delivery_authorisation_trip['code']==DELIVERY_UNAUTHORIZED


def test_non_authorisation_input_error_does_not_get_the_delivery_trip_label():
    o,t=engine();trade=open_position(o)
    o.broker.place_order=Mock(side_effect=InputException('Invalid tick size'))
    with pytest.raises(InputException,match='tick size'):
        o._execute_exit('INFY',t,trade,99,'stop')
    assert o._execution_halted and not hasattr(o,'_delivery_authorisation_trip')


def test_adapter_surfaces_authorisation_rejection():
    client=Mock();client.VARIETY_REGULAR='regular'
    client.place_order.side_effect=OrderException('e-DIS authorisation required')
    receipt=KiteConnectBrokerAdapter(client=client).place_order('TITAN','SELL',10,'LIMIT',limit_price=85.5,product='CNC')
    assert not receipt['passed'] and receipt['trip_code']==DELIVERY_UNAUTHORIZED


def test_gtt_authorisation_failure_propagates_fatal_plant_trip(tmp_path):
    _,_,store,truth=carry(tmp_path);store.close()
    o,_,store=boot(tmp_path);broker=recovered_broker(truth)
    broker.ensure_cnc_gtt=Mock(return_value=dict(passed=False,verified=False,
        trip_code=DELIVERY_UNAUTHORIZED,reason='TPIN authorisation missing'))
    with pytest.raises(DeliveryAuthorisationTrip,match=DELIVERY_UNAUTHORIZED):
        o._reconcile_morning_startup(account_id='fixture-account',broker=broker)
    assert o._execution_halted and not o.open_trades
    store.close()


def test_kite_gtt_exception_is_not_blindly_retried(tmp_path):
    con=sqlite3.connect(tmp_path/'gtt.sqlite',isolation_level=None)
    client,_=gtt_client();client.place_gtt.side_effect=InputException('CDSL TPIN authorisation missing')
    adapter=KiteConnectBrokerAdapter(client=client,account_id='fixture-account',recovery_journal=StateRecoveryJournal(con))
    result=adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)
    assert result['trip_code']==DELIVERY_UNAUTHORIZED
    assert not adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)['passed']
    assert client.place_gtt.call_count==1
    con.close()


def test_ecs_column_and_saved_tick_restore_without_clock_guessing(tmp_path):
    first,_,store,truth=carry(tmp_path)
    saved=pd.Timestamp('2023-12-05 15:10',tz='Asia/Kolkata')
    first._native_timestamp=saved;first._native_bar_index=2
    first._checkpoint_morning_recovery(account_id='fixture-account',timestamp=pd.Timestamp('2023-12-05 15:25'))
    assert store.connection.execute('SELECT ecs_demand_pu FROM boot_checkpoints ORDER BY seq DESC LIMIT 1').fetchone()==(0.8,)
    store.close()
    o,_,store=boot(tmp_path)
    receipt=o._reconcile_morning_startup(account_id='fixture-account',broker=recovered_broker(truth),
                                        next_timestamp=pd.Timestamp('2023-12-05 09:41',tz='UTC'))
    assert o._native_timestamp==saved and o._native_bar_index==2
    assert o.plant_control.ecs._previous_demand==0.8 and receipt['ecs_demand_pu']==0.8
    assert not receipt['admissions_allowed']
    store.close()


def test_overlapping_tick_is_rejected_before_gtt_request(tmp_path):
    first,_,store,truth=carry(tmp_path)
    first._native_timestamp=pd.Timestamp('2023-12-05 15:10',tz='Asia/Kolkata');first._native_bar_index=2
    first._checkpoint_morning_recovery(account_id='fixture-account',timestamp=pd.Timestamp('2023-12-05 15:25'))
    store.close();o,_,store=boot(tmp_path);broker=recovered_broker(truth)
    with pytest.raises(RuntimeError,match='advance'):
        o._reconcile_morning_startup(account_id='fixture-account',broker=broker,next_timestamp='2023-12-05 15:10')
    assert o._execution_halted and broker._simulated_gtts=={}
    store.close()


def test_empty_or_missing_prefix_cannot_unblock_admissions(tmp_path):
    o,_,store=boot(tmp_path)
    with pytest.raises(RuntimeError,match='existing journal'):
        o.resume_verified_paper_replay({},journal_path=tmp_path/'missing.sqlite')
    assert o._execution_halted
    store.close()


@pytest.mark.parametrize('crash', ['pre_entry','open_position'])
def test_real_titan_subprocess_resume_matches_uninterrupted_ledger(tmp_path,crash):
    for mode in ('baseline',crash,'resume'):
        result=subprocess.run([sys.executable,'-m','tests.test_r5_recovery_hardening',str(tmp_path),mode],
                              capture_output=True,text=True,timeout=90)
        assert result.returncode==({'pre_entry':81,'open_position':82}.get(mode,0)),result.stderr
    baseline=json.loads((tmp_path/'baseline_ledger.json').read_text())
    recovered=json.loads((tmp_path/'resumed_ledger.json').read_text())
    receipt=json.loads((tmp_path/'resumed_receipt.json').read_text())
    assert recovered==baseline and len(recovered)==1
    assert receipt['admissions_resumed'] and receipt['appended_checkpoints']>0
    assert receipt['reconciled_checkpoints']==receipt['prefix_checkpoints']
    assert not receipt['warmup_bypassed']


if __name__=='__main__':
    root=Path(sys.argv[1]);mode=sys.argv[2];path=root/mode;path.mkdir(parents=True,exist_ok=True)
    o,_,store,_,bars,warmup=build_orchestrator(path)
    journal_path=root/'interrupted.sqlite'
    if mode=='resume':
        report=o.resume_verified_paper_replay({'TITAN':bars},journal_path=journal_path,warmup=warmup)
        (root/'resumed_receipt.json').write_text(json.dumps(report['paper_recovery'],default=str,indent=2))
        (root/'resumed_ledger.json').write_text(json.dumps(report['trades'],default=str,sort_keys=True,indent=2))
    else:
        def after_commit(journal,eng):
            if mode=='pre_entry' and journal.cursor==1:os._exit(81)
            if mode=='open_position' and eng.open_trades:os._exit(82)
        journal=PaperStateJournal(root/'baseline.sqlite' if mode=='baseline' else journal_path,after_commit=after_commit)
        o.paper_journal=journal
        report=o.run({'TITAN':bars},warmup=warmup)
        (root/'baseline_ledger.json').write_text(json.dumps(report['trades'],default=str,sort_keys=True,indent=2))
        journal.close()
    store.close()
