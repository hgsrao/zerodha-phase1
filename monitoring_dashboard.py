#!/usr/bin/env python3
"""Real-time monitoring dashboard for paper trading"""

import json
import time
import os
from datetime import datetime
from pathlib import Path

class MonitoringDashboard:
    def __init__(self):
        self.execution_log_file = 'PAPER_EXECUTION_LOG.json'
        self.signals_log_file = 'PAPER_SIGNALS_LOG.json'
        self.trade_log_file = 'PAPER_TRADE_LOG.jsonl'

    def display(self):
        os.system('clear' if os.name == 'posix' else 'cls')
        print("="*80)
        print(f"📊 ZERODHA PAPER TRADING DASHBOARD - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)

        # Execution stats
        if Path(self.execution_log_file).exists():
            with open(self.execution_log_file) as f:
                try:
                    orders = json.load(f)
                    print(f"\n📋 ORDERS EXECUTED: {len(orders)}")
                    for order in orders[-10:]:
                        print(f"   {order['timestamp'][:19]} | {order['side']:4} | {order['quantity']} {order['symbol']:12} @ ₹{order['price']:.2f}")
                except:
                    print("\n📋 No orders yet")

        # Signals generated
        if Path(self.signals_log_file).exists():
            with open(self.signals_log_file) as f:
                try:
                    signals = json.load(f)
                    print(f"\n🎯 SIGNALS GENERATED: {len(signals)}")
                    for signal in signals[-10:]:
                        if signal.get('action'):
                            print(f"   {signal['timestamp'][:19]} | {signal['action']:4} | {signal['symbol']:12} @ ₹{signal['current_price']:.2f} (MA20: ₹{signal['ma20']:.2f})")
                except:
                    print("\n🎯 No signals yet")

        print("\n" + "="*80)
        print("Press Ctrl+C to stop | Refresh every 10 seconds")
        print("="*80)

    def run_continuous(self, interval: int = 10):
        try:
            while True:
                self.display()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n✓ Monitoring stopped")

if __name__ == "__main__":
    dashboard = MonitoringDashboard()
    dashboard.run_continuous(interval=10)
