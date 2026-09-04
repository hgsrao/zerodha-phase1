#!/usr/bin/env python3
"""Run corrected backtest on 5 symbols first"""
from backtest_corrected import BacktestCorrected
import json

print("\n" + "="*90)
print("PHASE 2 CORRECTED - 5 SYMBOL VALIDATION")
print("="*90)

symbols_5 = ['INFY', 'TCS', 'RELIANCE', 'SUNPHARMA', 'HDFCLIFE']

engine = BacktestCorrected(initial_capital=1000000)
results = engine.run(symbols_5)

# Save results
with open("PHASE2_CORRECTED_5SYMBOL_RESULTS.json", "w") as f:
    json.dump({
        'test': '5-symbol corrected backtest',
        'symbols': symbols_5,
        'initial_capital': results['initial_capital'],
        'final_equity': results['final_equity'],
        'total_pnl': results['total_pnl'],
        'total_trades': results['total_trades'],
        'win_rate': results['win_rate'],
        'max_drawdown': max(
            ((max(results['equity_curve'][:i+1]) - results['equity_curve'][i]) /
             max(results['equity_curve'][:i+1]) * 100)
            for i in range(len(results['equity_curve']))
            if max(results['equity_curve'][:i+1]) > 0
        ) if results['equity_curve'] else 0
    }, f, indent=2)

print("\n✅ 5-symbol validation complete")
print("   Ready for 48-symbol run\n")
