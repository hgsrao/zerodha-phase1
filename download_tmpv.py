import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from kiteconnect import KiteConnect

API_KEY = os.getenv("KITE_API_KEY", "f5qmn3ug0i6brql3")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "xnuhgHYK0Ko504EWCJjlz5CLCRqfg83t")

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

RAW_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\data\raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

print("[*] Fetching NSE instruments to locate TMPV...")
insts = kite.instruments("NSE")
tmpv_token = None
for inst in insts:
    sym = inst.get("tradingsymbol", "")
    if sym == "TMPV" or (sym == "TATAMOTORS" and inst.get("instrument_type") == "EQ"):
        tmpv_token = inst["instrument_token"]
        print(f"[+] Found {sym} (Token: {tmpv_token})")
        break

if not tmpv_token:
    # Fallback to general search if symbol string differs slightly
    matches = [i for i in insts if "MOTOR" in i.get("tradingsymbol", "") and i.get("instrument_type") == "EQ"]
    if matches:
        tmpv_token = matches[0]["instrument_token"]
        print(f"[+] Fallback using {matches[0]['tradingsymbol']} (Token: {tmpv_token})")
    else:
        print("[!] Symbol not found.")
        sys.exit(1)

to_date = datetime.now()
from_date = to_date - timedelta(days=365 * 5)
chunk_days = 60
current_from = from_date
all_candles = []

print(f"[→] Ingesting 5-year 1-min history for Token {tmpv_token}...")
while current_from < to_date:
    current_to = min(current_from + timedelta(days=chunk_days), to_date)
    c_from = current_from.strftime("%Y-%m-%d %H:%M:%S")
    c_to = current_to.strftime("%Y-%m-%d %H:%M:%S")
    try:
        recs = kite.historical_data(
            instrument_token=tmpv_token,
            from_date=c_from,
            to_date=c_to,
            interval="minute"
        )
        if recs:
            all_candles.extend(recs)
            print(f"  ✓ Chunk: {len(recs):>5} bars ({current_from.strftime('%Y-%m-%d')} to {current_to.strftime('%Y-%m-%d')})")
    except Exception as e:
        print(f"  [!] Chunk error: {e}")
    current_from = current_to + timedelta(minutes=1)
    time.sleep(0.35)

if all_candles:
    df = pd.DataFrame(all_candles)
    df["date"] = pd.to_datetime(df["date"])
    df.sort_values("date", inplace=True)
    df.drop_duplicates(subset=["date"], inplace=True)
    df.set_index("date", inplace=True)
    
    out_path = RAW_DIR / "TMPV_1min.parquet"
    df.to_parquet(out_path, engine="pyarrow", compression="snappy")
    print(f"\n[✓] TMPV COMPLETE: {len(df):,} bars saved -> {out_path}")
