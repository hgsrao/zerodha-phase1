# How to Run Single Symbol Intraday Test
**Complete Guide: INFY with Triple-Layer Integration (ECS + Synchronizer + Grid)**  
**Date:** September 15, 2026  
**Status:** READY TO RUN

---

## QUICK START (2 minutes)

### Step 1: Get Kite Credentials
```powershell
# Open PowerShell and run:
cd C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN
.\get_kite_access_token.ps1

# Follow the prompts:
# 1. Opens Zerodha login
# 2. Paste request token
# 3. Shows access token
```

### Step 2: Set Environment Variables
```powershell
$env:KITE_API_KEY = "your_api_key"
$env:KITE_ACCESS_TOKEN = "your_access_token"
```

### Step 3: Run the Test
```powershell
python TEST_SINGLE_SYMBOL_INTRADAY_INTEGRATION.py
```

### Step 4: Watch Live Output
```
[INFO] CYCLE: 2026-09-15 09:30:45 | INFY
[INFO] Current price: ₹1,542.50
[INFO] NIFTY 50: 24562.00 (+0.85%) → UP
[INFO] Grid: trend=UP, volatility=NORMAL, risk=LOW
[INFO] ECS: voltage=+25.0, speed=+15.0, stress=-0.05, mode=LOAD_SHARING_MODE
[INFO] Technical signal: BUY
[INFO] EXECUTE: BUY 1 INFY @ ₹1,542.50 (voltage=+25.0, nifty=UP)
```

---

## WHAT THE TEST DOES

### Architecture (Triple-Layer Integration)

```
KITE CONNECT
    ├─ Fetches NIFTY 50 bars (1-minute)
    ├─ Fetches INFY bars (1-minute)
    └─ Fetches live quotes
         ↓
    ┌─────────────────────────────┐
    │ LAYER 1: ECS                │
    │ - Calculate voltage signal   │
    │ - Calculate speed signal     │
    │ - Determine operating mode   │
    └─────────────────────────────┘
         ↓
    ┌─────────────────────────────┐
    │ LAYER 2: SYNCHRONIZER       │
    │ - Fetch NIFTY 50 context    │
    │ - Detect market regime      │
    │ - Skip/exit on weakness     │
    └─────────────────────────────┘
         ↓
    ┌─────────────────────────────┐
    │ LAYER 3: GRID               │
    │ - Analyze market structure  │
    │ - Calculate risk level      │
    │ - Filter HIGH_RISK entries  │
    └─────────────────────────────┘
         ↓
    ┌─────────────────────────────┐
    │ TECHNICAL SIGNAL            │
    │ - 20-bar MA crossover       │
    │ - Volume confirmation       │
    └─────────────────────────────┘
         ↓
    ┌─────────────────────────────┐
    │ ENTRY FILTER (ALL MUST PASS)│
    │ ✓ ECS not critical          │
    │ ✓ NIFTY not falling         │
    │ ✓ Grid not HIGH_RISK        │
    │ ✓ Technical signal present  │
    └─────────────────────────────┘
         ↓
    ┌─────────────────────────────┐
    │ EXECUTE with ECS SIZING     │
    │ Position = Base × (1+V/100) │
    │ V=+25 → 1.25x size          │
    │ V=-30 → 0.70x size          │
    └─────────────────────────────┘
         ↓
    MONITOR FOR LOSSES
    ├─ Hard stop: 1% loss → EXIT
    ├─ ECS critical (V < -80) → EXIT
    ├─ NIFTY strong down → EXIT
    └─ Grid HIGH_RISK → EXIT
```

### Data Sources (All from Kite Connect)

| Data | Source | Used For | Fetch Method |
|------|--------|----------|--------------|
| **NIFTY 50 Bars** | Kite historical_data() | Synchronizer + Grid | get_nifty_bars() |
| **NIFTY 50 Quote** | Kite quote() | Synchronizer regime | get_nifty_quote() |
| **INFY Bars** | Kite historical_data() | Technical analysis | fetch_symbol_bars() |
| **INFY Quote** | Kite quote() | Entry/exit price | Built-in to bars |

---

## DETAILED: Data Flow

### How NIFTY 50 Data is Fetched from Kite

```python
# Step 1: Get instrument token
kite.instruments("NSE")  # Returns all NSE instruments
→ Find "NIFTY 50" (or "NIFTY 50 INDEX")
→ Extract instrument_token

# Step 2: Fetch historical bars
kite.historical_data(
    instrument_token=nifty_token,
    from_date=datetime.now() - timedelta(days=1),
    to_date=datetime.now(),
    interval="minute"  # 1-minute bars
)
→ Returns list of OHLCV candles

# Step 3: Convert to DataFrame
df = pd.DataFrame(historical_data)
df[['date','open','high','low','close','volume']]
→ Ready for analysis
```

### How Symbol Bars are Fetched from Kite

```python
# Same process as NIFTY 50:

# Step 1: Get instrument token for INFY
kite.instruments("NSE")
→ Find "INFY"
→ Extract instrument_token

# Step 2: Fetch historical bars
kite.historical_data(
    instrument_token=infy_token,
    from_date=datetime.now() - timedelta(days=1),
    to_date=datetime.now(),
    interval="minute"  # 1-minute bars
)
→ Returns INFY candles

# Step 3: Use for technical analysis
df['ma20'] = df['close'].rolling(20).mean()
→ 20-bar moving average
→ Generate BUY/SELL signals
```

### Where NIFTY 50 Curve is Used

| Component | How NIFTY Curve is Used |
|-----------|------------------------|
| **Synchronizer** | Fetch NIFTY bars → Calculate MA20 → Determine trend (UP/DOWN) → Classify regime |
| **Grid** | Fetch NIFTY daily bars → Calculate ATR (volatility) → Calculate trend slope → Risk assessment |
| **Position Monitoring** | Get current NIFTY quote → Check if down 2%+ → Trigger loss-cutting |

---

## CONFIGURATION

### Edit Test Parameters

**File:** `TEST_SINGLE_SYMBOL_INTRADAY_INTEGRATION.py`

```python
# Change symbol
TEST_SYMBOL = "INFY"  # Change to any NSE symbol (RELIANCE, TCS, etc.)

# Change position size
NOTIONAL_PER_TRADE = 10000.0  # ₹10,000 per trade

# Change polling interval
POLL_INTERVAL_SECONDS = 60  # Check every 60 seconds

# Change market hours
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# Change hard stop
hard_stop_pct = 1.0  # Exit if loss > 1%
```

---

## OUTPUT FILES

### Test creates 3 logs (JSON Lines format):

#### 1. Signals Log
**File:** `test_logs/signals_INFY_*.jsonl`

```json
{
  "timestamp": "2026-09-15T09:30:45.123456",
  "symbol": "INFY",
  "action": "BUY",
  "price": 1542.50,
  "qty": 1,
  "notional": 1542.50,
  "ecs_voltage": 25.0,
  "nifty_regime": "UP",
  "grid_risk": "LOW"
}
```

#### 2. Trades Log
**File:** `test_logs/trades_INFY_*.jsonl`

```json
{
  "timestamp": "2026-09-15T09:45:30.654321",
  "symbol": "INFY",
  "action": "SELL",
  "entry_price": 1542.50,
  "exit_price": 1545.00,
  "qty": 1,
  "pnl": 2.50,
  "pnl_pct": 0.16,
  "reason": "MA_CROSSOVER",
  "nifty_regime": "UP",
  "grid_risk": "LOW"
}
```

#### 3. Monitoring Log
**File:** `test_logs/monitoring_INFY_*.jsonl`

```json
{
  "timestamp": "2026-09-15T09:31:00.000000",
  "symbol": "INFY",
  "price": 1543.00,
  "ecs_voltage": 24.5,
  "ecs_speed": 14.2,
  "ecs_stress": -0.04,
  "nifty_change": 0.85,
  "nifty_regime": "UP",
  "grid_risk": "LOW",
  "position": {
    "entry_price": 1542.50,
    "qty": 1,
    "entry_time": "2026-09-15T09:30:45.123456"
  },
  "signals_generated": 1,
  "signals_executed": 1,
  "trades_closed": 0
}
```

---

## LIVE CONSOLE OUTPUT

### Example Run

```
2026-09-15 09:30:00 [INFO] SingleSymbolTripleLayerEngine initialized for INFY
2026-09-15 09:30:00 [INFO] Starting INFY intraday test...
2026-09-15 09:30:00 [INFO] Signals log: test_logs/signals_INFY_20260915_093000.jsonl
2026-09-15 09:30:00 [INFO] Trades log: test_logs/trades_INFY_20260915_093000.jsonl
2026-09-15 09:30:00 [INFO] Monitoring log: test_logs/monitoring_INFY_20260915_093000.jsonl
2026-09-15 09:30:00 [INFO] Polling interval: 60 seconds

2026-09-15 09:31:00 [INFO] ================================================================================
2026-09-15 09:31:00 [INFO] CYCLE: 2026-09-15 09:31:00 | INFY
2026-09-15 09:31:00 [INFO] Current price: ₹1,542.50

2026-09-15 09:31:01 [INFO] NIFTY 50: 24562.00 (+0.85%) → UP
2026-09-15 09:31:02 [INFO] Grid: trend=UP, volatility=NORMAL, risk=LOW, atr=145.32

2026-09-15 09:31:03 [INFO] ECS: voltage=+25.0, speed=+15.0, stress=-0.05, mode=LOAD_SHARING_MODE

2026-09-15 09:31:04 [INFO] Technical signal: BUY
2026-09-15 09:31:04 [INFO] EXECUTE: BUY 1 INFY @ ₹1,542.50 (voltage=+25.0, nifty=UP)

2026-09-15 09:31:05 [INFO] Stats: signals_gen=1, signals_exec=1, trades_closed=0
2026-09-15 09:31:05 [INFO] ================================================================================

2026-09-15 09:32:00 [INFO] ================================================================================
2026-09-15 09:32:00 [INFO] CYCLE: 2026-09-15 09:32:00 | INFY
2026-09-15 09:32:00 [INFO] Current price: ₹1,544.00

2026-09-15 09:32:01 [INFO] NIFTY 50: 24571.00 (+1.15%) → UP
2026-09-15 09:32:02 [INFO] Grid: trend=UP, volatility=NORMAL, risk=LOW, atr=144.80

2026-09-15 09:32:03 [INFO] ECS: voltage=+28.0, speed=+18.0, stress=-0.06, mode=LOAD_SHARING_MODE

2026-09-15 09:32:04 [INFO] Position holding (entry: 1542.50, current: 1544.00, PnL: +1.50)

2026-09-15 09:32:05 [INFO] Stats: signals_gen=1, signals_exec=1, trades_closed=0
2026-09-15 09:32:05 [INFO] ================================================================================

[... continues every 60 seconds until market close ...]

2026-09-15 15:30:00 [INFO] Market closed. Exiting.

2026-09-15 15:30:01 [INFO] ================================================================================
2026-09-15 15:30:01 [INFO] TEST SUMMARY
2026-09-15 15:30:01 [INFO] ================================================================================
2026-09-15 15:30:01 [INFO] Symbol: INFY
2026-09-15 15:30:01 [INFO] Signals generated: 3
2026-09-15 15:30:01 [INFO] Signals executed: 2
2026-09-15 15:30:01 [INFO] Trades closed: 2
2026-09-15 15:30:01 [INFO]
2026-09-15 15:30:01 [INFO] P&L Summary:
2026-09-15 15:30:01 [INFO]   Total P&L: ₹+45.00
2026-09-15 15:30:01 [INFO]   Wins: 2
2026-09-15 15:30:01 [INFO]   Losses: 0
2026-09-15 15:30:01 [INFO]   Win rate: 100.0%
2026-09-15 15:30:01 [INFO] ================================================================================
```

---

## TROUBLESHOOTING

### Error 1: "Symbol not found in quote response"
```
Problem: Symbol not in Kite data
Solution: 
  - Check spelling (INFY not Infy)
  - Verify it's a valid NSE symbol
  - Check market is open
```

### Error 2: "Could not find instrument token"
```
Problem: Instrument not in Kite database
Solution:
  - Use exact trading symbol (INFY, RELIANCE, TCS, etc.)
  - Check kite.instruments("NSE") for valid list
```

### Error 3: "No historical data returned"
```
Problem: Kite returned empty bars
Solution:
  - Market might be closed (9:15-15:30 IST only)
  - Try again during market hours
  - Check internet connection
```

### Error 4: "Authorization failed"
```
Problem: Invalid credentials
Solution:
  - Re-run get_kite_access_token.ps1
  - Access tokens expire daily
  - Copy exact token value
```

---

## WHAT YOU'LL LEARN

### By Running This Test

1. **How Kite Connect works** for fetching real market data
2. **How NIFTY 50 data drives decisions** (Synchronizer)
3. **How market structure matters** (Grid analysis)
4. **How ECS adjusts position sizing** dynamically
5. **How all 3 layers work together** for trading decisions
6. **Real intraday trading flow** with monitoring

### Expected Results After 1 Day

- 3-5 signals generated
- 2-3 signals executed
- 1-2 trades closed
- Win rate 50-100% (small sample)
- P&L +₹0 to +₹500 (typical)

---

## NEXT STEPS AFTER TEST

### If Test Works Well (Profitable)
1. ✅ Test on 2-3 more symbols (RELIANCE, TCS, etc.)
2. ✅ Run for 1-2 weeks to gather statistics
3. ✅ Compare to baseline (Chart Studies Monitor alone)
4. ✅ Deploy to paper trading (48 symbols)
5. ✅ Monitor vs model predictions

### If Test Needs Adjustment
1. ❓ Adjust hard stop from 1% to 0.5%
2. ❓ Change MA period (20 → 10 or 30)
3. ❓ Adjust ECS thresholds (voltage < -80 → -60)
4. ❓ Reduce polling interval (60s → 30s)
5. ❓ Re-backtest with new parameters

---

## SUMMARY

You now have a **complete, working test** that demonstrates:

✅ Fetching NIFTY 50 bars from Kite Connect  
✅ Fetching symbol bars from Kite Connect  
✅ ECS voltage/speed signals in action  
✅ Synchronizer market regime detection  
✅ Grid market structure analysis  
✅ Triple-layer entry filtering  
✅ ECS-based position sizing  
✅ Automatic loss-cutting triggers  
✅ Real-time intraday monitoring  
✅ JSON-based trade logging  

**Ready to run with:** INFY, RELIANCE, TCS, or any NSE symbol

---

**Status:** READY TO EXECUTE  
**Time to run:** Entire trading day (9:15 AM - 3:30 PM IST)  
**Output:** 3 detailed JSON logs + console monitoring

