import pandas as pd
import numpy as np

df = pd.read_csv('causal_funnel_results.csv')

print("=" * 75)
print(f"CAUSAL FUNNEL DECOMPOSITION MATRIX (N = {len(df)} Trades)")
print("=" * 75)

if len(df) == 0:
    print("No trades found in causal_funnel_results.csv.")
    exit()

# 1. Exit Reason Distribution
print("\n--- 1. EXIT REASON DISTRIBUTION ---")
summary = df.groupby('exit_reason').agg(
    count=('gross_r', 'count'),
    mean_gross_r=('gross_r', 'mean'),
    mean_net_r=('net_r', 'mean'),
    win_rate=('gross_r', lambda x: (x > 0).mean() * 100),
    avg_bars_held=('bars_held', 'mean')
).reset_index()

summary['pct_of_total'] = (summary['count'] / len(df)) * 100
for _, r in summary.iterrows():
    print(f"[{r['exit_reason']:<14}] Count: {int(r['count']):<5} ({r['pct_of_total']:>5.1f}%) | "
          f"Gross: {r['mean_gross_r']:>+6.3f}R | Net: {r['mean_net_r']:>+6.3f}R | "
          f"WinRate: {r['win_rate']:>5.1f}% | Bars: {r['avg_bars_held']:>4.1f}")

# 2. Stop-Out Path Diagnostic (Losers MFE check)
stop_outs = df[df['exit_reason'] == 'STOP_LOSS']
print("\n--- 2. STOP-OUT PATH DIAGNOSTIC (Premature Exits) ---")
if len(stop_outs) > 0:
    stopped_025 = (stop_outs['mfe_r'] >= 0.25).mean() * 100
    stopped_050 = (stop_outs['mfe_r'] >= 0.50).mean() * 100
    stopped_100 = (stop_outs['mfe_r'] >= 1.00).mean() * 100
    print(f"Stopped trades that reached +0.25R MFE before SL: {stopped_025:>5.1f}%")
    print(f"Stopped trades that reached +0.50R MFE before SL: {stopped_050:>5.1f}%")
    print(f"Stopped trades that reached +1.00R MFE before SL: {stopped_100:>5.1f}%")
    print(f"Avg MFE of stopped trades: {stop_outs['mfe_r'].mean():>5.3f}R")
else:
    print("No stop-outs observed.")

# 3. Overall Edge & Metrics
print("\n--- 3. PORTFOLIO & EDGE METRICS ---")
gross_expectancy = df['gross_r'].mean()
net_expectancy = df['net_r'].mean()
total_net_r = df['net_r'].sum()
overall_win_rate = (df['gross_r'] > 0).mean() * 100

wins = df[df['gross_r'] > 0]['gross_r']
losses = df[df['gross_r'] <= 0]['gross_r']
profit_factor = (wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else np.nan

print(f"Overall Win Rate     : {overall_win_rate:.2f}%")
print(f"Gross Expectancy (E) : {gross_expectancy:+.3f}R / trade")
print(f"Net Expectancy (E)   : {net_expectancy:+.3f}R / trade (after 0.18R drag)")
print(f"Profit Factor (PF)   : {profit_factor:.2f}")
print(f"Total Net Accumulated: {total_net_r:+.2f}R")

# 4. Excursion Statistics (MFE vs MAE)
print("\n--- 4. EXCURSION PROFILES (MFE vs MAE) ---")
print(f"Median MFE: {df['mfe_r'].median():.3f}R | Mean MFE: {df['mfe_r'].mean():.3f}R")
print(f"Median MAE: {df['mae_r'].median():.3f}R | Mean MAE: {df['mae_r'].mean():.3f}R")
print(f"Trades hitting +0.25R MFE: {(df['reached_025r']).mean() * 100:.1f}%")
print(f"Trades hitting +0.50R MFE: {(df['reached_050r']).mean() * 100:.1f}%")
print(f"Trades hitting +1.00R MFE: {(df['reached_100r']).mean() * 100:.1f}%")
print("=" * 75)
