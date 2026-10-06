import pandas as pd
import pytest
from dataclasses import replace
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.position_lifecycle import open_position, request_transfer, acknowledge_transfer

def record():
    return acknowledge_transfer(request_transfer(open_position(position_id='p',symbol='TITAN',direction='BUY',initial_risk_r=10.,anchor_price=100.,initial_stop_price=90.,created_bar_timestamp=pd.Timestamp('2023-12-05 15:00'))))

def bars(prices, start='2023-12-05 15:00'):
    return pd.DataFrame({'close':prices,'high':[p+1 for p in prices],'low':[p-1 for p in prices]},index=pd.date_range(start,periods=len(prices),freq='min'))

def evaluate(controller,r,frame,n=1):
    return controller.evaluate(r,frame,frame.index[-1],n)

def test_explicit_opt_in_and_owner():
    with pytest.raises(ValueError): evaluate(EngineBController(EngineBPolicy()),record(),bars([100]))
    with pytest.raises(ValueError): evaluate(EngineBController(EngineBPolicy(enabled=True)),replace(record(),direction='SELL'),bars([100]))

def test_cross_session_hold_no_intraday_clock_or_micro_exit():
    c=EngineBController(EngineBPolicy(enabled=True))
    for n,date in enumerate(['2023-12-05 15:25','2023-12-06 09:15','2023-12-07 15:25'],1):
        assert evaluate(c,record(),bars([100],date),n).action=='HOLD'
    assert evaluate(c,record(),bars([100],'2023-12-08 09:15'),4).reason=='ENGINE_B_MAX_SESSIONS'

def test_structural_reversal_requires_confirmation_and_restart():
    policy=EngineBPolicy(enabled=True,structural_window=2,reversal_confirmation_bars=3,trail_mode='NONE')
    c=EngineBController(policy); r=record(); frame=bars([110,110,109,108,107])
    assert evaluate(c,r,frame.iloc[:3]).action=='HOLD'
    import json
    receipt=json.loads(json.dumps(c.export_state()))
    restored=EngineBController(policy);restored.restore_state(receipt)
    assert evaluate(restored,r,frame.iloc[:4]).action=='HOLD'
    assert evaluate(restored,r,frame).reason=='ENGINE_B_STRUCTURAL_REVERSAL'
    assert restored.export_state()['positions']['p']['reversal_count']==3
    assert evaluate(restored,r,frame).reversal_count==3

def test_trailing_monotonic_after_gap_and_next_bar():
    c=EngineBController(EngineBPolicy(enabled=True,trail_distance_r=1,structural_window=20))
    first=evaluate(c,record(),bars([130]))
    assert first.proposed_stop_price==121
    assert first.effective_from=='NEXT_BAR'
    # Root executor applies proposed protection and owns gap-stop execution.
    updated=replace(record(),current_stop_price=first.proposed_stop_price)
    second=evaluate(c,updated,bars([80],'2023-12-06 09:15'),2)
    assert second.proposed_stop_price>=first.proposed_stop_price
    assert updated.initial_risk_r==10

def test_atr_and_causal_reference():
    c=EngineBController(EngineBPolicy(enabled=True,structural_window=2,trail_mode='ATR',atr_multiple=2))
    frame=bars([100,110,120]);d=evaluate(c,record(),frame)
    assert d.structural_reference==105
    assert d.proposed_stop_price>=90
    with pytest.raises(ValueError):c.evaluate(record(),frame,frame.index[-2],1)


def test_session_boundary_horizon_exits_without_fourth_session():
    c=EngineBController(EngineBPolicy(enabled=True))
    frame=bars([102],'2023-12-07 15:29')
    decision=c.evaluate(record(),frame,frame.index[-1],3,session_last_bar=True)
    assert decision.reason=='ENGINE_B_MAX_SESSIONS'
    assert decision.action=='EXIT'
