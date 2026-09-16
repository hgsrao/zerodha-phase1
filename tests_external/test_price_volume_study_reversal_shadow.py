import numpy as np
import pandas as pd
from revision2_external.price_volume_study_reversal_shadow import PriceVolumeStudyReversalShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

def test_price_volume_study_sensor_only_queues_next_bar_candidate():
    x=np.linspace(0,8*np.pi,90); close=100+np.sin(x); close[-2]=99.;close[-1]=101.
    bars=pd.DataFrame({'open':close-.1,'high':close+.2,'low':close-.2,'close':close,'volume':[100.]*89+[250.]})
    ledger=StudyEntryShadowLedger(); studies={'direction':1,'confidence':.7,'votes':{'stochastic':1}}
    obs=PriceVolumeStudyReversalShadow(ledger).observe('TEST',89,'t89',bars.iloc[-1],bars,studies,.5)
    assert obs['ready'] is True
    assert 'dprice_dt_atr' in obs and 'dvolume_dt_relative' in obs
    assert not ledger.resolved
