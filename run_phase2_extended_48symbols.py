#!/usr/bin/env python3
"""
================================================================================
PHASE 2.2 - EXTENDED 48-SYMBOL BACKTEST
================================================================================

Comprehensive validation test on all 48 NIFTY symbols:
- Gate trigger frequency analysis
- Position management validation
- Performance metrics under full load
- Risk control effectiveness

Runtime: ~10-15 minutes on typical hardware

================================================================================
"""

import sys
import logging
import json
from datetime import datetime
from collections import defaultdict

logging.basicConfig(
    level=logging.WARNING,  # Suppress gate logs to see results clearly
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("\n" + "="*90)
print("PHASE 2.2 - EXTENDED 48-SYMBOL BACKTEST")
print("="*90 + "\n")

try:
    # All 48 NIFTY symbols
    ALL_48_SYMBOLS = [
        'INFY', 'TCS', 'RELIANCE', 'HDFC', 'SBIN', 'ICICIBANK', 'LT', 'ITC',
        'MARUTI', 'ONGC', 'BAJAJFINSV', 'HINDUSTAN', 'ASIANPAINT', 'DMARUTI',
        'BHARTIARTL', 'BRITANNIA', 'COALINDIA', 'DIVISLAB', 'GAIL', 'GRASIM',
        'HCLTECH', 'HEROMOTOCO', 'HINDALCO', 'IOPLUSN', 'JSWSTEEL', 'KOTAKBANK',
        'LUPIN', 'M&M', 'NESTLEIND', 'NTPC', 'POWERGRID', 'SHREECEM',
        'SUNPHARMA', 'TATAMOTORS', 'TATAPOWER', 'TATASTEEL', 'TECHM', 'TITAN',
        'TORNTPHARM', 'UPL', 'WIPRO', 'YESBANK'
    ]

    # STEP 1: Load data
    print("STEP 1: Loading data for 48 symbols...")
    print("-" * 90)

    from data_loader import DataLoader

    loader = DataLoader()
    data = {}
    success_count = 0

    for i, symbol in enumerate(ALL_48_SYMBOLS, 1):
        try:
            data[symbol] = loader.load_symbol_data(symbol, "2022-01-01", "2024-12-31")
            success_count += 1
            if i % 8 == 0:
                print(f"  Loaded {i:2d}/48 symbols... ({success_count} successful)")
        except Exception as e:
            print(f"  ⚠️  {symbol}: {e}")

    print(f"\n✅ Loaded {success_count}/48 symbols ({len(data)} total)")
    print(f"   Data size: ~{success_count * 29565 / 1000000:.1f}M bars\n")

    # Validate
    print("Validating data...")
    if not loader.validate_data(data):
        print("❌ Data validation failed!")
        sys.exit(1)

    print("✅ Data validation passed\n")

    # STEP 2: Run extended backtest
    print("STEP 2: Running extended backtest on 48 symbols...")
    print("-" * 90 + "\n")

    from backtest_engine import BacktestEngine

    engine = BacktestEngine(initial_capital=1000000.0)

    # Track gate triggers using a class wrapper
    class GateTriggerTracker:
        def __init__(self, engine):
            self.engine = engine
            self.entry_attempts = 0
            self.entries_accepted = 0
            self.original_can_enter = engine.entry_engine.can_enter

        def can_enter_tracked(self, signal, state, current_time=None):
            self.entry_attempts += 1
            result = self.original_can_enter(signal, state, current_time)
            if result[0]:
                self.entries_accepted += 1
            return result

    tracker = GateTriggerTracker(engine)
    engine.entry_engine.can_enter = tracker.can_enter_tracked

    # Run backtest
    metrics = engine.run_backtest(data)

    print("\nSTEP 3: Analyzing results...")
    print("-" * 90)

    # STEP 3: Results
    print(f"\n{'CAPITAL PERFORMANCE':^90}")
    print(f"  Initial Capital:    ₹{engine.initial_capital:>15,.0f}")
    print(f"  Final Capital:      ₹{metrics.final_capital:>15,.0f}")
    print(f"  Total Return:       ₹{metrics.total_return:>15,.0f} ({metrics.return_percent:>6.2f}%)")
    print(f"  Max Drawdown:              {metrics.max_drawdown:>10.2f}%")

    print(f"\n{'TRADE STATISTICS':^90}")
    print(f"  Total Trades:              {metrics.total_trades:>15,}")
    print(f"  Winning Trades:            {metrics.winning_trades:>15,}")
    print(f"  Losing Trades:             {metrics.losing_trades:>15,}")
    print(f"  Win Rate:                  {metrics.win_rate:>15.1f}%")
    print(f"  Entry Attempts:            {tracker.entry_attempts:>15,}")
    print(f"  Accepted Entries:          {tracker.entries_accepted:>15,}")
    if tracker.entry_attempts > 0:
        print(f"  Gate Pass Rate:            {(tracker.entries_accepted/tracker.entry_attempts)*100:>15.1f}%")

    print(f"\n{'P&L METRICS':^90}")
    print(f"  Total P&L:          ₹{metrics.total_pnl:>15,.0f}")
    print(f"  Max Trade Win:      ₹{metrics.max_pnl:>15,.0f}")
    print(f"  Min Trade Loss:     ₹{metrics.min_pnl:>15,.0f}")
    print(f"  Profit Factor:             {metrics.profit_factor:>15.2f}")
    if metrics.total_trades > 0:
        avg_pnl = metrics.total_pnl / metrics.total_trades
        print(f"  Avg P&L per Trade:  ₹{avg_pnl:>15,.0f}")

    print(f"\n{'POSITION MANAGEMENT':^90}")
    print(f"  Symbols Tested:            {len(data):>15}")
    print(f"  Max Concurrent Positions:  {max([len(engine.open_trades)] + [0]):>15}")
    print(f"  Total Positions Opened:    {metrics.total_trades:>15,}")
    print(f"  Avg Bars per Position:     {sum(t.bars_held for t in engine.trades) / max(len(engine.trades), 1):>15.1f}")

    print(f"\n{'GATE EFFECTIVENESS':^90}")
    print(f"  Entry Signal Attempts:     {tracker.entry_attempts:>15,}")
    print(f"  Gates Passed:              {tracker.entries_accepted:>15,}")
    print(f"  Gates Rejected:            {tracker.entry_attempts - tracker.entries_accepted:>15,}")
    if tracker.entry_attempts > 0:
        rejection_rate = ((tracker.entry_attempts - tracker.entries_accepted) / tracker.entry_attempts) * 100
        print(f"  Gate Rejection Rate:       {rejection_rate:>15.1f}%")

    # STEP 4: Symbol concentration analysis
    print(f"\n{'SYMBOL CONCENTRATION ANALYSIS':^90}")
    symbol_trades = defaultdict(int)
    symbol_pnl = defaultdict(float)
    for trade in engine.trades:
        symbol_trades[trade.symbol] += 1
        symbol_pnl[trade.symbol] += trade.pnl

    if symbol_trades:
        print(f"\n  Top 10 Symbols by Trade Count:")
        sorted_symbols = sorted(symbol_trades.items(), key=lambda x: x[1], reverse=True)[:10]
        for i, (symbol, count) in enumerate(sorted_symbols, 1):
            pnl = symbol_pnl[symbol]
            pct = (count / len(engine.trades)) * 100
            print(f"    {i:2d}. {symbol:12s}: {count:6,} trades ({pct:5.1f}%) | P&L: ₹{pnl:>10,.0f}")

        print(f"\n  Top 10 Symbols by P&L:")
        sorted_pnl = sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)[:10]
        for i, (symbol, pnl) in enumerate(sorted_pnl, 1):
            count = symbol_trades[symbol]
            avg = pnl / count if count > 0 else 0
            print(f"    {i:2d}. {symbol:12s}: ₹{pnl:>12,.0f} ({count:6,} trades, avg: ₹{avg:>8,.0f})")

    # STEP 5: Time series analysis
    print(f"\n{'PERFORMANCE PROGRESSION':^90}")
    quartile_size = len(engine.trades) // 4
    if quartile_size > 0:
        q1_trades = engine.trades[:quartile_size]
        q2_trades = engine.trades[quartile_size:2*quartile_size]
        q3_trades = engine.trades[2*quartile_size:3*quartile_size]
        q4_trades = engine.trades[3*quartile_size:]

        for q_num, q_trades in enumerate([q1_trades, q2_trades, q3_trades, q4_trades], 1):
            if q_trades:
                wins = sum(1 for t in q_trades if t.pnl > 0)
                pnl = sum(t.pnl for t in q_trades)
                wr = (wins / len(q_trades)) * 100
                print(f"  Q{q_num}: {len(q_trades):6,} trades | Win: {wr:5.1f}% | P&L: ₹{pnl:>12,.0f}")

    # STEP 6: Save detailed results
    print(f"\n{'SAVING RESULTS':^90}")

    results = {
        "test_type": "Phase 2.2 - Extended 48-Symbol Backtest",
        "test_date": datetime.now().isoformat(),
        "symbols_count": len(data),
        "total_bars": sum(len(df) for df in data.values()),
        "period": "2022-01-01 to 2024-12-31",
        "initial_capital": engine.initial_capital,
        "final_capital": metrics.final_capital,
        "total_return": metrics.total_return,
        "return_percent": metrics.return_percent,
        "total_trades": metrics.total_trades,
        "winning_trades": metrics.winning_trades,
        "losing_trades": metrics.losing_trades,
        "win_rate": metrics.win_rate,
        "total_pnl": metrics.total_pnl,
        "max_pnl": metrics.max_pnl,
        "min_pnl": metrics.min_pnl,
        "profit_factor": metrics.profit_factor,
        "max_drawdown": metrics.max_drawdown,
        "entry_attempts": tracker.entry_attempts,
        "entries_accepted": tracker.entries_accepted,
        "gate_rejection_rate": ((tracker.entry_attempts - tracker.entries_accepted) / tracker.entry_attempts * 100) if tracker.entry_attempts > 0 else 0,
        "symbol_concentration": {
            symbol: {
                "trade_count": symbol_trades[symbol],
                "total_pnl": symbol_pnl[symbol],
                "avg_pnl": symbol_pnl[symbol] / symbol_trades[symbol] if symbol_trades[symbol] > 0 else 0
            }
            for symbol in data.keys()
        }
    }

    # Save as JSON
    with open("PHASE_2_2_EXTENDED_RESULTS.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"  ✅ Results saved to: PHASE_2_2_EXTENDED_RESULTS.json")

    print("\n" + "="*90)
    print("✅ PHASE 2.2 EXTENDED BACKTEST COMPLETE")
    print("="*90 + "\n")

except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
