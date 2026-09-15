#!/usr/bin/env python3
"""
PAPER TRADING SYSTEM - 1 DAY TEST
==================================

Test the LEARNED system on NEW data (2026-08-14)

What we're testing:
✓ PA Weights learned from 100 trades
✓ Lambda (0.5) learned from 100 trades
✓ All 48 equities
✓ Full trading day with detailed trade logs

Shows:
- Every entry time, price, PA score
- Every exit time, price, P&L
- Synchronization gate status
- Virtual order placement
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime

# ============================================================================
# LEARNED CONFIGURATION (From 100-trade learning)
# ============================================================================

LEARNED_CONFIG = {
    'pa_weights': {
        'momentum': 0.1847,      # ← LEARNED
        'rsi': 0.1963,           # ← LEARNED
        'macd': 0.2163,          # ← LEARNED (strongest indicator!)
        'volume_ratio': 0.2152,  # ← LEARNED
        'roc': 0.1847,           # ← LEARNED
    },
    'id_threshold': 0.50,        # Entry requirement
    'risk_lambda': 0.5000,       # ← LEARNED (conservative position sizing)
    'cost_bps': 2,               # NSE commission
    'position_limit': 0.20,      # 20% per symbol
}

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")

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
# PAPER TRADING ENGINE
# ============================================================================

class PaperTradingEngine:
    """Simulate trading with learned system"""

    def __init__(self, config, starting_capital=1_000_000.0):
        self.config = config
        self.equity = starting_capital
        self.starting_equity = starting_capital
        self.trades = []
        self.all_data = {}

    def load_symbol(self, symbol):
        """Load symbol data"""
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

    def compute_pa_score(self, row, history):
        """Calculate PA score using learned weights"""
        if len(history) < 20:
            return 0.5, {}

        close = row['close']

        # Component 1: Momentum
        momentum = (close - history['close'].iloc[-20]) / history['close'].iloc[-20]
        momentum_score = np.clip(momentum + 0.5, 0, 1)

        # Component 2: RSI
        if len(history) >= 14:
            gains = losses = 0
            for i in range(1, 15):
                ch = history['close'].iloc[-15+i] - history['close'].iloc[-16+i]
                if ch > 0:
                    gains += ch
                else:
                    losses += abs(ch)
            rs = gains / (losses + 1e-6)
            rsi = 100 - (100 / (1 + rs))
            rsi_score = rsi / 100.0
        else:
            rsi_score = 0.5

        # Component 3: MACD
        if len(history) >= 26:
            ema12 = history['close'].ewm(span=12).mean().iloc[-1]
            ema26 = history['close'].ewm(span=26).mean().iloc[-1]
            macd = ema12 - ema26
            macd_score = np.clip(macd / 10 + 0.5, 0, 1)
        else:
            macd_score = 0.5

        # Component 4: Volume
        volume_ratio = row['volume'] / (history['volume'].mean() + 1e-6)
        volume_score = np.clip(volume_ratio / 2, 0, 1)

        # Component 5: ROC
        roc = (close - history['close'].iloc[-1]) / history['close'].iloc[-1]
        roc_score = np.clip(roc + 0.5, 0, 1)

        # Weighted score using LEARNED weights
        weights = self.config['pa_weights']
        pa_score = (
            momentum_score * weights['momentum'] +
            rsi_score * weights['rsi'] +
            macd_score * weights['macd'] +
            volume_score * weights['volume_ratio'] +
            roc_score * weights['roc']
        )

        components = {
            'momentum': momentum_score,
            'rsi': rsi_score,
            'macd': macd_score,
            'volume': volume_score,
            'roc': roc_score
        }

        return np.clip(pa_score, 0, 1), components

    def check_synchronization_gate(self, row, history):
        """Check if price/volume are synchronized (gate condition)"""
        if len(history) < 3:
            return True, "Insufficient history"

        # dP/dt: Price momentum (last 3 bars)
        price_change = row['close'] - history['close'].iloc[-3]
        dp_dt = "UP" if price_change > 0 else "DOWN"

        # dV/dt: Volume trend (last 3 bars)
        vol_change = row['volume'] - history['volume'].mean()
        dv_dt = "UP" if vol_change > 0 else "DOWN"

        # Phase alignment: Price and volume moving same direction?
        aligned = (dp_dt == "UP" and dv_dt == "UP") or (dp_dt == "DOWN" and dv_dt == "DOWN")

        return aligned, f"Price:{dp_dt} Volume:{dv_dt} Aligned:{aligned}"

    def execute_trade(self, symbol, bar_idx, df, entry_row, pa_score, components):
        """Execute a single trade: entry at current bar, exit at next bar"""
        if bar_idx >= len(df) - 1:
            return None

        exit_row = df.iloc[bar_idx + 1]

        # Entry
        entry_price = entry_row['close']
        entry_time = entry_row['timestamp']

        # Position sizing
        max_position_value = self.equity * self.config['position_limit']
        position_size = int(max_position_value / entry_price)
        position_size = int(position_size * self.config['risk_lambda'])  # Apply learned lambda
        position_size = max(1, min(position_size, 1000))

        # Entry cost
        entry_cost = position_size * entry_price * self.config['cost_bps'] / 10000

        # Exit
        exit_price = exit_row['close']
        exit_time = exit_row['timestamp']

        # Exit cost
        exit_cost = position_size * exit_price * self.config['cost_bps'] / 10000

        # P&L
        gross_pnl = (exit_price - entry_price) * position_size
        total_cost = entry_cost + exit_cost
        net_pnl = gross_pnl - total_cost

        self.equity += net_pnl

        # Synchronization gate
        sync_ok, sync_msg = self.check_synchronization_gate(entry_row, df.iloc[max(0, bar_idx-3):bar_idx])

        # Record trade
        trade = {
            'symbol': symbol,
            'entry_time': str(entry_time),
            'entry_price': float(entry_price),
            'exit_time': str(exit_time),
            'exit_price': float(exit_price),
            'position_size': int(position_size),
            'entry_cost': float(entry_cost),
            'exit_cost': float(exit_cost),
            'gross_pnl': float(gross_pnl),
            'total_cost': float(total_cost),
            'net_pnl': float(net_pnl),
            'is_win': net_pnl > 0,
            'pa_score': float(pa_score),
            'components': {k: float(v) for k, v in components.items()},
            'sync_gate': sync_ok,
            'sync_msg': sync_msg,
        }

        self.trades.append(trade)
        return trade

    def run_1day(self, start_date, end_date):
        """Run trading for a period on all 48 symbols"""
        print(f"\n{'='*100}")
        print(f"PAPER TRADING: {start_date} to {end_date} | LEARNED SYSTEM ON 48 EQUITIES")
        print(f"{'='*100}\n")

        start_ts = pd.Timestamp(start_date, tz='Asia/Kolkata')
        end_ts = pd.Timestamp(end_date, tz='Asia/Kolkata')
        symbols_traded = 0
        total_entries = 0

        for i, symbol in enumerate(SYMBOLS_48, 1):
            df = self.load_symbol(symbol)
            if df is None:
                continue

            # Filter to this period
            df_period = df[(df['timestamp'] >= start_ts) & (df['timestamp'] <= end_ts)].copy()

            if len(df_period) < 50:
                continue

            symbol_trades = 0

            # Process each bar
            for idx in range(50, len(df_period) - 1):
                row = df_period.iloc[idx]
                history = df_period.iloc[max(0, idx-50):idx]

                # PA score
                pa_score, components = self.compute_pa_score(row, history)

                # ID threshold check
                if pa_score < self.config['id_threshold']:
                    continue

                # Execute trade
                full_idx = df[df['timestamp'] == row['timestamp']].index[0]
                trade = self.execute_trade(symbol, full_idx, df, row, pa_score, components)

                if trade:
                    symbol_trades += 1
                    total_entries += 1

            if symbol_trades > 0:
                symbols_traded += 1
                print(f"  [{i:2}/{len(SYMBOLS_48)}] {symbol:15} | {symbol_trades:2} trades")

        return symbols_traded, total_entries

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*98 + "╗")
    print("║" + " PAPER TRADING - LEARNED SYSTEM VALIDATION ".center(98) + "║")
    print("║" + " Test on NEW data (2026-08-10 to 2026-08-14) with learned PA weights ".center(98) + "║")
    print("║" + " All 48 equities - Virtual trading, NO real money ".center(98) + "║")
    print("╚" + "="*98 + "╝\n")

    print("LEARNED CONFIGURATION (from 100-trade learning):")
    print(f"  PA Weights:   {LEARNED_CONFIG['pa_weights']}")
    print(f"  Lambda:       {LEARNED_CONFIG['risk_lambda']} (learned position sizing)")
    print(f"  Threshold:    {LEARNED_CONFIG['id_threshold']}")
    print(f"  Starting Capital: ₹1,000,000\n")

    # Run paper trading for 5-day period (2026-08-10 to 2026-08-14)
    engine = PaperTradingEngine(LEARNED_CONFIG)

    start_date = '2026-08-10'
    end_date = '2026-08-14'
    print(f"Loading data for {start_date} to {end_date}...\n")

    symbols_traded, total_entries = engine.run_1day(start_date, end_date)

    # Results
    print(f"\n{'='*100}")
    print("PAPER TRADING RESULTS - 5 DAY PERIOD")
    print(f"{'='*100}\n")

    if len(engine.trades) == 0:
        print("No trades executed on this date.")
        return

    print(f"📊 TRADING SUMMARY:")
    print(f"   Total trades:        {len(engine.trades)}")
    print(f"   Symbols with trades: {symbols_traded}/48")
    print(f"   Total entries scanned: {total_entries}\n")

    winning = len([t for t in engine.trades if t['is_win']])
    total_pnl = sum([t['net_pnl'] for t in engine.trades])
    win_rate = winning / len(engine.trades) if engine.trades else 0

    print(f"💰 P&L RESULTS:")
    print(f"   Winning trades:      {winning}")
    print(f"   Losing trades:       {len(engine.trades) - winning}")
    print(f"   Win rate:            {win_rate:.1%}")
    print(f"   Total P&L:           ₹{total_pnl:+,.2f}")
    print(f"   Starting equity:     ₹{engine.starting_equity:,.2f}")
    print(f"   Final equity:        ₹{engine.equity:,.2f}")
    print(f"   Return:              {(engine.equity / engine.starting_equity - 1) * 100:+.2f}%\n")

    # Sync gate stats
    sync_passed = len([t for t in engine.trades if t['sync_gate']])
    print(f"🛡️  SYNCHRONIZATION GATE:")
    print(f"   Passed:              {sync_passed}/{len(engine.trades)} ({sync_passed/len(engine.trades)*100:.1f}%)")
    print(f"   Failed:              {len(engine.trades) - sync_passed}\n")

    # Detailed trades
    print(f"📋 DETAILED TRADES:\n")
    print(f"{'Symbol':10} {'Entry Time':25} {'Entry Price':12} {'Exit Price':12} {'Pos Size':8} {'P&L':12} {'Win':5}")
    print(f"{'-'*100}")

    for trade in engine.trades[:20]:  # Show first 20
        print(f"{trade['symbol']:10} {str(trade['entry_time'])[-8:]:25} "
              f"₹{trade['entry_price']:>10.2f} ₹{trade['exit_price']:>10.2f} "
              f"{trade['position_size']:>7} ₹{trade['net_pnl']:>10.2f} "
              f"{'✓' if trade['is_win'] else '✗':>5}")

    if len(engine.trades) > 20:
        print(f"\n... and {len(engine.trades) - 20} more trades\n")

    # PA score distribution
    pa_scores = [t['pa_score'] for t in engine.trades]
    print(f"\n📊 PA SCORE DISTRIBUTION:")
    print(f"   Min:                 {min(pa_scores):.3f}")
    print(f"   Max:                 {max(pa_scores):.3f}")
    print(f"   Mean:                {np.mean(pa_scores):.3f}")

    # Save detailed report
    report = {
        'timestamp': datetime.now().isoformat(),
        'test_period': f"{start_date} to {end_date}",
        'configuration': LEARNED_CONFIG,
        'summary': {
            'total_trades': len(engine.trades),
            'winning_trades': winning,
            'win_rate': float(win_rate),
            'total_pnl': float(total_pnl),
            'final_equity': float(engine.equity),
            'return_pct': float((engine.equity / engine.starting_equity - 1) * 100),
            'symbols_traded': symbols_traded,
            'sync_gate_passed': sync_passed,
        },
        'trades': engine.trades
    }

    report_file = Path(f"PAPER_TRADING_5DAY_{start_date.replace('-', '')}_TO_{end_date.replace('-', '')}_REPORT_20260829.json")
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n{'='*100}")
    print(f"✓ Report saved: {report_file}")
    print(f"{'='*100}\n")

if __name__ == '__main__':
    main()
