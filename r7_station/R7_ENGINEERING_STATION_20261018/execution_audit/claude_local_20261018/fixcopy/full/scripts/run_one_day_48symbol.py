#!/usr/bin/env python3
"""
REVISION 04: One-Day Intraday Test (All 48 Symbols)

Target: ₹1,000 profit
Investment: ₹1,00,000
Duration: One trading day (9:15 AM - 3:30 PM)
Symbols: All 48 NSE stocks

Uses same sealed calibration engine.
"""

import sys
sys.path.insert(0, '.')

import hashlib
import json
from datetime import datetime
import pandas as pd
import numpy as np
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision4_production.sealed_calibration import (
    ExperimentSeal,
    PortfolioLedger,
    IndexedMarketData,
    Bar,
    Order,
    TenBoxPipeline,
    ExecutionBroker,
    CalibrationResult,
)

print('\n' + '='*160)
print('REVISION 04: ONE-DAY INTRADAY TEST (48 SYMBOLS)')
print('='*160 + '\n')

# ============= CHOOSE DATE =============

print('[DATE] Selecting trading day...')

# Use a recent trading day with good data
test_date = "2024-08-02"  # Friday, August 2, 2024

print(f'  Date: {test_date}')
print(f'  Target: ₹1,000 profit')
print(f'  Investment: ₹1,00,000')
print(f'  Duration: Intraday (9:15 AM - 3:30 PM)')

# ============= LOAD ALL 48 SYMBOLS =============

print('\n[LOAD] Loading all 48 symbols for one day...')

manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

# Get all 48 symbols from manifest
all_symbols = [f.symbol for f in manifest.files[:48]]

data = {}
data_parts = []

for symbol in all_symbols:
    try:
        df = loader._load_symbol_csv(symbol)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

        # Extract exactly one day
        df_day = df[df['timestamp'].dt.strftime('%Y-%m-%d') == test_date]

        if len(df_day) > 0:
            data[symbol] = df_day.reset_index(drop=True)
            data_parts.append(df_day.to_csv(index=False))
        else:
            print(f'  ✗ {symbol:12s}: No data for {test_date}')
    except Exception as e:
        print(f'  ✗ {symbol:12s}: Error')

loaded = len(data)
print(f'\n✓ Loaded {loaded}/48 symbols')

if loaded < 48:
    print(f'  Warning: Only {loaded} symbols available for {test_date}')
    print(f'  (Will proceed with available data)')

# ============= SEAL =============

print('\n[SEAL] Creating experiment seal...')

data_concatenated = ''.join(sorted(data_parts))
data_hash = hashlib.sha256(data_concatenated.encode()).hexdigest()

seal = ExperimentSeal(
    month_start=test_date,
    month_end=test_date,
    warmup_bars=60,
    symbols=list(data.keys()),
    data_hash=data_hash[:16],
    code_commit="HEAD",
    config_hash="one_day_test_v1",
)

print(f'  Symbols in seal: {len(seal.symbols)}')
print(f'  Data hash: {data_hash[:16]}...')

# ============= BUILD INDEXED STREAM =============

print('\n[INDEX] Building chronological event stream...')

indexed = IndexedMarketData()

for symbol, df in data.items():
    for _, row in df.iterrows():
        bar = Bar(
            symbol=symbol,
            timestamp=str(row['timestamp']),
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=int(row['volume']),
        )
        indexed.add_bar(bar)

indexed.sort_timestamps()

print(f'  Total timestamps: {len(indexed.timestamps):,}')
if indexed.timestamps:
    print(f'  First: {indexed.timestamps[0]}')
    print(f'  Last: {indexed.timestamps[-1]}')

# ============= INITIALIZE PORTFOLIO =============

print('\n[INIT] Initializing portfolio...')

ledger = PortfolioLedger(starting_cash=100_000.0)
pipeline = TenBoxPipeline(seal)

print(f'  Starting equity: ₹{ledger.starting_cash:,.2f}')
print(f'  Target profit: ₹1,000.00')
print(f'  Max concurrent positions: 5')

# ============= PROCESS DAY =============

print('\n[PROCESS] Running intraday session...')
print('-'*160)

bar_count = 0
signal_count = 0
order_count = 0
fill_count = 0
exit_count = 0

closes_history = {sym: [] for sym in seal.symbols}
volumes_history = {sym: [] for sym in seal.symbols}

for bar_index, (timestamp, bar_data) in enumerate(indexed.iterate_timestamps()):
    bar_count += 1

    # Print progress every 30 bars
    if bar_count % 30 == 0:
        time_str = timestamp.split('T')[1][:5] if 'T' in timestamp else timestamp
        print(f'  {time_str} | Pos: {len(ledger.positions):1d} | P&L: ₹{ledger.realized_pnl:>10,.0f} | Equity: ₹{ledger.marked_equity:>10,.0f}')

    # Update history
    for symbol, bar in bar_data.items():
        closes_history[symbol].append(bar.close)
        volumes_history[symbol].append(bar.volume)

    # Check exits on existing positions
    exits = ExecutionBroker.check_exits(ledger, bar_data, bar_index, seal)
    exit_count += len(exits)

    # Fill pending orders from previous bar
    orders_to_fill = []
    for order_id, order in list(ledger.pending_orders.items()):
        if ExecutionBroker.fill_order(order, bar_data, ledger, seal):
            fill_count += 1
            orders_to_fill.append(order_id)

    for order_id in orders_to_fill:
        del ledger.pending_orders[order_id]

    # Generate new entry signals
    for symbol in seal.symbols:
        if symbol not in bar_data:
            continue

        if symbol in ledger.positions or any(o.symbol == symbol for o in ledger.pending_orders.values()):
            continue

        if len(ledger.positions) >= 5:
            continue

        closes_arr = np.array(closes_history[symbol][-60:])
        volumes_arr = np.array(volumes_history[symbol][-60:])

        signal = pipeline.process_bar(
            timestamp=timestamp,
            bar_index=bar_index,
            symbol=symbol,
            bar=bar_data[symbol],
            portfolio=ledger,
            closes_history=closes_arr,
            volumes_history=volumes_arr,
        )

        if signal and signal.valid:
            signal_count += 1

            order_id = len(ledger.pending_orders) + 1000
            order = Order(
                order_id=order_id,
                symbol=symbol,
                direction=signal.direction,
                quantity=int(signal.mpc_adjusted_size),
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                created_at_timestamp=timestamp,
                created_at_bar_index=bar_index,
            )

            ledger.pending_orders[order_id] = order
            ledger.reserved_cash += order.quantity * bar_data[symbol].close
            order_count += 1

    # Reconcile every 60 bars
    if bar_count % 60 == 0:
        ok, msg = ledger.reconcile(bar_data)

print(f'\n  ✓ Processed {bar_count:,} bars')

# ============= EOD FLATTENING =============

print('\n[EOD] Closing all positions (market close)...')

if indexed.timestamps:
    last_timestamp = indexed.timestamps[-1]
    last_bars = indexed.get_bars_at(last_timestamp)

    for symbol, position in list(ledger.positions.items()):
        if symbol in last_bars:
            bar = last_bars[symbol]
            exit_price = bar.close
            pnl = (exit_price - position.entry_price) * position.quantity - position.cost_paid
            ledger.realized_pnl += pnl
            ledger.cash += exit_price * position.quantity

            ledger.add_event(
                "EXIT",
                symbol,
                "ExecutionBroker",
                {"reason": "EOD_FLATTENING", "exit_price": exit_price, "pnl": pnl},
                seal.config_hash,
                seal.data_hash,
            )

            del ledger.positions[symbol]

print(f'  Closed: {exit_count} positions at market close')

# ============= FINAL RECONCILIATION =============

print('\n[FINAL] Reconciling ledger...')

final_bar_data = indexed.get_bars_at(indexed.timestamps[-1]) if indexed.timestamps else {}
ok, msg = ledger.reconcile(final_bar_data)

print(f'  Status: {msg}')
print(f'  Cash: ₹{ledger.cash:,.2f}')
print(f'  Positions: {len(ledger.positions)}')
print(f'  Realized P&L: ₹{ledger.realized_pnl:,.2f}')
print(f'  Marked equity: ₹{ledger.marked_equity:,.2f}')

# ============= GENERATE REPORT =============

print('\n[REPORT] Generating one-day result...')

pnls = [
    e.data.get("pnl", 0) for e in ledger.events
    if e.event_type in ["EXIT"]
]

total_pnl = sum(pnls) if pnls else 0
winning = len([p for p in pnls if p > 0])
losing = len([p for p in pnls if p < 0])
total_trades = winning + losing

# Evaluation
target_pnl = 1_000  # One day target
if total_trades == 0:
    evaluation = "INSUFFICIENT_TRADES"
elif total_pnl >= target_pnl:
    evaluation = "PASS"
else:
    evaluation = "FAIL"

result = CalibrationResult(
    seal=seal,
    total_trades=total_trades,
    winning_trades=winning,
    losing_trades=losing,
    win_rate=winning / total_trades if total_trades > 0 else 0,
    total_pnl=total_pnl,
    realized_pnl=ledger.realized_pnl,
    unrealized_pnl=ledger.unrealized_pnl,
    total_costs=ledger.total_costs,
    starting_equity=ledger.starting_cash,
    ending_equity=ledger.marked_equity,
    max_drawdown=ledger.max_drawdown,
    sharpe_ratio=0.0,
    profit_factor=0.0,
    target_pnl=target_pnl,
    evaluation=evaluation,
)

# ============= PRINT REPORT =============

print('\n' + '='*160)
print(f'ONE-DAY INTRADAY RESULT: {test_date}')
print('='*160 + '\n')

print(f'Portfolio:')
print(f'  Starting equity: ₹{result.starting_equity:,.2f}')
print(f'  Ending equity: ₹{result.ending_equity:,.2f}')
print(f'  Total P&L: ₹{result.total_pnl:,.2f}')
print(f'  Return: {(result.total_pnl / result.starting_equity) * 100:.3f}%\n')

print(f'Execution:')
print(f'  Bars processed: {bar_count:,}')
print(f'  Signals: {signal_count}')
print(f'  Orders: {order_count}')
print(f'  Fills: {fill_count}')
print(f'  Exits: {exit_count}\n')

print(f'Trades:')
print(f'  Total: {result.total_trades}')
print(f'  Winning: {result.winning_trades}')
print(f'  Losing: {result.losing_trades}')
print(f'  Win rate: {result.win_rate * 100:.1f}%')
if result.total_trades > 0:
    print(f'  Avg P&L/trade: ₹{result.total_pnl / result.total_trades:,.2f}\n')

print(f'Target vs Actual:')
print(f'  Target: ₹{result.target_pnl:,.2f}')
print(f'  Actual: ₹{result.total_pnl:,.2f}')
print(f'  Gap: ₹{result.target_pnl - result.total_pnl:,.2f}\n')

print(f'EVALUATION: {result.evaluation}')
print('='*160 + '\n')

# Save report
report_file = f'one_day_result_{test_date}.json'
with open(report_file, 'w') as f:
    json.dump(result.to_dict(), f, indent=2, default=str)

print(f'Report saved: {report_file}\n')

# Summary
if result.evaluation == "PASS":
    print(f'✓ SUCCESS: Target ₹{result.target_pnl:,} achieved with ₹{result.total_pnl:,.0f} profit')
elif result.evaluation == "FAIL":
    print(f'✗ MISS: Target ₹{result.target_pnl:,}, actual ₹{result.total_pnl:,.0f}')
else:
    print(f'ℹ Insufficient trades ({result.total_trades}) to evaluate')
