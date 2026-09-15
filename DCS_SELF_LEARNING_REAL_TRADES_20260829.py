#!/usr/bin/env python3
"""
DCS SELF-LEARNING SYSTEM - VERSION 2
=====================================

Run 100 REAL PA-scored trades and let system learn optimal PA weights.

Key difference from V1:
- Generates trades only when PA score > threshold (REAL signal)
- PA weights have actual impact on entry decisions
- System learns which indicators (momentum, RSI, MACD, etc.) work best
- Shows calibrated weights after learning

This is true closed-loop learning!
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
# LEARNABLE PARAMETERS
# ============================================================================

INITIAL_CONFIG = {
    'pa_weights': {
        'momentum': 0.20,
        'rsi': 0.20,
        'macd': 0.20,
        'volume_ratio': 0.20,
        'roc': 0.20,
    },
    'id_threshold': 0.50,
    'risk_lambda': 1.0,
    'feedback_loop_1': {
        'target_win_rate': 0.52,
        'kp': 0.15,      # Increase Kp for faster learning
        'ki': 0.02,
        'kd': 0.02,
        'integral': 0.0,
        'prev_error': 0.0,
    },
    'feedback_loop_2': {
        'target_drawdown': -0.03,
        'kp': 0.08,
        'ki': 0.008,
        'kd': 0.008,
        'integral': 0.0,
        'prev_error': 0.0,
    },
}

# ============================================================================
# REAL DCS LEARNING SYSTEM
# ============================================================================

class RealDCSLearning:
    """DCS that learns from real PA-scored trades"""

    def __init__(self, config):
        self.config = config
        self.trades = []
        self.learning_history = []
        self.equity = 1_000_000.0
        self.peak_equity = 1_000_000.0
        self.trade_count = 0

    def compute_pa_score(self, row, history):
        """Calculate PA score using current LEARNABLE weights"""
        if len(history) < 20:
            return 0.5

        close_price = row['close']

        # Component 1: Momentum (20-bar)
        momentum = (close_price - history['close'].iloc[-20]) / history['close'].iloc[-20]
        momentum_score = np.clip(momentum + 0.5, 0, 1)

        # Component 2: RSI (14-bar)
        if len(history) >= 14:
            gains = 0
            losses = 0
            for i in range(1, 15):
                change = history['close'].iloc[-15+i] - history['close'].iloc[-16+i]
                if change > 0:
                    gains += change
                else:
                    losses += abs(change)
            avg_gain = gains / 14
            avg_loss = losses / 14
            rs = avg_gain / (avg_loss + 1e-6)
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

        # Component 4: Volume Ratio
        volume_ratio = row['volume'] / (history['volume'].mean() + 1e-6)
        volume_score = np.clip(volume_ratio / 2, 0, 1)

        # Component 5: Rate of Change (intrabar)
        if len(history) > 0:
            roc = (close_price - history['close'].iloc[-1]) / history['close'].iloc[-1]
            roc_score = np.clip(roc + 0.5, 0, 1)
        else:
            roc_score = 0.5

        # WEIGHTED score using LEARNABLE weights
        weights = self.config['pa_weights']
        pa_score = (
            momentum_score * weights['momentum'] +
            rsi_score * weights['rsi'] +
            macd_score * weights['macd'] +
            volume_score * weights['volume_ratio'] +
            roc_score * weights['roc']
        )

        return np.clip(pa_score, 0, 1), {
            'momentum': momentum_score,
            'rsi': rsi_score,
            'macd': macd_score,
            'volume_ratio': volume_score,
            'roc': roc_score
        }

    def feedback_loop_1_pa_learning(self):
        """PID: Adjust PA weights toward target win rate"""
        if len(self.trades) < 2:
            return None

        winning = len([t for t in self.trades if t['pnl'] > 0])
        current_wr = winning / len(self.trades)

        target_wr = self.config['feedback_loop_1']['target_win_rate']
        error = target_wr - current_wr

        fl1 = self.config['feedback_loop_1']
        fl1['integral'] += error
        derivative = error - fl1['prev_error']

        adjustment = (
            fl1['kp'] * error +
            fl1['ki'] * fl1['integral'] +
            fl1['kd'] * derivative
        )

        # Increase weights of indicators that were highest in winning trades
        if self.trades and self.trades[-1]['is_win']:
            # Reward the indicator that contributed most to this win
            components = self.trades[-1].get('components', {})
            max_component = max(components, key=components.get) if components else None
            if max_component:
                self.config['pa_weights'][max_component] = np.clip(
                    self.config['pa_weights'][max_component] + adjustment * 0.02,
                    0.05, 0.40
                )
        else:
            # If loss, reduce weights slightly
            for key in self.config['pa_weights']:
                self.config['pa_weights'][key] = np.clip(
                    self.config['pa_weights'][key] - abs(adjustment) * 0.01,
                    0.05, 0.40
                )

        # Renormalize to sum to 1.0
        total = sum(self.config['pa_weights'].values())
        for key in self.config['pa_weights']:
            self.config['pa_weights'][key] /= total

        fl1['prev_error'] = error

        return {
            'current_wr': current_wr,
            'target_wr': target_wr,
            'error': error,
            'adjustment': adjustment
        }

    def feedback_loop_2_risk_learning(self):
        """PID: Adjust Lambda based on drawdown"""
        drawdown = (self.equity - self.peak_equity) / (self.peak_equity + 1e-6)

        target_dd = self.config['feedback_loop_2']['target_drawdown']
        error = target_dd - drawdown

        fl2 = self.config['feedback_loop_2']
        fl2['integral'] += error
        derivative = error - fl2['prev_error']

        adjustment = (
            fl2['kp'] * error +
            fl2['ki'] * fl2['integral'] +
            fl2['kd'] * derivative
        )

        old_lambda = self.config['risk_lambda']
        new_lambda = np.clip(old_lambda + adjustment, 0.5, 1.5)
        self.config['risk_lambda'] = new_lambda

        fl2['prev_error'] = error

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        return {
            'current_dd': drawdown,
            'target_dd': target_dd,
            'error': error,
            'adjustment': adjustment
        }

    def execute_trade(self, entry_price, exit_price, position_size, components):
        """Execute trade and update PID controllers"""
        cost_bps = 2
        fees = (entry_price + exit_price) * position_size * cost_bps / 10000
        pnl = (exit_price - entry_price) * position_size - fees

        self.trades.append({
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'position_size': int(position_size),
            'pnl': float(pnl),
            'is_win': pnl > 0,
            'components': components
        })

        self.equity += pnl
        self.trade_count += 1

        # Learn from this trade
        fl1_result = self.feedback_loop_1_pa_learning()
        fl2_result = self.feedback_loop_2_risk_learning()

        learning_point = {
            'trade_number': self.trade_count,
            'pnl': float(pnl),
            'is_win': pnl > 0,
            'win_rate': len([t for t in self.trades if t['is_win']]) / len(self.trades),
            'equity': float(self.equity),
            'drawdown': (self.equity - self.peak_equity) / (self.peak_equity + 1e-6),
            'pa_weights': self.config['pa_weights'].copy(),
            'risk_lambda': float(self.config['risk_lambda']),
            'id_threshold': float(self.config['id_threshold']),
        }

        self.learning_history.append(learning_point)

# ============================================================================
# TRADE GENERATOR - REAL PA-SCORED TRADES
# ============================================================================

class RealTradeGenerator:
    """Generate 100 real PA-scored trades"""

    def __init__(self, system):
        self.system = system
        self.data = {}

    def load_data(self):
        """Load all symbol data"""
        for symbol in SYMBOLS_48:
            files = list(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))
            if files:
                try:
                    df = pd.read_csv(files[0])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
                    df = df.sort_values('timestamp').reset_index(drop=True)
                    self.data[symbol] = df
                except:
                    pass
        print(f"✓ Loaded {len(self.data)} symbols")

    def generate_100_real_trades(self):
        """Generate 100 trades based on real PA signals"""
        trades_generated = 0
        symbols_list = list(self.data.keys())

        while trades_generated < 100 and trades_generated < 500:  # Try up to 500 to get 100 real trades
            symbol = np.random.choice(symbols_list)
            df = self.data[symbol]

            if len(df) < 100:
                continue

            # Random start point
            start_idx = np.random.randint(50, len(df) - 30)

            row = df.iloc[start_idx]
            history = df.iloc[max(0, start_idx-50):start_idx]

            # Calculate PA score
            pa_score, components = self.system.compute_pa_score(row, history)

            # Only trade if PA score > threshold (REAL signal)
            if pa_score < self.system.config['id_threshold']:
                continue

            # Entry price
            entry_price = row['close']

            # Exit after 1-20 bars
            exit_offset = np.random.randint(1, min(20, len(df) - start_idx - 1))
            exit_row = df.iloc[start_idx + exit_offset]
            exit_price = exit_row['close']

            # Position size
            position_size = np.random.randint(5, 100)

            # Execute this trade
            self.system.execute_trade(entry_price, exit_price, position_size, components)
            trades_generated += 1

            if trades_generated % 20 == 0:
                wr = len([t for t in self.system.trades if t['is_win']]) / len(self.system.trades)
                print(f"  Trade {trades_generated:3d} | PA Score req: {self.system.config['id_threshold']:.2f} | " +
                      f"Win Rate: {wr:.1%} | Equity: ₹{self.system.equity:>11,.0f} | " +
                      f"Lambda: {self.system.config['risk_lambda']:.4f}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*80 + "╗")
    print("║" + " DCS SELF-LEARNING V2: REAL PA-SCORED TRADES ".center(80) + "║")
    print("║" + " 100 trades with feedback loop learning PA weights ".center(80) + "║")
    print("╚" + "="*80 + "╝\n")

    system = RealDCSLearning(INITIAL_CONFIG.copy())

    print("INITIAL CONFIGURATION:")
    print(f"  PA Weights:      {system.config['pa_weights']}")
    print(f"  ID Threshold:    {system.config['id_threshold']:.2f}")
    print(f"  Risk Lambda:     {system.config['risk_lambda']:.4f}")
    print(f"  Target Win Rate: {system.config['feedback_loop_1']['target_win_rate']:.1%}\n")

    generator = RealTradeGenerator(system)
    generator.load_data()

    print("GENERATING 100 REAL PA-SCORED TRADES:\n")
    start_time = datetime.now()
    generator.generate_100_real_trades()
    elapsed = (datetime.now() - start_time).total_seconds()

    # Results
    print(f"\n{'='*80}")
    print("SELF-LEARNING COMPLETE")
    print(f"{'='*80}\n")

    if len(system.trades) == 0:
        print("No trades generated. Thresholds may be too high.")
        return

    winning = len([t for t in system.trades if t['is_win']])
    total_pnl = sum([t['pnl'] for t in system.trades])
    win_rate = winning / len(system.trades)

    print(f"TRADES EXECUTED: {len(system.trades)}")
    print(f"  Winning Trades:  {winning}")
    print(f"  Win Rate:        {win_rate:.1%}")
    print(f"  Total P&L:       ₹{total_pnl:+,.2f}")
    print(f"  Final Equity:    ₹{system.equity:,.0f}")
    print(f"  Return:          {(system.equity / 1_000_000 - 1) * 100:.2f}%\n")

    print("CALIBRATED PA WEIGHTS (What System Learned):")
    print("  Component          Initial    →    Final      Change")
    print("  " + "-" * 50)
    for comp, init_val in INITIAL_CONFIG['pa_weights'].items():
        final_val = system.config['pa_weights'][comp]
        change = final_val - init_val
        print(f"  {comp:15} {init_val:.4f}  →  {final_val:.4f}  ({change:+.4f})")

    print(f"\n  Risk Lambda:     {INITIAL_CONFIG['risk_lambda']:.4f}  →  {system.config['risk_lambda']:.4f}")
    print(f"\n  Target Win Rate: {system.config['feedback_loop_1']['target_win_rate']:.1%}")
    print(f"  Achieved:        {win_rate:.1%}")

    print(f"\nLEARNING TRAJECTORY (Every 10 trades):")
    print("  Trade  Win Rate  Equity (₹)      Lambda    Top PA Weight")
    print("  " + "-" * 60)
    for i in range(0, len(system.learning_history), max(1, len(system.learning_history)//10)):
        point = system.learning_history[i]
        top_weight = max(point['pa_weights'], key=point['pa_weights'].get)
        top_val = point['pa_weights'][top_weight]
        print(f"  {point['trade_number']:3d}    {point['win_rate']:.1%}      " +
              f"₹{point['equity']:>10,.0f}  {point['risk_lambda']:.4f}    " +
              f"{top_weight} ({top_val:.3f})")

    # Save report
    report = {
        'timestamp': datetime.now().isoformat(),
        'total_trades': len(system.trades),
        'winning_trades': winning,
        'win_rate': float(win_rate),
        'total_pnl': float(total_pnl),
        'final_equity': float(system.equity),
        'return_pct': float((system.equity / 1_000_000 - 1) * 100),
        'execution_time': float(elapsed),
        'initial_config': {
            'pa_weights': INITIAL_CONFIG['pa_weights'],
            'id_threshold': INITIAL_CONFIG['id_threshold'],
            'risk_lambda': INITIAL_CONFIG['risk_lambda']
        },
        'final_config': {
            'pa_weights': system.config['pa_weights'].copy(),
            'id_threshold': float(system.config['id_threshold']),
            'risk_lambda': float(system.config['risk_lambda'])
        },
        'learning_history': system.learning_history
    }

    report_file = Path("DCS_SELF_LEARNING_REAL_TRADES_REPORT_20260829.json")
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n{'='*80}")
    print(f"✓ Report saved: {report_file}")
    print(f"✓ Execution time: {elapsed:.1f} seconds")
    print(f"{'='*80}\n")

if __name__ == '__main__':
    main()
