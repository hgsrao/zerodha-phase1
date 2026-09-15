#!/usr/bin/env python3
"""
Find and display an equity that EXECUTED a trade (passed all 6 stages)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem,
    UnifiedP01DGovernor
)

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

# Test 10 symbols to find one with execution
test_symbols = ['INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN', 'ICICIBANK', 'MARUTI', 'WIPRO', 'LT', 'M&M']

print("\n" + "="*100)
print("SCANNING EQUITIES FOR EXECUTED TRADES")
print("="*100 + "\n")

found_trade = None

for symbol in test_symbols:
    print(f"Scanning {symbol}...", end=" ")

    files = list(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))
    if not files:
        print("❌ No data")
        continue

    df = pd.read_csv(files[0])
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
    df = df.sort_values('timestamp').reset_index(drop=True)

    # Initialize system
    system = CompleteIntegratedTradingSystem(verbose=False)
    system.initialize_from_data({symbol: df}, [symbol])

    # Run paper trading
    results = system.run_paper_trading([symbol])

    # Check if any trades executed
    if results['metrics']['total_trades'] > 0:
        print(f"✅ FOUND {results['metrics']['total_trades']} trades!")

        # Get first trade
        trades = system.trade_records[symbol]
        if trades:
            first_trade = trades[0]
            found_trade = {
                'symbol': symbol,
                'trade': first_trade,
                'df': df,
                'thresholds': system.sync_thresholds[symbol],
                'params': {
                    'profit_target': system.starting_params[symbol]['profit_target']['current'],
                    'stop_loss': system.starting_params[symbol]['stop_loss']['current'],
                    'entry_pid_kp': system.starting_params[symbol]['entry_pid_kp']['current'],
                    'exit_pid_kp': system.starting_params[symbol]['exit_pid_kp']['current'],
                    'min_hold_bars': int(system.starting_params[symbol]['min_hold_bars']['current']),
                    'max_hold_bars': int(system.starting_params[symbol]['max_hold_bars']['current'])
                }
            }
            break
    else:
        print("No trades")

if found_trade:
    print("\n" + "="*100)
    print(f"EXECUTED TRADE FOUND: {found_trade['symbol']}")
    print("="*100 + "\n")

    trade = found_trade['trade']
    df = found_trade['df']

    print(f"Entry: {trade.entry_timestamp}")
    print(f"  Price: ₹{trade.entry_price:.2f}")
    print(f"  Signal: {trade.entry_signal:.4f}")
    print(f"  Position: {trade.position_size} shares")

    print(f"\nHold: {trade.hold_bars} bars ({trade.hold_bars * 15} minutes)")

    print(f"\nExit: {trade.exit_timestamp}")
    print(f"  Price: ₹{trade.exit_price:.2f}")
    print(f"  Signal: {trade.exit_signal:.4f}")
    print(f"  Reason: {trade.exit_reason}")

    print(f"\nP&L:")
    print(f"  Gross: ₹{trade.pnl_gross:+,.2f}")
    print(f"  Costs: ₹{trade.entry_cost + trade.exit_cost:,.2f}")
    print(f"  Net: ₹{trade.pnl_net:+,.2f}")
    print(f"  Result: {'✅ WIN' if trade.is_win else '❌ LOSS'}")

    # Save to JSON
    output = {
        'symbol': found_trade['symbol'],
        'trade_data': {
            'entry_timestamp': str(trade.entry_timestamp),
            'entry_price': float(trade.entry_price),
            'entry_signal': float(trade.entry_signal),
            'exit_timestamp': str(trade.exit_timestamp),
            'exit_price': float(trade.exit_price),
            'exit_signal': float(trade.exit_signal),
            'position_size': int(trade.position_size),
            'hold_bars': int(trade.hold_bars),
            'pnl_gross': float(trade.pnl_gross),
            'pnl_net': float(trade.pnl_net),
            'is_win': bool(trade.is_win),
            'exit_reason': trade.exit_reason,
            'entry_cost': float(trade.entry_cost),
            'exit_cost': float(trade.exit_cost)
        },
        'parameters': found_trade['params'],
        'thresholds': {
            'dp_dt_threshold': float(found_trade['thresholds']['dp_dt_threshold']),
            'dv_dt_threshold': float(found_trade['thresholds']['dv_dt_threshold']),
            'volatility_class': found_trade['thresholds']['volatility_class'],
            'atr': float(found_trade['thresholds']['atr'])
        }
    }

    with open('DCS_EXECUTED_TRADE.json', 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ Saved to: DCS_EXECUTED_TRADE.json")
    print(f"✓ Ready for Operator Panel visualization")

else:
    print("\n❌ No executed trades found in sample")
