"""One predeclared interaction holdout, selected before reading its test data."""
from __future__ import annotations
from typing import Any, Dict, Iterable

MORNING_START, MORNING_END = "10:15", "11:30"

def select_high_vol_morning(rows: Iterable[Dict[str, Any]], volatility_threshold: float) -> list[Dict[str, Any]]:
    return [r for r in rows if float(r["realized_vol_20"]) >= volatility_threshold and MORNING_START <= str(r["time_of_day"]) < MORNING_END]

def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    values=list(rows); target=sum(r["label"]=="TARGET_FIRST" for r in values); stop=sum(r["label"]=="STOP_FIRST" for r in values)
    return {"rows":len(values),"target_first":target,"stop_first":stop,"timeout":sum(r["label"]=="TIMEOUT" for r in values),"ambiguous":sum(r["label"]=="INTRABAR_ORDER_UNKNOWN" for r in values),"target_rate":target/(target+stop) if target+stop else None}
