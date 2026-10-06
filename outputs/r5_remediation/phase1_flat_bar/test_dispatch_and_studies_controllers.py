"""Native units: dispatch is merit feedback, studies PID controls vote weights."""
import numpy as np
import pandas as pd
import pytest
from revision5.ccpp_unified_plant import DynamicBayLoadDispatcher
from revision5.topology import BAY_IDS
import revision2_external.composite_study_signal as studies

def test_dispatch_recovers_after_losses_without_zero_lock():
    d=DynamicBayLoadDispatcher();bay=BAY_IDS[0]
    for _ in range(30):d.register_trade(bay,-1.)
    low=d.weights[bay]
    for i in range(100):
        d.register_trade(bay,1. if i%2==0 else -.1)
        assert all(x>0 for x in d.weights.values())
        assert sum(d.weights.values())==pytest.approx(1.)
    assert d.weights[bay]>low
    assert not hasattr(d,'integral_error')

@pytest.mark.parametrize('sign',[-1,1])
def test_studies_one_bar_price_noise_with_fixed_votes_has_no_large_pid_drop(monkeypatch,sign):
    for name in ('_ichimoku_vote','_bollinger_vote','_stochastic_vote','_session_vwap_vote'):
        vote=-sign if name=='_session_vwap_vote' else sign
        monkeypatch.setattr(studies,name,lambda *a,vote=vote:vote)
    unit=studies.CompositeStudySignal()
    close=100+sign*np.arange(90)*.01;close[-1]=close[-2]-.1*sign
    bars=pd.DataFrame({'timestamp':pd.date_range('2023-12-05 09:15',periods=90,freq='min'),'open':close,'close':close,'high':close+.02,'low':close-.02,'volume':1000.})
    before=None
    for i in range(len(bars)):
        result=unit.evaluate('TITAN',bars.iloc[:i+1])
        for v in result['weight_pid_audit'].values():assert abs(v['i'])<=unit.clamp
        if i==len(bars)-2:before=result['confidence']
    assert any(v['d']!=0 for v in result['weight_pid_audit'].values())
    assert result['confidence']>=.5*before

def test_vote_disagreement_can_zero_confidence_without_any_derivative(monkeypatch):
    unit=studies.CompositeStudySignal(kd=0)
    for name,vote in zip(('_ichimoku_vote','_bollinger_vote','_stochastic_vote','_session_vwap_vote'),(1,1,-1,-1)):
        monkeypatch.setattr(studies,name,lambda *a,vote=vote:vote)
    bars=pd.DataFrame({'timestamp':[pd.Timestamp('2023-12-05 09:15')],'open':[100.],'close':[100.],'high':[101.],'low':[99.],'volume':[1000.]})
    r=unit.evaluate('TITAN',bars)
    assert r['confidence']==0
    assert all(x['d']==0 for x in r['weight_pid_audit'].values())

def test_flat_move_grading_is_neutral_for_long_and_short_votes(monkeypatch):
    unit=studies.CompositeStudySignal()
    for name,vote in zip(('_ichimoku_vote','_bollinger_vote','_stochastic_vote','_session_vwap_vote'),(1,1,-1,-1)):
        monkeypatch.setattr(studies,name,lambda *a,vote=vote:vote)
    bars=pd.DataFrame({'timestamp':pd.date_range('2023-12-05 09:15',periods=30,freq='min'),'open':100.,'close':100.,'high':100.01,'low':99.99,'volume':1000.})
    for i in range(len(bars)):result=unit.evaluate('TITAN',bars.iloc[:i+1])
    assert all(rate == 0.5 for rate in result['hit_rates'].values())
    assert all(weight == pytest.approx(0.25) for weight in result['weights'].values())
    assert result['direction']==0
    assert result['confidence']==0
    assert all(v['graded_vote_count'] > 0 for v in result['weight_pid_audit'].values())

@pytest.mark.parametrize("price_sign", [-1, 1])
def test_nonzero_price_movement_preserves_directional_grading(monkeypatch, price_sign):
    unit = studies.CompositeStudySignal()
    for name, vote in zip(('_ichimoku_vote', '_bollinger_vote', '_stochastic_vote', '_session_vwap_vote'), (1, 1, -1, -1)):
        monkeypatch.setattr(studies, name, lambda *a, vote=vote: vote)
    close = 100 + price_sign * np.arange(30) * 0.01
    bars = pd.DataFrame({'timestamp': pd.date_range('2023-12-05 09:15', periods=30, freq='min'), 'open': close, 'close': close, 'high': close + 0.02, 'low': close - 0.02, 'volume': 1000.})
    for i in range(len(bars)):
        result = unit.evaluate('TITAN', bars.iloc[:i + 1])
    assert result['hit_rates']['ichimoku'] == (1 if price_sign == 1 else 0)
    assert result['hit_rates']['stochastic'] == (0 if price_sign == 1 else 1)
