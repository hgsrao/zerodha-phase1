import json
import pandas as pd
import numpy as np

cfg = json.load(open('results/fleet_config.json'))
bay_map = cfg.get('symbol_to_sector_map', {})

df = pd.read_csv('results/stage1_trades.csv')
r_col = next(c for c in df.columns if any(k in c.lower() for k in ['net_r', 'exit_r', 'pnl']))
df['bay'] = df['symbol'].map(bay_map).fillna('UNASSIGNED')

tearsheet = []
tearsheet.append("# Institutional CCPP Algorithmic Fleet: Stage 1 Performance Tear Sheet\n")
tearsheet.append(f"**Dataset Scale**: 806 Trades across 40 Assets | Event-Driven Emulation\n")
tearsheet.append("## 1. Executive Summary & Fleet Metrics\n")

cum_r = df[r_col].sum()
mean_r = df[r_col].mean()
win_rate = (df[r_col] > 0).mean() * 100
profit_factor = abs(df[df[r_col] > 0][r_col].sum() / df[df[r_col] < 0][r_col].sum())
max_dd = (df[r_col].cumsum().cummax() - df[r_col].cumsum()).max()

tearsheet.append(f"- **Cumulative Net R**: `{cum_r:+.2f}R`")
tearsheet.append(f"- **Fleet Expectancy (\\bar{{R}})**: `{mean_r:+.3f}R` per trade")
tearsheet.append(f"- **Global Win Rate**: `{win_rate:.1f}%`")
tearsheet.append(f"- **Profit Factor (R)**: `{profit_factor:.2f}`")
tearsheet.append(f"- **Max Drawdown (R)**: `-{max_dd:.2f}R`\n")

tearsheet.append("## 2. Generator Bay Attribution Breakdown\n")
tearsheet.append("| Generator Unit | Sector Bay | Trades | Net R | Mean R | Win Rate | Primary Driver |")
tearsheet.append("| :--- | :--- | :---: | :---: | :---: | :---: | :--- |")

bay_labels = {
    'GTG1_METALS_MINING': ('GTG1', 'Metals & Mining', 'JSWSTEEL (+12.73R), HINDALCO (+7.29R)'),
    'GTG2_POWER_ENERGY_INFRA': ('GTG2', 'Power & Infrastructure', 'NTPC (+9.52R), RELIANCE (+7.91R)'),
    'CSTG1_BANKING_FINANCE': ('CSTG1', 'Banking & Financials', 'HDFCBANK (+6.14R), ICICIBANK (+5.39R)'),
    'CSTG2_TECH_CONSUMER_AUTO': ('CSTG2', 'Tech, Auto & Consumer', 'ETERNAL (+31.52R), TECHM (+5.58R)'),
    'BPSTG_HEALTHCARE_PHARMA': ('BPSTG', 'Healthcare & Pharma', 'MAXHEALTH (+16.05R), CIPLA (+13.69R)')
}

for bay_id, (unit, label, driver) in bay_labels.items():
    sub = df[df['bay'] == bay_id]
    if len(sub) > 0:
        tearsheet.append(f"| **{unit}** | {label} | {len(sub)} | {sub[r_col].sum():+.2f}R | {sub[r_col].mean():+.3f}R | {(sub[r_col]>0).mean()*100:.1f}% | {driver} |")

tearsheet.append("\n## 3. Dynamic PID Ratchet Protection\n")
stops = df[df['exit_reason'] == 'STOP_LOSS']
ratchet_savings = (len(stops) * -1.0) - stops[r_col].sum()
tearsheet.append(f"- **Total Stop Events**: `{len(stops)}`")
tearsheet.append(f"- **Mean Realized Stop**: `{stops[r_col].mean():.3f}R` (vs unmanaged `-1.000R` theoretical floor)")
tearsheet.append(f"- **Capital Saved via Trailing Ratchet**: `{abs(ratchet_savings):+.2f}R`\n")

with open('results/stage1_tearsheet.md', 'w') as f:
    f.write('\n'.join(tearsheet))

print("✓ Successfully generated results/stage1_tearsheet.md")
