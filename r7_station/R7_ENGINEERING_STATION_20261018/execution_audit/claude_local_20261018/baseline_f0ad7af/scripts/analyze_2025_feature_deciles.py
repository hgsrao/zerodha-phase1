#!/usr/bin/env python3
import argparse,json
from pathlib import Path
import pandas as pd
from revision2_external.frozen_decile_analysis import FEATURES,analyze
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument("--input",default=str(ROOT/"diagnostic_output"/"feature_discovery_2025_summary.rows.csv.gz"));p.add_argument("--output",default=str(ROOT/"diagnostic_output"/"feature_decile_analysis_2025.json"));a=p.parse_args()
 cols=["split","label","time_of_day",*FEATURES]
 frame=pd.read_csv(a.input,usecols=cols,compression="gzip")
 report={"input":a.input,"rows":int(len(frame)),**analyze(frame)}
 Path(a.output).write_text(json.dumps(report,indent=2));print(json.dumps({"output":a.output,"rows":len(frame)},indent=2))
if __name__=="__main__":main()
