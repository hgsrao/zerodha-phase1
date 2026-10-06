"""Synthetic carry fixture: real paper fill, handoff/store and normal engine boots.

This is boot-preparation evidence, not a profitability or live account test.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
import sqlite3
import pandas as pd
import pytest

from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.position_lifecycle import B_OPEN
from revision5.state_recovery import StateRecoveryJournal
from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter
from revision2_external.paper_execution import CostedPaperBrokerAdapter
from revision5.topology import bay_for_symbol


def boot(path):
    path.mkdir(parents=True, exist_ok=True)
    engine, runtime, store, *_ = build_orchestrator(path)
    runtime.engine_b = EngineBController(EngineBPolicy(enabled=True, structural_window=2, trail_mode='NONE'))
    return engine, runtime, store


def carry(path):
    engine, runtime, store = boot(path)
    fill = engine.broker.place_order('TITAN', 'BUY', 10, 'MARKET', 100,
                                   engine.safety_contract.as_dict(), engine.registry)
    assert fill['passed']
    entry = fill['filled_price']
    trade = dict(trade_id='trade-1', symbol='TITAN', side='BUY', quantity=10,
                 entry_price=entry, stop_price=90, initial_stop_price=90,
                 target_price=200, governor_mfe_r=1.25,
                 entry_timestamp=pd.Timestamp('2023-12-05 15:07'))
    engine._trade_sequence = 1
    engine.open_trades['TITAN'] = trade
    engine._register_position_lifecycle('TITAN', trade, trade['entry_timestamp'])
    engine._exit_controller_states['TITAN'] = engine.exit_controller.open_position('BUY', entry, 90, 200, 375)
    bay_id = bay_for_symbol('TITAN')
    engine._bay_governors[bay_id].begin_position(position_id='trade-1')
    for minute in ('15:08','15:09','15:10'):
        runtime.handle_bar(engine, 'TITAN', pd.Timestamp('2023-12-05 '+minute),
                           dict(open=110,high=111,low=109,close=110), False)
    assert store.load('trade-1').record.lifecycle_state == B_OPEN
    # Explicit historical fleet fixture; admission parameters are not changed.
    engine._mtm_peak = engine.starting_equity + 4000
    engine.plant_control.ecs._previous_demand = 0.8
    engine.symbol_cooldown_until_bar['TITAN'] = 7
    engine.symbol_tripped['TITAN'] = False
    engine.plant_control.dispatch_controller.merit_source.register_trade(bay_id, -1)
    engine.real_plant_dcs.bays[bay_id].cooldown_until_bar_exclusive = 16
    engine._checkpoint_morning_recovery(account_id='fixture-account', timestamp=pd.Timestamp('2023-12-05 15:25'))
    truth = engine.broker.snapshot()
    for order in truth['paper_state']['protection'].values():
        order['status'] = 'CANCELLED'  # explicitly simulate DAY expiry
    return engine, runtime, store, truth


def recovered_broker(truth):
    broker = CostedPaperBrokerAdapter()
    broker.restore_snapshot(truth)
    return broker


def test_overnight_hydrates_supported_fleet_and_requests_simulated_gtt(tmp_path):
    first, runtime, store, truth = carry(tmp_path)
    expected_weights = dict(first.plant_control.dispatch_controller.merit_source.weights)
    expected_peak = first._mtm_peak
    store.close()
    second, runtime, store = boot(tmp_path)
    broker = recovered_broker(truth)
    receipt = second._reconcile_morning_startup(account_id='fixture-account', broker=broker)
    assert second._mtm_peak == expected_peak
    assert second.plant_control.ecs._previous_demand == 0.8
    assert second.plant_control.dispatch_controller.merit_source.weights == expected_weights
    assert second.symbol_cooldown_until_bar['TITAN'] == 7
    assert second.real_plant_dcs.bays[bay_for_symbol('TITAN')].cooldown_until_bar_exclusive == 16
    assert second.open_trades['TITAN']['governor_mfe_r'] == 1.25
    assert second.open_trades['TITAN']['initial_stop_price'] == 90
    assert second._position_lifecycle['trade-1'].lifecycle_state == B_OPEN
    assert second._bay_governors[bay_for_symbol('TITAN')]._inner_states['trade-1'].active
    assert second._exit_controller_states['TITAN'].initial_stop_price == 90
    assert broker._simulated_gtts['trade-1']['limit_price'] == 85.5
    assert receipt['prepared'] and not receipt['admissions_allowed'] and second._execution_halted
    with pytest.raises(RuntimeError, match='resume cursor'):
        second.run({})
    store.close()


@pytest.mark.parametrize('fault', ['account','quantity','product','unknown_position','missing_position','unknown_order','gtt_failure','store_advanced','unknown_gtt','active_day'])
def test_failed_boot_blocks_hydration_and_admissions(tmp_path, fault):
    first, _, store, truth = carry(tmp_path)
    store.close()
    second, runtime, store = boot(tmp_path)
    broker = recovered_broker(truth)
    account = 'wrong' if fault == 'account' else 'fixture-account'
    if fault == 'quantity': broker.positions['TITAN']['quantity'] = 9
    if fault == 'product': broker.positions['TITAN']['product'] = 'MIS'
    if fault == 'missing_position': broker.positions['TITAN']['quantity'] = 0
    if fault == 'unknown_position': broker.positions['INFY'] = dict(quantity=1,avg_price=100,product='CNC')
    if fault == 'unknown_order':
        original = broker.snapshot
        def snapshot():
            result = original()
            result['orders'].append(dict(order_id='unknown',status='OPEN',quantity=1))
            return result
        broker.snapshot = snapshot
    if fault == 'gtt_failure': broker.ensure_cnc_gtt = Mock(return_value=dict(passed=False,verified=False))
    if fault == 'unknown_gtt': broker.get_gtts = Mock(return_value=[dict(status='active',condition=dict(tradingsymbol='INFY'))])
    if fault == 'active_day':
        for order in broker._contingent_protection.values(): order['status']='TRIGGER PENDING'
    if fault == 'store_advanced':
        s = store.load('trade-1')
        store.save(s.record,s.trade,s.protection,s.revision)
    with pytest.raises((RuntimeError,ValueError)):
        second._reconcile_morning_startup(account_id=account, broker=broker)
    assert second._execution_halted and not second.open_trades
    assert not getattr(second,'_morning_recovery_prepared',False)
    if fault != 'gtt_failure': assert broker._simulated_gtts == {}
    store.close()


def test_opt_in_tick_checkpoints_use_the_actual_replay(tmp_path):
    engine,runtime,store,_,bars,warmup = build_orchestrator(tmp_path)
    engine.recovery_account_id='fixture-account'
    report=engine.run({'TITAN':bars},warmup=warmup)
    assert len(report['trades'])==1
    state=StateRecoveryJournal(store.connection).load_boot()
    assert state['timestamp']=='FINAL'
    assert state['mtm_peak']==engine._mtm_peak
    assert store.connection.execute('SELECT count(*) FROM boot_checkpoints').fetchone()[0]>1
    baseline_path=tmp_path/'baseline'
    baseline_path.mkdir()
    baseline,_,baseline_store,_,baseline_bars,baseline_warmup=build_orchestrator(baseline_path)
    baseline_report=baseline.run({'TITAN':baseline_bars},warmup=baseline_warmup)
    assert report['trades']==baseline_report['trades']
    baseline_store.close()
    store.close()


def gtt_client():
    client = Mock()
    client.profile.return_value={'user_id':'fixture-account'}
    client.instruments.return_value=[dict(tradingsymbol='TITAN',tick_size=0.05)]
    order = dict(exchange='NSE',tradingsymbol='TITAN',transaction_type='SELL',quantity=10,
                 order_type='LIMIT',product='CNC',price=85.5)
    row = dict(id=123,type='single',status='active',condition=dict(exchange='NSE',tradingsymbol='TITAN',trigger_values=[90.0]),orders=[order])
    client.get_gtts.return_value=[]
    client.get_gtt.return_value=row
    client.ltp.return_value={'NSE:TITAN':{'last_price':100}}
    client.place_gtt.return_value={'trigger_id':123}
    return client,row


def test_kite_gtt_readback_and_restart_do_not_duplicate_submission(tmp_path):
    con=sqlite3.connect(tmp_path/'gtt.sqlite', isolation_level=None)
    client,row=gtt_client()
    adapter=KiteConnectBrokerAdapter(client=client,account_id='fixture-account',recovery_journal=StateRecoveryJournal(con))
    result=adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)
    assert result['verified'] and result['limit_price']==85.5
    assert client.place_gtt.call_args.kwargs['orders'][0]['price']==85.5
    con.close()
    con=sqlite3.connect(tmp_path/'gtt.sqlite', isolation_level=None)
    adapter=KiteConnectBrokerAdapter(client=client,account_id='fixture-account',recovery_journal=StateRecoveryJournal(con))
    assert adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)['verified']
    assert client.place_gtt.call_count==1
    row['status']='triggered'
    assert not adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)['verified']
    assert client.place_gtt.call_count==1
    con.close()


@pytest.mark.parametrize('fault', ['timeout','wrong_quantity','wrong_stop','gap_below_stop','duplicate_active'])
def test_gtt_ambiguity_and_bad_readback_block_protection(tmp_path,fault):
    con=sqlite3.connect(tmp_path/'gtt.sqlite',isolation_level=None)
    client,row=gtt_client()
    if fault=='timeout':client.place_gtt.side_effect=TimeoutError('unknown write')
    if fault=='wrong_quantity':row['orders'][0]['quantity']=9
    if fault=='wrong_stop':row['condition']['trigger_values']=[89]
    if fault=='gap_below_stop':client.ltp.return_value={'NSE:TITAN':{'last_price':89}}
    if fault=='duplicate_active':client.get_gtts.return_value=[row,row]
    adapter=KiteConnectBrokerAdapter(client=client,account_id='fixture-account',recovery_journal=StateRecoveryJournal(con))
    assert not adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)['verified']
    count=client.place_gtt.call_count
    assert not adapter.ensure_cnc_gtt('trade-1','TITAN',90,10)['verified']
    assert client.place_gtt.call_count==count
    con.close()


def test_actual_process_shutdown_and_second_boot(tmp_path):
    for mode in ('shutdown','recover'):
        result=subprocess.run([sys.executable,'-m','tests.test_r5_morning_startup',str(tmp_path),mode],
                              capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stderr
    result=json.loads((tmp_path/'recovered.json').read_text())
    assert result['restored_positions']==1 and result['gtt_requests']==1
    assert result['mtm_matches'] and result['weights_match'] and not result['admissions_allowed']


if __name__=='__main__':
    path=Path(sys.argv[1])
    if sys.argv[2]=='shutdown':
        first,_,store,truth=carry(path)
        (path/'broker.json').write_text(json.dumps(truth))
        (path/'expected.json').write_text(json.dumps(dict(mtm=first._mtm_peak,weights=first.plant_control.dispatch_controller.merit_source.weights)))
        store.close()
    else:
        second,_,store=boot(path)
        broker=recovered_broker(json.loads((path/'broker.json').read_text()))
        receipt=second._reconcile_morning_startup(account_id='fixture-account',broker=broker)
        expected=json.loads((path/'expected.json').read_text())
        receipt.update(mtm_matches=second._mtm_peak==expected['mtm'],
                       weights_match=second.plant_control.dispatch_controller.merit_source.weights==expected['weights'],
                       gtt_requests=len(broker._simulated_gtts))
        (path/'recovered.json').write_text(json.dumps(receipt,default=str))
        store.close()
