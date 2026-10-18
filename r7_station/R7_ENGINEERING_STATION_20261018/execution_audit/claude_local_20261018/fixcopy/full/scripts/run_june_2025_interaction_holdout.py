#!/usr/bin/env python3
"""Untouched June 2025 holdout for one predeclared feature interaction."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest,verify_manifest
from revision2_external.interaction_holdout import select_high_vol_morning,summarize
from revision2_external.triple_barrier_discovery import extract_day
ROOT=Path(__file__).resolve().parents[1]; MANIFEST=ROOT/"revision2"/"DATASET_MANIFEST_48SYMBOL_1MIN.json"
def main():
 feature=json.loads((ROOT/"diagnostic_output"/"feature_decile_analysis_2025.json").read_text())
 threshold=float(feature["features"]["realized_vol_20"]["train_decile_edges"][9])
 m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
 if not v.valid:raise RuntimeError(v.message)
 loader=MarketDataLoader(m.data_dir,synthetic_if_missing=False);rows=[]
 for n,record in enumerate(sorted(m.files,key=lambda r:r.symbol),1):
  frame=loader._load_symbol_csv(record.symbol);tz=frame.timestamp.dt.tz;s=pd.Timestamp("2025-06-01");e=pd.Timestamp("2025-07-01")
  if tz is not None:s=s.tz_localize(tz);e=e.tz_localize(tz)
  part=frame[(frame.timestamp>=s)&(frame.timestamp<e)];print(f"[LOAD {n:02d}/{len(m.files)}] {record.symbol}",flush=True)
  for _,day in part.groupby(part.timestamp.dt.date,sort=True):rows.extend(extract_day(record.symbol,day))
 chosen=select_high_vol_morning(rows,threshold)
 report={"run_type":"untouched_june_2025_predeclared_interaction_holdout","selection":"realized_vol_20 >= January-March 2025 train 90th percentile AND time 10:15-11:30; interaction nominated from train+April validation only","volatility_threshold":threshold,"june_all_rows":summarize(rows),"june_selected_rows":summarize(chosen)}
 out=ROOT/"diagnostic_output"/"june_2025_highvol_morning_holdout.json";out.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=="__main__":main()
