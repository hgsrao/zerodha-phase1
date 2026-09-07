# Download REAL Nifty 50 Data - Step by Step

## What You'll Get

✅ **Real Nifty 50** 1-minute candles (3 years: 2023-07-03 to 2026-08-24)  
✅ **Real India VIX** data (if available)  
✅ Files saved in `data/NSE_NIFTY50_REAL_minute.csv` and `data/NSE_INDIAVIX_REAL_minute.csv`

---

## Prerequisites

You already have:
- ✅ Zerodha account
- ✅ API Key: `f5qmn3ug0i6brql3`
- ✅ API Secret: `f26rzpezo9ksp0fwpv8vgmkanitedgu`
- ✅ Client ID: `CE2003`

---

## Step 1: Run the Download Script

```bash
cd /home/shrinivas/ECS_Project_external_engine
python3 scripts/download_real_nifty_zerodha.py
```

You'll see:

```
====================================================================================================
DOWNLOADING REAL NIFTY 50 DATA FROM ZERODHA
====================================================================================================

API Key: f5qmn3ug0i6brql3
Client ID: CE2003

[1] Initializing KiteConnect...
✓ Login URL generated

[2] MANUAL LOGIN REQUIRED
────────────────────────────────────────────────────────────────────────────────────────────────────
1. Open this URL in your browser:
   https://kite.zerodha.com/?api_key=f5qmn3ug0i6brql3&...

2. Login with your Zerodha credentials
3. You will be redirected to a page with request_token
4. Copy the request_token and paste it below

────────────────────────────────────────────────────────────────────────────────────────────────────

Enter request_token (from redirect URL):
```

---

## Step 2: Login & Get Request Token

1. **Copy the login URL** from the terminal (starts with `https://kite.zerodha.com/?api_key=...`)

2. **Open it in your browser** (Chrome, Firefox, etc.)

3. **Login with your Zerodha credentials** (username + password + 2FA)

4. **You'll be redirected** to a page like:
   ```
   http://127.0.0.1/?request_token=ABCD1234EF5G6H7I8J&status=success
   ```

5. **Copy the request_token value** (the part after `request_token=` and before `&status`)
   - Example: `ABCD1234EF5G6H7I8J`

---

## Step 3: Paste Request Token

Back in your terminal, paste the request token:

```
Enter request_token (from redirect URL): ABCD1234EF5G6H7I8J
```

Press Enter.

---

## Step 4: Watch the Download

```
[3] Exchanging request_token for access_token...
✓ Access token obtained: abc123def456ghi789...

[4] Verifying connection...
✓ Connected as: Your Name

[5] Downloading Nifty 50 data...
    (This may take 5-10 minutes for 3 years of data)

  [1] 2023-07-03 to 2023-09-01... ✓ (14,400 bars)
  [2] 2023-09-01 to 2023-10-30... ✓ (14,400 bars)
  [3] 2023-10-30 to 2023-12-29... ✓ (14,400 bars)
  ...
  ✓ Downloaded 750,000 Nifty 50 candles

[6] Saved Nifty 50
    Path: data/NSE_NIFTY50_REAL_minute.csv
    Rows: 750,000
    Date range: 2023-07-03 to 2026-08-24
    Price range: ₹16,500.00 - ₹25,200.00

[7] Downloading India VIX data...
  [1] 2023-07-03 to 2023-09-01... ✓ (14,400 bars)
  ...
  ✓ Downloaded 750,000 VIX candles

[8] Saved India VIX
    Path: data/NSE_INDIAVIX_REAL_minute.csv
    Rows: 750,000
    VIX range: 12.50 - 35.80

====================================================================================================
✓ DOWNLOAD COMPLETE - REAL DATA READY
====================================================================================================

Files created:
  ✓ data/NSE_NIFTY50_REAL_minute.csv (750,000 rows)
  ✓ data/NSE_INDIAVIX_REAL_minute.csv (750,000 rows)

Next: Run grid sync test with REAL Nifty 50 data
====================================================================================================
```

---

## Step 5: Verify Download

Check that files were created:

```bash
ls -lh data/NSE_NIFTY50_REAL*.csv data/NSE_INDIAVIX_REAL*.csv
```

You should see:
```
-rw-rw-r-- 1 user user 45M  Sep 07 12:15 data/NSE_NIFTY50_REAL_minute.csv
-rw-rw-r-- 1 user user 45M  Sep 07 12:20 data/NSE_INDIAVIX_REAL_minute.csv
```

---

## Step 6: Run Grid Sync Test With REAL Data

Once download is complete:

```bash
python3 scripts/test_grid_sync_with_real_nifty.py
```

This will:
- Load the REAL Nifty 50 data
- Run the MacroGridSynchronizer on REAL VIX
- Test all 62 trades against REAL market regime
- Report: How many trades does real grid sync reject?

---

## Troubleshooting

### "Login failed" / "Invalid credentials"
- Make sure you're using the EXACT credentials shown above
- Check that Zerodha account is active
- Try opening https://kite.zerodha.com directly to verify login works

### "Invalid request_token"
- Make sure you copied it correctly from the redirect URL
- Don't include `&status=success` part
- Only copy the token value (e.g., `ABCD1234EF5G6H7I8J`)

### "No data retrieved"
- Instrument tokens may vary by broker
- Nifty 50: 256265 (NSE Index)
- India VIX: 256423 (NSE Volatility)
- If these don't work, check Zerodha KiteConnect documentation

### Download taking too long
- This is normal for 3 years of 1-minute data
- ~750,000 candles per file
- Expected time: 5-15 minutes depending on connection
- Be patient, don't interrupt

### Still having issues?
Open the Zerodha API console:
1. Go to https://kite.zerodha.com/settings/developer/tokens
2. Verify your app "DailyBreakoutBot" is active
3. Check that "Kite Connect" permission is enabled
4. Try generating a fresh access token if needed

---

## Once Download Complete

You'll have:
- **Real market regime data** (Nifty 50 + VIX for 3 years)
- **Validated grid synchronization** (tested against actual index)
- **Proof** that grid sync correctly identifies bad trading periods

Then run the full test to see: **How many of the 62 trades would grid sync REALLY reject with actual market data?**

---

**Ready? Run:**
```bash
python3 scripts/download_real_nifty_zerodha.py
```

Let me know once it completes!
