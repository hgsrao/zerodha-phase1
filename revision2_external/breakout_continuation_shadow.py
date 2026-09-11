"""One predeclared OHLCV breakout-continuation alpha, shadow-only.

This is intentionally narrow: it is a research hypothesis, not an optimizer.
It requires an intraday 20-bar breakout, directional EMA slope, session-VWAP
alignment and expanding volume, then uses the shared conservative next-bar
fill/cost/terminal-bar model.
"""
from __future__ import annotations
from typing import Any, Dict
import numpy as np
import pandas as pd
import talib
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

class BreakoutContinuationShadow:
    def __init__(self, ledger: StudyEntryShadowLedger) -> None: self.ledger=ledger
    def observe(self, symbol:str,index:int,timestamp:object,bar:Any,history:pd.DataFrame,atr:float)->None:
        if len(history)<25:return
        close=history.close.to_numpy(float);high=history.high.to_numpy(float);low=history.low.to_numpy(float);volume=history.volume.to_numpy(float)
        ema=talib.EMA(close,timeperiod=20);vol_ref=max(float(np.median(volume[-21:-1])),1e-12);vol_ratio=float(volume[-1]/vol_ref)
        vwap=float(np.sum(close*volume)/max(np.sum(volume),1e-12)); prior_high=float(np.max(high[-21:-1]));prior_low=float(np.min(low[-21:-1]))
        side=None
        if close[-1]>prior_high and close[-1]>vwap and ema[-1]>ema[-5] and vol_ratio>=1.2: side='BUY'
        elif close[-1]<prior_low and close[-1]<vwap and ema[-1]<ema[-5] and vol_ratio>=1.2: side='SELL'
        if side is None:return
        # Fill occurs only next bar; this setup chooses a one-ATR intended
        # stop geometry relative to the decision close, then records actual
        # next-open risk after conservative fill.
        scale=max(float(atr),1e-6); setup_extreme=float(close[-1]-.75*scale if side=='BUY' else close[-1]+.75*scale)
        obs={'timestamp':str(timestamp),'symbol':symbol,'index':index,'alpha':'breakout_continuation_v1','side':side,'close':float(close[-1]),'prior_20_high':prior_high,'prior_20_low':prior_low,'session_vwap':vwap,'ema20':float(ema[-1]),'volume_ratio':vol_ratio}
        self.ledger.observations.append(obs);self.ledger.schedule(symbol=symbol,index=index,side=side,setup_extreme=setup_extreme,atr=scale,observation=obs)
