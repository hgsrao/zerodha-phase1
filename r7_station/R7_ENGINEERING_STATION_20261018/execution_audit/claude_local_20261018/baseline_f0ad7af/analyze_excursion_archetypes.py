import json
import glob
import numpy as np
from pathlib import Path

# Look for files in either directory
files = glob.glob('diagnostic_output/macro_enriched_months/*.json')
if not files:
    files = glob.glob('diagnostic_output/*.json')

if not files:
    print("No monthly files found. Please ensure the sweep has completed.")
    exit(1)

winners_mfe = []
winners_mae = []
losers = []
rejects, stalls, heartbreakers = 0, 0, 0

print(f"Parsing {len(files)} files for Path-Aware Excursion Telemetry...")

for f in files:
    try:
        report = json.loads(Path(f).read_text())
    except Exception:
        continue
        
    telemetry = report.get('controller_telemetry', [])
    outcomes = [x for x in telemetry if x.get('event_type') == 'CONTROLLER_OUTCOME']
    
    for t in outcomes:
        mfe = t.get('mfe_r')
        mae = t.get('mae_r')
        pnl = t.get('net_pnl', 0)
        
        # Skip if telemetry didn't register (e.g. 0-bar holds or missing fields)
        if mfe is None or mae is None:
            continue
            
        if pnl > 0:
            winners_mfe.append(mfe)
            winners_mae.append(mae)
        else:
            losers.append({'mfe': mfe, 'mae': mae, 'bars': t.get('bars_held', 0)})
            
            if mfe < 0.25:
                rejects += 1
            elif 0.25 <= mfe < 1.0:
                stalls += 1
            else:
                heartbreakers += 1

total_losers = len(losers)

print("\n=== LOSING TRADE ARCHETYPES ===")
if total_losers > 0:
    print(f"Total Valid Losers Analyzed : {total_losers}")
    print(f"1. Immediate Rejections (MFE < +0.25R) : {rejects} ({rejects/total_losers*100:.1f}%)")
    print(f"2. Stalled Bleeds (MFE +0.25R to +1.0R): {stalls} ({stalls/total_losers*100:.1f}%)")
    print(f"3. Heartbreakers (MFE >= +1.0R)        : {heartbreakers} ({heartbreakers/total_losers*100:.1f}%)")
else:
    print("No valid losing trades with excursion data found.")

print("\n=== WINNING TRADE HEAT ===")
if winners_mae:
    print(f"Average Heat Taken (MAE) before Target: {np.mean(winners_mae):.2f}R")
    deep_heat = len([x for x in winners_mae if x < -0.75])
    print(f"Winners taking deep heat (< -0.75R)   : {deep_heat} ({deep_heat/len(winners_mae)*100:.1f}%)")
else:
    print("No winning trades with excursion data found.")
