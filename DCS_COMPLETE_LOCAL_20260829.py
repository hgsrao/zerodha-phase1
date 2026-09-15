#!/usr/bin/env python3
"""
COMPLETE DCS (Local Mode)
=========================

Master + Worker combined in single process (no network)
Runs on same PC: 192.168.0.47
Processes 48 symbols with 1-day, 1-month, 3-year backtests
Uses certified P01D-V2B data
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime
import time

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
MANIFEST_HASH = "552AD9DDCE81BBA822C33F4A4DC42CF613D715184300EE0899772CA04544AE1E"

COMMISSION_PER_SIDE = 0.0002
SLIPPAGE_ENTRY = 0.0005
SLIPPAGE_EXIT = 0.0005
STARTING_CAPITAL = 1_000_000.0

TEST_PERIODS = {
    '1_DAY': {
        'name': '1 Day',
        'start': '2026-08-27',
        'end': '2026-08-27',
    },
    '1_MONTH': {
        'name': '1 Month',
        'start': '2026-08-01',
        'end': '2026-08-28',
    },
    '3_YEAR': {
        'name': '3 Years',
        'start': '2023-08-14',
        'end': '2026-08-13',
    }
}

# ============================================================================
# WORKER LOGIC (Local)
# ============================================================================

class LocalBacktester:
    """Backtest engine (runs locally on PC)"""

    def __init__(self, symbol, candles):
        self.symbol = symbol
        self.candles = candles
        self.df = None
        self.trades = []

    def build_dataframe(self):
        """Convert candle list to DataFrame with indicators"""
        if not self.candles or len(self.candles) == 0:
            return False

        data = []
        for candle in self.candles:
            data.append({
                'timestamp': candle['timestamp'],
                'open': candle['o'],
                'high': candle['h'],
                'low': candle['l'],
                'close': candle['c'],
                'volume': candle['v']
            })

        self.df = pd.DataFrame(data)

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
        """Run backtest"""
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

            # Entry
            if position is None:
                if (pd.notna(row['SMA20']) and pd.notna(row['SMA50']) and
                    row['close'] > row['SMA20'] > row['SMA50'] and
                    pd.notna(row['RSI']) and row['RSI'] > 50 and
                    pd.notna(row['MACD']) and pd.notna(row['MACD_Signal']) and
                    row['MACD'] > row['MACD_Signal']):

                    entry_price = row['close'] * (1 + SLIPPAGE_ENTRY)
                    entry_cost = entry_price * (1 + COMMISSION_PER_SIDE)

                    position = {
                        'entry_price': entry_price,
                        'entry_cost': entry_cost,
                        'entry_bar': idx
                    }

            # Exit
            elif position is not None:
                bars_held = idx - position['entry_bar']
                if next_row['close'] < row['SMA20'] or bars_held >= 5:

                    exit_price = next_row['open'] * (1 - SLIPPAGE_EXIT)
                    exit_cost = exit_price * (1 - COMMISSION_PER_SIDE)

                    pnl = (exit_price - position['entry_price']) - (
                        (position['entry_cost'] + exit_cost) / 100
                    )

                    trades.append({
                        'pnl': float(pnl),
                        'bars_held': bars_held
                    })

                    equity += pnl
                    position = None

        if position is not None:
            last_row = self.df.iloc[-1]
            exit_price = last_row['close'] * (1 - SLIPPAGE_EXIT)
            exit_cost = exit_price * (1 - COMMISSION_PER_SIDE)

            pnl = (exit_price - position['entry_price']) - (
                (position['entry_cost'] + exit_cost) / 100
            )

            trades.append({'pnl': float(pnl)})
            equity += pnl

        if len(trades) == 0:
            return {
                'symbol': self.symbol,
                'trades': [],
                'total_pnl': 0,
                'final_equity': STARTING_CAPITAL,
                'win_rate': 0,
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
        }

# ============================================================================
# MASTER LOGIC (Local)
# ============================================================================

class LocalMasterNode:
    """Master orchestrator (runs locally on PC)"""

    def __init__(self):
        self.all_results = {}

    def load_symbol_data(self, symbol):
        """Load symbol from certified data"""
        files = list(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))
        if not files:
            return None

        try:
            df = pd.read_csv(files[0])
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
            df = df.sort_values('timestamp').reset_index(drop=True)
            return df
        except:
            return None

    def package_candles(self, df, period_start, period_end):
        """Package candles for backtest"""
        df_period = df[(df['timestamp'] >= period_start) &
                       (df['timestamp'] <= period_end)].copy()

        if len(df_period) == 0:
            return None

        candles = [
            {
                'timestamp': row['timestamp'],
                'o': float(row['open']),
                'h': float(row['high']),
                'l': float(row['low']),
                'c': float(row['close']),
                'v': int(row['volume'])
            }
            for _, row in df_period.iterrows()
        ]

        return candles

    def run_backtest_period(self, symbols, period_name, period_config):
        """Run backtest for period"""

        period_start = pd.Timestamp(period_config['start'], tz='Asia/Kolkata')
        period_end = pd.Timestamp(period_config['end'], tz='Asia/Kolkata')

        print("\n" + "="*80)
        print(f"BACKTEST: {period_config['name']}")
        print(f"Period: {period_config['start']} to {period_config['end']}")
        print("="*80 + "\n")

        results = {}
        total_trades = 0
        total_pnl = 0
        symbols_with_trades = 0

        for i, symbol in enumerate(symbols, 1):
            df = self.load_symbol_data(symbol)
            if df is None:
                results[symbol] = {'error': 'no_data'}
                continue

            candles = self.package_candles(df, period_start, period_end)
            if candles is None:
                results[symbol] = {'error': 'no_data_in_period'}
                continue

            # Run backtest
            backtester = LocalBacktester(symbol, candles)
            backtest_result = backtester.backtest()

            if backtest_result:
                results[symbol] = backtest_result
                total_trades += backtest_result['total_trades']
                total_pnl += backtest_result['total_pnl']
                if backtest_result['total_trades'] > 0:
                    symbols_with_trades += 1

                if i % 10 == 0 or i == len(symbols):
                    print(f"  [{i:2}/{len(symbols)}] {symbol:15} | {backtest_result['total_trades']:3} trades | Win: {backtest_result['win_rate']:.1%} | P&L: ₹{backtest_result['total_pnl']:+8.2f}")

        # Aggregate
        all_trades = []
        for r in results.values():
            if 'trades' in r and r['trades']:
                all_trades.extend(r['trades'])

        winning = len([t for t in all_trades if t['pnl'] > 0])
        win_rate = winning / len(all_trades) if all_trades else 0

        aggregate = {
            'total_trades': total_trades,
            'winning_trades': winning,
            'win_rate': float(win_rate),
            'total_pnl': float(total_pnl),
            'symbols_with_trades': symbols_with_trades
        }

        return {
            'period': period_name,
            'config': period_config,
            'symbol_results': results,
            'aggregate': aggregate
        }

    def run_all(self, symbols):
        """Run all test periods"""

        for period_key, period_config in TEST_PERIODS.items():
            result = self.run_backtest_period(symbols, period_key, period_config)
            self.all_results[period_key] = result

    def save_report(self):
        """Save results to JSON"""

        report = {
            'timestamp': datetime.now().isoformat(),
            'system': 'DCS_COMPLETE_LOCAL',
            'data_source': 'P01D-V2B-CERTIFIED',
            'manifest_hash': MANIFEST_HASH,
            'periods': {}
        }

        for period_key, result in self.all_results.items():
            agg = result['aggregate']
            report['periods'][period_key] = {
                'config': result['config'],
                'aggregate': agg
            }

        report_file = Path("DCS_COMPLETE_LOCAL_REPORT_20260829.json")
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        return report_file

    def print_summary(self):
        """Print summary"""

        print("\n" + "="*80)
        print("COMPLETE DCS SUMMARY (LOCAL MODE)")
        print("="*80 + "\n")

        for period_key, result in self.all_results.items():
            agg = result['aggregate']
            cfg = result['config']

            print(f"{cfg['name']:15} ({cfg['start']} to {cfg['end']})")
            print(f"  Trades:        {agg['total_trades']}")
            print(f"  Win Rate:      {agg['win_rate']:.1%}")
            print(f"  P&L:           ₹{agg['total_pnl']:+,.2f}")
            print(f"  Symbols:       {agg['symbols_with_trades']}")
            print()

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " COMPLETE DCS - LOCAL MODE (No Network Needed) ".center(78) + "║")
    print("║" + " PC (192.168.0.47) - All Processing Local ".center(78) + "║")
    print("║" + " 1-Day + 1-Month + 3-Year Backtests ".center(78) + "║")
    print("╚" + "="*78 + "╝\n")

    # 48 symbols
    symbols_48 = [
        'INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN',
        'BAJFINANCE', 'ICICIBANK', 'SUNPHARMA', 'KOTAKBANK', 'LT',
        'AXISBANK', 'BHARTIARTL', 'HINDUNILVR', 'ITC', 'MARUTI',
        'NTPC', 'POWERGRID', 'TATASTEEL', 'ZYDUSLIFE', 'LAURUSLABS',
        'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'BAJAJ-AUTO',
        'BAJAJFINSV', 'BEL', 'CIPLA', 'COALINDIA', 'DRREDDY',
        'EICHERMOT', 'ETERNAL', 'GRASIM', 'HCLTECH', 'HDFCLIFE',
        'HINDALCO', 'INDIGO', 'JIOFIN', 'JSWSTEEL', 'M&M',
        'MAXHEALTH', 'ONGC', 'SBILIFE', 'SHRIRAMFIN', 'TATACONSUM',
        'TECHM', 'TITAN', 'TRENT', 'ULTRACEMCO', 'WIPRO'
    ]

    master = LocalMasterNode()

    start_time = datetime.now()

    master.run_all(symbols_48)

    elapsed = (datetime.now() - start_time).total_seconds()

    master.print_summary()

    report_file = master.save_report()

    print(f"✓ Report saved: {report_file}")
    print(f"✓ Total execution time: {elapsed:.1f} seconds\n")

if __name__ == '__main__':
    main()
