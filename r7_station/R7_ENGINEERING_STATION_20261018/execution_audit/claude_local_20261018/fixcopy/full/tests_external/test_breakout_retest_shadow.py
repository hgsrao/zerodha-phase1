from revision2_external.breakout_retest_shadow import BreakoutRetestShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def _metrics(close=101.):
 return {'close':close,'prior_high':100.,'prior_low':90.,'session_vwap':99.,'ema20':101.,'ema20_lag4':100.,'volume_ratio':1.3}


def test_retest_waits_for_later_completed_bar_before_scheduling_entry():
 ledger=StudyEntryShadowLedger();alpha=BreakoutRetestShadow(ledger)
 alpha.observe_precomputed('X',60,'t60',{'high':101.2,'low':100.8,'close':101.},1.,_metrics())
 assert not ledger._pending
 alpha.observe_precomputed('X',61,'t61',{'high':101.2,'low':99.8,'close':100.7},1.,_metrics(100.7))
 assert len(ledger._pending)==1
 assert ledger._pending[0].fill_index==62


def test_retest_expiring_without_confirmation_never_schedules_entry():
 ledger=StudyEntryShadowLedger();alpha=BreakoutRetestShadow(ledger,retest_bars=1)
 alpha.observe_precomputed('X',60,'t60',{'high':101.2,'low':100.8,'close':101.},1.,_metrics())
 alpha.observe_precomputed('X',62,'t62',{'high':102.,'low':101.,'close':101.5},1.,_metrics(101.5))
 assert not ledger._pending
