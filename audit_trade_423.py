import pandas as pd

df = pd.read_csv('results/nautilus_positions.csv')
print("Total rows in file:", len(df))
print("\n--- Available Columns ---")
print(list(df.columns))

if 423 in df.index:
    row = df.loc[423]
    print("\n--- Trade #423 Details ---")
    for col, val in row.items():
        if pd.notna(val):
            # Check for nanosecond unix timestamps and convert to readable IST
            if isinstance(val, (int, float)) and val > 1e12:
                try:
                    dt = pd.to_datetime(val, unit='ns').tz_localize('UTC').tz_convert('Asia/Kolkata')
                    print(f"{col:25}: {val}  -->  {dt}")
                    continue
                except Exception:
                    pass
            print(f"{col:25}: {val}")
else:
    print("Index 423 not found. Showing worst loss trades instead:")
    pnl_col = [c for c in df.columns if 'realized_pnl' in c][0]
    print(df.sort_values(by=pnl_col).head(3))
