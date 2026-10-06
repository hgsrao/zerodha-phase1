"""Causal OHLCV feature extraction and cost-aware triple-barrier labels."""
from __future__ import annotations
from typing import Any, Dict, List
import numpy as np
import pandas as pd
import talib
from revision2_external.study_entry_shadow import StudyEntryShadowLedger
from revision2_external.causal_fibonacci_shadow import features as fibonacci_features

HORIZON_BARS = 30

def _label(entry: float, barrier: float, side: str, future: pd.DataFrame) -> str:
    upper, lower = (entry + barrier, entry - barrier)
    for _, bar in future.iterrows():
        up, down = float(bar.high) >= upper, float(bar.low) <= lower
        if up and down:
            return "INTRABAR_ORDER_UNKNOWN"
        hit_target = up if side == "BUY" else down
        hit_stop = down if side == "BUY" else up
        if hit_target: return "TARGET_FIRST"
        if hit_stop: return "STOP_FIRST"
    return "TIMEOUT"

def extract_day(symbol: str, day: pd.DataFrame) -> List[Dict[str, Any]]:
    """Two directional research labels per completed eligible bar; no orders."""
    d = day.reset_index(drop=True)
    if len(d) < 62: return []
    close=d.close.to_numpy(float); high=d.high.to_numpy(float); low=d.low.to_numpy(float); volume=d.volume.to_numpy(float)
    atr=talib.ATR(high,low,close,timeperiod=14)
    fib=fibonacci_features(high,low,close,atr)
    ret=np.zeros(len(d)); ret[1:]=np.diff(np.log(close))
    rv=pd.Series(ret).rolling(20,min_periods=20).std().to_numpy(float)
    autocorr=pd.Series(ret).rolling(20,min_periods=20).apply(lambda x: x.autocorr(lag=1),raw=False).to_numpy(float)
    prior_close=np.r_[np.nan,close[:-1]]
    rows=[]
    for i in range(60,len(d)-HORIZON_BARS-1):
        if not np.isfinite(atr[i]) or not np.isfinite(rv[i]) or not np.isfinite(autocorr[i]): continue
        b=d.iloc[i]; next_open=float(d.iloc[i+1].open); scale=max(float(atr[i]),1e-9)
        rng=max(float(b.high-b.low),1e-9); body=(float(b.close-b.open))/rng; close_loc=(float(b.close-b.low))/rng
        gap=(float(b.open)-float(prior_close[i]))/scale if np.isfinite(prior_close[i]) else 0.0
        fib_context={"fib_direction":np.nan,"fib_impulse_atr":np.nan,"fib_382_distance_atr":np.nan,"fib_500_distance_atr":np.nan,"fib_618_distance_atr":np.nan,"fib_nearest_distance_atr":np.nan,"fib_bars_since_confirmation":np.nan};fib_context.update(fib[i])
        base={"timestamp":str(b.timestamp),"symbol":symbol,"bar_index":i,"atr14":scale,"realized_vol_20":float(rv[i]),"return_autocorr_20":float(autocorr[i]),"body_fraction":body,"close_location":close_loc,"gap_atr":gap,"range_atr":rng/scale,"time_of_day":str(b.timestamp)[11:16],**fib_context}
        future=d.iloc[i+2:i+2+HORIZON_BARS]
        for side,entry in (("BUY",StudyEntryShadowLedger._fill_price(next_open,"BUY")),("SELL",StudyEntryShadowLedger._fill_price(next_open,"SELL"))):
            cost=StudyEntryShadowLedger._leg_cost(entry,side)+StudyEntryShadowLedger._leg_cost(entry,"SELL" if side=="BUY" else "BUY")
            barrier=max(scale,2.0*cost)
            rows.append({**base,"side":side,"entry_reference":entry,"round_trip_cost":cost,"barrier_price":barrier,"label":_label(entry,barrier,side,future)})
    return rows

def summarize(rows: List[Dict[str,Any]]) -> Dict[str,Any]:
    labels={}
    for row in rows: labels[row["label"]]=labels.get(row["label"],0)+1
    eligible=labels.get("TARGET_FIRST",0)+labels.get("STOP_FIRST",0)
    return {"rows":len(rows),"labels":labels,"directional_target_rate":labels.get("TARGET_FIRST",0)/eligible if eligible else None}
