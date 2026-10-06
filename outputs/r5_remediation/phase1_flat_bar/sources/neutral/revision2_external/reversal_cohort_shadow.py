"""Nested causal entry cohorts for testing what each reversal input adds."""
from __future__ import annotations

from typing import Any, Dict
import numpy as np
import pandas as pd
import talib

from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def _angle_delta(a: float, b: float) -> float:
    return float((a - b + 180.0) % 360.0 - 180.0)


class ReversalCohortShadow:
    """Four nested cohorts using identical execution/cost assumptions.

    studies → studies+price rate → +volume rate → +phase coherence.
    """
    names = ("studies", "studies_price", "studies_price_volume", "studies_price_volume_phase")

    def __init__(self) -> None:
        self.ledgers = {name: StudyEntryShadowLedger() for name in self.names}
        self.funnel = {name: 0 for name in self.names}

    def advance(self, symbol: str, index: int, timestamp: object, bar: Any) -> None:
        for ledger in self.ledgers.values(): ledger.advance(symbol, index, timestamp, bar)

    def observe(self, symbol: str, index: int, timestamp: object, bar: Any, history: pd.DataFrame, studies: Dict[str, Any], atr: float) -> None:
        close=history.close.to_numpy(float); high=history.high.to_numpy(float); low=history.low.to_numpy(float); volume=history.volume.to_numpy(float)
        if len(close) < 64: return
        scale=max(float(atr),1e-6); price_rate=(close[-1]-close[-2])/scale; prior_rate=(close[-2]-close[-3])/scale
        volume_ref=max(float(np.median(volume[-21:-1])),1e-12); volume_rate=(volume[-1]-volume[-2])/volume_ref; volume_ratio=volume[-1]/volume_ref
        bar_range=max(high[-1]-low[-1],1e-12); close_location=(close[-1]-low[-1])/bar_range
        phases=talib.HT_DCPHASE(close); phase=float(phases[-1]); prior_phase=float(phases[-2]); phase_velocity=_angle_delta(phase,prior_phase)
        phase_coherent=bool(np.isfinite(phase) and np.isfinite(prior_phase) and 1<=abs(phase_velocity)<=30)
        stoch=int(studies['votes'].get('stochastic',0)); side=None
        if stoch==1 and close_location>=.60: side='BUY'
        elif stoch==-1 and close_location<=.40: side='SELL'
        if side is None: return
        price_reversal=(side=='BUY' and prior_rate<=0<price_rate) or (side=='SELL' and prior_rate>=0>price_rate)
        volume_expanding=volume_rate>0 and volume_ratio>=1
        base={"timestamp":str(timestamp),"symbol":symbol,"index":index,"side":side,"dprice_dt_atr":float(price_rate),"prior_dprice_dt_atr":float(prior_rate),"dvolume_dt_relative":float(volume_rate),"volume_ratio":float(volume_ratio),"close_location":float(close_location),"phase_angle_degrees":phase if np.isfinite(phase) else None,"phase_velocity_degrees_per_bar":phase_velocity if np.isfinite(phase_velocity) else None,"phase_coherent":phase_coherent,"study_votes":dict(studies['votes']),"study_composite_direction":studies['direction'],"study_composite_confidence":studies['confidence']}
        passes={"studies":True,"studies_price":price_reversal,"studies_price_volume":price_reversal and volume_expanding,"studies_price_volume_phase":price_reversal and volume_expanding and phase_coherent}
        for name, passed in passes.items():
            if not passed: continue
            self.funnel[name]+=1
            observation={**base,"cohort":name}
            extreme=float(low[-1] if side=='BUY' else high[-1])
            self.ledgers[name].schedule(symbol=symbol,index=index,side=side,setup_extreme=extreme,atr=scale,observation=observation)

    def finalize(self, symbol: str, timestamp: object, bar: Any) -> None:
        for ledger in self.ledgers.values(): ledger.finalize(symbol,timestamp,bar)

    def summary(self) -> Dict[str, Any]:
        result={}
        for name, ledger in self.ledgers.items():
            s=ledger.summary(); rows=s['outcomes']; gross=sum(float(x['gross_pnl_per_share']) for x in rows); costs=sum(float(x['costs_per_share']) for x in rows)
            result[name]={**s,"gross_pnl_per_share":gross,"costs_per_share":costs,"target_first_rate":(s['target_before_stop']/s['resolved'] if s['resolved'] else None),"mean_required_break_even_probability":(sum(float(x['required_break_even_probability']) for x in rows)/len(rows) if rows else None)}
        return result
