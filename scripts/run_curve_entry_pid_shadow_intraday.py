#!/usr/bin/env python3
"""Run SUNPHARMA curve-range input through an entry PID in shadow mode."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
import talib
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.curve_entry_pid_shadow import CurveEntryPidShadow
from revision2_external.curve_synchronizer_shadow import CurveSynchronizerShadow
from revision2_external.newton_phase_setpoint import NewtonPhaseSetpoints
from revision2_external.study_entry_shadow import StudyEntryShadowLedger

ROOT = Path(__file__).resolve().parents[1]; MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"
def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--symbol", default="SUNPHARMA"); p.add_argument("--date", default="2023-09-01")
    p.add_argument("--phase-center", type=float, default=0.0); p.add_argument("--phase-tolerance", type=float, default=10.0)
    p.add_argument("--relative-tolerance", type=float, default=.10); p.add_argument("--warmup", type=int, default=124); p.add_argument("--output"); p.add_argument("--adaptive-phase", action="store_true")
    a = p.parse_args(); manifest = DatasetManifest.load(str(MANIFEST)); checked = verify_manifest(manifest)
    if not checked.valid: raise RuntimeError(checked.message)
    data = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)._load_symbol_csv(a.symbol); tz=data.timestamp.dt.tz
    start=pd.Timestamp(a.date).tz_localize(tz) if tz is not None else pd.Timestamp(a.date)
    day=data[(data.timestamp>=start)&(data.timestamp<start+pd.DateOffset(days=1))].reset_index(drop=True)
    atr=talib.ATR(day.high.to_numpy(float),day.low.to_numpy(float),day.close.to_numpy(float),timeperiod=14)
    ledger=StudyEntryShadowLedger(); pid=CurveEntryPidShadow(a.phase_center,a.phase_tolerance,a.relative_tolerance); newton=NewtonPhaseSetpoints() if a.adaptive_phase else None
    sync=CurveSynchronizerShadow(ledger, phase_center_degrees=a.phase_center, phase_tolerance_degrees=a.phase_tolerance, entry_pid=pid, phase_setpoints=newton)
    for i in range(a.warmup,len(day)):
        bar=day.iloc[i]; ledger.advance(a.symbol,i,bar.timestamp,bar)
        if i<len(day)-1: sync.observe(a.symbol,i,bar.timestamp,bar,day.iloc[:i+1],float(atr[i]) if pd.notna(atr[i]) else max(float(bar.high-bar.low),.001))
    ledger.finalize(a.symbol,day.iloc[-1].timestamp,day.iloc[-1])
    obs=ledger.observations; ready=[x for x in obs if x.get('entry_pid',{}).get('entry_pid_ready')]; synchronized=[x for x in ready if x['entry_pid']['synchronized']]
    out=Path(a.output or ROOT/'diagnostic_output'/f'curve_entry_pid_shadow_{a.symbol}_{a.date.replace("-","")}_p{int(a.phase_tolerance)}.json')
    artifact={"run_type":"curve_synchronizer_entry_pid_shadow","symbol":a.symbol,"date":a.date,"manifest_hash":manifest.manifest_hash,"ranges":{"phase_center":a.phase_center,"phase_plus_minus":a.phase_tolerance,"voltage_plus_minus_pct":a.relative_tolerance*100,"frequency_plus_minus_pct":a.relative_tolerance*100},"adaptive_phase_newton":a.adaptive_phase,"curve_shadow":ledger.summary(),"entry_pid_summary":{"ready_bars":len(ready),"fully_synchronized_bars":len(synchronized)},"observations":obs,"note":"Shadow-only; entry PID multiplier is telemetry and cannot alter real orders or sizing."}
    out.write_text(json.dumps(artifact,indent=2,default=str)); print(json.dumps({"output":str(out),"ready_bars":len(ready),"synchronized_bars":len(synchronized),"setups":ledger.summary()['setups'],"net_pnl_per_share":ledger.summary()['net_pnl_per_share']},indent=2))
if __name__=='__main__': main()
