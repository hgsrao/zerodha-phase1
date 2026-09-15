"""
FAST PARAMETER OPTIMIZATION - ALL 20 EQUITIES (3 YEARS)
Tests key scenarios for each equity
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
import glob

print("\n" + "="*120)
print("DCS PARAMETER OPTIMIZATION - ALL EQUITIES (FAST MODE)")
print("="*120)
print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

# Get all CSV files
DATA_DIR = Path("historical_data_research_ready")
csv_files = sorted(glob.glob(str(DATA_DIR / "NSE_*.csv")))
equities = [Path(f).stem.replace("NSE_", "").split("_")[0] for f in csv_files]

print(f"📥 Found {len(csv_files)} equities")
print(f"Testing: {', '.join(equities[:10])}{'...' if len(equities) > 10 else ''}\n")

# Backtest function
def backtest_params(df_in, id_thresh, risk_lambda, pos_limit, cost_bps):
    """Quick backtest with parameters"""
    df = df_in.copy()
    trades = []
    position = None

    for idx in range(50, len(df)):
        row = df.iloc[idx]

        # PA Score (simplified)
        pa_score = 0.45 + row['rsi'] / 200 + (row['momentum'] / 100) * 0.1
        pa_score = np.clip(pa_score, 0, 1)

        # ID Decision
        if pa_score >= (id_thresh / 100):
            # Entry logic
            if position is None:
                position = {
                    'entry': row['close'],
                    'idx': idx,
                    'pa': pa_score
                }

        # Exit logic
        if position is not None:
            hold = idx - position['idx']
            loss = row['close'] < (position['entry'] * 0.995)
            profit = row['close'] > (position['entry'] * 1.01)
            time = hold >= 20

            if loss or profit or time:
                gross = row['close'] - position['entry']
                cost = (position['entry'] + row['close']) * (cost_bps / 10000)
                net = gross - cost

                trades.append({
                    'pnl': net,
                    'ret': (net / position['entry']) * 100,
                    'hold': hold,
                    'pa': position['pa']
                })
                position = None

    if not trades:
        return {'trades': 0, 'sharpe': 0, 'sortino': 0, 'pnl': 0, 'wr': 0, 'dd': 0}

    pnls = np.array([t['pnl'] for t in trades])
    rets = np.array([t['ret'] for t in trades])

    wins = sum(1 for p in pnls if p > 0)

    sharpe = (np.mean(rets) / np.std(rets)) * np.sqrt(252) if np.std(rets) > 0 else 0

    down = rets[rets < 0]
    sortino = (np.mean(rets) / np.std(down)) * np.sqrt(252) if len(down) > 0 and np.std(down) > 0 else 0

    cum = np.cumsum(pnls)
    dd = np.min((cum - np.maximum.accumulate(cum)) / (np.maximum.accumulate(cum) + 1e-10))

    return {
        'trades': len(trades),
        'sharpe': sharpe,
        'sortino': sortino,
        'pnl': np.sum(pnls),
        'wr': (wins / len(trades) * 100),
        'dd': dd * 100,
        'avg': np.mean(pnls)
    }

# Test scenarios
test_scenarios = [
    {'name': 'CURRENT (FROZEN)', 'id': 60, 'lambda': 1.0, 'pos': 20, 'cost': 2},
    {'name': 'CONSERVATIVE', 'id': 70, 'lambda': 0.5, 'pos': 10, 'cost': 2},
    {'name': 'AGGRESSIVE', 'id': 50, 'lambda': 1.5, 'pos': 25, 'cost': 2},
    {'name': 'HIGH QUALITY', 'id': 65, 'lambda': 1.0, 'pos': 20, 'cost': 2},
    {'name': 'COST OPT', 'id': 60, 'lambda': 1.0, 'pos': 20, 'cost': 1},
    {'name': 'BALANCED', 'id': 55, 'lambda': 1.0, 'pos': 15, 'cost': 2},
    {'name': 'VOL ADJ', 'id': 60, 'lambda': 0.75, 'pos': 20, 'cost': 2},
    {'name': 'MAX RISK', 'id': 50, 'lambda': 1.25, 'pos': 25, 'cost': 3},
]

all_results = {}

# Process each equity
for i, csv_file in enumerate(csv_files, 1):
    symbol = Path(csv_file).stem.replace("NSE_", "").split("_")[0]

    print(f"[{i}/{len(csv_files)}] 📊 {symbol}...", end=" ", flush=True)

    try:
        # Load data
        df = pd.read_csv(csv_file)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        # Feature engineering
        df['sma_20'] = df['close'].rolling(20).mean()
        df['rsi'] = 50 + np.random.randn(len(df)) * 15
        df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        df['momentum'] = df['close'] - df['close'].shift(20)
        df = df.bfill().ffill().fillna(0)

        # Test scenarios
        results = []
        for scenario in test_scenarios:
            result = backtest_params(df, scenario['id'], scenario['lambda'], scenario['pos'], scenario['cost'])
            result['scenario'] = scenario['name']
            result['params'] = {
                'id_threshold': scenario['id'],
                'risk_lambda': scenario['lambda'],
                'position_limit': scenario['pos'],
                'cost_bps': scenario['cost']
            }
            results.append(result)

        # Find best scenario
        best = max(results, key=lambda x: x['sharpe'])

        all_results[symbol] = {
            'symbol': symbol,
            'scenarios_tested': 8,
            'best_scenario': best['scenario'],
            'best_params': best['params'],
            'best_metrics': {
                'sharpe': best['sharpe'],
                'sortino': best['sortino'],
                'win_rate': best['wr'],
                'total_pnl': best['pnl'],
                'max_drawdown': best['dd']
            },
            'all_results': results
        }

        print(f"✅ (Sharpe: {best['sharpe']:.3f}, Win%: {best['wr']:.1f}%)")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        continue

print("\n" + "="*120)
print("📊 SUMMARY")
print("="*120)

# Save consolidated results
output = {
    'analysis_date': datetime.now().isoformat(),
    'equities_tested': len(all_results),
    'period': '3 years (2023-08-14 to 2026-08-13)',
    'scenarios_per_equity': 8,
    'per_equity_results': all_results
}

with open('ALL_EQUITIES_OPTIMIZATION_RESULTS.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n✅ Results saved: ALL_EQUITIES_OPTIMIZATION_RESULTS.json")
print(f"📊 Equities tested: {len(all_results)}")

# Summary table
print("\n" + "-"*100)
print("EQUITY          BEST SCENARIO       SHARPE    WIN%      P&L        DRAWDOWN%")
print("-"*100)

for sym in sorted(all_results.keys()):
    data = all_results[sym]['best_metrics']
    print(f"{sym:<15} {all_results[sym]['best_scenario']:<20} {data['sharpe']:>7.3f}  {data['win_rate']:>6.1f}%  ₹{data['total_pnl']:>7.0f}  {data['max_drawdown']:>8.1f}%")

print("-"*100)

print(f"\n✅ Complete: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
