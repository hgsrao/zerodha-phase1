#!/usr/bin/env python3
"""Sealed 48-symbol train/validation/test shadow test for one fixed alpha."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
import talib
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest,verify_manifest
from revision2_external.breakout_continuation_shadow import BreakoutContinuationShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger
from revision2_external.sealed_research_evaluator import evaluate_fixed_alpha
from revision2_external.entry_path_diagnostic import diagnose
from revision2_external.feature_separability_diagnostic import diagnose as feature_diagnose

ROOT=Path(__file__).resolve().parents[1];MANIFEST=ROOT/'revision2'/'DATASET_MANIFEST_48SYMBOL_1MIN.json'
def _period(frame,start,end):
 tz=frame.timestamp.dt.tz;s=pd.Timestamp(start);e=pd.Timestamp(end)
 if tz is not None:s=s.tz_localize(tz);e=e.tz_localize(tz)
 return frame[(frame.timestamp>=s)&(frame.timestamp<e)].copy()
def _run(symbol,frame):
 resolved=[];observations=0
 for _,day in frame.groupby(frame.timestamp.dt.date,sort=True):
  day=day.reset_index(drop=True)
  if len(day)<65:continue
  close=day.close.to_numpy(float);volume=day.volume.to_numpy(float)
  atr=talib.ATR(day.high.to_numpy(float),day.low.to_numpy(float),close,timeperiod=14);ema=talib.EMA(close,timeperiod=20)
  prior_high=day.high.shift(1).rolling(20,min_periods=20).max().to_numpy(float);prior_low=day.low.shift(1).rolling(20,min_periods=20).min().to_numpy(float)
  prior_volume_median=day.volume.shift(1).rolling(20,min_periods=20).median().to_numpy(float);session_vwap=(day.close*day.volume).cumsum().div(day.volume.cumsum()).to_numpy(float)
  ledger=StudyEntryShadowLedger(max_hold_bars=30);alpha=BreakoutContinuationShadow(ledger)
  for i in range(60,len(day)):
   b=day.iloc[i];ledger.advance(symbol,i,b.timestamp,b)
   if i<len(day)-1:
    scale=float(atr[i]) if pd.notna(atr[i]) else max(float(b.high-b.low),.001)
    alpha.observe_precomputed(symbol,i,b.timestamp,b,scale,{'close':close[i],'prior_high':prior_high[i],'prior_low':prior_low[i],'session_vwap':session_vwap[i],'ema20':ema[i],'ema20_lag4':ema[i-4],'volume_ratio':volume[i]/max(prior_volume_median[i],1e-12)})
  ledger.finalize(symbol,day.iloc[-1].timestamp,day.iloc[-1]);resolved.extend(ledger.resolved);observations+=len(ledger.observations)
 return observations,resolved
def _summary(rows,observations):
 gross=sum(float(x['gross_pnl_per_share']) for x in rows);costs=sum(float(x['costs_per_share']) for x in rows);targets=sum(x['target_before_stop'] for x in rows)
 return {'observations':observations,'candidates':len(rows),'target_before_stop':targets,'target_first_rate':targets/len(rows) if rows else None,'gross_pnl_per_share':gross,'costs_per_share':costs,'net_pnl_per_share':gross-costs,'mean_required_break_even_probability':sum(float(x['required_break_even_probability']) for x in rows)/len(rows) if rows else None}
def main():
 p=argparse.ArgumentParser();p.add_argument('--train-start',default='2023-09-01');p.add_argument('--validation-start',default='2023-10-01');p.add_argument('--test-start',default='2023-11-01');p.add_argument('--end',default='2023-12-01');p.add_argument('--output');a=p.parse_args()
 windows={'train':(a.train_start,a.validation_start),'validation':(a.validation_start,a.test_start),'test':(a.test_start,a.end)};m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
 if not v.valid:raise RuntimeError(v.message)
 loader=MarketDataLoader(m.data_dir,synthetic_if_missing=False);all_rows={k:[] for k in windows};all_obs={k:0 for k in windows}
 for n,r in enumerate(sorted(m.files,key=lambda x:x.symbol),1):
  f=loader._load_symbol_csv(r.symbol);print(f'[LOAD {n:02d}/{len(m.files)}] {r.symbol}',flush=True)
  for name,(s,e) in windows.items():
   obs,rows=_run(r.symbol,_period(f,s,e));all_obs[name]+=obs;all_rows[name].extend(rows)
 results={k:_summary(all_rows[k],all_obs[k]) for k in windows}
 report={'run_type':'sealed_fixed_breakout_continuation_shadow','manifest_hash':m.manifest_hash,'windows':windows,'alpha_contract':'20-bar session breakout + EMA20 slope + session VWAP alignment + volume ratio >=1.2; no tuning','results':results,'path_diagnostics':{k:diagnose(all_rows[k]) for k in windows},'feature_separability':feature_diagnose(all_rows['train'],all_rows),'evaluation':evaluate_fixed_alpha(results),'note':'Shadow research only. The test window is not used to alter this hypothesis.'}
 out=Path(a.output or ROOT/'diagnostic_output'/'breakout_continuation_sealed_202309_202311.json');out.write_text(json.dumps(report,indent=2,default=str));print(json.dumps({'output':str(out),'results':report['results']},indent=2))
if __name__=='__main__':main()
