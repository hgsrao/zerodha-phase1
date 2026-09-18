# 📊 DATABASE MONITORING GUIDE
## SQLite State Persistence - Live Queries

**Database Location:**
```
/mnt/c/Users/Dishan/Documents/Codex/Zerodha_live_bot_3.4_CORRECTED_20260918/paper_trading_state.db
```

---

## 🔍 MONITORING COMMANDS

### 1️⃣ View Open Positions (With Columns)
```bash
sqlite3 -header -column paper_trading_state.db 'SELECT * FROM open_positions;'
```

**Output Format:**
```
id | symbol | entry_time | entry_price | qty | stop_price | target_price
---|--------|------------|-------------|-----|------------|-------------
1  | TCS    | 2026-09-18 | 3425.50     | 100 | 3356.99    | 3494.01
2  | INFY   | 2026-09-18 | 2850.00     | 50  | 2793.00    | 2907.00
```

**Empty Result:**
```
(No rows = No open positions yet - engine waiting for signals)
```

---

### 2️⃣ View Table Structure
```bash
sqlite3 paper_trading_state.db '.schema open_positions'
```

**Shows:**
```
CREATE TABLE open_positions (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    entry_time TIMESTAMP,
    entry_price REAL,
    qty INTEGER,
    stop_price REAL,
    target_price REAL
)
```

---

### 3️⃣ View Closed Trades (Audit Trail)
```bash
sqlite3 -header -column paper_trading_state.db 'SELECT * FROM closed_trades;'
```

**Output Format:**
```
id | symbol | entry_time | exit_time | entry_price | exit_price | qty | pnl    | reason
---|--------|------------|-----------|-------------|------------|-----|--------|----------
1  | TCS    | 2026-09-18 | 2026-09-18| 3425.50     | 3450.00    | 100 | 2450.0 | TARGET_HIT
2  | INFY   | 2026-09-18 | 2026-09-18| 2850.00     | 2835.00    | 50  | -750.0 | STOP_LOSS
```

---

### 4️⃣ Count Open Positions
```bash
sqlite3 paper_trading_state.db 'SELECT COUNT(*) as open_positions FROM open_positions;'
```

**Output:**
```
0  (or N if trades are open)
```

---

### 5️⃣ Count Closed Trades
```bash
sqlite3 paper_trading_state.db 'SELECT COUNT(*) as closed_trades FROM closed_trades;'
```

**Output:**
```
0  (or N for total trades executed)
```

---

### 6️⃣ Total P&L (All Closed Trades)
```bash
sqlite3 paper_trading_state.db 'SELECT SUM(pnl) as total_pnl FROM closed_trades;'
```

**Output:**
```
1700.0  (or negative if losses)
```

---

### 7️⃣ Win Rate
```bash
sqlite3 paper_trading_state.db 'SELECT 
    ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 2) as win_rate_pct
FROM closed_trades WHERE pnl IS NOT NULL;'
```

**Output:**
```
50.0  (percentage of winning trades)
```

---

### 8️⃣ Breakdown by Reason
```bash
sqlite3 -header -column paper_trading_state.db 'SELECT reason, COUNT(*) as count, SUM(pnl) as total_pnl 
FROM closed_trades 
GROUP BY reason;'
```

**Output:**
```
reason       | count | total_pnl
-------------|-------|----------
TARGET_HIT   | 5     | 3500.0
STOP_LOSS    | 2     | -800.0
SIGNAL_CLOSE | 1     | 200.0
```

---

## 🔄 LIVE MONITORING LOOP

### Watch for New Trades (Updates Every 5 Seconds)
```bash
watch -n 5 'echo "=== OPEN POSITIONS ===" && sqlite3 -header -column paper_trading_state.db "SELECT symbol, entry_price, qty, stop_price, target_price FROM open_positions;" && echo "" && echo "=== CLOSED TRADES ===" && sqlite3 paper_trading_state.db "SELECT COUNT(*) as total_trades, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, ROUND(SUM(pnl), 2) as net_pnl FROM closed_trades;"'
```

---

## 📈 QUICK STATS DASHBOARD

### All-In-One Query
```bash
sqlite3 paper_trading_state.db "
SELECT 
    'Open Positions' as metric, COUNT(*) as value 
FROM open_positions
UNION ALL
SELECT 'Closed Trades', COUNT(*) FROM closed_trades
UNION ALL
SELECT 'Total P&L', ROUND(SUM(pnl), 2) FROM closed_trades
UNION ALL
SELECT 'Winning Trades', SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) FROM closed_trades
UNION ALL
SELECT 'Losing Trades', SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) FROM closed_trades;
"
```

**Output:**
```
metric           | value
-----------------|-------
Open Positions   | 2
Closed Trades    | 15
Total P&L        | 5420.50
Winning Trades   | 10
Losing Trades    | 5
```

---

## ⚠️ TROUBLESHOOTING

### Database Locked Error
```bash
# If you see "database is locked", the engine is writing
# Wait a few seconds and retry
# Or use read-only mode:
sqlite3 paper_trading_state.db -readonly 'SELECT * FROM open_positions;'
```

### Schema Not Found
```bash
# If table doesn't exist, engine hasn't run yet
# Run the engine first: python3 paper_trading_engine_HARDENED.py
```

### Empty Tables
```bash
# Normal on first run - means no signals have fired yet
# Engine will populate tables when:
# 1. Z-score < -2.0 (oversold detected)
# 2. RSI < 32 (reversal confirmed)
# 3. Close > Prior High (entry signal fires)
```

---

## 🔐 BACKUP COMMANDS

### Backup Database
```bash
cp paper_trading_state.db paper_trading_state_backup_$(date +%Y%m%d_%H%M%S).db
```

### Export to CSV
```bash
sqlite3 -header -csv paper_trading_state.db 'SELECT * FROM closed_trades;' > closed_trades_export.csv
```

### Restore from Backup
```bash
cp paper_trading_state_backup_20260918_102900.db paper_trading_state.db
```

---

## 📊 SAMPLE OUTPUT (After Trades Execute)

### Open Positions (Mid-Trade)
```
sqlite3 -header -column paper_trading_state.db 'SELECT * FROM open_positions;'

id | symbol | entry_time          | entry_price | qty | stop_price | target_price
---|--------|---------------------|-------------|-----|------------|-------------
1  | INFY   | 2026-09-18 10:45:30 | 2850.00     | 50  | 2793.00    | 2907.00
2  | TCS    | 2026-09-18 10:50:15 | 3425.50     | 100 | 3356.99    | 3494.01
```

### Closed Trades (After Execution)
```
sqlite3 -header -column paper_trading_state.db 'SELECT symbol, entry_price, exit_price, qty, pnl, reason FROM closed_trades;'

symbol | entry_price | exit_price | qty | pnl      | reason
-------|-------------|------------|-----|----------|----------
INFY   | 2850.00     | 2907.00    | 50  | 2850.00  | TARGET_HIT
TCS    | 3425.50     | 3356.99    | 100 | -6850.00 | STOP_LOSS
RELIAN | 2850.50     | 2875.00    | 75  | 1837.50  | SIGNAL_CLOSE
```

---

## 🎯 KEY FIELDS EXPLAINED

| Field | Meaning | Example |
|-------|---------|---------|
| `id` | Unique trade ID | 1, 2, 3... |
| `symbol` | Stock symbol | INFY, TCS, RELIANCE |
| `entry_time` | When position opened | 2026-09-18 10:45:30 |
| `entry_price` | Entry price per share | 2850.00 |
| `qty` | Quantity of shares | 50 |
| `stop_price` | Stop-loss level | 2793.00 |
| `target_price` | Profit target level | 2907.00 |
| `exit_time` | When position closed | 2026-09-18 10:50:00 |
| `exit_price` | Exit price per share | 2907.00 |
| `pnl` | Profit/Loss (qty × price diff) | +2850.00 |
| `reason` | Why it closed | TARGET_HIT, STOP_LOSS, SIGNAL_CLOSE |

---

## 📝 QUICK REFERENCE SHEET

```bash
# Everything in one command
alias trading_dashboard='sqlite3 -header -column paper_trading_state.db "SELECT * FROM open_positions; SELECT \"---CLOSED---\"; SELECT symbol, entry_price, exit_price, pnl, reason FROM closed_trades ORDER BY id DESC LIMIT 10;"'

# Then just run:
trading_dashboard
```

---

**Generated:** 2026-09-18 | Database Monitoring Guide
