import pandas as pd
from pathlib import Path

df = pd.read_csv('P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES/NSE_INFY_15minute_2023-08-14_2026-08-16.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')

print('Data Range:')
print(f'  First date: {df["timestamp"].min()}')
print(f'  Last date:  {df["timestamp"].max()}')
print(f'\n  Last 10 unique dates:')
for date in df['timestamp'].dt.date.unique()[-10:]:
    print(f'    {date}')
