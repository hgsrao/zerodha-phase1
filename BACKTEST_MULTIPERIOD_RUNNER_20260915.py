#!/usr/bin/env python3
"""
Multi-Period Backtest Runner
Generates comprehensive P&L reports for 1-month, 1-year, 2-year, 3-year periods
Shows all entry/exit trades with detailed analysis
"""

import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, '.')

class BacktestReportGenerator:
    """Generate comprehensive backtest reports across multiple time periods"""

    def __init__(self):
        self.results = {}
        self.periods = {
            '1M': {'months': 1, 'days': 30},
            '1Y': {'months': 12, 'days': 365},
            '2Y': {'months': 24, 'days': 730},
            '3Y': {'months': 36, 'days': 1095}
        }

    def run_backtests(self):
        """Run backtests for all periods"""
        print("="*80)
        print("MULTI-PERIOD BACKTEST RUNNER")
        print("="*80)
        print(f"Started: {datetime.now()}\n")

        # Try to use existing backtest data first
        existing_data = self._load_existing_backtest()
        if existing_data:
            print("[OK] Loaded existing backtest results")
            self._generate_reports_from_existing(existing_data)
        else:
            print("⚠ No existing backtest data found")
            print("Run COMPLETE_SYSTEM_PIPELINE_20260829.py to generate backtest data")
            self._show_sample_report()

        self._save_final_report()
        self._display_summary()

    def _load_existing_backtest(self):
        """Load existing backtest results"""
        try:
            backtest_files = list(Path('.').glob('BACKTEST_*_3YEARS_*.json'))
            if backtest_files:
                latest = sorted(backtest_files)[-1]
                print(f"Found: {latest}")
                with open(latest) as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading backtest: {e}")
        return None

    def _generate_reports_from_existing(self, data):
        """Generate reports from existing backtest data"""
        try:
            # Extract trade data if available
            if 'trades' in data:
                trades = data['trades']
            elif 'results' in data and 'trades' in data['results']:
                trades = data['results']['trades']
            else:
                print("No trade data in backtest results")
                return

            # Generate reports for each period
            end_date = datetime.now()

            for period_name, period_info in self.periods.items():
                start_date = end_date - timedelta(days=period_info['days'])

                print(f"\n[{period_name}] Period: {start_date.date()} → {end_date.date()}")

                # Filter trades for this period
                period_trades = self._filter_trades_by_date(trades, start_date, end_date)

                # Calculate P&L
                report = self._calculate_pnl(period_trades, period_name)
                self.results[period_name] = report

                # Display summary
                self._display_period_summary(period_name, report)

        except Exception as e:
            print(f"Error generating reports: {e}")

    def _filter_trades_by_date(self, trades, start_date, end_date):
        """Filter trades within date range"""
        filtered = []
        for trade in trades:
            try:
                # Parse trade date (handle various formats)
                if isinstance(trade.get('entry_time'), str):
                    trade_date = datetime.fromisoformat(trade['entry_time'].split('T')[0])
                elif isinstance(trade.get('date'), str):
                    trade_date = datetime.fromisoformat(trade['date'].split('T')[0])
                else:
                    continue

                if start_date <= trade_date <= end_date:
                    filtered.append(trade)
            except:
                pass

        return filtered

    def _calculate_pnl(self, trades, period_name):
        """Calculate P&L metrics"""
        if not trades:
            return {
                'period': period_name,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'avg_pnl_per_trade': 0,
                'trades': []
            }

        winning = sum(1 for t in trades if t.get('pnl', 0) > 0)
        losing = sum(1 for t in trades if t.get('pnl', 0) < 0)
        total_pnl = sum(t.get('pnl', 0) for t in trades)

        return {
            'period': period_name,
            'total_trades': len(trades),
            'winning_trades': winning,
            'losing_trades': losing,
            'win_rate': f"{(winning/len(trades)*100):.2f}%" if trades else "0%",
            'total_pnl': f"₹{total_pnl:,.2f}",
            'avg_pnl_per_trade': f"₹{total_pnl/len(trades):,.2f}" if trades else "0",
            'trades': trades[:10]  # Show first 10 trades
        }

    def _display_period_summary(self, period_name, report):
        """Display summary for a period"""
        print(f"  Total Trades:    {report['total_trades']}")
        print(f"  Winning:         {report['winning_trades']} | Losing: {report['losing_trades']}")
        print(f"  Win Rate:        {report['win_rate']}")
        print(f"  Total P&L:       {report['total_pnl']}")
        print(f"  Avg P&L/Trade:   {report['avg_pnl_per_trade']}")

        if report['trades']:
            print(f"  Sample Trades:")
            for i, trade in enumerate(report['trades'][:3], 1):
                pnl = trade.get('pnl', 0)
                symbol = trade.get('symbol', 'N/A')
                print(f"    {i}. {symbol}: {pnl:+.2f}")

    def _show_sample_report(self):
        """Show sample report structure"""
        print("\n" + "="*80)
        print("SAMPLE BACKTEST REPORT STRUCTURE")
        print("="*80)

        sample = {
            "1M": {
                "period": "1 Month",
                "total_trades": 45,
                "winning_trades": 20,
                "losing_trades": 25,
                "win_rate": "44.44%",
                "total_pnl": "₹12,500.00",
                "avg_pnl_per_trade": "₹277.78",
                "sample_trades": [
                    {
                        "symbol": "RELIANCE",
                        "entry_price": 2850.50,
                        "exit_price": 2875.25,
                        "quantity": 1,
                        "pnl": 24.75,
                        "entry_time": "2026-09-01 09:30:00",
                        "exit_time": "2026-09-01 14:30:00"
                    }
                ]
            }
        }

        print(json.dumps(sample, indent=2))

    def _save_final_report(self):
        """Save final consolidated report"""
        report = {
            'generated_at': datetime.now().isoformat(),
            'periods': self.results,
            'summary': self._generate_summary()
        }

        filename = f"BACKTEST_MULTIPERIOD_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"\n[OK] Report saved: {filename}")

    def _generate_summary(self):
        """Generate overall summary"""
        if not self.results:
            return {}

        summary = {}
        for period, report in self.results.items():
            summary[period] = {
                'trades': report.get('total_trades', 0),
                'win_rate': report.get('win_rate', '0%'),
                'pnl': report.get('total_pnl', '₹0')
            }
        return summary

    def _display_summary(self):
        """Display final summary"""
        print("\n" + "="*80)
        print("BACKTEST SUMMARY - ALL PERIODS")
        print("="*80)

        print(f"\n{'Period':<10} {'Trades':<10} {'Win Rate':<12} {'Total P&L':<15}")
        print("-" * 50)

        for period in sorted(self.results.keys()):
            report = self.results[period]
            print(f"{period:<10} {report.get('total_trades', 0):<10} "
                  f"{report.get('win_rate', '0%'):<12} {report.get('total_pnl', '₹0'):<15}")

        print("\n" + "="*80)
        print("[COMPLETE] BACKTEST FINISHED")
        print("="*80)

def main():
    generator = BacktestReportGenerator()
    generator.run_backtests()

if __name__ == "__main__":
    main()
