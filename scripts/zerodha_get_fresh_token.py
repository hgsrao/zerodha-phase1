#!/usr/bin/env python3
"""
Get fresh Zerodha request_token

The request_token from earlier has expired (1-hour limit).
This script guides you through getting a NEW one.
"""

print('\n' + '='*160)
print('ZERODHA: GET FRESH REQUEST_TOKEN')
print('='*160 + '\n')

API_KEY = "f5qmn3ug0i6brql3"

print('[STEP 1] Click this link to login and get a fresh request_token:')
print()
print(f'https://kite.zerodha.com/connect/login?v=3&app_id={API_KEY}')
print()
print('[STEP 2] After login, you\'ll be redirected to:')
print()
print('http://127.0.0.1/?type=login&status=success&request_token=XXXXX...')
print()
print('[STEP 3] Copy the request_token from the URL (the XXXXX... part)')
print()
print('[STEP 4] Run this command with your fresh token:')
print()
print('export ZERODHA_REQUEST_TOKEN="your-fresh-token"')
print('export ZERODHA_API_SECRET="f26rzpez09ksp0fwpv8vgmkanitedgu"')
print('python3 scripts/download_nifty_zerodha_v2.py')
print()
print('='*160 + '\n')
