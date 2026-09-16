import pandas as pd
import numpy as np

df = pd.read_parquet('alpha_48symbols_3years_master_ledger.parquet')

# 1. Identify and exclude toxic trend-persistent symbols
toxic_symbols = ['ADANIENT', 'RELIANCE', 'AXISBANK', 'SHRIRAMFIN', 'INFY']
filtered = df[~df['symbol'].isin(toxic_symbols)].copy()

print("=" * 70)
print(f"IMPACT OF PRUNING TOP 5 TOXIC ASSETS ACROSS 3-YEAR LEDGER")
print("=" * 70)
print(f"Original Trades  : {len(df):<6} | Net P&L: ₹{df['net_pnl'].sum():+12,.2f}")
print(f"Pruned Trades    : {len(filtered):<6} | Net P&L: ₹{filtered['net_pnl'].sum():+12,.2f}")
print(f"Capital Rescued  : ₹{filtered['net_pnl'].sum() - df['net_pnl'].sum():+12,.2f}")
print("-" * 70)
