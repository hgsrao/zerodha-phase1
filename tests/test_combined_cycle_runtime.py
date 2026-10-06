from types import SimpleNamespace
import pandas as pd
import pytest
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.handoff_manager import HandoffManager,HandoffConfig
from revision5.engine_b_management import EngineBController,EngineBPolicy
from revision5.combined_cycle_runtime import CombinedCycleRuntime,CombinedCycleRuntimeConfig,CombinedCycleReconciliationError
from revision5.position_lifecycle import open_position,B_OPEN,close_position
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


class Broker:
    def __init__(self): self.quantity=10;self.product='MIS';self.receipts={};self.calls=0;self.protection=None
    def ensure_protection(self,symbol,stop_price,quantity,product):
        self.protection=dict(order_id='protection',status='TRIGGER PENDING',trigger_price=stop_price,quantity=quantity,product=product)
        return 'protection'
    def snapshot(self): return {'orders':[self.protection] if self.protection else []}
    def get_position(self,symbol): return dict(quantity=self.quantity,product=self.product)
    def request_product_conversion(self,request_id,symbol,quantity,**kwargs):
        self.calls+=1;self.product='CNC'
        self.receipts[request_id]=dict(request_id=request_id,status='ACKNOWLEDGED',product='CNC',quantity=quantity,timestamp=kwargs['timestamp'])
    def conversion_receipt(self,request_id): return self.receipts.get(request_id)


def build(tmp_path,trail=2):
    store=CombinedCycleStore(tmp_path/'cycle.db')
    runtime=CombinedCycleRuntime(store,HandoffManager(store,HandoffConfig(enabled=True)),EngineBController(EngineBPolicy(enabled=True,structural_window=2,trail_distance_r=trail)),CombinedCycleRuntimeConfig(end_of_run_disposition='PERSIST'))
    engine=Revision2ExternalEngineOrchestrator.__new__(Revision2ExternalEngineOrchestrator)
    engine.combined_cycle_runtime=runtime;engine._position_lifecycle={};engine._exit_controller_states={};engine._micom_trip=None
    engine._close_feedback_receipts={}
    engine.config=SimpleNamespace(require=lambda key:.2);engine.safety_contract=SimpleNamespace(values={'safety_drawdown_halt_threshold':.2})
    engine._current_drawdown=lambda:0;engine.broker=Broker();engine.completed_trades=[]
    trade=dict(trade_id='p',symbol='TITAN',side='BUY',quantity=10,entry_price=100,stop_price=90,target_price=200)
    engine.open_trades={'TITAN':trade}
    engine._register_position_lifecycle('TITAN',trade,pd.Timestamp('2023-12-05 15:07'))
    def exit_(symbol,ts,trade,price,reason):
        engine.broker.quantity=0;engine._close_position_lifecycle(trade);engine.open_trades.pop(symbol);engine.completed_trades.append(dict(price=price,reason=reason))
    engine._execute_exit=exit_
    return engine,runtime


def bar(price,low=None): return dict(open=price,high=price+1,low=price-1 if low is None else low,close=price)

def transfer(engine,runtime):
    for clock in ('15:08','15:09','15:10'):
        runtime.handle_bar(engine,'TITAN',pd.Timestamp('2023-12-05 '+clock),bar(110),False)
    assert engine._position_lifecycle['p'].lifecycle_state==B_OPEN


def test_actual_orchestrator_hook_handoff_day2_gap(tmp_path):
    engine,runtime=build(tmp_path);transfer(engine,runtime)
    assert engine.broker.calls==1 and not engine.completed_trades
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-05 15:25'),bar(110),None,18,True,0,{})
    assert runtime.persist_end_of_run(engine,'TITAN')
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-06 09:15'),bar(89),None,19,False,0,{})
    assert engine.completed_trades==[dict(price=89,reason='stop_gap')]
    assert not runtime.store.list_open()


def test_new_stop_effective_next_bar_not_same_low(tmp_path):
    engine,runtime=build(tmp_path,trail=.5);transfer(engine,runtime)
    # Handoff bar low=109, proposed stop=106; next completed high arms 116.
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-05 15:11'),bar(120,107),None,4,False,0,{})
    assert not engine.completed_trades
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-05 15:12'),bar(115),None,5,False,0,{})
    assert engine.completed_trades[-1]['reason']=='stop_gap'


def test_third_session_close_and_product_mismatch(tmp_path):
    engine,runtime=build(tmp_path);transfer(engine,runtime)
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-06 15:25'),bar(110),None,20,True,0,{})
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-07 15:25'),bar(110),None,21,True,0,{})
    assert engine.completed_trades[-1]['reason']=='ENGINE_B_MAX_SESSIONS'


def test_restart_reconciles_before_restoring_and_does_not_repeat_conversion(tmp_path):
    engine,runtime=build(tmp_path);transfer(engine,runtime)
    engine.open_trades={};engine._position_lifecycle={}
    runtime.restore(engine)
    engine._maybe_exit('TITAN',pd.Timestamp('2023-12-06 09:15'),bar(110),None,19,False,0,{})
    assert engine.broker.calls==1
    engine.broker.product='MIS'
    with pytest.raises(CombinedCycleReconciliationError):runtime.restore(engine)
