#!/usr/bin/env python3
"""
Zerodha Kite Live Adapter for Paper Trading with Simulated Orders
Real market data + simulated order execution (no real money)
"""

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    from kiteconnect import KiteConnect
except ImportError:
    logger.error("kiteconnect not installed. Run: pip install kiteconnect")
    sys.exit(1)

class ZerodhaKiteLiveAdapter:
    """Adapter for live Zerodha Kite data and simulated order execution"""

    def __init__(self, api_key: str, access_token: str):
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.paper_positions = {}
        self.paper_orders = {}
        self.trade_log = []
        logger.info(f"Zerodha Kite adapter initialized (API: {api_key[:10]}...)")

    def get_live_quotes(self, symbols: List[str]) -> Dict:
        """Fetch live quotes from Zerodha"""
        try:
            quotes = self.kite.quote(symbols)
            logger.info(f"✓ Fetched live quotes for {len(symbols)} symbols")
            return quotes
        except Exception as e:
            logger.error(f"Failed to fetch quotes: {e}")
            return {}

    def get_1min_candles(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Fetch last N 1-min candles"""
        try:
            instrument_token = self._get_instrument_token(symbol)
            if not instrument_token:
                logger.warning(f"Could not find instrument token for {symbol}")
                return []

            candles = self.kite.historical_data(
                instrument_token,
                from_date='2024-01-01',
                to_date=datetime.now().strftime('%Y-%m-%d'),
                interval='minute'
            )
            result = candles[-limit:] if len(candles) > limit else candles
            logger.info(f"✓ Fetched {len(result)} candles for {symbol}")
            return result
        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol}: {e}")
            return []

    def submit_paper_order(self, symbol: str, quantity: int, side: str, price: Optional[float] = None) -> Dict:
        """
        Submit simulated paper order (not real Zerodha order, no real money)
        """
        order_id = f"PAPER_{int(time.time() * 1000)}"
        final_price = price or self._get_last_price(symbol)

        order = {
            'order_id': order_id,
            'symbol': symbol,
            'quantity': quantity,
            'side': side,  # BUY or SELL
            'price': final_price,
            'timestamp': datetime.now().isoformat(),
            'status': 'SUBMITTED',
            'paper_only': True
        }

        self.paper_orders[order_id] = order
        logger.info(f"Paper order: {order_id} | {side} {quantity} {symbol} @ ₹{final_price}")

        # Log to file
        self.trade_log.append(order)
        try:
            with open('PAPER_TRADE_LOG.jsonl', 'a') as f:
                f.write(json.dumps(order) + '\n')
        except Exception as e:
            logger.error(f"Failed to log order: {e}")

        return order

    def _get_last_price(self, symbol: str) -> float:
        """Get last traded price"""
        try:
            quote = self.kite.quote([symbol])
            if symbol in quote:
                return quote[symbol]['last_price']
        except Exception as e:
            logger.warning(f"Could not fetch price for {symbol}: {e}")
        return 0.0

    def _get_instrument_token(self, symbol: str) -> int:
        """Get instrument token from symbol"""
        try:
            instruments = self.kite.instruments()
            for inst in instruments:
                if inst['tradingsymbol'] == symbol and inst['segment'] == 'NSE':
                    return inst['instrument_token']
        except Exception as e:
            logger.error(f"Failed to get instrument token: {e}")
        return 0

    def get_paper_positions(self) -> Dict:
        """Get all paper trading positions"""
        return self.paper_positions

    def get_paper_orders(self) -> Dict:
        """Get all paper trading orders"""
        return self.paper_orders

# Usage
if __name__ == "__main__":
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key or not access_token:
        print("ERROR: Set KITE_API_KEY and KITE_ACCESS_TOKEN")
        sys.exit(1)

    adapter = ZerodhaKiteLiveAdapter(api_key, access_token)
    symbols = ['RELIANCE', 'TCS', 'INFY']
    quotes = adapter.get_live_quotes(symbols)

    print(f"\nLive quotes as of {datetime.now()}:")
    for symbol, data in quotes.items():
        print(f"  {symbol}: ₹{data.get('last_price', 'N/A')}")
