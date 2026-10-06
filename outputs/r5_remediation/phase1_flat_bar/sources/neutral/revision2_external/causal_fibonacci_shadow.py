"""Causally confirmed swing/impulse Fibonacci context, shadow-only."""
from __future__ import annotations
from typing import Dict
import numpy as np

LEFT_BARS, CONFIRM_BARS, MIN_IMPULSE_ATR = 20, 5, 2.0

def features(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray) -> list[Dict[str,float]]:
    """Features available at each bar; a pivot appears only after confirmation."""
    out=[{} for _ in range(len(close))]; last=None; active=None
    for i in range(len(close)):
        pivot_i=i-CONFIRM_BARS
        if pivot_i>=LEFT_BARS:
            a,b=pivot_i-LEFT_BARS,pivot_i+CONFIRM_BARS+1
            is_high=high[pivot_i]>=np.max(high[a:b]); is_low=low[pivot_i]<=np.min(low[a:b])
            if is_high or is_low:
                kind="H" if is_high else "L"; price=float(high[pivot_i] if kind=="H" else low[pivot_i])
                if last and last["kind"]!=kind and np.isfinite(atr[pivot_i]) and abs(price-last["price"])>=MIN_IMPULSE_ATR*float(atr[pivot_i]):
                    active={"direction":1.0 if last["kind"]=="L" else -1.0,"low":min(price,last["price"]),"high":max(price,last["price"]),"confirmed":i}
                last={"kind":kind,"price":price,"index":pivot_i}
        if active and np.isfinite(atr[i]):
            span=max(active["high"]-active["low"],1e-12); scale=max(float(atr[i]),1e-12)
            # 38.2/50/61.8 retracement prices measured from the impulse end.
            end=active["high"] if active["direction"]>0 else active["low"]
            sign=-1.0 if active["direction"]>0 else 1.0
            levels=[end+sign*span*r for r in (.382,.5,.618)]
            out[i]={"fib_direction":active["direction"],"fib_impulse_atr":span/scale,"fib_382_distance_atr":(close[i]-levels[0])/scale,"fib_500_distance_atr":(close[i]-levels[1])/scale,"fib_618_distance_atr":(close[i]-levels[2])/scale,"fib_nearest_distance_atr":min(abs(close[i]-x) for x in levels)/scale,"fib_bars_since_confirmation":float(i-active["confirmed"])}
    return out
