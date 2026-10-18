"""Frozen train-decile analysis for causal discovery labels."""
from __future__ import annotations
from typing import Any, Dict
import numpy as np
import pandas as pd

FEATURES=("realized_vol_20","return_autocorr_20","body_fraction","close_location","gap_atr","range_atr","fib_impulse_atr","fib_nearest_distance_atr","fib_bars_since_confirmation")

def _stats(frame: pd.DataFrame, group: str) -> list[Dict[str,Any]]:
    out=[]
    for key, part in frame.groupby(["split",group], dropna=False, sort=True):
        split,bucket=key; targets=int((part.label=="TARGET_FIRST").sum()); stops=int((part.label=="STOP_FIRST").sum())
        out.append({"split":split,"bucket":str(bucket),"rows":int(len(part)),"target_first":targets,"stop_first":stops,"timeout":int((part.label=="TIMEOUT").sum()),"ambiguous":int((part.label=="INTRABAR_ORDER_UNKNOWN").sum()),"target_rate":targets/(targets+stops) if targets+stops else None})
    return out

def analyze(frame: pd.DataFrame) -> Dict[str,Any]:
    result={"method":"Train deciles frozen and applied unchanged to validation/test. No decile is selected as a trade rule.","features":{}}
    train=frame[frame.split=="train"]
    for feature in FEATURES:
        values=train[feature].dropna().to_numpy(float); edges=np.quantile(values,np.linspace(0,1,11))
        # Feature values with duplicate edges remain in the same deterministic
        # right-side bin rather than silently changing the number of buckets.
        working=frame[["split","label",feature]].dropna().copy()
        working["decile"]=np.searchsorted(edges[1:-1],working[feature].to_numpy(float),side="right")+1
        result["features"][feature]={"train_decile_edges":[float(x) for x in edges],"cohorts":_stats(working,"decile")}
    working=frame[["split","label","time_of_day"]].copy()
    t=working.time_of_day.astype(str)
    working["session_bucket"]=np.select([t<"10:15",(t>="10:15")&(t<"11:30"),(t>="11:30")&(t<"13:00"),(t>="13:00")&(t<"14:15"),t>="14:15"],["open_0915_1015","morning_1015_1130","midday_1130_1300","afternoon_1300_1415","close_1415_1530"],default="outside")
    result["time_of_day"]={"buckets":_stats(working,"session_bucket")}
    return result
