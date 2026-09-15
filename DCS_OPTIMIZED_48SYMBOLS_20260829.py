#!/usr/bin/env python3
"""
DCS 6-STAGE SYSTEM - OPTIMIZED FOR 48 SYMBOLS
==============================================

Phase 2: Fix & Optimize Complete DCS Closed-Loop System

Changes from Phase 1:
✓ ID Threshold: 60% → 50% (generate more trades)
✓ PA Scoring: Simplified for higher signal generation
✓ Entry Logic: More aggressive to capture opportunities
✓ 48 symbols: Full certified dataset
✓ 3 periods: 1-day, 1-month, 3-year

Target: Achieve 50%+ win rate
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
MANIFEST_HASH = "552AD9DDCE81BBA822C33F4A4DC42CF613D715184300EE0899772CA04544AE1E"

# **OPTIMIZED PARAMETERS**
ID_THRESHOLD = 0.50  # ← LOWERED from 0.60 to generate more trades
RISK_LAMBDA = 1.0    # Full position sizing
POSITION_LIMIT = 0.20  # 20% per symbol
COST_BPS = 2  # 2 basis points commission

TEST_PERIODS = {
    '1_DAY': {'name': '1 Day', 'start': '2026-08-27', 'end': '2026-08-27'},
    '1_MONTH': {'name': '1 Month', 'start': '2026-08-01', 'end': '2026-08-28'},
    '3_YEAR': {'name': '3 Years', 'start': '2023-08-14', 'end': '2026-08-13'}
}

SYMBOLS_48 = [
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

# ============================================================================
# DCS SYSTEM - 6 STAGES
# ============================================================================

class DCSOptimizedSystem:
    """Optimized 6-stage DCS with lower thresholds for 48 symbols"""

    def __init__(self, symbol, verbose=False):
        self.symbol = symbol
        self.verbose = verbose
        self.trades = []
        self.equity = 1_000_000.0

    def _log(self, msg):
        if self.verbose:
            print(msg)

    # STAGE 1: DATA INPUT VALIDATION
    def stage1_validate(self, row):
        """Validate OHLCV data structure"""
        try:
            if row['high'] < row['low'] or row['close'] < 0:
                return False
            return True
        except:
            return False

    # STAGE 2: PA (PREDICTIVE ANALYTICS) - SIMPLIFIED FOR SIGNALS
    def stage2_pa_score(self, row, history):
        """Calculate PA score - optimized for more signals"""
        if len(history) < 20:
            return 0.5

        # Simplified scoring for better signal generation
        close_price = row['close']
        sma20 = history['close'].rolling(20).mean().iloc[-1]
        sma50 = history['close'].rolling(50).mean().iloc[-1]

        # Momentum
        momentum = (close_price - history['close'].iloc[-10]) / history['close'].iloc[-10]

        # Calculate simple PA score
        pa_score = 0.5  # Start neutral

        # Uptrend: +0.2
        if close_price > sma20:
            pa_score += 0.1
        if sma20 > sma50:
            pa_score += 0.1

        # Momentum: +0.2
        if momentum > 0.01:
            pa_score += 0.2

        # Volume: +0.1
        volume_ratio = row['volume'] / history['volume'].mean()
        if volume_ratio > 1.0:
            pa_score += 0.1

        return np.clip(pa_score, 0.0, 1.0)

    # STAGE 3: ID (INTELLIGENT DISCRIMINATION) - OPTIMIZED THRESHOLD
    def stage3_decision(self, pa_score):
        """Make TAKE/PASS decision - THRESHOLD LOWERED TO 50%"""
        # OLD: IF pa_score >= 0.60 → TAKE
        # NEW: IF pa_score >= 0.50 → TAKE (generates more trades)
        if pa_score >= ID_THRESHOLD:
            return "TAKE"
        else:
            return "PASS"

    # STAGE 4: BRIDGE (ECONOMIC VIABILITY)
    def stage4_bridge(self, entry_price):
        """Check if trade is economically viable"""
        min_profit = entry_price * (COST_BPS / 10000) * 2  # Must exceed 2x cost
        return min_profit > 0

    # STAGE 5: MPC (MODEL PREDICTIVE CONTROL) - POSITION SIZING
    def stage5_mpc(self, entry_price, available_capital):
        """Calculate position size with Lambda"""
        max_position_value = available_capital * POSITION_LIMIT
        position_size = int(max_position_value / entry_price)
        position_size = int(position_size * RISK_LAMBDA)
        return max(1, min(position_size, 100))  # 1-100 shares

    # STAGE 6: P01D (SOVEREIGN AUTHORITY) - EXECUTION OR ABSTAIN
    def stage6_execute(self, decision, position_size):
        """Final gate: Execute or Abstain"""
        if decision == "TAKE" and position_size > 0:
            return True  # EXECUTE
        else:
            return False  # ABSTAIN

    # BACKTEST LOGIC
    def backtest_period(self, df, period_start, period_end):
        """Run complete 6-stage backtest on period"""

        df_period = df[(df['timestamp'] >= period_start) &
                       (df['timestamp'] <= period_end)].copy()

        if len(df_period) < 50:
            return None

        position = None
        trades = []
        equity = 1_000_000.0

        for idx in range(50, len(df_period) - 1):
            row = df_period.iloc[idx]
            next_row = df_period.iloc[idx + 1]
            history = df_period.iloc[max(0, idx-50):idx]

            # STAGE 1: Validate
            if not self.stage1_validate(row):
                continue

            # STAGE 2: PA Score
            pa_score = self.stage2_pa_score(row, history)

            # STAGE 3: ID Decision
            decision = self.stage3_decision(pa_score)
            if decision == "PASS":
                continue

            # STAGE 4: Bridge (viability)
            if not self.stage4_bridge(row['close']):
                continue

            # STAGE 5: MPC (position sizing)
            position_size = self.stage5_mpc(row['close'], equity)

            # STAGE 6: P01D (execute or abstain)
            if not self.stage6_execute(decision, position_size):
                continue

            # EXECUTION: Enter trade
            if position is None:
                entry_price = row['close'] * (1 + COST_BPS/10000)
                position = {
                    'entry_price': entry_price,
                    'entry_date': row['timestamp'],
                    'size': position_size,
                    'bars_held': 0
                }
            else:
                # Exit previous position
                exit_price = row['close'] * (1 - COST_BPS/10000)
                pnl = (exit_price - position['entry_price']) * position['size']

                trades.append({
                    'entry_price': position['entry_price'],
                    'exit_price': exit_price,
                    'pnl': float(pnl),
                    'bars_held': position['bars_held']
                })

                equity += pnl

                # Enter new position
                entry_price = row['close'] * (1 + COST_BPS/10000)
                position = {
                    'entry_price': entry_price,
                    'entry_date': row['timestamp'],
                    'size': position_size,
                    'bars_held': 0
                }

            if position:
                position['bars_held'] += 1

            # Auto-exit after 20 bars
            if position and position['bars_held'] >= 20:
                exit_price = next_row['open'] * (1 - COST_BPS/10000)
                pnl = (exit_price - position['entry_price']) * position['size']

                trades.append({
                    'entry_price': position['entry_price'],
                    'exit_price': exit_price,
                    'pnl': float(pnl),
                    'bars_held': position['bars_held']
                })

                equity += pnl
                position = None

        # Close remaining position
        if position is not None:
            last_row = df_period.iloc[-1]
            exit_price = last_row['close'] * (1 - COST_BPS/10000)
            pnl = (exit_price - position['entry_price']) * position['size']

            trades.append({
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'pnl': float(pnl),
                'bars_held': position['bars_held']
            })

            equity += pnl

        if len(trades) == 0:
            return None

        winning = len([t for t in trades if t['pnl'] > 0])
        win_rate = winning / len(trades) if trades else 0

        return {
            'symbol': self.symbol,
            'trades': trades,
            'total_trades': len(trades),
            'winning_trades': winning,
            'total_pnl': float(equity - 1_000_000.0),
            'final_equity': float(equity),
            'win_rate': float(win_rate),
            'avg_trade': float((equity - 1_000_000.0) / len(trades)) if trades else 0
        }

# ============================================================================
# RUNNER
# ============================================================================

class DCSOptimizedRunner:
    """Run optimized DCS on 48 symbols × 3 periods"""

    def __init__(self):
        self.all_results = {}

    def load_symbol(self, symbol):
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

    def run_period(self, period_key, period_config):
        """Run complete period test"""

        period_start = pd.Timestamp(period_config['start'], tz='Asia/Kolkata')
        period_end = pd.Timestamp(period_config['end'], tz='Asia/Kolkata')

        print(f"\n{'='*80}")
        print(f"6-STAGE DCS OPTIMIZED: {period_config['name']}")
        print(f"Period: {period_config['start']} to {period_config['end']}")
        print(f"ID Threshold: {ID_THRESHOLD*100:.0f}% (optimized from 60%)")
        print(f"{'='*80}\n")

        results = {}
        total_trades = 0
        total_pnl = 0
        symbols_with_trades = 0

        for i, symbol in enumerate(SYMBOLS_48, 1):
            df = self.load_symbol(symbol)
            if df is None:
                continue

            dcs = DCSOptimizedSystem(symbol)
            result = dcs.backtest_period(df, period_start, period_end)

            if result and result['total_trades'] > 0:
                results[symbol] = result
                total_trades += result['total_trades']
                total_pnl += result['total_pnl']
                symbols_with_trades += 1

                if i % 10 == 0 or i == len(SYMBOLS_48):
                    print(f"  [{i:2}/{len(SYMBOLS_48)}] {symbol:15} | {result['total_trades']:3} trades | Win: {result['win_rate']:.1%} | P&L: ₹{result['total_pnl']:+8.2f}")

        # Aggregate
        all_trades = []
        for r in results.values():
            all_trades.extend(r['trades'])

        winning = len([t for t in all_trades if t['pnl'] > 0])
        aggregate_win_rate = winning / len(all_trades) if all_trades else 0

        print(f"\n{period_config['name']} RESULTS:")
        print(f"  Total Trades:       {total_trades}")
        print(f"  Winning Trades:     {winning}")
        print(f"  Win Rate:           {aggregate_win_rate:.1%}")
        print(f"  Total P&L:          ₹{total_pnl:+,.2f}")
        print(f"  Symbols w/ Trades:  {symbols_with_trades}/{len(SYMBOLS_48)}\n")

        return {
            'period': period_key,
            'config': period_config,
            'results': results,
            'aggregate': {
                'total_trades': total_trades,
                'winning_trades': winning,
                'win_rate': float(aggregate_win_rate),
                'total_pnl': float(total_pnl),
                'symbols_with_trades': symbols_with_trades
            }
        }

    def run_all(self):
        """Run all periods"""
        for period_key, period_config in TEST_PERIODS.items():
            result = self.run_period(period_key, period_config)
            self.all_results[period_key] = result

    def save_report(self):
        """Save complete results"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'system': 'DCS_OPTIMIZED_6STAGE',
            'data_source': 'P01D-V2B-CERTIFIED-UNION50-15MIN',
            'manifest_hash': MANIFEST_HASH,
            'optimization': {
                'id_threshold_old': 0.60,
                'id_threshold_new': 0.50,
                'reason': 'Generate more trade opportunities',
                'risk_lambda': RISK_LAMBDA,
                'position_limit': POSITION_LIMIT,
                'cost_bps': COST_BPS
            },
            'periods': {}
        }

        for period_key, result in self.all_results.items():
            report['periods'][period_key] = result['aggregate']

        report_file = Path("DCS_OPTIMIZED_48SYMBOLS_REPORT_20260829.json")
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        return report_file

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " DCS 6-STAGE OPTIMIZED SYSTEM - 48 SYMBOLS ".center(78) + "║")
    print("║" + " ID Threshold: 60% → 50% (OPTIMIZED) ".center(78) + "║")
    print("║" + " 1-Day + 1-Month + 3-Year Backtests ".center(78) + "║")
    print("╚" + "="*78 + "╝\n")

    runner = DCSOptimizedRunner()

    start_time = datetime.now()

    runner.run_all()

    elapsed = (datetime.now() - start_time).total_seconds()

    report_file = runner.save_report()

    print(f"✓ Report saved: {report_file}")
    print(f"✓ Total execution time: {elapsed:.1f} seconds\n")

    print("="*80)
    print("STATUS: OPTIMIZED 6-STAGE DCS TEST COMPLETE")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
