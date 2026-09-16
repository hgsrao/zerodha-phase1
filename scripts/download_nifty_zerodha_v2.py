#!/usr/bin/env python3
"""
Download NIFTY 50 data from Zerodha (v2: with fresh token)

Requires:
  export ZERODHA_REQUEST_TOKEN="fresh-token-from-login"
  export ZERODHA_API_SECRET="f26rzpez09ksp0fwpv8vgmkanitedgu"
"""

import os
import hashlib
import requests
import pandas as pd
from datetime import datetime, timedelta

print('\n' + '='*160)
print('ZERODHA NIFTY 50 DOWNLOAD (v2)')
print('='*160 + '\n')

API_KEY = "f5qmn3ug0i6brql3"
REQUEST_TOKEN = os.environ.get('ZERODHA_REQUEST_TOKEN')
API_SECRET = os.environ.get('ZERODHA_API_SECRET')

if not REQUEST_TOKEN:
    print('✗ ZERODHA_REQUEST_TOKEN not set')
    print('  Run: export ZERODHA_REQUEST_TOKEN="your-fresh-token"')
    sys.exit(1)

if not API_SECRET:
    print('✗ ZERODHA_API_SECRET not set')
    print('  Run: export ZERODHA_API_SECRET="f26rzpez09ksp0fwpv8vgmkanitedgu"')
    sys.exit(1)

print(f'[1] Credentials loaded')
print(f'  API Key: {API_KEY}')
print(f'  Request Token: {REQUEST_TOKEN[:20]}...')
print(f'  API Secret: {API_SECRET[:20]}...\n')

# Generate checksum
checksum_input = f"{API_KEY}{REQUEST_TOKEN}{API_SECRET}"
checksum = hashlib.sha256(checksum_input.encode()).hexdigest()

print(f'[2] Exchanging request_token for access_token...\n')

auth_url = "https://api.kite.trade/session/token"
auth_payload = {
    "api_key": API_KEY,
    "request_token": REQUEST_TOKEN,
    "checksum": checksum
}

try:
    response = requests.post(auth_url, data=auth_payload, timeout=10)

    print(f'Status: {response.status_code}')
    print(f'Response: {response.text[:200]}\n')

    if response.status_code == 200:
        auth_result = response.json()

        if "data" in auth_result and "access_token" in auth_result["data"]:
            access_token = auth_result["data"]["access_token"]
            print(f'✓ Access token obtained: {access_token[:20]}...\n')

            # Download NIFTY data
            print('[3] Downloading NIFTY 50 historical data...\n')

            headers = {
                "Authorization": f"Bearer {API_KEY} {access_token}",
                "X-Kite-Version": "3"
            }

            # NIFTY 50 token
            instrument_token = 256265

            # Last 6 months
            end_date = datetime.now()
            start_date = end_date - timedelta(days=180)

            data_url = f"https://api.kite.trade/instruments/historical/{instrument_token}/minute"
            params = {
                "from": start_date.strftime("%Y-%m-%d"),
                "to": end_date.strftime("%Y-%m-%d"),
                "continuous": 0,
                "oi": 1
            }

            print(f'URL: {data_url}')
            print(f'Range: {start_date.date()} to {end_date.date()}\n')

            data_response = requests.get(data_url, headers=headers, params=params, timeout=30)

            if data_response.status_code == 200:
                data_result = data_response.json()

                if "data" in data_result and "candles" in data_result["data"]:
                    candles = data_result["data"]["candles"]
                    print(f'✓ Downloaded {len(candles)} candles\n')

                    # Save to CSV
                    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'])

                    os.makedirs('data', exist_ok=True)
                    output_path = 'data/NIFTY_50_real.csv'
                    df.to_csv(output_path, index=False)

                    print(f'Data saved to: {output_path}')
                    print(f'Rows: {len(df)}')
                    print(f'Date range: {df["timestamp"].min()} to {df["timestamp"].max()}')
                    print(f'Close range: ₹{df["close"].min():.2f} to ₹{df["close"].max():.2f}\n')

                    print('='*160)
                    print('✓ SUCCESS: Real NIFTY 50 data downloaded')
                    print('='*160 + '\n')
                else:
                    print(f'✗ No candle data: {data_result}')
            else:
                print(f'✗ Data download failed: {data_response.status_code}')
                print(f'  {data_response.text[:200]}')
        else:
            print(f'✗ Auth response missing access_token: {auth_result}')
    else:
        print(f'✗ Auth failed: {response.status_code}')
        print(f'  {response.text[:200]}')

except requests.exceptions.RequestException as e:
    print(f'✗ Request failed: {e}')

print('='*160 + '\n')
