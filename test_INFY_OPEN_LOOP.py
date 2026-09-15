"""
DCS SYSTEM - OPEN LOOP TEST (NO FEEDBACK)
Shows behavior WITHOUT feedback loops
PA weights and Lambda stay FIXED throughout
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime

print("\n" + "="*100)
print("🔴 OPEN LOOP TEST - INFY ONE DAY (NO FEEDBACK, NO LEARNING)")
print("="*100)
print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*100 + "\n")

# Load data
data_files = list(Path(".").glob("**/INFY*.csv"))
df = pd.read_csv(data_files[0], encoding='utf-8')
df_one_day = df.head(16).copy()

print(f"📂 Loading: {data_files[0].name}")
print(f"   Using: First {len(df_one_day)} candles (1 trading session)\n")

class OpenLoopSystem:
    def __init__(self, symbol):
        self.symbol = symbol
        self.trades = []
        self.position = None

        # FIXED WEIGHTS - NEVER CHANGE
        self.pa_weights = {
            'momentum': 0.20,
            'rsi': 0.20,
            'macd': 0.20,
            'volume': 0.20,
            'roc': 0.20
        }

        # FIXED LAMBDA - NEVER CHANGE
        self.lambda_risk = 1.0

        # Metrics
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.max_drawdown = 0.0
        self.equity = 1000.0
        self.equity_peak = 1000.0

    def calculate_pa_score(self, idx, df):
        """PA Model - STATIC, never learns"""
        if idx < 5:
            return np.random.random()

        recent = df.iloc[max(0, idx-5):idx+1]
        momentum = (recent['close'].iloc[-1] - recent['close'].iloc[0]) / recent['close'].iloc[0]

        pa_score = (
            self.pa_weights['momentum'] * abs(momentum) +
            self.pa_weights['rsi'] * (np.random.random()) +
            self.pa_weights['macd'] * (np.random.random() - 0.5) +
            self.pa_weights['volume'] * (recent['volume'].mean() / (df['volume'].mean() + 1e-6)) +
            self.pa_weights['roc'] * abs((df.iloc[idx]['close'] - df.iloc[max(0, idx-3)]['close']) / df.iloc[max(0, idx-3)]['close'])
        ) / sum(self.pa_weights.values())

        return pa_score

    def run_one_day(self, df_day):
        print("\n📈 ONE DAY TRADING SESSION (OPEN LOOP - NO FEEDBACK)")
        print("-" * 100)
        print(f"Symbol: {self.symbol} | PA Weights: FIXED | Lambda: FIXED at 1.0x")
        print("-" * 100)

        for idx in range(len(df_day)):
            candle = df_day.iloc[idx]
            price = candle['close']
            timestamp = candle['timestamp']

            # STAGE 2: PA Model (STATIC)
            pa_score = self.calculate_pa_score(idx, df_day)

            # STAGE 3: ID Decision
            signal = 'BUY' if pa_score > 0.45 else 'ABSTAIN'

            # Entry
            if signal == 'BUY' and self.position is None:
                self.position = {
                    'entry_price': price,
                    'entry_idx': idx,
                    'timestamp': timestamp,
                    'pa_score': pa_score,
                    'qty': 1
                }
                print(f"🔴 BUY  [{idx:2d}] {timestamp} | Price: ₹{price:.2f} | PA: {pa_score:.2%} | Qty: {self.lambda_risk:.2f}")

            # Exit
            if self.position is not None:
                entry_price = self.position['entry_price']
                profit_loss = (price - entry_price) * self.position['qty']

                should_exit = (
                    profit_loss >= 1.0 or
                    profit_loss <= -0.5 or
                    idx - self.position['entry_idx'] >= 8
                )

                if should_exit:
                    comm = entry_price * 0.0002 + price * 0.0002
                    net_pnl = profit_loss - comm

                    is_win = net_pnl > 0
                    self.wins += is_win
                    self.losses += not is_win
                    self.total_pnl += net_pnl

                    self.equity += net_pnl
                    self.equity_peak = max(self.equity_peak, self.equity)
                    drawdown = (self.equity - self.equity_peak) / self.equity_peak
                    self.max_drawdown = min(self.max_drawdown, drawdown)

                    self.trades.append({
                        'entry': entry_price,
                        'exit': price,
                        'pnl': net_pnl,
                        'win': is_win
                    })

                    print(f"🟢 EXIT [{idx:2d}] {timestamp} | Price: ₹{price:.2f} | P&L: {net_pnl:+.2f} | Win: {is_win}")
                    self.position = None

        return self.trades

# Run test
system = OpenLoopSystem('INFY')
trades = system.run_one_day(df_one_day)

# Results
print("\n" + "="*100)
print("📊 OPEN LOOP RESULTS (NO FEEDBACK)")
print("="*100)

print(f"\n🔴 PA WEIGHTS (FIXED - NEVER CHANGED):")
print(f"   Momentum: 0.200 (unchanged)")
print(f"   RSI:      0.200 (unchanged)")
print(f"   MACD:     0.200 (unchanged)")
print(f"   Volume:   0.200 (unchanged)")
print(f"   ROC:      0.200 (unchanged)")

print(f"\n🔴 POSITION SIZE (FIXED - NEVER CHANGED):")
print(f"   Lambda:   1.00x (unchanged)")

print(f"\n💰 TRADING RESULTS:")
print(f"   Total Trades: {len(trades)}")
print(f"   Wins: {system.wins}, Losses: {system.losses}")
print(f"   Win Rate: {system.wins / max(1, system.wins + system.losses):.1%}")
print(f"   Total P&L: ₹{system.total_pnl:+.2f}")
print(f"   Return: {((system.equity - 1000) / 1000):.2%}")
print(f"   Max Drawdown: {system.max_drawdown:.2%}")

open_loop_results = {
    'type': 'OPEN_LOOP',
    'total_trades': len(trades),
    'wins': int(system.wins),
    'losses': int(system.losses),
    'win_rate': float(system.wins / max(1, system.wins + system.losses)),
    'total_pnl': float(system.total_pnl),
    'return': float((system.equity - 1000) / 1000),
    'max_drawdown': float(system.max_drawdown),
    'pa_weights': system.pa_weights,
    'lambda': float(system.lambda_risk),
    'trades': [{'entry': float(t['entry']), 'exit': float(t['exit']), 'pnl': float(t['pnl']), 'win': int(t['win'])} for t in trades]
}

with open('INFY_OPEN_LOOP_RESULTS.json', 'w', encoding='utf-8') as f:
    json.dump(open_loop_results, f, indent=2)

print("\n✅ Results saved: INFY_OPEN_LOOP_RESULTS.json")
print("="*100 + "\n")
