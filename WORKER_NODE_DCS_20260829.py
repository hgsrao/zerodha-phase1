#!/usr/bin/env python3
"""
WORKER NODE - Distributed Computing System (DCS)
================================================

Laptop (Worker) Responsibilities:
1. Listen on port 5001 for requests from Master
2. Receive JSON package (~700 bytes per symbol)
3. Process 48 symbols:
   - Calculate SMA20, SMA50, ATR, RSI, MACD
   - Generate entry/exit signals
   - Simulate trades with real costs
   - Calculate P&L per symbol
4. Return results (~15 KB JSON per batch)
5. Closed-loop feedback for next iteration

Network: Receives from PC (192.168.0.47)
Processing: ~40ms for 48 symbols (after receiving)
Response: ~15 KB JSON with complete results
"""

from flask import Flask, request, jsonify
import pandas as pd
import numpy as np
import json
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

COMMISSION_PER_SIDE = 0.0002
SLIPPAGE_ENTRY = 0.0005
SLIPPAGE_EXIT = 0.0005
STARTING_CAPITAL = 1_000_000.0

# ============================================================================
# TRADING LOGIC (Worker-side)
# ============================================================================

class SimpleBacktester:
    """Backtest logic for single symbol"""

    def __init__(self, symbol, candles):
        self.symbol = symbol
        self.candles = candles
        self.df = None
        self.trades = []

    def build_dataframe(self):
        """Convert candle list to DataFrame with indicators"""
        data = []
        for candle in self.candles:
            data.append({
                'timestamp': pd.Timestamp(candle['t']),
                'open': candle['o'],
                'high': candle['h'],
                'low': candle['l'],
                'close': candle['c'],
                'volume': candle['v']
            })

        self.df = pd.DataFrame(data)
        if len(self.df) == 0:
            return False

        # Calculate indicators
        self.df['SMA20'] = self.df['close'].rolling(20).mean()
        self.df['SMA50'] = self.df['close'].rolling(50).mean()

        # ATR
        tr1 = self.df['high'] - self.df['low']
        tr2 = (self.df['high'] - self.df['close'].shift()).abs()
        tr3 = (self.df['low'] - self.df['close'].shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        self.df['ATR'] = tr.rolling(14).mean()

        # RSI
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-6)
        self.df['RSI'] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = self.df['close'].ewm(span=12).mean()
        ema26 = self.df['close'].ewm(span=26).mean()
        self.df['MACD'] = ema12 - ema26
        self.df['MACD_Signal'] = self.df['MACD'].ewm(span=9).mean()

        return True

    def backtest(self):
        """Simple momentum-based backtest"""
        if not self.build_dataframe():
            return None

        if len(self.df) < 50:
            return None

        equity = STARTING_CAPITAL
        position = None
        trades = []

        for idx in range(50, len(self.df) - 1):
            row = self.df.iloc[idx]
            next_row = self.df.iloc[idx + 1]

            # Entry: Close > SMA20 > SMA50, RSI > 50, MACD > Signal
            if position is None:
                if (pd.notna(row['SMA20']) and pd.notna(row['SMA50']) and
                    row['close'] > row['SMA20'] > row['SMA50'] and
                    pd.notna(row['RSI']) and row['RSI'] > 50 and
                    pd.notna(row['MACD']) and pd.notna(row['MACD_Signal']) and
                    row['MACD'] > row['MACD_Signal']):

                    entry_price = row['close'] * (1 + SLIPPAGE_ENTRY)
                    entry_cost = entry_price * (1 + COMMISSION_PER_SIDE)

                    position = {
                        'entry_idx': idx,
                        'entry_price': entry_price,
                        'entry_cost': entry_cost,
                        'entry_date': row['timestamp'],
                        'entry_bar': idx
                    }

            # Exit: Close < SMA20 OR 5 bars held
            elif position is not None:
                bars_held = idx - position['entry_bar']
                exit_signal = (next_row['close'] < row['SMA20']) or (bars_held >= 5)

                if exit_signal:
                    exit_price = next_row['open'] * (1 - SLIPPAGE_EXIT)
                    exit_cost = exit_price * (1 - COMMISSION_PER_SIDE)

                    # P&L calculation
                    pnl = (exit_price - position['entry_price']) - (
                        (position['entry_cost'] + exit_cost) / 100
                    )

                    trades.append({
                        'entry_date': position['entry_date'].isoformat(),
                        'exit_date': next_row['timestamp'].isoformat(),
                        'entry_price': position['entry_price'],
                        'exit_price': exit_price,
                        'pnl': float(pnl),
                        'bars_held': bars_held
                    })

                    equity += pnl
                    position = None

        # Close remaining positions at end
        if position is not None:
            last_row = self.df.iloc[-1]
            exit_price = last_row['close'] * (1 - SLIPPAGE_EXIT)
            exit_cost = exit_price * (1 - COMMISSION_PER_SIDE)

            pnl = (exit_price - position['entry_price']) - (
                (position['entry_cost'] + exit_cost) / 100
            )

            trades.append({
                'entry_date': position['entry_date'].isoformat(),
                'exit_date': last_row['timestamp'].isoformat(),
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'pnl': float(pnl),
                'bars_held': len(self.df) - position['entry_bar']
            })

            equity += pnl

        if len(trades) == 0:
            return {
                'symbol': self.symbol,
                'trades': [],
                'total_pnl': 0,
                'final_equity': STARTING_CAPITAL,
                'win_rate': 0,
                'candles_processed': len(self.df)
            }

        winning = len([t for t in trades if t['pnl'] > 0])
        win_rate = winning / len(trades) if trades else 0

        return {
            'symbol': self.symbol,
            'trades': trades,
            'total_pnl': float(equity - STARTING_CAPITAL),
            'final_equity': float(equity),
            'win_rate': float(win_rate),
            'total_trades': len(trades),
            'candles_processed': len(self.df)
        }

# ============================================================================
# FLASK ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'HEALTHY',
        'worker': 'WORKER_NODE_DCS',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route('/backtest', methods=['POST'])
def backtest():
    """Main backtest endpoint - receives from Master"""

    start_time = datetime.now()
    data = request.get_json()

    period = data.get('period', 'UNKNOWN')
    symbols_data = data.get('symbols', {})

    logger.info(f"[WORKER] Received backtest request for {len(symbols_data)} symbols")
    logger.info(f"[WORKER] Period: {period}")
    logger.info(f"[WORKER] Start processing...")

    results = {}
    total_trades = 0
    total_pnl = 0

    # Process each symbol
    for symbol, candles_data in symbols_data.items():
        candles = candles_data.get('candles', [])

        if not candles:
            results[symbol] = {'error': 'No candles'}
            continue

        # Run backtest
        backtester = SimpleBacktester(symbol, candles)
        backtest_result = backtester.backtest()

        if backtest_result:
            results[symbol] = backtest_result
            total_trades += backtest_result.get('total_trades', 0)
            total_pnl += backtest_result.get('total_pnl', 0)

            logger.info(f"  ✓ {symbol:15} | {backtest_result['total_trades']:3} trades | P&L: ₹{backtest_result['total_pnl']:+8.2f}")
        else:
            results[symbol] = {'error': 'Insufficient data'}

    # Prepare response
    processing_time = (datetime.now() - start_time).total_seconds() * 1000

    response = {
        'period': period,
        'status': 'COMPLETE',
        'symbols': results,
        'aggregate': {
            'total_trades': total_trades,
            'total_pnl': float(total_pnl),
            'symbols_processed': len([r for r in results.values() if 'error' not in r])
        },
        'processing_time_ms': processing_time,
        'timestamp': datetime.now().isoformat()
    }

    logger.info(f"[WORKER] Complete in {processing_time:.1f}ms")
    logger.info(f"[WORKER] Sending {len(json.dumps(response)):,} bytes response")

    return jsonify(response), 200

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " WORKER NODE - DISTRIBUTED COMPUTING SYSTEM (DCS) ".center(78) + "║")
    print("║" + " Laptop (192.168.0.17) - Listening on Port 5001 ".center(78) + "║")
    print("║" + " Ready to receive backtest requests from Master (192.168.0.47) ".center(78) + "║")
    print("╚" + "="*78 + "╝\n")

    print("✓ Flask server starting on 192.168.0.17:5001")
    print("✓ Endpoints:")
    print("   GET  /health           - Health check")
    print("   POST /backtest         - Run backtest (receive from Master)")
    print("\nWaiting for Master (PC) to connect...\n")

    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
