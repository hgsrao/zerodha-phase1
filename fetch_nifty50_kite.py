import os
import sys
import time
import datetime
import pandas as pd
from kiteconnect import KiteConnect

API_KEY = os.getenv("KITE_API_KEY", "")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")

if not API_KEY or not ACCESS_TOKEN:
    print("[ERROR] KITE_API_KEY and KITE_ACCESS_TOKEN environment variables must be set.")
    print("Usage: KITE_API_KEY='xxx' KITE_ACCESS_TOKEN='yyy' python3 fetch_nifty50_kite.py")
    sys.exit(1)

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

NIFTY_TOKEN = 256265  # NSE:NIFTY 50

start_date = datetime.date(2023, 7, 1)
end_date = datetime.date(2026, 8, 24)

print(f"Fetching NIFTY 50 1-min historical bars ({start_date} to {end_date})...")

current_start = start_date
all_records = []

while current_start < end_date:
    current_end = min(current_start + datetime.timedelta(days=59), end_date)
    print(f"  Fetching: {current_start} -> {current_end}...", end='\r')
    
    try:
        data = kite.historical_data(
            instrument_token=NIFTY_TOKEN,
            from_date=current_start,
            to_date=current_end,
            interval="minute"
        )
        if data:
            all_records.extend(data)
    except Exception as e:
        print(f"\n[WARN] Failed block {current_start} to {current_end}: {e}")
        time.sleep(1)
    
    current_start = current_end + datetime.timedelta(days=1)
    time.sleep(0.4)

if all_records:
    df = pd.DataFrame(all_records)
    out_file = "NIFTY_50_1min_true.csv"
    df.to_csv(out_file, index=False)
    print(f"\n[SUCCESS] Saved {len(df):,} NIFTY 50 1-min bars to {out_file}")
else:
    print("\n[ERROR] No data returned from Kite Connect.")
