#!/usr/bin/env python3
"""Manifest-verified 2025 train/validation/test triple-barrier discovery."""
from __future__ import annotations
import argparse,csv,gzip,json
from pathlib import Path
import pandas as pd
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest,verify_manifest
from revision2_external.triple_barrier_discovery import extract_day,summarize
ROOT=Path(__file__).resolve().parents[1]; MANIFEST=ROOT/"revision2"/"DATASET_MANIFEST_48SYMBOL_1MIN.json"
WINDOWS={"train":("2025-01-01","2025-04-01"),"validation":("2025-04-01","2025-05-01"),"test":("2025-05-01","2025-06-01")}
def main():
 p=argparse.ArgumentParser();p.add_argument("--output",default=str(ROOT/"diagnostic_output"/"feature_discovery_2025_summary.json"));p.add_argument("--emit-rows",action="store_true");a=p.parse_args()
 m=DatasetManifest.load(str(MANIFEST)); verified=verify_manifest(m)
 if not verified.valid: raise RuntimeError(verified.message)
 loader=MarketDataLoader(m.data_dir,synthetic_if_missing=False); all_rows={k:[] for k in WINDOWS}
 for n,record in enumerate(sorted(m.files,key=lambda r:r.symbol),1):
  frame=loader._load_symbol_csv(record.symbol); print(f"[LOAD {n:02d}/{len(m.files)}] {record.symbol}",flush=True)
  for name,(start,end) in WINDOWS.items():
   tz=frame.timestamp.dt.tz;s=pd.Timestamp(start);e=pd.Timestamp(end)
   if tz is not None:s=s.tz_localize(tz);e=e.tz_localize(tz)
   part=frame[(frame.timestamp>=s)&(frame.timestamp<e)]
   for _,day in part.groupby(part.timestamp.dt.date,sort=True): all_rows[name].extend(extract_day(record.symbol,day))
 report={"run_type":"causal_2025_feature_discovery","manifest_hash":m.manifest_hash,"windows":WINDOWS,"label_contract":"Two direction-specific labels per completed bar; next-bar conservative entry; barrier=max(ATR14,2x round-trip cost); 30-bar horizon; ambiguous terminal bars excluded.","results":{k:summarize(v) for k,v in all_rows.items()}}
 if a.emit_rows:
  path=Path(a.output).with_suffix(".rows.csv.gz"); path.parent.mkdir(parents=True,exist_ok=True)
  with gzip.open(path,"wt",newline="") as f:
   w=csv.DictWriter(f,fieldnames=["split",*all_rows["train"][0].keys()]);w.writeheader()
   for split,rows in all_rows.items():
    for row in rows:w.writerow({"split":split,**row})
  report["rows_file"]=str(path)
 out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=="__main__":main()
