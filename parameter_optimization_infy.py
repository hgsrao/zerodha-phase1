"""
PARAMETER OPTIMIZATION FRAMEWORK - INFY ONLY
3-Year Closed-Loop Tuning (Aug 2023 - Aug 2026)

Purpose: Fine-tune all constants in the DCS pipeline
Test: ID threshold, PA weighting, Risk lambda, position sizing
Measure: Sharpe ratio, Sortino ratio, win rate, P&L, max drawdown
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
from itertools import product
import warnings
warnings.filterwarnings('ignore')

print("\n" + "="*100)
print("DCS PARAMETER OPTIMIZATION - INFY ONLY (3 YEARS)")
print("="*100)
print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*100 + "\n")

# ==============================================================================
# LOAD DATA
# ==============================================================================

print("📥 Loading INFY data (3 years)...")
DATA_DIR = Path("historical_data_research_ready")
csv_file = DATA_DIR / "NSE_INFY_15minute_2023-08-14_2026-08-13.csv"

if not csv_file.exists():
    print(f"❌ Error: {csv_file} not found")
    exit(1)

df = pd.read_csv(csv_file)
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)

print(f"✅ Loaded {len(df)} candles")
print(f"   Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
print(f"   Data shape: {df.shape}")

# ==============================================================================
# FEATURE ENGINEERING
# ==============================================================================

def engineer_features(df_in, lookback=20):
    """Engineer 48 features from OHLCV data"""
    df = df_in.copy()

    # Price features
    df['price_range'] = df['high'] - df['low']
    df['body_size'] = (df['close'] - df['open']).abs()
    df['upper_wick'] = df['high'] - df[['open', 'close']].max(axis=1)
    df['lower_wick'] = df[['open', 'close']].min(axis=1) - df['low']
    df['close_position'] = (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-10)

    # Returns
    df['return_pct'] = (df['close'] - df['open']) / df['open']
    df['next_return'] = df['close'].shift(-1) / df['close'] - 1
    df['log_return'] = np.log(df['close'] / df['close'].shift(1))

    # Moving averages
    for period in [5, 10, 20]:
        df[f'sma_{period}'] = df['close'].rolling(period).mean()
        df[f'sma_dist_{period}'] = (df['close'] - df[f'sma_{period}']) / df[f'sma_{period}']

    # Volatility
    df['volatility_10'] = df['log_return'].rolling(10).std()
    df['volatility_20'] = df['log_return'].rolling(20).std()

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    df['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    ema_12 = df['close'].ewm(span=12).mean()
    ema_26 = df['close'].ewm(span=26).mean()
    df['macd'] = ema_12 - ema_26
    df['signal_line'] = df['macd'].ewm(span=9).mean()
    df['macd_hist'] = df['macd'] - df['signal_line']

    # Bollinger Bands
    sma_20 = df['close'].rolling(20).mean()
    std_20 = df['close'].rolling(20).std()
    df['bb_upper'] = sma_20 + (std_20 * 2)
    df['bb_lower'] = sma_20 - (std_20 * 2)
    df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-10)

    # Volume
    df['volume_sma'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / (df['volume_sma'] + 1e-10)

    # Momentum
    df['momentum'] = df['close'] - df['close'].shift(10)
    df['roc'] = (df['close'] - df['close'].shift(10)) / df['close'].shift(10)

    # ATR
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            np.abs(df['high'] - df['close'].shift(1)),
            np.abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].rolling(14).mean()

    # Stochastic
    low_14 = df['low'].rolling(14).min()
    high_14 = df['high'].rolling(14).max()
    df['stoch_k'] = 100 * (df['close'] - low_14) / (high_14 - low_14 + 1e-10)
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()

    # Time features
    df['hour'] = df['timestamp'].dt.hour
    df['minute'] = df['timestamp'].dt.minute
    df['day_of_week'] = df['timestamp'].dt.dayofweek

    # Fill NaNs
    df = df.bfill().ffill().fillna(0)

    return df

print("\n🔧 Engineering 48 features...")
df = engineer_features(df)
feature_cols = [col for col in df.columns if col not in ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'tr']]
print(f"✅ Created {len(feature_cols)} features")

# ==============================================================================
# PA MODEL - SIMPLIFIED PREDICTIONS
# ==============================================================================

def get_pa_score(row, features):
    """Generate PA score from features (simplified ensemble)"""
    try:
        feature_vals = row[features].astype(float).values

        # Ridge-like: linear combination of key features
        key_features = ['momentum', 'rsi', 'macd_hist', 'roc', 'stoch_k']
        ridge_score = 0
        for feat in key_features:
            if feat in row.index:
                ridge_score += row[feat] / 5.0
        ridge_score = np.clip(ridge_score / len(key_features), 0, 1)

        # XGBoost-like: non-linear combination
        xgb_score = 0
        if row['close'] > row['sma_20']:
            xgb_score += 0.15
        if row['rsi'] > 50:
            xgb_score += 0.15
        if row['macd_hist'] > 0:
            xgb_score += 0.20
        if row['volume_ratio'] > 1:
            xgb_score += 0.10
        if row['close'] > row['bb_lower'] and row['close'] < row['bb_upper']:
            xgb_score += 0.10
        xgb_score = np.clip(xgb_score, 0, 1)

        # Ensemble
        pa_score = (ridge_score + xgb_score) / 2.0
        return np.clip(pa_score, 0, 1)
    except:
        return 0.5

# ==============================================================================
# BACKTEST ENGINE
# ==============================================================================

class DCSBacktestOptimizer:
    def __init__(self, df_in, id_threshold, risk_lambda, position_limit, cost_bps=2):
        self.df = df_in.copy()
        self.id_threshold = id_threshold / 100.0  # Convert to 0.0-1.0
        self.risk_lambda = risk_lambda
        self.position_limit = position_limit
        self.cost_bps = cost_bps
        self.trades = []
        self.position = None
        self.daily_pnl = {}
        self.monthly_pnl = {}

    def run(self):
        """Run complete backtest"""
        for idx in range(20, len(self.df)):  # Need 20 bars lookback
            self._process_candle(idx)
        return self._get_results()

    def _process_candle(self, idx):
        """Process one candle through all 6 stages"""
        row = self.df.iloc[idx]
        timestamp = row['timestamp']
        close_price = row['close']

        # Stage 1: Data Input (always passes)
        # Stage 2: PA Score
        pa_score = get_pa_score(row, feature_cols)

        # Stage 3: ID Discrimination
        id_decision = "TAKE" if pa_score >= self.id_threshold else "PASS"

        # Stage 4: Bridge (economic validation)
        last_20_low = self.df.iloc[idx-20:idx]['low'].min()
        signal_price = last_20_low * 1.005
        bridge_valid = close_price >= signal_price and id_decision == "TAKE"

        # Stage 5: MPC (position sizing)
        if bridge_valid and self.position is None:
            position_size = int(self.risk_lambda * 1)  # 1 share base
            self.position = {
                'entry_idx': idx,
                'entry_price': close_price,
                'entry_time': timestamp,
                'qty': position_size,
                'pa_score': pa_score,
                'id_decision': id_decision
            }

        # Stage 6: P01D (exit logic)
        if self.position is not None:
            entry_price = self.position['entry_price']
            entry_idx = self.position['entry_idx']
            candles_held = idx - entry_idx

            # Exit conditions
            stop_loss = close_price < (entry_price * 0.995)
            take_profit = candles_held >= 10
            time_exit = candles_held >= 30

            if stop_loss or take_profit or time_exit:
                # Close trade
                gross = (close_price - entry_price) * self.position['qty']
                comm = (entry_price + close_price) * self.position['qty'] * (self.cost_bps / 10000)
                net = gross - comm

                trade = {
                    'entry_time': self.position['entry_time'],
                    'exit_time': timestamp,
                    'entry_price': entry_price,
                    'exit_price': close_price,
                    'qty': self.position['qty'],
                    'gross_pnl': gross,
                    'net_pnl': net,
                    'return_pct': (net / entry_price / self.position['qty']) * 100,
                    'hold_candles': candles_held,
                    'pa_score': self.position['pa_score']
                }
                self.trades.append(trade)

                # Track daily/monthly P&L
                date = timestamp.date()
                month = timestamp.strftime('%Y-%m')
                self.daily_pnl[date] = self.daily_pnl.get(date, 0) + net
                self.monthly_pnl[month] = self.monthly_pnl.get(month, 0) + net

                self.position = None

    def _get_results(self):
        """Calculate performance metrics"""
        if not self.trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'avg_pnl': 0,
                'sharpe': 0,
                'sortino': 0,
                'max_drawdown': 0,
                'params': {
                    'id_threshold': self.id_threshold * 100,
                    'risk_lambda': self.risk_lambda,
                    'position_limit': self.position_limit,
                    'cost_bps': self.cost_bps
                }
            }

        pnls = np.array([t['net_pnl'] for t in self.trades])
        returns = np.array([t['return_pct'] for t in self.trades])

        winning = sum(1 for p in pnls if p > 0)
        losing = sum(1 for p in pnls if p <= 0)

        total_pnl = np.sum(pnls)
        avg_pnl = np.mean(pnls)

        # Sharpe Ratio (annualized, 252 trading days)
        if np.std(returns) > 0:
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252)
        else:
            sharpe = 0

        # Sortino Ratio (downside deviation)
        downside_returns = returns[returns < 0]
        if len(downside_returns) > 0:
            downside_std = np.std(downside_returns)
            sortino = (np.mean(returns) / downside_std) * np.sqrt(252) if downside_std > 0 else 0
        else:
            sortino = 0

        # Max Drawdown
        cumulative = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / (running_max + 1e-10)
        max_dd = np.min(drawdown)

        return {
            'total_trades': len(self.trades),
            'winning_trades': winning,
            'losing_trades': losing,
            'win_rate': (winning / len(self.trades) * 100) if self.trades else 0,
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(avg_pnl, 2),
            'sharpe': round(sharpe, 4),
            'sortino': round(sortino, 4),
            'max_drawdown': round(max_dd * 100, 2),
            'daily_pnl_count': len(self.daily_pnl),
            'monthly_pnl_count': len(self.monthly_pnl),
            'params': {
                'id_threshold': self.id_threshold * 100,
                'risk_lambda': self.risk_lambda,
                'position_limit': self.position_limit,
                'cost_bps': self.cost_bps
            }
        }

# ==============================================================================
# PARAMETER GRID FOR OPTIMIZATION
# ==============================================================================

print("\n🔄 CLOSED-LOOP PARAMETER OPTIMIZATION\n")

# Parameters to test
id_thresholds = [50, 55, 60, 65, 70]      # ID confidence threshold (%)
risk_lambdas = [0.5, 0.75, 1.0, 1.25]     # Risk adjustment factor
position_limits = [10, 15, 20, 25]         # Max position size (%)
cost_bps_list = [1, 2, 3]                  # Trading costs (bps)

# Run optimization
results = []
total_combos = len(id_thresholds) * len(risk_lambdas) * len(position_limits) * len(cost_bps_list)
combo_idx = 0

print(f"Testing {total_combos} parameter combinations...\n")

for id_th, risk_l, pos_l, cost in product(id_thresholds, risk_lambdas, position_limits, cost_bps_list):
    combo_idx += 1

    engine = DCSBacktestOptimizer(df, id_th, risk_l, pos_l, cost)
    result = engine.run()
    result['combo_index'] = combo_idx
    results.append(result)

    if combo_idx % 5 == 0:
        print(f"  Completed {combo_idx}/{total_combos} combinations...")

print(f"\n✅ All {total_combos} combinations tested!\n")

# ==============================================================================
# ANALYSIS & RANKING
# ==============================================================================

print("="*100)
print("TOP 10 PARAMETER COMBINATIONS (RANKED BY SHARPE RATIO)")
print("="*100 + "\n")

results_df = pd.DataFrame(results)
results_df = results_df.sort_values('sharpe', ascending=False)

print(f"{'Rank':<5} {'Sharpe':<10} {'Sortino':<10} {'Win%':<8} {'Total P&L':<12} {'Trades':<8} {'Drawdown%':<12} {'ID%':<6} {'λ':<6} {'PosSz%':<8} {'Cost bps':<10}")
print("-" * 130)

for idx, row in results_df.head(10).iterrows():
    print(f"{row['combo_index']:<5} {row['sharpe']:<10.4f} {row['sortino']:<10.4f} {row['win_rate']:<8.1f} {row['total_pnl']:<12.2f} {row['total_trades']:<8} {row['max_drawdown']:<12.2f}% {row['params']['id_threshold']:<6.0f} {row['params']['risk_lambda']:<6.2f} {row['params']['position_limit']:<8.0f} {row['params']['cost_bps']:<10}")

# ==============================================================================
# BEST PARAMETERS
# ==============================================================================

best_result = results_df.iloc[0]
best_params = best_result['params']

print("\n" + "="*100)
print("🏆 OPTIMAL PARAMETERS (HIGHEST SHARPE RATIO)")
print("="*100)
print(f"\nID Threshold:       {best_params['id_threshold']:.0f}% (currently frozen at 60%)")
print(f"Risk Lambda (λ):    {best_params['risk_lambda']:.2f} (currently frozen at 1.0)")
print(f"Position Limit:     {best_params['position_limit']:.0f}% (currently frozen at 20%)")
print(f"Cost (bps):         {best_params['cost_bps']:.0f} (currently frozen at 2 bps)")
print(f"\nPerformance Metrics:")
print(f"  • Total Trades:      {best_result['total_trades']}")
print(f"  • Winning Trades:    {best_result['winning_trades']}")
print(f"  • Win Rate:          {best_result['win_rate']:.1f}%")
print(f"  • Total P&L:         ₹{best_result['total_pnl']:.2f}")
print(f"  • Avg P&L/Trade:     ₹{best_result['avg_pnl']:.2f}")
print(f"  • Sharpe Ratio:      {best_result['sharpe']:.4f}")
print(f"  • Sortino Ratio:     {best_result['sortino']:.4f}")
print(f"  • Max Drawdown:      {best_result['max_drawdown']:.2f}%")
print(f"  • Days Tested:       {best_result['daily_pnl_count']}")
print(f"  • Months Tested:     {best_result['monthly_pnl_count']}")

# ==============================================================================
# SENSITIVITY ANALYSIS
# ==============================================================================

print("\n" + "="*100)
print("📊 SENSITIVITY ANALYSIS - IMPACT OF EACH PARAMETER")
print("="*100 + "\n")

# ID Threshold sensitivity
print("ID THRESHOLD Impact (keeping other params at best values):")
id_results = results_df[
    (results_df['params'].apply(lambda x: x['risk_lambda']) == best_params['risk_lambda']) &
    (results_df['params'].apply(lambda x: x['position_limit']) == best_params['position_limit']) &
    (results_df['params'].apply(lambda x: x['cost_bps']) == best_params['cost_bps'])
].sort_values('params')
print(f"{'ID%':<8} {'Sharpe':<10} {'Win%':<8} {'Trades':<8} {'P&L':<12}")
print("-" * 50)
for idx, row in id_results.iterrows():
    print(f"{row['params']['id_threshold']:<8.0f} {row['sharpe']:<10.4f} {row['win_rate']:<8.1f} {row['total_trades']:<8} {row['total_pnl']:<12.2f}")

# Risk Lambda sensitivity
print("\nRISK LAMBDA (λ) Impact:")
lambda_results = results_df[
    (results_df['params'].apply(lambda x: x['id_threshold']) == best_params['id_threshold']) &
    (results_df['params'].apply(lambda x: x['position_limit']) == best_params['position_limit']) &
    (results_df['params'].apply(lambda x: x['cost_bps']) == best_params['cost_bps'])
]
print(f"{'λ':<8} {'Sharpe':<10} {'Win%':<8} {'Trades':<8} {'P&L':<12}")
print("-" * 50)
for idx, row in lambda_results.iterrows():
    print(f"{row['params']['risk_lambda']:<8.2f} {row['sharpe']:<10.4f} {row['win_rate']:<8.1f} {row['total_trades']:<8} {row['total_pnl']:<12.2f}")

# ==============================================================================
# SAVE RESULTS
# ==============================================================================

print("\n" + "="*100)
print("💾 SAVING OPTIMIZATION RESULTS")
print("="*100 + "\n")

# Save all results
results_json = json.dumps(
    {
        'optimization_date': datetime.now().isoformat(),
        'symbol': 'INFY',
        'data_period': '3 years (2023-08-14 to 2026-08-13)',
        'total_combinations_tested': total_combos,
        'best_parameters': best_params,
        'best_metrics': {
            'sharpe': best_result['sharpe'],
            'sortino': best_result['sortino'],
            'win_rate': best_result['win_rate'],
            'total_pnl': best_result['total_pnl'],
            'max_drawdown': best_result['max_drawdown']
        },
        'all_results': results
    },
    indent=2
)

with open('INFY_PARAMETER_OPTIMIZATION_RESULTS.json', 'w') as f:
    f.write(results_json)

print("✅ Saved: INFY_PARAMETER_OPTIMIZATION_RESULTS.json")

# Save summary report
summary = f"""
INFY PARAMETER OPTIMIZATION REPORT
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

ANALYSIS PERIOD: Aug 14, 2023 - Aug 13, 2026 (3 years)
TOTAL COMBINATIONS TESTED: {total_combos}

BEST PARAMETERS FOUND:
  ID Threshold:    {best_params['id_threshold']:.0f}%
  Risk Lambda:     {best_params['risk_lambda']:.2f}
  Position Limit:  {best_params['position_limit']:.0f}%
  Cost (bps):      {best_params['cost_bps']:.0f}

PERFORMANCE:
  Total Trades:    {best_result['total_trades']}
  Win Rate:        {best_result['win_rate']:.1f}%
  Total P&L:       ₹{best_result['total_pnl']:.2f}
  Sharpe Ratio:    {best_result['sharpe']:.4f}
  Sortino Ratio:   {best_result['sortino']:.4f}
  Max Drawdown:    {best_result['max_drawdown']:.2f}%

RECOMMENDATION:
  Current frozen params (60% ID, 1.0 λ, 20% limit, 2 bps) are
  {'OPTIMAL' if best_params['id_threshold'] == 60 and best_params['risk_lambda'] == 1.0 else 'SUB-OPTIMAL'}

  Consider adjusting to optimal values above for potential improvement.
"""

with open('INFY_PARAMETER_OPTIMIZATION_SUMMARY.txt', 'w') as f:
    f.write(summary)

print("✅ Saved: INFY_PARAMETER_OPTIMIZATION_SUMMARY.txt")

print("\n" + "="*100)
print("✅ OPTIMIZATION COMPLETE")
print("="*100)
print(f"\nEnd Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"\nFiles generated:")
print(f"  1. INFY_PARAMETER_OPTIMIZATION_RESULTS.json (complete results)")
print(f"  2. INFY_PARAMETER_OPTIMIZATION_SUMMARY.txt (summary report)")
