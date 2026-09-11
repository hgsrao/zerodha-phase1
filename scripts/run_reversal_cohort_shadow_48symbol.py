#!/usr/bin/env python3
"""Compare chart-study reversal cohorts over one 48-symbol session."""
from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
import pandas as pd
import talib
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest,verify_manifest
from revision2_external.composite_study_signal import CompositeStudySignal
from revision2_external.reversal_cohort_shadow import ReversalCohortShadow

ROOT=Path(__file__).resolve().parents[1]; MANIFEST=ROOT/'revision2'/'DATASET_MANIFEST_48SYMBOL_1MIN.json'
def _day(f,d):
 s=pd.Timestamp(d);s=s.tz_localize(f.timestamp.dt.tz) if f.timestamp.dt.tz is not None else s
 return f[(f.timestamp>=s)&(f.timestamp<s+pd.DateOffset(days=1))].reset_index(drop=True)
def _symbol(sym,day,warmup):
 atr=talib.ATR(day.high.to_numpy(float),day.low.to_numpy(float),day.close.to_numpy(float),timeperiod=14); c=ReversalCohortShadow();studies=CompositeStudySignal()
 for i in range(warmup,len(day)):
  b=day.iloc[i];c.advance(sym,i,b.timestamp,b)
  if i<len(day)-1:
   signal=studies.evaluate(sym,day.iloc[:i+1]);a=float(atr[i]) if pd.notna(atr[i]) else max(float(b.high-b.low),.001);c.observe(sym,i,b.timestamp,b,day.iloc[:i+1],signal,a)
 c.finalize(sym,day.iloc[-1].timestamp,day.iloc[-1]);return c.summary(),c.funnel
def main():
 p=argparse.ArgumentParser();p.add_argument('--date',default='2023-09-01');p.add_argument('--warmup',type=int,default=64);p.add_argument('--output');a=p.parse_args()
 m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
 if not v.valid:raise RuntimeError(v.message)
 loader=MarketDataLoader(m.data_dir,synthetic_if_missing=False);symbols={};funnel=Counter()
 for n,r in enumerate(sorted(m.files,key=lambda x:x.symbol),1):
  d=_day(loader._load_symbol_csv(r.symbol),a.date);print(f'[LOAD {n:02d}/{len(m.files)}] {r.symbol}: {len(d)} bars',flush=True)
  if len(d)>a.warmup+1:
   summary,f=_symbol(r.symbol,d,a.warmup);symbols[r.symbol]=summary;funnel.update(f)
 cohorts={}
 for name in ReversalCohortShadow.names:
  rows=[s[name] for s in symbols.values()];resolved=sum(x['resolved'] for x in rows);targets=sum(x['target_before_stop'] for x in rows)
  cohorts[name]={"candidates":sum(x['setups'] for x in rows),"resolved":resolved,"target_before_stop":targets,"target_first_rate":targets/resolved if resolved else None,"gross_pnl_per_share":sum(x['gross_pnl_per_share'] for x in rows),"costs_per_share":sum(x['costs_per_share'] for x in rows),"net_pnl_per_share":sum(x['net_pnl_per_share'] for x in rows),"mean_required_break_even_probability":(sum(x['mean_required_break_even_probability']*x['resolved'] for x in rows if x['mean_required_break_even_probability'] is not None)/resolved if resolved else None)}
 art={"run_type":"48symbol_nested_reversal_cohort_shadow","date":a.date,"manifest_hash":m.manifest_hash,"cohort_definitions":{"studies":"stochastic agrees with directional close location","studies_price":"studies plus dP/dt sign reversal","studies_price_volume":"prior cohort plus expanding dV/dt and volume ratio >=1","studies_price_volume_phase":"prior cohort plus single-symbol phase coherence"},"funnel":dict(funnel),"cohorts":cohorts,"symbols":symbols,"note":"Identical next-bar fill, stop/target, and modeled costs for each cohort. Shadow research only; not portfolio P&L or an execution recommendation."}
 out=Path(a.output or ROOT/'diagnostic_output'/f'reversal_cohorts_48symbol_{a.date.replace("-","")}.json');out.write_text(json.dumps(art,indent=2,default=str));print(json.dumps({'output':str(out),'cohorts':cohorts},indent=2))
if __name__=='__main__':main()
