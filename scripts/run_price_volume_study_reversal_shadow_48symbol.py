#!/usr/bin/env python3
"""One-day 48-symbol dP/dt + dV/dt + phase + studies shadow audit."""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path
import pandas as pd
import talib
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.composite_study_signal import CompositeStudySignal
from revision2_external.price_volume_study_reversal_shadow import PriceVolumeStudyReversalShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

ROOT=Path(__file__).resolve().parents[1]; MANIFEST=ROOT/'revision2'/'DATASET_MANIFEST_48SYMBOL_1MIN.json'
def day_slice(frame,date):
    start=pd.Timestamp(date); start=start.tz_localize(frame.timestamp.dt.tz) if frame.timestamp.dt.tz is not None else start
    return frame[(frame.timestamp>=start)&(frame.timestamp<start+pd.DateOffset(days=1))].reset_index(drop=True)
def run_symbol(symbol,day,warmup):
    atr=talib.ATR(day.high.to_numpy(float),day.low.to_numpy(float),day.close.to_numpy(float),timeperiod=14); ledger=StudyEntryShadowLedger(); sensor=PriceVolumeStudyReversalShadow(ledger); studies=CompositeStudySignal()
    for i in range(warmup,len(day)):
        bar=day.iloc[i]; ledger.advance(symbol,i,bar.timestamp,bar)
        if i<len(day)-1:
            result=studies.evaluate(symbol,day.iloc[:i+1]); value=float(atr[i]) if pd.notna(atr[i]) else max(float(bar.high-bar.low),.001)
            sensor.observe(symbol,i,bar.timestamp,bar,day.iloc[:i+1],result,value)
    ledger.finalize(symbol,day.iloc[-1].timestamp,day.iloc[-1]); s=ledger.summary()
    return {"bars":len(day),"observations":s['observations'],"setups":s['setups'],"resolved":s['resolved'],"target_before_stop":s['target_before_stop'],"net_pnl_per_share":s['net_pnl_per_share'],"outcomes":s['outcomes']}
def main():
    p=argparse.ArgumentParser();p.add_argument('--date',default='2023-09-01');p.add_argument('--warmup',type=int,default=64);p.add_argument('--output');a=p.parse_args()
    m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
    if not v.valid: raise RuntimeError(v.message)
    loader=MarketDataLoader(m.data_dir,synthetic_if_missing=False); results={}
    for n,r in enumerate(sorted(m.files,key=lambda x:x.symbol),1):
        d=day_slice(loader._load_symbol_csv(r.symbol),a.date); print(f'[LOAD {n:02d}/{len(m.files)}] {r.symbol}: {len(d)} bars',flush=True)
        if len(d)>a.warmup+1: results[r.symbol]=run_symbol(r.symbol,d,a.warmup)
    totals=Counter()
    for row in results.values():
        for k in ('bars','observations','setups','resolved','target_before_stop'): totals[k]+=row[k]
    totals['net_pnl_per_share']=sum(row['net_pnl_per_share'] for row in results.values())
    artifact={'run_type':'48symbol_price_volume_phase_studies_reversal_shadow','date':a.date,'manifest_hash':m.manifest_hash,'inputs':{'price':'dP/dt normalized by ATR','volume':'dV/dt normalized by causal median volume','phase':'single-symbol Hilbert phase coherence','studies':'stochastic reversal vote plus full composite telemetry'},'symbols':results,'totals':dict(totals),'note':'Shadow-only. No order, position size, stop, target, gate, or PID gain was modified.'}
    out=Path(a.output or ROOT/'diagnostic_output'/f'price_volume_study_reversal_shadow_48symbol_{a.date.replace("-","")}.json');out.write_text(json.dumps(artifact,indent=2,default=str));print(json.dumps({'output':str(out),'totals':dict(totals)},indent=2))
if __name__=='__main__':main()
