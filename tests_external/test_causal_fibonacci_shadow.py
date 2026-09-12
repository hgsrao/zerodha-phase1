import numpy as np
from revision2_external.causal_fibonacci_shadow import features

def test_swing_is_not_available_until_confirmation_bars_have_closed():
    high=np.array([1.,2.,3.,2.,1.,1.,1.,1.,1.]);low=high-.5;close=high-.2;atr=np.ones(len(high))
    rows=features(high,low,close,atr)
    assert not rows[2]
    assert not rows[7] or rows[7]["fib_bars_since_confirmation"]==0.0
