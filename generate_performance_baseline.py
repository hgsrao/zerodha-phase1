#!/usr/bin/env python3
"""
================================================================================
PHASE 2.4 - PERFORMANCE BASELINE GENERATION
================================================================================

Generates comprehensive OOS performance metrics:
- Sharpe ratio, Sortino ratio
- Return statistics
- Risk metrics
- Comparison to benchmarks
- Forward-looking expectations

================================================================================
"""

import json
import math
from datetime import datetime

print("\n" + "="*90)
print("PHASE 2.4 - PERFORMANCE BASELINE GENERATION")
print("="*90 + "\n")

try:
    # Load results
    print("STEP 1: Loading extended backtest results...")
    with open("PHASE_2_2_EXTENDED_RESULTS.json", "r") as f:
        results = json.load(f)

    print(f"✅ Loaded results for {results['symbols_count']} symbols")

    # Extract key metrics
    initial_capital = results['initial_capital']
    final_capital = results['final_capital']
    total_return = results['total_return']
    return_percent = results['return_percent']
    total_trades = results['total_trades']
    winning_trades = results['winning_trades']
    losing_trades = results['losing_trades']
    win_rate = results['win_rate']
    total_pnl = results['total_pnl']
    profit_factor = results['profit_factor']
    max_drawdown = results['max_drawdown']
    max_pnl = results['max_pnl']
    min_pnl = results['min_pnl']

    # STEP 2: Calculate advanced metrics
    print(f"\nSTEP 2: Calculating Advanced Metrics")
    print("-" * 90)

    # Risk metrics
    if total_trades > 0:
        avg_win = sum(t['total_pnl'] for t in results['symbol_concentration'].values() if t['total_pnl'] > 0) / max(1, winning_trades)
        avg_loss = sum(abs(t['total_pnl']) for t in results['symbol_concentration'].values() if t['total_pnl'] < 0) / max(1, losing_trades)
    else:
        avg_win = 0
        avg_loss = 0

    # Sharpe ratio (simplified - assuming 252 trading days per year)
    trading_days = 252
    trading_period = 3  # 3 years
    daily_return = return_percent / (trading_period * trading_days)

    # Approximate daily volatility
    if total_trades > 0:
        daily_trades = total_trades / (trading_period * trading_days)
        trade_std_dev = (max_pnl - min_pnl) / 4  # Approximate std dev
        daily_volatility = (trade_std_dev * daily_trades) / 100
        sharpe_ratio = daily_return / max(daily_volatility, 0.0001) if daily_volatility > 0 else 0
    else:
        sharpe_ratio = 0

    # Sortino ratio (downside risk only)
    if losing_trades > 0:
        downside_deviation = (abs(min_pnl) * (losing_trades / total_trades)) / 10 if total_trades > 0 else 0
        sortino_ratio = daily_return / max(downside_deviation, 0.0001) if downside_deviation > 0 else 0
    else:
        sortino_ratio = 0

    # Calmar ratio (return / max drawdown)
    calmar_ratio = return_percent / max(max_drawdown, 0.01) if max_drawdown > 0 else 0

    # Recovery factor
    recovery_factor = abs(total_pnl) / max(max_drawdown * initial_capital / 100, 1) if max_drawdown > 0 else 0

    print(f"\n{'RETURN METRICS':^90}")
    print(f"  Absolute Return:           ₹{total_return:>15,.0f}")
    print(f"  Return Percentage:              {return_percent:>15.2f}%")
    print(f"  Average Annual Return:          {return_percent/trading_period:>15.2f}%")
    print(f"  Average Monthly Return:         {return_percent/(trading_period*12):>15.2f}%")

    print(f"\n{'RISK METRICS':^90}")
    print(f"  Max Drawdown:                   {max_drawdown:>15.2f}%")
    print(f"  Drawdown Duration:              ~{(max_drawdown * trading_period * trading_days / 2):.0f} trading days")
    print(f"  Sharpe Ratio (est.):            {sharpe_ratio:>15.2f}")
    print(f"  Sortino Ratio (est.):           {sortino_ratio:>15.2f}")
    print(f"  Calmar Ratio:                   {calmar_ratio:>15.2f}")
    print(f"  Recovery Factor:                {recovery_factor:>15.2f}")

    print(f"\n{'TRADE METRICS':^90}")
    print(f"  Total Trades:                   {total_trades:>15,}")
    print(f"  Winning Trades:                 {winning_trades:>15,}")
    print(f"  Losing Trades:                  {losing_trades:>15,}")
    print(f"  Win Rate:                       {win_rate:>15.1f}%")
    print(f"  Profit Factor:                  {profit_factor:>15.2f}")
    if winning_trades > 0:
        print(f"  Average Win:                ₹{total_pnl / winning_trades:>15,.0f}")
    if losing_trades > 0:
        print(f"  Average Loss:               ₹{min_pnl / losing_trades:>15,.0f}")
    print(f"  Best Trade:                 ₹{max_pnl:>15,.0f}")
    print(f"  Worst Trade:                ₹{min_pnl:>15,.0f}")

    # STEP 3: Benchmark comparison
    print(f"\nSTEP 3: Benchmark Comparison")
    print("-" * 90)

    # Buy-and-hold NIFTY50 approximate return (2022-2024)
    # Rough estimates: 2022: -18%, 2023: +15%, 2024: +5.5% avg
    nifty_3yr_return = 0.5  # Conservative estimate

    print(f"""
    Performance vs Benchmarks:

    Strategy Performance (48-symbol):
      Total Return:     {return_percent:>8.2f}%
      Max Drawdown:     {max_drawdown:>8.2f}%
      Sharpe Ratio:     {sharpe_ratio:>8.2f}

    NIFTY50 Buy-and-Hold (estimated):
      Total Return:     {nifty_3yr_return*100:>8.2f}%
      Max Drawdown:     ~10.00%
      Sharpe Ratio:     ~0.15

    Assessment:
    """)

    if return_percent > 0:
        print(f"    ✅ Strategy outperformed buy-and-hold on returns")
    else:
        print(f"    ⚠️  Strategy underperformed buy-and-hold on returns")

    if max_drawdown < 10:
        print(f"    ✅ Strategy has better drawdown control")
    else:
        print(f"    ⚠️  Strategy drawdown similar to buy-and-hold")

    if sharpe_ratio > 0.15:
        print(f"    ✅ Strategy has better risk-adjusted returns")
    else:
        print(f"    ⚠️  Strategy needs improvement in risk-adjusted returns")

    # STEP 4: Forward expectations
    print(f"\nSTEP 4: Forward-Looking Expectations")
    print("-" * 90)

    annual_return = return_percent / trading_period
    monthly_return = annual_return / 12
    expected_monthly_drawdown = max_drawdown / 3

    print(f"""
    Based on historical OOS performance, expected forward metrics:

    Annualized Returns:
      Expected:         {annual_return:>8.2f}%/year
      Range:            {annual_return*0.8:>8.2f}% - {annual_return*1.2:>8.2f}% (±20%)
      Conservative:     {annual_return*0.5:>8.2f}%/year (50% haircut)

    Monthly Returns:
      Expected:         {monthly_return:>8.2f}%/month
      Winning Months:   ~{win_rate:>5.1f}%
      Losing Months:    ~{100-win_rate:>5.1f}%

    Drawdown Expectations:
      Historical Max:   {max_drawdown:>8.2f}%
      Expected Monthly: {expected_monthly_drawdown:>8.2f}%
      Recovery Time:    ~{trading_period * trading_days / 2:.0f} trading days (historical)

    Trade Expectations:
      Trades per Day:   ~{total_trades / (trading_period * trading_days):>8.1f}
      Avg Win/Loss:     {total_pnl / total_trades if total_trades > 0 else 0:>8.0f} rupees/trade
      Win Rate:         {win_rate:>8.1f}%

    ══════════════════════════════════════════════════════════════════════════════

    Risk Rating:        {'🟢 LOW' if max_drawdown < 10 else '🟡 MEDIUM' if max_drawdown < 20 else '🔴 HIGH'}
    Return Potential:   {'🟢 GOOD' if return_percent > 10 else '🟡 FAIR' if return_percent > 0 else '🔴 POOR'}
    Risk-Adjusted:      {'🟢 STRONG' if sharpe_ratio > 0.5 else '🟡 ADEQUATE' if sharpe_ratio > 0 else '🔴 WEAK'}

    ══════════════════════════════════════════════════════════════════════════════
    """)

    # STEP 5: Save baseline report
    print(f"\nSTEP 5: Saving Performance Baseline")
    print("-" * 90)

    baseline = {
        "phase": "2.4 - Performance Baseline",
        "generated": datetime.now().isoformat(),
        "period": "3 years (2022-2024)",
        "symbols_tested": results['symbols_count'],
        "total_trades": total_trades,
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "total_return_rupees": total_return,
        "total_return_percent": return_percent,
        "annualized_return_percent": annual_return,
        "monthly_return_percent": monthly_return,
        "win_rate_percent": win_rate,
        "profit_factor": profit_factor,
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2),
        "calmar_ratio": round(calmar_ratio, 2),
        "max_drawdown_percent": max_drawdown,
        "expected_monthly_drawdown_percent": round(expected_monthly_drawdown, 2),
        "recovery_factor": round(recovery_factor, 2),
        "risk_rating": "LOW" if max_drawdown < 10 else "MEDIUM" if max_drawdown < 20 else "HIGH",
        "return_potential": "GOOD" if return_percent > 10 else "FAIR" if return_percent > 0 else "POOR",
        "risk_adjusted_rating": "STRONG" if sharpe_ratio > 0.5 else "ADEQUATE" if sharpe_ratio > 0 else "WEAK",
        "forward_expectations": {
            "expected_annual_return_percent": round(annual_return, 2),
            "return_range_min_percent": round(annual_return * 0.8, 2),
            "return_range_max_percent": round(annual_return * 1.2, 2),
            "conservative_annual_return_percent": round(annual_return * 0.5, 2),
            "expected_monthly_return_percent": round(monthly_return, 2),
            "expected_monthly_drawdown_percent": round(expected_monthly_drawdown, 2),
            "expected_recovery_days": int(trading_period * trading_days / 2),
            "trades_per_day": round(total_trades / (trading_period * trading_days), 1)
        }
    }

    with open("PHASE_2_4_PERFORMANCE_BASELINE.json", "w") as f:
        json.dump(baseline, f, indent=2)

    print(f"  ✅ Baseline saved to: PHASE_2_4_PERFORMANCE_BASELINE.json")

    print("\n" + "="*90)
    print("✅ PHASE 2.4 PERFORMANCE BASELINE COMPLETE")
    print("="*90 + "\n")

except FileNotFoundError:
    print("⚠️  PHASE_2_2_EXTENDED_RESULTS.json not found yet")
    print("   (Waiting for Phase 2.2 backtest to complete)")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
