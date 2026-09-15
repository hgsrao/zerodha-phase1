#!/usr/bin/env python3
"""
MASTER NODE - Distributed Computing System (DCS)
================================================

PC (Master) Responsibilities:
1. Load 48 certified symbols from authoritative P01D-V2B dataset
2. Package data as JSON (~700 bytes per symbol)
3. Send to Laptop Worker via HTTP
4. Receive results (15 KB JSON per batch)
5. Aggregate statistics
6. Save final reports
7. Track closed-loop feedback

Network: PC (192.168.0.47) → Laptop (192.168.0.17), port 5001
Latency: ~10-15ms per request
"""

import requests
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import time

# ============================================================================
# CONFIGURATION
# ============================================================================

WORKER_URL = "http://192.168.0.17:5001"
WORKER_ENDPOINT = "/backtest"

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
MANIFEST_HASH = "552AD9DDCE81BBA822C33F4A4DC42CF613D715184300EE0899772CA04544AE1E"

# Test periods
TEST_PERIODS = {
    '1_DAY': {
        'name': '1 Day',
        'start': '2026-08-27',
        'end': '2026-08-27',
        'desc': 'Single trading day (most recent)'
    },
    '1_MONTH': {
        'name': '1 Month',
        'start': '2026-08-01',
        'end': '2026-08-28',
        'desc': 'August 2026 (last 28 days)'
    },
    '3_YEAR': {
        'name': '3 Years',
        'start': '2023-08-14',
        'end': '2026-08-13',
        'desc': 'Complete 3-year period (frozen)'
    }
}

# ============================================================================
# MASTER NODE
# ============================================================================

class MasterNodeDCS:
    """Orchestrates distributed backtest across Worker nodes"""

    def __init__(self):
        self.worker_alive = False
        self.all_results = {}
        self.network_stats = {
            'total_requests': 0,
            'total_latency': 0,
            'successful_requests': 0,
            'failed_requests': 0
        }

    def check_worker_health(self):
        """Verify Worker is reachable"""
        try:
            response = requests.get(f"{WORKER_URL}/health", timeout=2)
            if response.status_code == 200:
                self.worker_alive = True
                print(f"✓ Worker health check OK (Laptop 192.168.0.17:5001)")
                return True
        except Exception as e:
            print(f"✗ Worker UNREACHABLE: {e}")
            print(f"  Make sure Laptop has WORKER_NODE_DCS_20260829.py running")
            return False

    def load_symbol_data(self, symbol):
        """Load single symbol from certified dataset"""
        csv_file = DATA_DIR / f"NSE_{symbol}_15minute_*.csv"
        files = list(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))

        if not files:
            return None

        try:
            df = pd.read_csv(files[0])
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
            df = df.sort_values('timestamp').reset_index(drop=True)
            return df
        except Exception as e:
            print(f"  [ERR] Failed to load {symbol}: {str(e)[:30]}")
            return None

    def package_for_worker(self, symbol, df, period_start, period_end):
        """Package symbol data as compact JSON"""
        df_period = df[(df['timestamp'] >= period_start) &
                       (df['timestamp'] <= period_end)].copy()

        if len(df_period) == 0:
            return None

        # Convert to ISO format and select key columns
        data = {
            'symbol': symbol,
            'candles': [
                {
                    't': row['timestamp'].isoformat(),
                    'o': float(row['open']),
                    'h': float(row['high']),
                    'l': float(row['low']),
                    'c': float(row['close']),
                    'v': int(row['volume'])
                }
                for _, row in df_period.iterrows()
            ]
        }

        return data

    def send_batch_to_worker(self, batch_symbols, period_name, period_start, period_end):
        """Send batch of symbols to Worker for processing"""

        print(f"\n  [MASTER] Preparing batch ({len(batch_symbols)} symbols)...")

        # Load all symbols
        symbol_data = {}
        for symbol in batch_symbols:
            df = self.load_symbol_data(symbol)
            if df is not None:
                packaged = self.package_for_worker(symbol, df, period_start, period_end)
                if packaged:
                    symbol_data[symbol] = packaged

        if not symbol_data:
            print(f"  [ERR] No data loaded for batch")
            return None

        print(f"  [MASTER] Loaded {len(symbol_data)} symbols, packaging...")

        # Prepare request
        request_payload = {
            'period': period_name,
            'start': period_start.isoformat(),
            'end': period_end.isoformat(),
            'symbols': symbol_data,
            'timestamp': datetime.now().isoformat()
        }

        payload_size = len(json.dumps(request_payload))
        print(f"  [MASTER] Payload size: {payload_size:,} bytes")

        # Send to Worker
        print(f"  [MASTER] Sending to Worker (192.168.0.17:5001)...")
        start_time = time.time()

        try:
            response = requests.post(
                f"{WORKER_URL}{WORKER_ENDPOINT}",
                json=request_payload,
                timeout=120
            )
            latency = (time.time() - start_time) * 1000  # ms

            self.network_stats['total_requests'] += 1
            self.network_stats['total_latency'] += latency

            print(f"  [MASTER] Response received in {latency:.1f}ms")

            if response.status_code == 200:
                results = response.json()
                self.network_stats['successful_requests'] += 1

                print(f"  [MASTER] ✓ Received {len(results['symbols'])} results")

                response_size = len(response.text)
                print(f"  [MASTER] Response size: {response_size:,} bytes")

                return results

            else:
                print(f"  [ERR] Worker returned status {response.status_code}")
                self.network_stats['failed_requests'] += 1
                return None

        except Exception as e:
            print(f"  [ERR] Network error: {str(e)}")
            self.network_stats['failed_requests'] += 1
            return None

    def run_distributed_backtest(self, symbols, period_name, period_config):
        """Execute distributed backtest for period"""

        period_start = pd.Timestamp(period_config['start'], tz='Asia/Kolkata')
        period_end = pd.Timestamp(period_config['end'], tz='Asia/Kolkata')

        print("\n" + "="*80)
        print(f"DISTRIBUTED BACKTEST: {period_config['name']}")
        print(f"Period: {period_config['start']} to {period_config['end']}")
        print(f"Symbols: {len(symbols)}")
        print("="*80)

        # Send batch to Worker
        results = self.send_batch_to_worker(symbols, period_name, period_start, period_end)

        if not results:
            print(f"  [MASTER] Backtest failed for {period_name}")
            return None

        # Aggregate results
        aggregate = self._aggregate_results(results, period_name)

        return {
            'period': period_name,
            'config': period_config,
            'symbol_results': results['symbols'],
            'aggregate': aggregate,
            'network': {
                'response_size': len(json.dumps(results)),
                'latency_ms': self.network_stats['total_latency'] / self.network_stats['total_requests']
            }
        }

    def _aggregate_results(self, results, period_name):
        """Aggregate across all symbols"""

        all_trades = []
        total_pnl = 0
        total_equity = 0
        symbols_with_trades = 0

        for symbol, data in results['symbols'].items():
            if 'trades' in data and len(data['trades']) > 0:
                all_trades.extend(data['trades'])
                total_pnl += data['total_pnl']
                symbols_with_trades += 1

            if 'final_equity' in data:
                total_equity += data['final_equity']

        if len(all_trades) == 0:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'symbols_with_trades': 0
            }

        winning = [t for t in all_trades if t['pnl'] > 0]
        win_rate = len(winning) / len(all_trades) if all_trades else 0

        return {
            'total_trades': len(all_trades),
            'winning_trades': len(winning),
            'win_rate': win_rate,
            'avg_trade': total_pnl / len(all_trades) if all_trades else 0,
            'total_pnl': total_pnl,
            'total_equity': total_equity,
            'symbols_with_trades': symbols_with_trades,
            'symbols_total': len(results['symbols'])
        }

    def save_results(self):
        """Save all results to JSON"""

        report = {
            'timestamp': datetime.now().isoformat(),
            'system': 'MASTER_NODE_DCS',
            'worker': '192.168.0.17:5001',
            'data_source': 'P01D-V2B-CERTIFIED',
            'manifest_hash': MANIFEST_HASH,
            'periods_tested': self.all_results,
            'network_statistics': {
                'total_requests': self.network_stats['total_requests'],
                'successful': self.network_stats['successful_requests'],
                'failed': self.network_stats['failed_requests'],
                'avg_latency_ms': (self.network_stats['total_latency'] /
                                  self.network_stats['total_requests']
                                  if self.network_stats['total_requests'] > 0 else 0)
            }
        }

        report_file = Path("DCS_MASTER_BACKTEST_REPORT_20260829.json")
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        print(f"\n✓ Master report saved: {report_file}")

    def print_summary(self):
        """Print summary of all backtests"""

        print("\n" + "="*80)
        print("MASTER NODE DISTRIBUTED BACKTEST SUMMARY")
        print("="*80 + "\n")

        for period_name, result in self.all_results.items():
            if result is None:
                print(f"{period_name:15} - FAILED")
                continue

            agg = result['aggregate']
            config = result['config']

            print(f"{config['name']:15} ({config['start']} to {config['end']})")
            print(f"  Trades:        {agg['total_trades']}")
            print(f"  Win Rate:      {agg['win_rate']:.1%}")
            print(f"  Total P&L:     ₹{agg['total_pnl']:+,.2f}")
            print(f"  Symbols:       {agg['symbols_with_trades']}/{agg['symbols_total']}")
            print()

        print("Network Statistics:")
        print(f"  Total Requests: {self.network_stats['total_requests']}")
        print(f"  Successful:     {self.network_stats['successful_requests']}")
        print(f"  Failed:         {self.network_stats['failed_requests']}")
        avg_lat = (self.network_stats['total_latency'] /
                  self.network_stats['total_requests']
                  if self.network_stats['total_requests'] > 0 else 0)
        print(f"  Avg Latency:    {avg_lat:.1f}ms")
        print()

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " MASTER NODE - DISTRIBUTED COMPUTING SYSTEM (DCS) ".center(78) + "║")
    print("║" + " PC (192.168.0.47) → Laptop (192.168.0.17:5001) ".center(78) + "║")
    print("║" + " Certified P01D-V2B Data: 1-Day + 1-Month + 3-Year Backtests ".center(78) + "║")
    print("╚" + "="*78 + "╝\n")

    # Load list of 48 symbols
    symbols_48 = [
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

    print(f"Testing {len(symbols_48)} symbols\n")

    # Initialize Master
    master = MasterNodeDCS()

    # Check Worker health
    if not master.check_worker_health():
        print("\n⚠ Worker is offline. Start WORKER_NODE_DCS_20260829.py on Laptop first.")
        print("  Command: python WORKER_NODE_DCS_20260829.py")
        return

    # Run backtests for all periods
    for period_key, period_config in TEST_PERIODS.items():
        result = master.run_distributed_backtest(symbols_48, period_key, period_config)
        master.all_results[period_key] = result

    # Save and print results
    master.save_results()
    master.print_summary()

    print("="*80)
    print("MASTER NODE COMPLETE")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
