#!/usr/bin/env python3
"""
Paper Trading Engine with Live Market Data
Runs trading signals on real market data without real money
"""

import json
import logging
import time
from datetime import datetime
from typing import List, Dict
import sys
import os

sys.path.insert(0, '.')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PaperTradingEngine:
    """Execute trading signals on live market data with simulated orders"""

    def __init__(self, kite_adapter, symbols: List[str]):
        self.adapter = kite_adapter
        self.symbols = symbols
        self.paper_positions = {}
        self.execution_log = []
        self.signal_log = []
        logger.info(f"Paper trading engine initialized for {len(symbols)} symbols")

    def fetch_live_data(self) -> Dict:
        """Fetch current 1-min candles for all symbols"""
        data = {}
        for symbol in self.symbols:
            try:
                candles = self.adapter.get_1min_candles(symbol, limit=100)
                if candles:
                    data[symbol] = candles
            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")
        return data

    def generate_signals(self, data: Dict) -> Dict:
        """
        Generate trading signals based on live data
        Simple momentum strategy:
        - Buy if price > 20-bar MA and volume > average
        - Sell if price < 20-bar MA
        """
        signals = {}

        for symbol, candles in data.items():
            try:
                if len(candles) < 20:
                    continue

                closes = [c['close'] for c in candles]
                volumes = [c['volume'] for c in candles]

                ma20 = sum(closes[-20:]) / 20
                current_price = closes[-1]
                current_volume = volumes[-1]
                avg_volume = sum(volumes[-20:]) / 20

                signal = {
                    'symbol': symbol,
                    'timestamp': datetime.now().isoformat(),
                    'current_price': current_price,
                    'ma20': ma20,
                    'current_volume': current_volume,
                    'avg_volume': avg_volume,
                    'action': None,
                    'quantity': 1
                }

                # Simple momentum signal
                if current_price > ma20 and current_volume > avg_volume:
                    signal['action'] = 'BUY'
                    signals[symbol] = signal
                elif current_price < ma20 and symbol in self.paper_positions:
                    signal['action'] = 'SELL'
                    signals[symbol] = signal

            except Exception as e:
                logger.error(f"Error generating signal for {symbol}: {e}")

        return signals

    def execute_signals(self, signals: Dict) -> List[Dict]:
        """Execute trading signals"""
        executed_orders = []

        for symbol, signal in signals.items():
            try:
                logger.info(f"Signal → {signal['action']} {quantity} {symbol} @ ₹{signal['current_price']:.2f}")

                order = self.adapter.submit_paper_order(
                    symbol=symbol,
                    quantity=signal['quantity'],
                    side=signal['action'],
                    price=signal['current_price']
                )

                executed_orders.append(order)
                self.execution_log.append(order)
                self.signal_log.append(signal)

                # Track position
                if signal['action'] == 'BUY':
                    self.paper_positions[symbol] = signal['quantity']
                elif signal['action'] == 'SELL' and symbol in self.paper_positions:
                    del self.paper_positions[symbol]

            except Exception as e:
                logger.error(f"Error executing signal for {symbol}: {e}")

        return executed_orders

    def run_once(self):
        """Execute one cycle: fetch data → generate signals → execute"""
        try:
            logger.info("="*70)
            logger.info(f"CYCLE STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # Fetch live data
            data = self.fetch_live_data()
            if not data:
                logger.warning("No data received, skipping cycle")
                return

            logger.info(f"✓ Fetched data for {len(data)} symbols")

            # Generate signals
            signals = self.generate_signals(data)
            logger.info(f"✓ Generated {len(signals)} signals")

            # Execute signals
            if signals:
                orders = self.execute_signals(signals)
                logger.info(f"✓ Executed {len(orders)} orders")
            else:
                logger.info("No signals to execute")

            # Save logs
            self._save_logs()

            logger.info(f"CYCLE COMPLETED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("="*70 + "\n")

        except Exception as e:
            logger.error(f"Error in cycle: {e}", exc_info=True)

    def run_continuous(self, interval_seconds: int = 60):
        """Run continuously at specified interval"""
        logger.info(f"Starting continuous execution (interval: {interval_seconds}s)")
        logger.info("Press Ctrl+C to stop\n")

        try:
            cycle = 0
            while True:
                cycle += 1
                logger.info(f"[Cycle {cycle}]")
                self.run_once()
                logger.info(f"Waiting {interval_seconds}s until next cycle...")
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("\nStopping continuous execution")

    def _save_logs(self):
        """Save execution and signal logs"""
        try:
            with open('PAPER_EXECUTION_LOG.json', 'w') as f:
                json.dump(self.execution_log, f, indent=2)

            with open('PAPER_SIGNALS_LOG.json', 'w') as f:
                json.dump(self.signal_log, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving logs: {e}")

# Main execution
if __name__ == "__main__":
    from zerodha_kite_live_adapter import ZerodhaKiteLiveAdapter

    # Get credentials
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key or not access_token:
        print("ERROR: Set KITE_API_KEY and KITE_ACCESS_TOKEN environment variables")
        sys.exit(1)

    # Initialize
    adapter = ZerodhaKiteLiveAdapter(api_key, access_token)
    symbols = ['RELIANCE', 'TCS', 'INFY', 'HDFC', 'ICICIBANK', 'SBIN', 'MARUTI', 'LT']

    engine = PaperTradingEngine(adapter, symbols)

    # Run single cycle (test)
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        logger.info("Running test cycle...")
        engine.run_once()
    else:
        # Run continuous
        engine.run_continuous(interval_seconds=60)
