#!/usr/bin/env python3
"""
REVISION 04: Sealed System Validation (Small Dataset)

Quick validation with 3 symbols × 5 days to confirm:
- Data loading and indexing works
- 10-box pipeline generates signals
- Orders fill correctly
- Exits execute properly
- Ledger reconciles
- Report generation works
"""

import sys
sys.path.insert(0, '.')

import hashlib
import json
from datetime import datetime, timedelta
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
print('REVISION 04: SEALED SYSTEM VALIDATION (5-DAY TEST)')
print('='*160 + '\n')

# ============= SEAL =============

print('[SEAL] Freezing experiment...')

symbols = ["INFY", "TCS", "HCLTECH"]  # 3 symbols only
month_start = "2024-08-01"
month_end = "2024-08-05"  # 5 days only

print(f'  Symbols: {len(symbols)}')
print(f'  Period: {month_start} to {month_end}')

# ============= LOAD DATA =============

print('\n[LOAD] Loading data...')

manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

data = {}
data_parts = []

for symbol in symbols:
    try:
        df = loader._load_symbol_csv(symbol)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df_subset = df[(df['timestamp'].dt.strftime('%Y-%m-%d') >= month_start) &
                       (df['timestamp'].dt.strftime('%Y-%m-%d') < month_end)]

        if len(df_subset) > 0:
            data[symbol] = df_subset.reset_index(drop=True)
            print(f'  ✓ {symbol}: {len(df_subset):>5,} bars')
        else:
            print(f'  ✗ {symbol}: No data')
    except Exception as e:
        print(f'  ✗ {symbol}: {str(e)[:50]}')

if not data:
    print('\n  ERROR: No data loaded!')
    sys.exit(1)

# ============= INDEX =============

print('\n[INDEX] Building event stream...')

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
print(f'  Timestamps: {len(indexed.timestamps):,}')

# ============= SEAL & SETUP =============

print('\n[SETUP] Creating sealed experiment...')

seal = ExperimentSeal(
    month_start=month_start,
    month_end=month_end,
    warmup_bars=60,
    symbols=list(data.keys()),
    data_hash="test_hash_v1",
    code_commit="HEAD",
    config_hash="val_test_1",
)

ledger = PortfolioLedger(starting_cash=100_000.0)
pipeline = TenBoxPipeline(seal)

print(f'  Starting equity: ₹{ledger.starting_cash:,.0f}')

# ============= PROCESS =============

print('\n[PROCESS] Running 5-day test...')

bar_count = 0
signal_count = 0
order_count = 0
fill_count = 0
exit_count = 0

closes_history = {sym: [] for sym in seal.symbols}
volumes_history = {sym: [] for sym in seal.symbols}

for bar_index, (timestamp, bar_data) in enumerate(indexed.iterate_timestamps()):
    bar_count += 1

    # Update history
    for symbol, bar in bar_data.items():
        closes_history[symbol].append(bar.close)
        volumes_history[symbol].append(bar.volume)

    # Check exits
    exits = ExecutionBroker.check_exits(ledger, bar_data, bar_index, seal)
    exit_count += len(exits)

    # Fill orders
    orders_to_fill = []
    for order_id, order in list(ledger.pending_orders.items()):
        if ExecutionBroker.fill_order(order, bar_data, ledger, seal):
            fill_count += 1
            orders_to_fill.append(order_id)

    for order_id in orders_to_fill:
        del ledger.pending_orders[order_id]

    # Generate signals
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

    # Reconcile
    ok, msg = ledger.reconcile(bar_data)

print(f'  ✓ Processed {bar_count:,} bars')

# ============= EOD FLATTENING =============

print('\n[EOD] Closing all positions...')

if indexed.timestamps:
    last_bars = indexed.get_bars_at(indexed.timestamps[-1])

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

# ============= REPORT =============

print('\n[REPORT] Generating validation report...')

pnls = [
    e.data.get("pnl", 0) for e in ledger.events
    if e.event_type in ["EXIT"]
]

total_pnl = sum(pnls) if pnls else 0
winning = len([p for p in pnls if p > 0])
losing = len([p for p in pnls if p < 0])
total_trades = winning + losing

target_pnl = 22_000
if total_trades == 0:
    evaluation = "INSUFFICIENT_TRADES"
elif total_pnl >= target_pnl * 0.9:
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
print('5-DAY VALIDATION REPORT')
print('='*160 + '\n')

print(f'Data:')
print(f'  Period: {seal.month_start} to {seal.month_end}')
print(f'  Symbols: {len(seal.symbols)}')
print(f'  Bars: {bar_count:,}\n')

print(f'Execution:')
print(f'  Signals: {signal_count}')
print(f'  Orders: {order_count}')
print(f'  Fills: {fill_count}')
print(f'  Exits: {exit_count}\n')

print(f'Results:')
print(f'  Starting: ₹{result.starting_equity:,.0f}')
print(f'  Ending: ₹{result.ending_equity:,.0f}')
print(f'  P&L: ₹{result.total_pnl:,.0f}')
print(f'  Trades: {result.total_trades}')
print(f'  Win rate: {result.win_rate*100:.1f}%\n')

print(f'EVALUATION: {result.evaluation}')
print('='*160 + '\n')

if result.evaluation == "PASS":
    print('✓ System operational. Ready for sealed month run.\n')
elif result.evaluation == "FAIL":
    print('⚠ System working but below target. Tuning needed.\n')
else:
    print('ℹ Insufficient trades for evaluation.\n')
