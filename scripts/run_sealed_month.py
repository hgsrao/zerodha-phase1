#!/usr/bin/env python3
"""
REVISION 04: Sealed One-Month Calibration Run

Complete end-to-end implementation:
- Sealed data (Aug 1-31, 2024)
- Indexed chronological processing
- 10-box decision pipeline
- Shared ₹1,00,000 portfolio
- Honest PASS/FAIL/INSUFFICIENT_TRADES reporting
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
print('REVISION 04: SEALED ONE-MONTH CALIBRATION RUN')
print('='*160 + '\n')

# ============= STEP 1: SEAL EXPERIMENT =============

print('[SEAL] Freezing experiment parameters...')

month_start = "2024-08-01"
month_end = "2024-08-31"
warmup_bars = 60

symbols = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-FSL", "BAJAJFINSV", "BAJAJHLDNG", "BANKBARODA", "BANKINDIA",
    "BHARTIARTL", "BPCL", "BRITANNIA", "CANBK", "CHOLAFIN",
    "CIPLA", "COALINDIA", "COLPAL", "CONCOR", "DIVISLAB",
    "DLF", "DRREDDY", "EICHERMOT", "ESCORTS", "EXIDEIND",
    "GAIL", "GRASIM", "HCLTECH", "HDFCAMC", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDPETRO", "HINDUNILVR",
    "ICICIBANK", "ICICIGI", "ICICIPRULI", "INDIGO", "INDUSINDBK",
    "INFY", "IOC", "ITC", "JSWSTEEL", "KOTAKBANK",
]

print(f'  Symbols: {len(symbols)} NSE stocks')
print(f'  Date range: {month_start} to {month_end}')
print(f'  Warmup: {warmup_bars} bars')

# ============= STEP 2: LOAD AND HASH DATA =============

print('\n[LOAD] Loading 48-symbol dataset...')

manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

data = {}
data_parts = []

for symbol in symbols:
    try:
        df = loader._load_symbol_csv(symbol)

        # Extract sealed month
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df_month = df[(df['timestamp'].dt.strftime('%Y-%m-%d') >= month_start) &
                      (df['timestamp'].dt.strftime('%Y-%m-%d') < month_end)]

        if len(df_month) > 0:
            data[symbol] = df_month.reset_index(drop=True)
            # Collect for hashing
            data_parts.append(df_month.to_csv(index=False))
            print(f'  ✓ {symbol:12s}: {len(df_month):>5,} bars')
        else:
            print(f'  ✗ {symbol:12s}: No data in range')
    except Exception as e:
        print(f'  ✗ {symbol:12s}: {e}')

print(f'\n  Total symbols loaded: {len(data)}')

# Compute data hash
data_concatenated = ''.join(sorted(data_parts))
data_hash = hashlib.sha256(data_concatenated.encode()).hexdigest()
print(f'  Data hash: {data_hash[:16]}...')

# ============= STEP 3: CREATE SEALED EXPERIMENT =============

seal = ExperimentSeal(
    month_start=month_start,
    month_end=month_end,
    warmup_bars=warmup_bars,
    symbols=list(data.keys()),
    data_hash=data_hash,
    code_commit="HEAD",  # Would be actual commit
    config_hash="cfg_v1_Aug2024",
)

print(f'\n[SEAL] Experiment frozen:')
print(f'  {json.dumps(seal.to_dict(), indent=2)}')

# ============= STEP 4: BUILD INDEXED EVENT STREAM =============

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

if len(indexed.timestamps) > 0:
    print(f'  First: {indexed.timestamps[0]}')
    print(f'  Last: {indexed.timestamps[-1]}')

# ============= STEP 5: INITIALIZE PORTFOLIO =============

print('\n[INIT] Initializing portfolio...')

ledger = PortfolioLedger(starting_cash=100_000.0)
pipeline = TenBoxPipeline(seal)

print(f'  Starting equity: ₹{ledger.starting_cash:,.2f}')
print(f'  Max positions: 5')
print(f'  Max daily loss: ₹2,000')

# ============= STEP 6: PROCESS MONTH CHRONOLOGICALLY =============

print('\n[PROCESS] Running sealed month...')
print('-'*160)

bar_count = 0
signal_count = 0
order_count = 0
fill_count = 0
exit_count = 0

# Build history for each symbol
closes_history = {sym: [] for sym in seal.symbols}
volumes_history = {sym: [] for sym in seal.symbols}

for bar_index, (timestamp, bar_data) in enumerate(indexed.iterate_timestamps()):
    bar_count += 1

    if bar_count % 1000 == 0:
        print(f'  Bar {bar_count:>6,} | {timestamp} | Positions: {len(ledger.positions)} | Equity: ₹{ledger.marked_equity:,.0f}')

    # Step 6.1: Update history
    for symbol, bar in bar_data.items():
        closes_history[symbol].append(bar.close)
        volumes_history[symbol].append(bar.volume)

    # Step 6.2: Check exits on existing positions
    exits = ExecutionBroker.check_exits(ledger, bar_data, bar_index, seal)
    exit_count += len(exits)

    # Step 6.3: Fill pending orders from previous bar
    orders_to_fill = []
    for order_id, order in list(ledger.pending_orders.items()):
        if ExecutionBroker.fill_order(order, bar_data, ledger, seal):
            fill_count += 1
            orders_to_fill.append(order_id)

    for order_id in orders_to_fill:
        del ledger.pending_orders[order_id]

    # Step 6.4: Generate new entry signals (10-box pipeline)
    for symbol in seal.symbols:
        if symbol not in bar_data:
            continue

        bar = bar_data[symbol]

        # Skip if already have position or pending order
        if symbol in ledger.positions or any(o.symbol == symbol for o in ledger.pending_orders.values()):
            continue

        # Skip if max positions reached
        if len(ledger.positions) >= 5:
            continue

        # Generate signal
        closes_arr = np.array(closes_history[symbol][-60:])
        volumes_arr = np.array(volumes_history[symbol][-60:])

        signal = pipeline.process_bar(
            timestamp=timestamp,
            bar_index=bar_index,
            symbol=symbol,
            bar=bar,
            portfolio=ledger,
            closes_history=closes_arr,
            volumes_history=volumes_arr,
        )

        if signal and signal.valid:
            signal_count += 1

            # Create pending order (will fill at t+1)
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

            # Reserve cash
            ledger.pending_orders[order_id] = order
            ledger.reserved_cash += order.quantity * bar.close

            ledger.add_event(
                "ORDER",
                symbol,
                "TenBoxPipeline",
                {
                    "signal": signal.__dict__,
                    "order_id": order_id,
                    "quantity": order.quantity,
                },
                seal.config_hash,
                seal.data_hash,
            )

            order_count += 1

    # Step 6.5: Daily reconciliation
    current_date = timestamp.split('T')[0] if 'T' in timestamp else timestamp
    if current_date not in ledger.daily_pnl:
        ledger.daily_pnl[current_date] = 0

    # Mark to market
    ok, msg = ledger.reconcile(bar_data)
    if not ok:
        print(f'\n[RECONCILE ERROR at bar {bar_count}]: {msg}')
        break

print(f'\n  ✓ Processed {bar_count:,} bars')

# ============= STEP 7: END-OF-MONTH FLATTENING =============

print('\n[EOD] Closing all positions...')

if len(indexed.timestamps) > 0:
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

print(f'  Closed: {exit_count} positions')

# ============= STEP 8: FINAL RECONCILIATION =============

print('\n[FINAL] Reconciling ledger...')

final_bar_data = indexed.get_bars_at(indexed.timestamps[-1]) if indexed.timestamps else {}
ok, msg = ledger.reconcile(final_bar_data)

print(f'  Reconciliation: {msg}')
print(f'  Cash: ₹{ledger.cash:,.2f}')
print(f'  Positions: {len(ledger.positions)}')
print(f'  Realized P&L: ₹{ledger.realized_pnl:,.2f}')
print(f'  Marked equity: ₹{ledger.marked_equity:,.2f}')

# ============= STEP 9: GENERATE REPORT =============

print('\n[REPORT] Generating calibration result...')

pnls = [
    e.data.get("pnl", 0) for e in ledger.events
    if e.event_type in ["EXIT"]
]

total_pnl = sum(pnls) if pnls else 0
winning = len([p for p in pnls if p > 0])
losing = len([p for p in pnls if p < 0])
total_trades = winning + losing

# Evaluation
target_pnl = 22_000  # ₹1,000/day × 22 days
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
    sharpe_ratio=0.0,  # Would calculate
    profit_factor=0.0,  # Would calculate
    target_pnl=target_pnl,
    evaluation=evaluation,
)

# ============= PRINT FINAL REPORT =============

print('\n' + '='*160)
print('SEALED MONTH CALIBRATION REPORT')
print('='*160 + '\n')

print(f'Experiment Seal:')
print(f'  Date range: {seal.month_start} to {seal.month_end}')
print(f'  Symbols: {len(seal.symbols)}')
print(f'  Data hash: {seal.data_hash[:16]}...')
print(f'  Code commit: {seal.code_commit}')
print(f'  Config: {seal.config_hash}\n')

print(f'Execution Summary:')
print(f'  Bars processed: {bar_count:,}')
print(f'  Signals generated: {signal_count}')
print(f'  Orders created: {order_count}')
print(f'  Orders filled: {fill_count}')
print(f'  Positions exited: {exit_count}\n')

print(f'Portfolio Results:')
print(f'  Starting equity: ₹{result.starting_equity:,.2f}')
print(f'  Ending equity: ₹{result.ending_equity:,.2f}')
print(f'  Total P&L: ₹{result.total_pnl:,.2f}')
print(f'  Return: {(result.total_pnl / result.starting_equity) * 100:.2f}%')
print(f'  Max drawdown: ₹{result.max_drawdown:,.2f}\n')

print(f'Trade Statistics:')
print(f'  Total trades: {result.total_trades}')
print(f'  Winning: {result.winning_trades}')
print(f'  Losing: {result.losing_trades}')
print(f'  Win rate: {result.win_rate * 100:.1f}%')
print(f'  Avg P&L/trade: ₹{result.total_pnl / max(result.total_trades, 1):,.2f}\n')

print(f'Target Evaluation:')
print(f'  Monthly target (₹1,000/day × 22 days): ₹{result.target_pnl:,.2f}')
print(f'  Actual: ₹{result.total_pnl:,.2f}')
print(f'  Gap: ₹{result.target_pnl - result.total_pnl:,.2f}\n')

print(f'EVALUATION RESULT: {result.evaluation}')
print('='*160 + '\n')

# Save report
report_file = f'calibration_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
with open(report_file, 'w') as f:
    json.dump(result.to_dict(), f, indent=2, default=str)
print(f'Report saved: {report_file}\n')
