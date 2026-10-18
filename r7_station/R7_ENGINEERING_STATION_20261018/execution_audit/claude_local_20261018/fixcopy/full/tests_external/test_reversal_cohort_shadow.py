import numpy as np
import pandas as pd
from revision2_external.reversal_cohort_shadow import ReversalCohortShadow

def test_nested_cohorts_never_expand_after_a_gate():
 x=np.linspace(0,8*np.pi,90);close=100+np.sin(x);close[-2]=99;close[-1]=101
 bars=pd.DataFrame({'open':close-.1,'high':close+.2,'low':close-.2,'close':close,'volume':[100.]*89+[250.]})
 c=ReversalCohortShadow();c.observe('T',89,'t',bars.iloc[-1],bars,{'votes':{'stochastic':1},'direction':1,'confidence':.6},.5)
 f=c.funnel
 assert f['studies']>=f['studies_price']>=f['studies_price_volume']>=f['studies_price_volume_phase']
