#!/usr/bin/env python3
"""
ATR-SCALED PROFIT TARGET VERSION
Restores original design: profit_target scales with volatility (ATR)

Instead of:
  profit_target = ₹1.2549 (fixed, fails at high prices)

Use:
  profit_target = atr × multiplier (scales with volatility)

This works across all price ranges automatically.
"""

# This is the same as COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829.py
# but with ONE key change in the Smart Parameters initialization:

# CHANGE LOCATION: Line ~220 in initialize_smart_parameters()
#
# OLD (fixed rupees):
#   if atr_pct < 1.0:
#       profit_target = 0.50
#   elif atr_pct < 2.0:
#       profit_target = 1.00
#   else:
#       profit_target = 2.00
#
# NEW (ATR-scaled):
#   profit_target_atr_multiplier = 0.25  # ← This becomes the LEARNABLE parameter
#   profit_target = atr * profit_target_atr_multiplier
#
#   At INFY (low vol, ATR=10): profit_target = 10 × 0.25 = ₹2.50
#   At BANKNIFTY (high vol, ATR=80): profit_target = 80 × 0.25 = ₹20.00
#
#   Both scale → costs stay proportional to profit_target

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import logging
import sys
from dataclasses import dataclass

# Configure logging to suppress unicode errors
logging.basicConfig(level=logging.INFO, format='%(message)s')

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# ============================================================================
# LEARNABLE PARAMETERS (ATR-SCALED)
# ============================================================================

@dataclass
class LearnableParameters:
    """Parameters that meta-learning optimizes"""
    profit_target_atr_mult: float  # ← NEW: multiplier instead of fixed rupees
    stop_loss_atr_mult: float
    entry_pid_kp: float
    exit_pid_kp: float
    min_hold_bars: float
    max_hold_bars: float

# ============================================================================
# SYNC THRESHOLDS (unchanged)
# ============================================================================

class RelativeSynchronizationThresholds:
    """Calculate dynamic thresholds based on volatility"""
    def __init__(self, symbol, df):
        self.symbol = symbol
        self.atr = self._calculate_atr(df)
        self.price_mean = df['close'].mean()
        self.vol_mean = df['volume'].mean()

        # Thresholds: scale with ATR
        self.dp_dt_threshold = self.atr * 0.25
        self.dv_dt_threshold = self.vol_mean * 0.10

    @staticmethod
    def _calculate_atr(df, period=14):
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                np.abs(df['high'] - df['close'].shift(1)),
                np.abs(df['low'] - df['close'].shift(1))
            )
        )
        return df['tr'].rolling(period).mean().iloc[-1]

# ============================================================================
# ATR-SCALED SMART PARAMETERS
# ============================================================================

class ATRScaledSmartParameters:
    """Generate parameters that scale with market volatility"""

    def __init__(self, symbol, df, params: LearnableParameters):
        self.symbol = symbol
        self.df = df
        self.params = params
        self.atr = self._calculate_atr(df)
        self.price_mean = df['close'].mean()

        # CRITICAL: Scale parameters by ATR
        self.profit_target = self.atr * params.profit_target_atr_mult
        self.stop_loss = -self.atr * params.stop_loss_atr_mult
        self.entry_pid_kp = params.entry_pid_kp
        self.exit_pid_kp = params.exit_pid_kp
        self.min_hold_bars = int(params.min_hold_bars)
        self.max_hold_bars = int(params.max_hold_bars)

    @staticmethod
    def _calculate_atr(df, period=14):
        df_copy = df.copy()
        df_copy['tr'] = np.maximum(
            df_copy['high'] - df_copy['low'],
            np.maximum(
                np.abs(df_copy['high'] - df_copy['close'].shift(1)),
                np.abs(df_copy['low'] - df_copy['close'].shift(1))
            )
        )
        return df_copy['tr'].rolling(period).mean().iloc[-1]

# ============================================================================
# TEST HARNESS
# ============================================================================

def test_atr_scaling():
    """Demonstrate ATR scaling across different volatility regimes"""
    print("\n" + "="*80)
    print("ATR-SCALED PROFIT TARGET TEST")
    print("="*80 + "\n")

    # Test parameters
    params = LearnableParameters(
        profit_target_atr_mult=0.25,    # ← Learnable multiplier
        stop_loss_atr_mult=1.0,
        entry_pid_kp=0.15,
        exit_pid_kp=0.13,
        min_hold_bars=5,
        max_hold_bars=79
    )

    # Test on different symbols
    test_symbols = ['INFY', 'BANKNIFTY', 'HDFCBANK']

    for symbol in test_symbols:
        files = list(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))
        if not files:
            print(f"⚠️  No data for {symbol}, skipping")
            continue

        try:
            df = pd.read_csv(files[0])
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
            df = df.sort_values('timestamp').reset_index(drop=True)

            smart = ATRScaledSmartParameters(symbol, df, params)
            price_mean = df['close'].mean()
            costs = price_mean * 0.001  # 0.10% total costs

            print(f"{symbol}:")
            print(f"  Price (avg): ₹{price_mean:,.2f}")
            print(f"  ATR (14): ₹{smart.atr:.2f}")
            print(f"  → profit_target = ATR × {params.profit_target_atr_mult} = ₹{smart.profit_target:.2f}")
            print(f"  → stop_loss = -ATR × {params.stop_loss_atr_mult} = ₹{smart.stop_loss:.2f}")
            print(f"  → costs at avg price: ₹{costs:.2f}")
            print(f"  → Bridge check: ₹{smart.profit_target:.2f} > ₹{costs:.2f}? {smart.profit_target > costs and '✓ YES' or '✗ NO'}")
            print()

        except Exception as e:
            print(f"❌ Error processing {symbol}: {e}\n")

    print("="*80 + "\n")
    print("KEY INSIGHT:")
    print("  profit_target scales with ATR (volatility)")
    print("  costs scale with price")
    print("  → Both proportional → Bridge passes consistently")
    print("\n" + "="*80 + "\n")

if __name__ == "__main__":
    test_atr_scaling()
