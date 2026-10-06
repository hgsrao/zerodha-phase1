#!/usr/bin/env python3
"""C03 qualification: constructed BUY seed, unchanged production lifecycle/execution.

This is not Trial000 performance or live broker certification. Historical natural
SELL admission is exercised separately by the existing D01 integration fixture.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import pandas as pd
from tests.test_r5_morning_startup import boot
from revision5.topology import bay_for_symbol


def execute(output, outcome='ACKNOWLEDGED', protective=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'cycle.db').exists():
        raise FileExistsError('Qualification requires a fresh output database; preserve prior evidence')
    engine, runtime, store = boot(output)
    trace = []
    try:
        fill = engine.broker.place_order('TITAN', 'BUY', 10, 'MARKET', 100,
                                        engine.safety_contract.as_dict(), engine.registry)
        assert fill['passed']
        trade = dict(trade_id='trade-1', symbol='TITAN', side='BUY', quantity=10,
                     entry_price=fill['filled_price'], stop_price=90,
                     initial_stop_price=90, target_price=200, governor_mfe_r=1.25,
                     entry_timestamp=pd.Timestamp('2023-12-05 15:07'))
        engine._trade_sequence = 1
        engine.open_trades['TITAN'] = trade
        engine._register_position_lifecycle('TITAN', trade, trade['entry_timestamp'])
        engine._exit_controller_states['TITAN'] = engine.exit_controller.open_position(
            'BUY', trade['entry_price'], 90, 200, 375)
        bay_id = bay_for_symbol('TITAN')
        engine._bay_governors[bay_id].begin_position(position_id='trade-1')
        entry_costs = engine.broker.booked_costs
        actual_convert = engine.broker.request_product_conversion

        def observed_conversion(request_id, *args, **kwargs):
            pending = store.load('trade-1')
            request = next(row for row in store.requests() if row['request_id'] == request_id)
            assert pending.record.lifecycle_state == 'TRANSFER_REQUESTED'
            assert request['status'] == 'PENDING'
            # Independent SQLite reader sees committed intent before broker action.
            import sqlite3
            with sqlite3.connect(output / 'cycle.db') as reader:
                assert reader.execute('SELECT count(*) FROM conversions WHERE request_id=?',
                                      (request_id,)).fetchone()[0] == 1
            trace.append(dict(event='DURABLE_INTENT_BEFORE_SUBMIT', request_id=request_id))
            if outcome == 'ACKNOWLEDGED':
                return actual_convert(request_id, *args, **kwargs)
            receipt = dict(request_id=request_id, status=outcome, timestamp=str(kwargs['timestamp']))
            engine.broker._conversion_receipts[request_id] = receipt
            return receipt

        engine.broker.request_product_conversion = observed_conversion

        def tick(timestamp, price, last=False):
            bar = dict(open=price, high=price + 1, low=price - 1, close=price)
            runtime.handle_bar(engine, 'TITAN', pd.Timestamp(timestamp), bar, last)
            snapshot = store.load('trade-1')
            trace.append(dict(event='BAR', timestamp=timestamp, price=price,
                              state=snapshot.record.lifecycle_state,
                              owner=snapshot.record.owner_engine, product=snapshot.record.product,
                              stop=snapshot.record.current_stop_price,
                              sessions=snapshot.protection.get('sessions'),
                              broker=engine.broker.get_position('TITAN')))

        for minute in ('15:08', '15:09', '15:10'):
            tick('2023-12-05 ' + minute, 110)
        snap = store.load('trade-1')
        assert engine.broker.booked_costs == entry_costs
        if outcome == 'ACKNOWLEDGED':
            assert snap.record.lifecycle_state == 'B_OPEN'
            assert snap.record.owner_engine == 'ENGINE_B' and snap.record.product == 'CNC'
            assert len(engine.broker.fills) == 1  # conversion creates no trade/cost
            tick('2023-12-06 09:15', 111)
            assert store.load('trade-1').protection['sessions'] == ['2023-12-05', '2023-12-06']
            assert runtime.engine_b.export_state()['positions']['trade-1']['sessions_elapsed'] == 2
            runtime.reconcile(engine, store.load('trade-1'))
            if protective:
                # Pending discretionary exit must lose priority to opening-gap stop.
                trade['engine_b_exit_pending'] = dict(timestamp=pd.Timestamp('2023-12-06 09:15'),
                                                       reason='ENGINE_B_STRUCTURAL_REVERSAL')
                tick('2023-12-06 09:16', 89)
                assert engine.completed_trades[0]['reason'] == 'stop_gap'
            else:
                # Two prior completed closes then three adverse closes -> real B reversal.
                for minute, price in zip(('09:16', '09:17', '09:18', '09:19'), (109, 108, 107, 106)):
                    if engine.open_trades:
                        tick('2023-12-06 ' + minute, price)
                assert engine.completed_trades[0]['reason'] == 'ENGINE_B_STRUCTURAL_REVERSAL'
        else:
            assert snap.record.owner_engine == 'ENGINE_A'
            assert snap.record.lifecycle_state == ('A_OPEN' if outcome == 'REJECTED' else 'TRANSFER_REQUESTED')
            assert (outcome != 'UNKNOWN') or engine._execution_halted
            tick('2023-12-05 15:11', 89)
            assert engine.completed_trades[0]['reason'] == 'stop_gap'
        assert sum(row['event'] == 'DURABLE_INTENT_BEFORE_SUBMIT' for row in trace) == 1
        assert len(engine.completed_trades) == 1 and engine.open_trades == {}
        assert engine.broker.get_position('TITAN')['quantity'] == 0
        closed = store.load('trade-1')
        assert closed.record.lifecycle_state == 'CLOSED' and store.list_open() == []
        assert closed.protection['completed_trade'] == engine.completed_trades[0]
        key = engine._close_feedback_key('TITAN', trade)
        assert closed.protection['close_feedback_receipts'][key] == 'DONE'
        merit = engine.plant_control.dispatch_controller.merit_source
        governor = engine.real_plant_dcs.bays[bay_id].governor
        histories = deepcopy((merit.trade_history_r, governor.history_r))
        assert len(merit.trade_history_r[bay_id]) == 1 and len(governor.history_r) == 1
        for _ in range(3):
            engine._register_realized_r_close_feedback(symbol='TITAN', trade=trade,
                bay_id=bay_id, realized_r=999, reason='DUPLICATE_TEST')
        assert (merit.trade_history_r, governor.history_r) == histories
        result = dict(classification='CONSTRUCTED_BUY_LIFECYCLE_QUALIFICATION', outcome=outcome,
                      mock_protection_only=True, natural_admission=False, full_run_path=False,
                      durable_intent_before_submit=True, exactly_once_feedback_in_process=True,
                      next_session_managed=outcome == 'ACKNOWLEDGED',
                      ledger=engine.completed_trades, trace=trace,
                      source_hashes={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [ROOT/'revision5/combined_cycle_runtime.py',
                                    ROOT/'revision2_external/orchestrator.py',
                                    ROOT/'revision5/handoff_manager.py',
                                    ROOT/'revision5/engine_b_management.py']})
        (output/'receipt.json').write_text(json.dumps(result, default=str, indent=2))
        pd.DataFrame(engine.completed_trades).to_csv(output/'ledger.csv', index=False)
        return result
    finally:
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from tests.test_r5_d01_orchestrator_run_integration import build_orchestrator
    natural_path = args.output / 'natural_sell_replay'
    natural_path.mkdir(parents=True, exist_ok=True)
    if (natural_path / 'cycle.db').exists():
        raise FileExistsError('Qualification output already exists')
    natural_engine, _, natural_store, _, bars, warmup = build_orchestrator(natural_path)
    try:
        natural_report = natural_engine.run({'TITAN': bars}, warmup=warmup)
        assert len(natural_report['trades']) == 1
        assert natural_report['trades'][0]['side'] == 'SELL'
        (natural_path / 'receipt.json').write_text(json.dumps(dict(
            classification='DERIVED_REAL_REPLAY_FIXTURE', full_run_path=True,
            natural_admission=True, mock_protection_only=True, ledger=natural_report['trades']),
            default=str, indent=2))
        pd.DataFrame(natural_report['trades']).to_csv(natural_path / 'ledger.csv', index=False)
        print('natural_sell_replay', natural_report['trades'][0]['net_pnl'])
    finally:
        natural_store.close()
    for name, outcome, protective in [('b_reversal','ACKNOWLEDGED',False),
        ('b_protective','ACKNOWLEDGED',True), ('rejected','REJECTED',False), ('unknown','UNKNOWN',False)]:
        receipt = execute(args.output/name, outcome, protective)
        print(name, receipt['ledger'][0]['reason'], receipt['ledger'][0]['net_pnl'])
