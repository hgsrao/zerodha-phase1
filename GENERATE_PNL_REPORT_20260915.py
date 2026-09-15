#!/usr/bin/env python3
"""
Generate comprehensive P&L reports from backtest data
Shows entry/exit trades for 1-month, 1-year, 2-year, 3-year periods
"""

import json
from datetime import datetime
from pathlib import Path

def load_backtest_data():
    """Load the 3-year backtest results"""
    try:
        backtest_files = sorted(Path('.').glob('BACKTEST_48_EQUITIES_3YEARS_*.json'))
        if backtest_files:
            with open(backtest_files[-1]) as f:
                return json.load(f)
    except:
        pass
    return None

def generate_report(data):
    """Generate comprehensive P&L report"""

    print("="*90)
    print("COMPREHENSIVE P&L REPORT - PAPER TRADING SYSTEM")
    print("="*90)
    print()

    if not data:
        print("No backtest data found. Running backtest first...")
        print("\nTo generate backtest data, run:")
        print("  python COMPLETE_SYSTEM_PIPELINE_20260829.py")
        return

    # Extract aggregate statistics
    agg = data.get('aggregate', {})
    config = data.get('config', {})

    print(f"Test Date: {data.get('test_date', 'N/A')}")
    print(f"Period: 3 Years (2023-2026)")
    print()

    print("-" * 90)
    print("OVERALL 3-YEAR RESULTS")
    print("-" * 90)
    print()

    print(f"Total Trades:              {agg.get('total_trades', 0)}")
    print(f"Winning Trades:            {agg.get('winning_trades', 0)}")
    print(f"Losing Trades:             {agg.get('losing_trades', 0)}")
    print(f"Win Rate:                  {agg.get('win_rate', 0)*100:.2f}%")
    print(f"Total P&L (Rupees):        Rs. {agg.get('total_pnl', 0):,.2f}")
    print(f"Avg P&L per Trade:         Rs. {agg.get('avg_pnl_per_trade', 0):,.2f}")
    print(f"Best Trade:                Rs. {agg.get('max_trade_pnl', 0):,.2f}")
    print(f"Worst Trade:               Rs. {agg.get('min_trade_pnl', 0):,.2f}")
    print(f"Max Consecutive Wins:      {agg.get('consecutive_wins', 0)}")
    print(f"Max Consecutive Losses:    {agg.get('consecutive_losses', 0)}")
    print()

    # Show extrapolated results for different periods
    print("-" * 90)
    print("EXTRAPOLATED RESULTS BY PERIOD")
    print("-" * 90)
    print()

    total_trades = agg.get('total_trades', 1)
    total_pnl = agg.get('total_pnl', 0)
    win_rate = agg.get('win_rate', 0)

    periods = {
        '1 Month (30 days)': 30/1095,
        '1 Year (365 days)': 365/1095,
        '2 Years (730 days)': 730/1095,
        '3 Years (1095 days)': 1.0
    }

    for period_name, multiplier in periods.items():
        trades_in_period = int(total_trades * multiplier)
        pnl_in_period = total_pnl * multiplier

        print(f"{period_name:.<30} {trades_in_period:>4} trades")
        print(f"  Win Rate: {win_rate*100:.2f}% | Winning: {int(trades_in_period*win_rate)} | Losing: {int(trades_in_period*(1-win_rate))}")
        print(f"  Total P&L: Rs. {pnl_in_period:,.2f}")
        print(f"  Avg P&L per Trade: Rs. {pnl_in_period/trades_in_period:,.2f}" if trades_in_period > 0 else "  Avg P&L per Trade: N/A")
        print()

    # Show configuration
    print("-" * 90)
    print("SYSTEM CONFIGURATION")
    print("-" * 90)
    print()
    print(f"Symbols: {config.get('symbols', 'N/A')}")
    print(f"Start Date: {config.get('start_date', 'N/A')}")
    print(f"End Date: {config.get('end_date', 'N/A')}")
    print(f"Initial Capital: Rs. {config.get('initial_capital', 0):,.2f}")
    print(f"Position Size: {config.get('position_size', 'N/A')}")
    print()

    # Sample trades
    if 'results' in data and isinstance(data['results'], list) and data['results']:
        print("-" * 90)
        print("SAMPLE TRADES (First 10)")
        print("-" * 90)
        print()
        print(f"{'Symbol':<12} {'Entry Price':<15} {'Exit Price':<15} {'Entry Time':<20} {'P&L (Rs)':<12}")
        print("-" * 90)

        for i, trade in enumerate(data['results'][:10]):
            symbol = trade.get('symbol', 'N/A')
            entry = trade.get('entry_price', 0)
            exit_price = trade.get('exit_price', 0)
            entry_time = trade.get('entry_time', 'N/A')
            pnl = trade.get('pnl', 0)

            print(f"{symbol:<12} {entry:<15.2f} {exit_price:<15.2f} {str(entry_time):<20} {pnl:>+10,.2f}")

        print()

    # Save detailed report
    save_detailed_report(agg, periods)

    print("="*90)
    print("REPORT COMPLETE")
    print("="*90)

def save_detailed_report(agg, periods):
    """Save detailed report to JSON"""
    report = {
        'generated': datetime.now().isoformat(),
        '3_year_summary': agg,
        'period_breakdown': {}
    }

    total_trades = agg.get('total_trades', 1)
    total_pnl = agg.get('total_pnl', 0)
    win_rate = agg.get('win_rate', 0)

    for period_name, multiplier in periods.items():
        trades_in_period = int(total_trades * multiplier)
        pnl_in_period = total_pnl * multiplier

        report['period_breakdown'][period_name] = {
            'total_trades': trades_in_period,
            'win_rate': f"{win_rate*100:.2f}%",
            'winning_trades': int(trades_in_period * win_rate),
            'losing_trades': int(trades_in_period * (1-win_rate)),
            'total_pnl_rupees': round(pnl_in_period, 2),
            'avg_pnl_per_trade': round(pnl_in_period/trades_in_period, 2) if trades_in_period > 0 else 0
        }

    filename = f"COMPREHENSIVE_PNL_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(report, f, indent=2)

    print(f"\nDetailed report saved: {filename}")

def main():
    data = load_backtest_data()
    generate_report(data)

if __name__ == "__main__":
    main()
