import pandas as pd
from revision2_external.breakout_continuation_shadow import BreakoutContinuationShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

def test_breakout_shadow_queues_only_next_bar_fill():
 close=[100+i*.01 for i in range(24)]+[102.]
 bars=pd.DataFrame({'open':close,'high':[x+.1 for x in close],'low':[x-.1 for x in close],'close':close,'volume':[100]*24+[250]})
 ledger=StudyEntryShadowLedger();BreakoutContinuationShadow(ledger).observe('T',24,'t24',bars.iloc[-1],bars,1.)
 assert len(ledger._pending)==1 and not ledger.resolved

def test_precomputed_breakout_queues_only_next_bar_fill():
 ledger=StudyEntryShadowLedger();alpha=BreakoutContinuationShadow(ledger)
 alpha.observe_precomputed('T',24,'t24',{'close':102.},1.,{'close':102.,'prior_high':101.,'prior_low':99.,'session_vwap':101.,'ema20':101.,'ema20_lag4':100.,'volume_ratio':1.3})
 assert len(ledger._pending)==1 and ledger._pending[0].fill_index==25
