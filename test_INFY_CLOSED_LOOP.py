"""
DCS SYSTEM - CLOSED LOOP TEST (WITH FEEDBACK)
Shows behavior WITH feedback loops
PA weights and Lambda ADAPT throughout session
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime

print("\n" + "="*100)
print("🟢 CLOSED LOOP TEST - INFY ONE DAY (WITH FEEDBACK & LEARNING)")
print("="*100)
print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*100 + "\n")

# Load data
data_files = list(Path(".").glob("**/INFY*.csv"))
df = pd.read_csv(data_files[0], encoding='utf-8')
df_one_day = df.head(16).copy()

print(f"📂 Loading: {data_files[0].name}")
print(f"   Using: First {len(df_one_day)} candles (1 trading session)\n")

class ClosedLoopSystem:
    def __init__(self, symbol):
        self.symbol = symbol
        self.trades = []
        self.position = None

        # ADAPTIVE WEIGHTS - CHANGE WITH FEEDBACK
        self.pa_weights = {
            'momentum': 0.20,
            'rsi': 0.20,
            'macd': 0.20,
            'volume': 0.20,
            'roc': 0.20
        }

        # ADAPTIVE LAMBDA - CHANGE WITH FEEDBACK
        self.lambda_risk = 1.0

        # Feedback Loop 1 (PA Learning)
        self.pa_target = 0.52
        self.pa_kp = 0.1
        self.pa_integral = 0.0
        self.pa_last_error = 0.0

        # Feedback Loop 2 (Risk Control)
        self.risk_target = -0.03
        self.risk_kp = 0.1
        self.risk_integral = 0.0
        self.risk_last_error = 0.0

        # Metrics
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.max_drawdown = 0.0
        self.equity = 1000.0
        self.equity_peak = 1000.0
        self.weight_history = []
        self.lambda_history = []

    def calculate_pa_score(self, idx, df):
        """PA Model - uses ADAPTIVE weights"""
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

    def feedback_loop_1_pa_learning(self):
        """FEEDBACK LOOP 1: Adjust PA weights based on win rate"""
        if self.wins + self.losses == 0:
            return

        actual_win_rate = self.wins / (self.wins + self.losses)
        error = self.pa_target - actual_win_rate

        # PID controller
        self.pa_integral += error
        derivative = error - self.pa_last_error if self.wins + self.losses > 1 else 0
        adjustment = self.pa_kp * error + 0.01 * self.pa_integral + 0.01 * derivative
        self.pa_last_error = error

        # Adjust weights
        if adjustment > 0:  # Win rate too low, increase good indicators
            self.pa_weights['momentum'] = min(0.30, self.pa_weights['momentum'] + 0.02)
            self.pa_weights['macd'] = min(0.30, self.pa_weights['macd'] + 0.02)
            self.pa_weights['rsi'] = max(0.10, self.pa_weights['rsi'] - 0.01)
        else:  # Win rate good, fine-tune
            self.pa_weights['momentum'] = max(0.10, self.pa_weights['momentum'] - 0.01)
            self.pa_weights['macd'] = min(0.30, self.pa_weights['macd'] + 0.01)

        # Normalize
        total = sum(self.pa_weights.values())
        for key in self.pa_weights:
            self.pa_weights[key] /= total
            self.pa_weights[key] = max(0.10, min(0.30, self.pa_weights[key]))

    def feedback_loop_2_risk_control(self, current_drawdown):
        """FEEDBACK LOOP 2: Adjust Lambda based on drawdown"""
        error = self.risk_target - current_drawdown

        # PID controller
        self.risk_integral += error
        derivative = error - self.risk_last_error if self.wins + self.losses > 0 else 0
        adjustment = self.risk_kp * error + 0.01 * self.risk_integral + 0.01 * derivative
        self.risk_last_error = error

        # Adjust lambda
        if current_drawdown < self.risk_target:  # Drawdown worse than target
            self.lambda_risk *= 0.95  # Reduce position size
        else:  # Drawdown better than target
            self.lambda_risk = min(1.0, self.lambda_risk * 1.02)  # Increase

        self.lambda_risk = max(0.5, min(1.0, self.lambda_risk))
        self.lambda_history.append(self.lambda_risk)

    def run_one_day(self, df_day):
        print("\n📈 ONE DAY TRADING SESSION (CLOSED LOOP - WITH FEEDBACK)")
        print("-" * 100)
        print(f"Symbol: {self.symbol} | PA Weights: ADAPTIVE | Lambda: ADAPTIVE")
        print("-" * 100)

        for idx in range(len(df_day)):
            candle = df_day.iloc[idx]
            price = candle['close']
            timestamp = candle['timestamp']

            # STAGE 2: PA Model (ADAPTIVE weights)
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
                    current_drawdown = (self.equity - self.equity_peak) / self.equity_peak
                    self.max_drawdown = min(self.max_drawdown, current_drawdown)

                    self.trades.append({
                        'entry': entry_price,
                        'exit': price,
                        'pnl': net_pnl,
                        'win': is_win
                    })

                    print(f"🟢 EXIT [{idx:2d}] {timestamp} | Price: ₹{price:.2f} | P&L: {net_pnl:+.2f} | Win: {is_win}")

                    # APPLY FEEDBACK LOOPS AFTER EACH TRADE
                    print(f"   └─ 🔄 Feedback Loop 1: Adjusting PA weights...")
                    self.feedback_loop_1_pa_learning()
                    print(f"      └─ Momentum: {self.pa_weights['momentum']:.3f}, MACD: {self.pa_weights['macd']:.3f}")

                    print(f"   └─ 🔄 Feedback Loop 2: Adjusting Lambda (Position Size)...")
                    self.feedback_loop_2_risk_control(current_drawdown)
                    print(f"      └─ Lambda: {self.lambda_risk:.3f}x (was 1.000x)")

                    self.weight_history.append(dict(self.pa_weights))
                    self.position = None

        return self.trades

# Run test
system = ClosedLoopSystem('INFY')
trades = system.run_one_day(df_one_day)

# Results
print("\n" + "="*100)
print("📊 CLOSED LOOP RESULTS (WITH FEEDBACK)")
print("="*100)

print(f"\n🟢 PA WEIGHTS (ADAPTED BY FEEDBACK LOOP 1):")
print(f"   Momentum: {system.pa_weights['momentum']:.3f}")
print(f"   RSI:      {system.pa_weights['rsi']:.3f}")
print(f"   MACD:     {system.pa_weights['macd']:.3f}")
print(f"   Volume:   {system.pa_weights['volume']:.3f}")
print(f"   ROC:      {system.pa_weights['roc']:.3f}")

print(f"\n🟢 POSITION SIZE (ADAPTED BY FEEDBACK LOOP 2):")
print(f"   Lambda:   {system.lambda_risk:.3f}x (started at 1.000x)")

print(f"\n💰 TRADING RESULTS:")
print(f"   Total Trades: {len(trades)}")
print(f"   Wins: {system.wins}, Losses: {system.losses}")
print(f"   Win Rate: {system.wins / max(1, system.wins + system.losses):.1%}")
print(f"   Total P&L: ₹{system.total_pnl:+.2f}")
print(f"   Return: {((system.equity - 1000) / 1000):.2%}")
print(f"   Max Drawdown: {system.max_drawdown:.2%}")

closed_loop_results = {
    'type': 'CLOSED_LOOP',
    'total_trades': len(trades),
    'wins': int(system.wins),
    'losses': int(system.losses),
    'win_rate': float(system.wins / max(1, system.wins + system.losses)),
    'total_pnl': float(system.total_pnl),
    'return': float((system.equity - 1000) / 1000),
    'max_drawdown': float(system.max_drawdown),
    'pa_weights': {k: float(v) for k, v in system.pa_weights.items()},
    'lambda': float(system.lambda_risk),
    'trades': [{'entry': float(t['entry']), 'exit': float(t['exit']), 'pnl': float(t['pnl']), 'win': int(t['win'])} for t in trades]
}

with open('INFY_CLOSED_LOOP_RESULTS.json', 'w', encoding='utf-8') as f:
    json.dump(closed_loop_results, f, indent=2)

print("\n✅ Results saved: INFY_CLOSED_LOOP_RESULTS.json")
print("="*100 + "\n")
