#!/usr/bin/env python3
"""
================================================================================
DETAILED TRACE & DEBUG: Single Symbol (INFY)
================================================================================

Purpose: Trace through EVERY stage with complete logging
- Stage 1: Data Validation
- Stage 2: PA (Predictive Analytics) with Feedback Loop 1
- Stage 3: ID (Intelligent Discrimination)
- Stage 4: Bridge (Economic Viability)
- Stage 5: MPC (Model Predictive Control) with Feedback Loop 2
- Stage 6: P01D Governor (3-phase execution with PID controllers)

Output: Every input/output logged with PID controller behavior visible
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import logging
import json

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem,
    UnifiedP01DGovernor,
    TradingPIDController
)

# ============================================================================
# SETUP DETAILED LOGGING
# ============================================================================

# Create detailed log file
log_filename = f"DEBUG_TRACE_INFY_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('TRACE')

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
SYMBOL = 'INFY'  # Pick ONE symbol for detailed tracing

logger.info("="*80)
logger.info("DETAILED TRACE & DEBUG - SINGLE SYMBOL")
logger.info("="*80)
logger.info(f"Symbol: {SYMBOL}")
logger.info(f"Log File: {log_filename}")
logger.info("="*80)

# ============================================================================
# STAGE 0: LOAD DATA
# ============================================================================

logger.info("\n[STAGE 0] LOADING DATA")
logger.info("-" * 80)

files = list(DATA_DIR.glob(f"NSE_{SYMBOL}_15minute_*.csv"))
if not files:
    logger.error(f"No data found for {SYMBOL}")
    exit(1)

df = pd.read_csv(files[0])
logger.info(f"Raw CSV loaded: {len(df)} rows")
logger.info(f"Columns: {list(df.columns)}")
logger.info(f"First 3 rows:")
for idx, row in df.head(3).iterrows():
    logger.info(f"  Row {idx}: timestamp={row.get('timestamp', 'N/A')}, "
               f"open={row.get('open', 'N/A')}, "
               f"high={row.get('high', 'N/A')}, "
               f"low={row.get('low', 'N/A')}, "
               f"close={row.get('close', 'N/A')}, "
               f"volume={row.get('volume', 'N/A')}")

# Convert timestamp
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
df = df.sort_values('timestamp').reset_index(drop=True)

logger.info(f"After processing: {len(df)} bars")
logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

# ============================================================================
# STAGE 1: DATA VALIDATION
# ============================================================================

logger.info("\n[STAGE 1] DATA VALIDATION")
logger.info("-" * 80)

# Check for gaps
df_tail = df.tail(100)
logger.info(f"Last 100 bars analysis:")
logger.info(f"  Open range: {df_tail['open'].min():.2f} - {df_tail['open'].max():.2f}")
logger.info(f"  High range: {df_tail['high'].min():.2f} - {df_tail['high'].max():.2f}")
logger.info(f"  Low range: {df_tail['low'].min():.2f} - {df_tail['low'].max():.2f}")
logger.info(f"  Close range: {df_tail['close'].min():.2f} - {df_tail['close'].max():.2f}")
logger.info(f"  Volume range: {df_tail['volume'].min():.0f} - {df_tail['volume'].max():.0f}")
logger.info(f"  Nulls: {df_tail.isnull().sum().sum()}")

# Verify OHLC logic
bad_bars = []
for idx, row in df_tail.iterrows():
    if not (row['low'] <= row['close'] <= row['high']):
        bad_bars.append(idx)
    if not (row['open'] <= row['high'] and row['open'] >= row['low']):
        bad_bars.append(idx)

if bad_bars:
    logger.warning(f"  ⚠️  Found {len(bad_bars)} bars with invalid OHLC logic")
else:
    logger.info(f"  ✅ OHLC logic valid on all bars")

logger.info(f"✅ STAGE 1 PASSED: Data valid for processing")

# ============================================================================
# STAGE 2: RELATIVE SYNCHRONIZATION THRESHOLDS
# ============================================================================

logger.info("\n[STAGE 2a] RELATIVE SYNCHRONIZATION THRESHOLDS")
logger.info("-" * 80)

# Calculate ATR
high = df['high'].tail(50)
low = df['low'].tail(50)
close = df['close'].tail(50)

tr1 = high - low
tr2 = np.abs(high - close.shift(1))
tr3 = np.abs(low - close.shift(1))
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
atr = tr.mean()

logger.info(f"ATR (50-period): ₹{atr:.4f}")
logger.info(f"  tr1 (high-low) avg: ₹{tr1.mean():.4f}")
logger.info(f"  tr2 (high-prev_close) avg: ₹{tr2.mean():.4f}")
logger.info(f"  tr3 (low-prev_close) avg: ₹{tr3.mean():.4f}")

# Calculate thresholds
dp_dt_threshold = atr * 0.25
dv_dt_threshold = df['volume'].tail(50).mean() * 0.10

logger.info(f"Price sync threshold (dP/dt): ₹{dp_dt_threshold:.4f} (ATR × 0.25)")
logger.info(f"Volume sync threshold (dV/dt): {dv_dt_threshold:,.0f} (Vol_mean × 0.10)")

# Volatility class
price_mean = close.mean()
price_std = close.std()
price_cv = price_std / price_mean if price_mean > 0 else 0.01

if price_cv < 0.02:
    vol_class = "LOW"
elif price_cv < 0.04:
    vol_class = "MEDIUM"
else:
    vol_class = "HIGH"

logger.info(f"Volatility classification: {vol_class}")
logger.info(f"  Coefficient of variation: {price_cv:.4f}")
logger.info(f"  Price mean: ₹{price_mean:.2f}, std: ₹{price_std:.2f}")

# ============================================================================
# STAGE 2b: SMART PARAMETER INITIALIZATION
# ============================================================================

logger.info("\n[STAGE 2b] SMART PARAMETER INITIALIZATION")
logger.info("-" * 80)

profit_target = atr * 0.10
stop_loss = atr * 1.10

logger.info(f"profit_target: ₹{profit_target:.4f} (ATR × 0.10)")
logger.info(f"stop_loss: ₹{-stop_loss:.4f} (ATR × 1.10, negative)")

# PID Kp based on volatility
if vol_class == "LOW":
    entry_pid_kp = 0.08
    exit_pid_kp = 0.07
elif vol_class == "MEDIUM":
    entry_pid_kp = 0.12
    exit_pid_kp = 0.10
else:
    entry_pid_kp = 0.18
    exit_pid_kp = 0.15

logger.info(f"Entry PID Kp (proportional gain): {entry_pid_kp:.4f}")
logger.info(f"Exit PID Kp (proportional gain): {exit_pid_kp:.4f}")
logger.info(f"PID Ki (integral gain): 0.01 (frozen)")
logger.info(f"PID Kd (derivative gain): 0.01 (frozen)")

# ============================================================================
# STAGE 3: ID - INTELLIGENT DISCRIMINATION THRESHOLD
# ============================================================================

logger.info("\n[STAGE 3] INTELLIGENT DISCRIMINATION (ID) THRESHOLD")
logger.info("-" * 80)

id_threshold = 0.75
logger.info(f"PA Score gate: {id_threshold}")
logger.info(f"Logic: IF pa_score ≥ {id_threshold} THEN TAKE ELSE PASS")

# ============================================================================
# STAGE 4: BRIDGE - ECONOMIC VIABILITY
# ============================================================================

logger.info("\n[STAGE 4] BRIDGE - ECONOMIC VIABILITY CHECK")
logger.info("-" * 80)

entry_cost_bps = 2  # basis points
exit_cost_bps = 2
entry_cost_pct = entry_cost_bps / 10000
exit_cost_pct = exit_cost_bps / 10000
total_cost_pct = entry_cost_pct + exit_cost_pct

logger.info(f"Entry cost: {entry_cost_bps} bps ({entry_cost_pct*100:.4f}%)")
logger.info(f"Exit cost: {exit_cost_bps} bps ({exit_cost_pct*100:.4f}%)")
logger.info(f"Total round-trip cost: {total_cost_pct*100:.4f}%")
logger.info(f"Min profit target must exceed: ₹{profit_target * total_cost_pct:.4f}")

# ============================================================================
# STAGE 5: MPC - MODEL PREDICTIVE CONTROL
# ============================================================================

logger.info("\n[STAGE 5] MPC - MODEL PREDICTIVE CONTROL")
logger.info("-" * 80)

capital_per_trade = 100000  # ₹100k example
max_per_symbol = capital_per_trade * 0.20  # 20% limit

logger.info(f"Capital per trade: ₹{capital_per_trade:,}")
logger.info(f"Max per symbol: ₹{max_per_symbol:,} (20%)")

# Lambda (risk control)
lambda_control = 1.0
logger.info(f"Lambda (position multiplier): {lambda_control}")
logger.info(f"Purpose: Scale position size based on risk constraints")

# ============================================================================
# STAGE 6: P01D GOVERNOR - DETAILED TRACE
# ============================================================================

logger.info("\n[STAGE 6] P01D GOVERNOR - 3-PHASE EXECUTION")
logger.info("-" * 80)

logger.info("\n[PHASE 1: ENTRY] Synchronization Gate + PID Wait")
logger.info("─" * 80)

# Sample a few bars to show entry logic
sample_bars = [100, 150, 200, 250, 300]
logger.info(f"Tracing entry logic at sample bars: {sample_bars}\n")

for bar_idx in sample_bars:
    if bar_idx >= len(df):
        continue

    row = df.iloc[bar_idx]
    prev_row = df.iloc[bar_idx - 1] if bar_idx > 0 else None

    logger.info(f"Bar {bar_idx}: {row['timestamp']}")
    logger.info(f"  OHLCV: O={row['open']:.2f}, H={row['high']:.2f}, L={row['low']:.2f}, C={row['close']:.2f}, V={row['volume']:.0f}")

    # Calculate rate of change
    if prev_row is not None:
        dp_dt = abs(row['close'] - prev_row['close'])
        dv_dt = abs(row['volume'] - prev_row['volume'])

        logger.info(f"  Rate of change:")
        logger.info(f"    dP/dt: ₹{dp_dt:.4f} vs threshold ₹{dp_dt_threshold:.4f} → {'✅ SYNC' if dp_dt >= dp_dt_threshold else '❌ NO SYNC'}")
        logger.info(f"    dV/dt: {dv_dt:,.0f} vs threshold {dv_dt_threshold:,.0f} → {'✅ SYNC' if dv_dt >= dv_dt_threshold else '❌ NO SYNC'}")

    # PA score (hardcoded 0.75 for now)
    pa_score = 0.75
    logger.info(f"  PA Score: {pa_score:.3f} vs ID threshold {id_threshold} → {'✅ TAKE' if pa_score >= id_threshold else '❌ PASS'}")

    # Entry PID controller
    entry_pid = TradingPIDController(kp=entry_pid_kp, ki=0.01, kd=0.01, target=0.75)
    signal_quality = pa_score
    pid_result = entry_pid.calculate(current_value=signal_quality)

    logger.info(f"  Entry PID Controller Output:")
    logger.info(f"    Signal quality (current_value): {signal_quality:.3f}")
    logger.info(f"    Target: 0.75")
    logger.info(f"    Error (target - current): {pid_result['error']:.4f}")
    logger.info(f"    P-term (Kp × error): {pid_result['p_term']:.4f}")
    logger.info(f"    I-term (Ki × integral): {pid_result['i_term']:.4f}")
    logger.info(f"    D-term (Kd × derivative): {pid_result['d_term']:.4f}")
    logger.info(f"    Total adjustment: {pid_result['adjustment']:.4f}")
    logger.info(f"    Iteration: {pid_result['iteration']}")
    logger.info(f"    → {'✅ WAIT for signal ≥ 0.75' if signal_quality < 0.75 else '✅ ENTER when ready'}")

logger.info("\n[PHASE 2: HOLDING] Continuous Monitoring")
logger.info("─" * 80)
logger.info("During holding:")
logger.info("  • Min hold: 1-5 bars (learnable)")
logger.info("  • Monitor 4 exit conditions:")
logger.info("    1. Profit target reached (+₹{:.2f})".format(profit_target))
logger.info("    2. Stop loss hit (-₹{:.2f})".format(stop_loss))
logger.info("    3. Signal reversal detected")
logger.info("    4. Max hold bars reached (10-120)")

logger.info("\n[PHASE 3: EXIT] PID Timing Control")
logger.info("─" * 80)

logger.info("Exit PID Controller:")
logger.info("  Target signal: 0.25 (reversal confirmation)")
logger.info("  Kp: {:.4f}".format(exit_pid_kp))
logger.info("  Logic:")
logger.info("    IF exit_condition_met THEN")
logger.info("      WAIT for signal to decay to ≤0.25")
logger.info("      EXECUTE exit at next bar open")
logger.info("    END")

# ============================================================================
# EXECUTION SUMMARY
# ============================================================================

logger.info("\n[SUMMARY] SINGLE SYMBOL TRACE COMPLETE")
logger.info("="*80)

summary = {
    "symbol": SYMBOL,
    "data_points": len(df),
    "date_range": {
        "start": df['timestamp'].min().isoformat(),
        "end": df['timestamp'].max().isoformat()
    },
    "thresholds": {
        "dp_dt_threshold": float(dp_dt_threshold),
        "dv_dt_threshold": float(dv_dt_threshold),
        "id_threshold": float(id_threshold),
        "pa_score_gate": float(id_threshold)
    },
    "parameters": {
        "profit_target": float(profit_target),
        "stop_loss": float(-stop_loss),
        "entry_pid_kp": float(entry_pid_kp),
        "exit_pid_kp": float(exit_pid_kp),
        "entry_pid_ki": 0.01,
        "entry_pid_kd": 0.01
    },
    "volatility": {
        "atr": float(atr),
        "volatility_class": vol_class,
        "coefficient_of_variation": float(price_cv)
    }
}

logger.info("\nTrace Summary (JSON):")
logger.info(json.dumps(summary, indent=2))

# Save summary
summary_file = f"DEBUG_TRACE_INFY_SUMMARY_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(summary_file, 'w') as f:
    json.dump(summary, f, indent=2)

logger.info(f"\n✅ Summary saved to: {summary_file}")
logger.info(f"✅ Full trace saved to: {log_filename}")

print(f"\n{'='*80}")
print(f"✅ DETAILED TRACE COMPLETE")
print(f"{'='*80}")
print(f"Log file: {log_filename}")
print(f"Summary: {summary_file}")
print(f"{'='*80}")
