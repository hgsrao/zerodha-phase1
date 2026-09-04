#!/usr/bin/env python3
"""Run 48-symbol test with timestamp alignment and real signal"""

import json
from timestamp_aligned_backtest import TimestampAlignedBacktest

symbols_48 = [
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK',
    'BAJAJ-AUTO', 'BAJAJFINSV', 'BAJFINANCE', 'BEL', 'BHARTIARTL',
    'CIPLA', 'COALINDIA', 'DRREDDY', 'EICHERMOT', 'ETERNAL',
    'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE', 'HINDALCO',
    'HINDUNILVR', 'ICICIBANK', 'INDIGO', 'INFY', 'ITC',
    'JIOFIN', 'JSWSTEEL', 'KOTAKBANK', 'LT', 'M&M',
    'MARUTI', 'MAXHEALTH', 'NTPC', 'ONGC', 'POWERGRID',
    'RELIANCE', 'SBILIFE', 'SBIN', 'SHRIRAMFIN', 'SUNPHARMA',
    'TATACONSUM', 'TATASTEEL', 'TCS', 'TECHM', 'TITAN',
    'TRENT', 'ULTRACEMCO', 'WIPRO'
]

print(f"\n{'='*90}")
print(f"48-SYMBOL TIMESTAMP-ALIGNED TEST")
print(f"Real signal, all 48 NIFTY equities")
print(f"{'='*90}\n")

test = TimestampAlignedBacktest()
results = test.run(symbols_48)

with open("TIMESTAMP_ALIGNED_48SYMBOL_RESULTS.json", "w") as f:
    json.dump(results, f, indent=2, default=str)

print(f"[OK] Results saved to TIMESTAMP_ALIGNED_48SYMBOL_RESULTS.json")
