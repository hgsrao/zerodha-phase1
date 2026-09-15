# ============================================================================
# KITE ORDER IMBALANCE CONNECTOR
# Real-time tick streaming + order imbalance + Redis integration
# Production-grade implementation (NO SHORTCUTS)
# Date: August 30, 2026
# ============================================================================

import redis
import logging
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from collections import deque
import threading

from OrderImbalanceCore import OrderImbalanceEngine

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('KiteOrderImbalanceConnector')

# ============================================================================
# KITE TICK LISTENER
# ============================================================================

class KiteTickListener:
    """
    Listen to Kite WebSocket for real-time tick data

    Expects tick format:
    {
        'timestamp': datetime,
        'symbol': str,
        'last_price': float,
        'volume': int,
        'bid': float,
        'ask': float,
        'bid_qty': int,
        'ask_qty': int
    }
    """

    def __init__(self, max_queue_size: int = 10000):
        self.tick_queue = deque(maxlen=max_queue_size)
        self.is_streaming = False
        self.logger = logger
        self.stats = {
            'ticks_received': 0,
            'symbols_tracked': set(),
            'start_time': datetime.now()
        }

    def on_tick(self, tick: Dict):
        """Handle incoming tick from Kite WebSocket"""
        processed_tick = {
            'timestamp': datetime.now(),
            'symbol': tick.get('tradingsymbol', tick.get('symbol', 'UNKNOWN')),
            'price': tick.get('last_price', 0.0),
            'volume': tick.get('volume', 0),
            'bid': tick.get('bid', 0.0),
            'ask': tick.get('ask', 0.0),
            'bid_qty': tick.get('bid_qty', 0),
            'ask_qty': tick.get('ask_qty', 0)
        }

        self.tick_queue.append(processed_tick)
        self.stats['ticks_received'] += 1
        self.stats['symbols_tracked'].add(processed_tick['symbol'])

        return processed_tick

    def get_stats(self) -> Dict:
        """Get listener statistics"""
        uptime = (datetime.now() - self.stats['start_time']).total_seconds()
        return {
            'ticks_received': self.stats['ticks_received'],
            'symbols_tracked': len(self.stats['symbols_tracked']),
            'queue_size': len(self.tick_queue),
            'uptime_seconds': uptime,
            'ticks_per_second': self.stats['ticks_received'] / max(uptime, 1)
        }

# ============================================================================
# ORDER IMBALANCE CONNECTOR (Main Orchestrator)
# ============================================================================

class KiteOrderImbalanceConnector:
    """
    Main connector: Kite ticks → OrderImbalanceEngine → Redis → ECS

    Responsibilities:
    1. Listen to Kite WebSocket ticks
    2. Process through OrderImbalanceEngine
    3. Store results in Redis (state bus)
    4. Publish updates via Redis pub/sub
    5. Support circuit breaker queries

    Data flow:
    Kite WebSocket → on_tick() → imbalance_engine.process_tick()
                                  → redis.set() + redis.publish()
                                  ↓
                              ECS queries: get_imbalance()
                              Circuit breaker queries: detect_panic()
    """

    def __init__(self, redis_host: str = 'localhost', redis_port: int = 6379,
                 redis_db: int = 0):
        """Initialize connector with Redis backend"""

        # Redis connection
        try:
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                decode_responses=True,
                socket_keepalive=True,
                socket_keepalive_options={1: 1, 2: 1, 3: 3}
            )
            self.redis_client.ping()
            logger.info(f"Redis connected: {redis_host}:{redis_port}")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

        # Order imbalance engine
        self.imbalance_engine = OrderImbalanceEngine()

        # Tick listener
        self.tick_listener = KiteTickListener()

        # Symbol management
        self.symbols = []
        self.imbalance_cache = {}  # Local cache for fast access
        self.cache_update_time = {}

        self.logger = logger

    def initialize(self, symbols: List[str]):
        """Initialize connector for specific symbols"""

        self.symbols = symbols

        # Initialize Redis keys for each symbol
        for symbol in symbols:
            self.redis_client.set(f'imbalance:{symbol}', json.dumps({
                'value': 0.0,
                'value_pct': 0.0,
                'confidence': 0.0,
                'heat': 'NEUTRAL',
                'timestamp': datetime.now().isoformat()
            }))

        self.logger.info(f"Initialized connector for {len(symbols)} symbols")

    def process_tick(self, symbol: str, price: float, volume: int,
                     timestamp: Optional[datetime] = None) -> Dict:
        """
        Process single tick and update Redis

        Call this for each tick received from Kite
        """

        if timestamp is None:
            timestamp = datetime.now()

        try:
            # Process through engine
            result = self.imbalance_engine.process_tick(
                symbol=symbol,
                price=price,
                volume=volume,
                timestamp=timestamp
            )

            # Get ECS-ready signal
            ecs_signal = self.imbalance_engine.get_ecs_signal(symbol)

            # Update local cache
            self.imbalance_cache[symbol] = ecs_signal
            self.cache_update_time[symbol] = datetime.now()

            # Store in Redis
            redis_data = {
                'value': ecs_signal['imbalance'],
                'value_pct': ecs_signal['imbalance_pct'],
                'confidence': ecs_signal['confidence'],
                'heat': ecs_signal['heat_level'],
                'timestamp': ecs_signal['timestamp'].isoformat()
            }

            self.redis_client.set(
                f'imbalance:{symbol}',
                json.dumps(redis_data),
                ex=60  # Expire in 60 seconds (stale data cleanup)
            )

            # Publish to pub/sub for real-time listeners
            self.redis_client.publish(
                f'imbalance_updated:{symbol}',
                json.dumps(redis_data)
            )

            return ecs_signal

        except Exception as e:
            self.logger.error(f"Error processing tick for {symbol}: {e}")
            return None

    def get_imbalance(self, symbol: str, use_cache: bool = True) -> Dict:
        """
        Get current imbalance for symbol

        Args:
            symbol: Stock symbol
            use_cache: Use local cache if timestamp < 1 second old

        Returns:
            {
                'value': float (-1 to +1),
                'value_pct': float (-100 to +100),
                'confidence': float (0 to 1),
                'heat': str,
                'timestamp': str (ISO format)
            }
        """

        try:
            # Try cache first
            if use_cache and symbol in self.imbalance_cache:
                cache_age = (datetime.now() - self.cache_update_time[symbol]).total_seconds()
                if cache_age < 1.0:
                    return self.imbalance_cache[symbol]

            # Get from Redis
            data = self.redis_client.get(f'imbalance:{symbol}')
            if data:
                imbalance_data = json.loads(data)
                self.imbalance_cache[symbol] = imbalance_data
                self.cache_update_time[symbol] = datetime.now()
                return imbalance_data
            else:
                return self._empty_imbalance()

        except Exception as e:
            self.logger.error(f"Error getting imbalance for {symbol}: {e}")
            return self._empty_imbalance()

    def get_all_imbalances(self) -> Dict[str, Dict]:
        """Get imbalances for all tracked symbols"""

        results = {}
        for symbol in self.symbols:
            results[symbol] = self.get_imbalance(symbol)
        return results

    def detect_panic(self, symbol: str, volatility: float,
                     drawdown: float, correlation: float) -> Dict:
        """
        Detect panic for circuit breaker integration

        Args:
            symbol: Stock symbol
            volatility: Current volatility (0.0 to 1.0)
            drawdown: Current drawdown (-1.0 to 0.0)
            correlation: Portfolio correlation (0.0 to 1.0)

        Returns:
            {
                'panic_score': float (0 to 1),
                'severity': str,
                'should_halt': bool,
                'timestamp': datetime
            }
        """

        try:
            # Get current imbalance
            imbalance_data = self.imbalance_engine.get_ecs_signal(symbol)
            imbalance = imbalance_data['imbalance']

            # Detect panic
            panic_result = self.imbalance_engine.detect_crisis(
                imbalance=imbalance,
                volatility=volatility,
                drawdown=drawdown,
                correlation=correlation
            )

            # Store in Redis for circuit breaker
            self.redis_client.set(
                f'panic_score:{symbol}',
                json.dumps({
                    'score': panic_result['panic_score'],
                    'severity': panic_result['severity'],
                    'should_halt': panic_result['should_halt'],
                    'timestamp': panic_result['timestamp'].isoformat()
                })
            )

            # Publish panic alert if severe
            if panic_result['should_halt']:
                self.redis_client.publish(
                    'panic_alert',
                    json.dumps({
                        'symbol': symbol,
                        'panic_score': panic_result['panic_score'],
                        'severity': panic_result['severity'],
                        'timestamp': panic_result['timestamp'].isoformat()
                    })
                )
                self.logger.warning(f"PANIC ALERT: {symbol} panic_score={panic_result['panic_score']:.3f}")

            return panic_result

        except Exception as e:
            self.logger.error(f"Error detecting panic for {symbol}: {e}")
            return {
                'panic_score': 0.0,
                'severity': 'ERROR',
                'should_halt': False,
                'timestamp': datetime.now()
            }

    def get_stats(self) -> Dict:
        """Get connector statistics"""

        return {
            'tick_listener': self.tick_listener.get_stats(),
            'imbalance_engine': self.imbalance_engine.get_stats(),
            'cached_symbols': len(self.imbalance_cache),
            'total_symbols': len(self.symbols),
            'redis_status': 'OK' if self.redis_client.ping() else 'ERROR'
        }

    def _empty_imbalance(self) -> Dict:
        """Return empty imbalance when no data"""
        return {
            'value': 0.0,
            'value_pct': 0.0,
            'confidence': 0.0,
            'heat': 'NO_DATA',
            'timestamp': datetime.now().isoformat()
        }

    def health_check(self) -> Dict:
        """Check connector health"""

        try:
            # Redis health
            redis_ping = self.redis_client.ping()
            redis_info = self.redis_client.info() if redis_ping else None

            return {
                'status': 'HEALTHY' if redis_ping else 'UNHEALTHY',
                'redis': 'CONNECTED' if redis_ping else 'DISCONNECTED',
                'symbols_monitored': len(self.symbols),
                'cached_imbalances': len(self.imbalance_cache),
                'redis_memory_mb': redis_info.get('used_memory', 0) / 1024 / 1024 if redis_info else 0,
                'timestamp': datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return {
                'status': 'ERROR',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }

# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    # Initialize connector
    connector = KiteOrderImbalanceConnector(redis_host='localhost', redis_port=6379)

    # Initialize with symbols
    symbols = ['INFY', 'TCS', 'RELIANCE']
    connector.initialize(symbols)

    # Simulate tick stream
    print("\n=== SIMULATING TICK STREAM ===\n")

    prices = {
        'INFY': [1505.0, 1505.2, 1505.1, 1505.3, 1505.5],
        'TCS': [3200.0, 3200.5, 3200.2, 3200.8, 3201.0],
        'RELIANCE': [2800.0, 2800.3, 2800.1, 2800.6, 2800.9]
    }

    for i in range(5):
        for symbol in symbols:
            result = connector.process_tick(symbol, prices[symbol][i], volume=1000 + i*100)
            if result:
                print(f"{symbol}: Imbalance={result['imbalance_pct']:+.1f}% Heat={result['heat_level']}")

    # Get final state
    print("\n=== FINAL IMBALANCES ===\n")
    all_imbalances = connector.get_all_imbalances()
    for symbol, data in all_imbalances.items():
        print(f"{symbol}: {data['value_pct']:+.1f}% ({data['heat']})")

    # Health check
    print("\n=== HEALTH CHECK ===\n")
    health = connector.health_check()
    print(json.dumps(health, indent=2))
