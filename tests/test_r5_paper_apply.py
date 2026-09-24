"""Offline PAPER_APPLY admission invariants. Synthetic grid is an explicit unit fixture."""
import pandas as pd
import pytest
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.plant_control import GovernorDispatchReference, PlantControlError
from revision5.topology import bay_for_symbol
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine


def engine(vix=15.0, mode="PAPER_APPLY"):
    times = pd.date_range('2024-03-01 09:15', periods=100, freq='15min', tz='Asia/Kolkata')
    provider = SealedGridContextProvider(pd.DataFrame({'timestamp':times, 'close':21000.0}),
                                         pd.DataFrame({'timestamp':times, 'close':vix}))
    plant = CentralPlantMasterDCS(total_capital=1000000, db_path=':memory:')
    orch = Engine(['INFY','TCS'], grid_context_provider=provider, real_plant_dcs=plant,
                  plant_control_mode=mode)
    ts = times[-1] + pd.Timedelta('1min')
    for _ in range(10):orch._plant_control_shadow_step(ts, 1.0)
    return orch, ts


def test_paper_cap_consumes_reference_and_never_increases_quantity():
    o,t=engine()
    q=o._paper_plant_entry_limit('INFY',100000,100,t)
    assert 0 < q < 100000
    assert o._paper_plant_entry_limit('INFY',1,100,t)==1
    assert o._paper_admission_caps > 0


@pytest.mark.parametrize('vix',[0.5,35.0])
def test_unsynchronized_grid_blocks(vix):
    o,t=engine(vix)
    assert o._paper_plant_entry_limit('INFY',10,100,t)==0


def test_stale_or_failed_evaluation_does_not_reuse_healthy_reference():
    o,t=engine()
    assert o._paper_plant_entry_limit('INFY',10,100,t+pd.Timedelta('1min'))==0
    o._plant_control_shadow_step(t, float('nan'))
    assert o._paper_plant_entry_limit('INFY',10,100,t)==0


def test_missing_or_tripped_plant_fails_closed_after_evaluation():
    o,t=engine()
    o.real_plant_dcs.bays[bay_for_symbol('INFY')].tripped_offline=True
    assert o._paper_plant_entry_limit('INFY',10,100,t)==0
    o.real_plant_dcs=None
    assert o._paper_plant_entry_limit('INFY',10,100,t)==0


def test_sequential_candidates_share_bay_capacity():
    o,t=engine()
    first=o._paper_plant_entry_limit('INFY',100000,100,t)
    o.open_trades['INFY']={'quantity':first,'entry_price':100}
    second=o._paper_plant_entry_limit('TCS',100000,100,t)
    assert second==0


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_zero_dispatch_still_allows_existing_exit(side):
    from tests_external.test_audit_remediation import open_position
    o,t=engine(35)
    trade=open_position(o,side=side)
    o._execute_exit('INFY', t, trade, 100, 'force_close_time')
    assert len(o.completed_trades)==1 and o.broker.get_position('INFY')['quantity']==0
    assert o._paper_admission_evaluations==0


def test_shadow_unchanged_and_unknown_symbol_blocked_only_in_paper():
    o,t=engine(mode='SHADOW')
    assert o._paper_plant_entry_limit('AAA',123,100,t)==123
    o,t=engine()
    assert o._paper_plant_entry_limit('AAA',123,100,t)==0


def test_replacing_paper_broker_is_rejected():
    o,t=engine()
    o.broker=object()
    with pytest.raises(PlantControlError):o._paper_plant_entry_limit('INFY',10,100,t)


@pytest.mark.parametrize('fraction',[float('nan'),float('inf'),-1,1.1])
def test_invalid_dispatch_reference_fails_closed(fraction):
    o,t=engine();bay=bay_for_symbol('INFY')
    ref=GovernorDispatchReference(bay,fraction,1,'PAPER_APPLY')
    assert o._bay_governors[bay].cap_dispatch_entry(ref,requested_quantity=10,entry_price=100,
        equity=1000000,gross_limit_fraction=1,bay_notional=0,gross_notional=0)['quantity']==0


def test_master_trip_and_86_lockout_override_previously_valid_reference():
    o,t=engine()
    o.real_plant_dcs.trip_unit_breaker(bay_id=bay_for_symbol('INFY'),reason='TEST',source='TEST',lockout=True)
    assert o._paper_plant_entry_limit('INFY',10,100,t)==0
    o,t=engine()
    o.real_plant_dcs.grid_relay.evaluate_grid_intertie(
        nifty_15m_return=0,nifty_vol_z=0,fleet_equity_drawdown_pct=.05)
    assert o._paper_plant_entry_limit('INFY',10,100,t)==0


@pytest.mark.parametrize('vix,expect_fills',[(15,True),(35,False)])
def test_pipeline_applies_grid_hold_before_any_entry_order(monkeypatch,vix,expect_fills):
    from tests_external.test_audit_remediation import signal, long_bars
    from revision2.contracts import IDDecision
    o,t=engine(vix)
    monkeypatch.setattr(o.pa,'evaluate',lambda snapshot,cfg:(signal(),[]))
    monkeypatch.setattr(o.id_box,'evaluate',lambda *a,**kw:(IDDecision(True,'fixture',.8,2,.6),[]))
    monkeypatch.setattr(o.id_box,'_current_regime',lambda *a:'calm')
    frame=long_bars()
    frame['timestamp']=pd.date_range(t-pd.Timedelta('30min'),periods=len(frame),freq='min')
    report=o.run({'INFY':frame},warmup=30)
    assert report['plant_control']['applied']
    assert bool(report['fills']) is expect_fills
    assert report['safety_violations']==0
    if not expect_fills:
        assert report['orders_submitted']==0
        assert report['plant_control']['paper_admission_rejections']>0
