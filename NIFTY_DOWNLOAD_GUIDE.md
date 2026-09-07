# Download Nifty 50 Data from Zerodha (Professional)

## Step 1: Get Zerodha API Credentials

1. Go to https://kite.zerodha.com/settings/developer/tokens
2. Create a new token (if you don't have one)
3. Copy your **API Key** and **Access Token**

## Step 2: Set Environment Variables

```bash
export KITE_API_KEY="your_api_key_here"
export KITE_ACCESS_TOKEN="your_access_token_here"
```

Or pass via command line:

```bash
python3 scripts/download_nifty_from_zerodha.py \
  --api-key "your_api_key" \
  --access-token "your_access_token"
```

## Step 3: Run Download Script

```bash
cd /home/shrinivas/ECS_Project_external_engine

# Option A: Using environment variables
python3 scripts/download_nifty_from_zerodha.py

# Option B: Using command-line arguments
python3 scripts/download_nifty_from_zerodha.py \
  --api-key "YOUR_KEY" \
  --access-token "YOUR_TOKEN" \
  --start "2023-07-03" \
  --end "2026-08-24"
```

## Expected Output

```
════════════════════════════════════════════
🔄 ZERODHA NIFTY 50 & VIX DOWNLOAD
════════════════════════════════════════════

Connecting to Zerodha KiteConnect API...
✓ Connected as: Your Name

Downloading Nifty 50 (2023-07-03 to 2026-08-24, 60min candles)...
  Fetching data for instrument token: 256265
  ✓ Downloaded 750,000 Nifty 50 candles
  ✓ Downloaded 750,000 VIX candles

✓ Saved Nifty 50 to data/NSE_NIFTY50_minute.csv
✓ Saved VIX to data/NSE_INDIAVIX_minute.csv

SUCCESS: Data ready for grid synchronization
════════════════════════════════════════════
```

## Data Files

After download, you'll have:
- `data/NSE_NIFTY50_minute.csv` (Nifty 50 1-minute candles)
- `data/NSE_INDIAVIX_minute.csv` (India VIX 1-minute candles)

Both ready for Revision 3's MacroGridSynchronizer.

## Troubleshooting

### "Connection failed: Invalid API key"
- Verify API key is correct
- Check that KiteConnect token is fresh (valid for 6 months)
- Generate new token: https://kite.zerodha.com/settings/developer/tokens

### "No data retrieved"
- Instrument tokens may vary by broker setup
- Common tokens:
  - Nifty 50: 256265
  - India VIX: 256423
- Check Zerodha KiteConnect documentation for your setup

### "Access token not found"
- Provide credentials via:
  - Environment variables: `KITE_API_KEY` and `KITE_ACCESS_TOKEN`
  - Command-line args: `--api-key` and `--access-token`

## Next Steps

Once data is downloaded:
1. Run the 62-trade test with grid synchronization enabled
2. Measure impact on entry/exit decisions
3. Report P&L improvement
