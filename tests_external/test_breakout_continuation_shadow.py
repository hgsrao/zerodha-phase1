import pandas as pd
from revision2_external.breakout_continuation_shadow import BreakoutContinuationShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

def test_breakout_shadow_queues_only_next_bar_fill():
 close=[100+i*.01 for i in range(24)]+[102.]
 bars=pd.DataFrame({'open':close,'high':[x+.1 for x in close],'low':[x-.1 for x in close],'close':close,'volume':[100]*24+[250]})
 ledger=StudyEntryShadowLedger();BreakoutContinuationShadow(ledger).observe('T',24,'t24',bars.iloc[-1],bars,1.)
 assert len(ledger._pending)==1 and not ledger.resolved
