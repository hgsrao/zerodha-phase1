# ============================================================================
# ORDER IMBALANCE CORE PROCESSOR
# Tick classification + imbalance calculation + panic detection
# Production-grade implementation (NO SHORTCUTS)
# Date: August 30, 2026
# ============================================================================

import numpy as np
import pandas as pd
from collections import deque
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional, Tuple

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('OrderImbalanceCore')

# ============================================================================
# TICK CLASSIFIER - LEE-READY ALGORITHM
# ============================================================================

class TickClassifier:
    """
    Classify each trade as BUY or SELL using Lee-Ready tick rule

    Algorithm:
    1. If price > last_price → BUY (buyer initiated)
    2. If price < last_price → SELL (seller initiated)
    3. If price == last_price → Use prior tick direction

    Accuracy: ~85-90% (industry standard)
    """

    def __init__(self):
        self.last_price = None
        self.last_tick_type = None
        self.classified_ticks = deque(maxlen=1000)
        self.stats = {'total_classified': 0, 'ambiguous': 0}

    def classify(self, symbol: str, current_price: float, volume: int,
                 timestamp: datetime) -> Dict:
        """Classify a single tick"""

        if self.last_price is None:
            self.last_price = current_price
            return {
                'symbol': symbol,
                'price': current_price,
                'volume': volume,
                'timestamp': timestamp,
                'tick_type': 'NEUTRAL',
                'confidence': 0.0
            }

        # Lee-Ready classification
        if current_price > self.last_price:
            tick_type = 'BUY'
            confidence = 1.0
        elif current_price < self.last_price:
            tick_type = 'SELL'
            confidence = 1.0
        else:
            if self.last_tick_type in ['BUY', 'SELL']:
                tick_type = self.last_tick_type
                confidence = 0.5
            else:
                tick_type = 'NEUTRAL'
                confidence = 0.0
            self.stats['ambiguous'] += 1

        self.last_price = current_price
        self.last_tick_type = tick_type
        self.stats['total_classified'] += 1

        classified_tick = {
            'symbol': symbol,
            'price': current_price,
            'volume': volume,
            'timestamp': timestamp,
            'tick_type': tick_type,
            'confidence': confidence
        }

        self.classified_ticks.append(classified_tick)

        logger.debug(f"{symbol}: {current_price} ({volume}) → {tick_type} (conf={confidence:.1f})")

        return classified_tick

# ============================================================================
# IMBALANCE CALCULATOR - MULTI-TIMEFRAME
# ============================================================================

class ImbalanceCalculator:
    """
    Calculate order imbalance from classified ticks

    Imbalance = (BUY count - SELL count) / (BUY count + SELL count)

    Range: -1.0 (extreme sell) to +1.0 (extreme buy)

    Multi-timeframe strategy:
    - 30-second window
    - 60-second window (primary)
    - 120-second window
    - Return median (robust to noise)
    """

    def __init__(self):
        self.tick_history = {}
        self.imbalance_history = {}

    def add_tick(self, symbol: str, classified_tick: Dict):
        """Add classified tick to history"""
        if symbol not in self.tick_history:
            self.tick_history[symbol] = deque(maxlen=300)

        self.tick_history[symbol].append(classified_tick)

    def get_imbalance(self, symbol: str, window_seconds: int = 60) -> Dict:
        """Calculate imbalance for single timeframe"""

        if symbol not in self.tick_history or len(self.tick_history[symbol]) == 0:
            return self._empty_result(symbol, window_seconds)

        ticks = list(self.tick_history[symbol])
        now = datetime.now()
        cutoff_time = now - timedelta(seconds=window_seconds)
        recent_ticks = [t for t in ticks if t['timestamp'] >= cutoff_time]

        if len(recent_ticks) < 5:
            return self._empty_result(symbol, window_seconds)

        # Count BUY and SELL (exclude neutral)
        buy_ticks = [t for t in recent_ticks if t['tick_type'] == 'BUY']
        sell_ticks = [t for t in recent_ticks if t['tick_type'] == 'SELL']

        buy_count = len(buy_ticks)
        sell_count = len(sell_ticks)
        total_count = buy_count + sell_count

        if total_count == 0:
            return self._empty_result(symbol, window_seconds)

        # Calculate imbalance
        imbalance = (buy_count - sell_count) / total_count

        # Calculate volumes
        buy_volume = sum(t['volume'] for t in buy_ticks)
        sell_volume = sum(t['volume'] for t in sell_ticks)

        # Confidence based on sample size
        confidence = min(total_count / 50, 1.0)

        result = {
            'timestamp': now,
            'symbol': symbol,
            'imbalance': imbalance,
            'imbalance_pct': imbalance * 100,
            'buy_count': buy_count,
            'sell_count': sell_count,
            'total_count': total_count,
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'sample_size': len(recent_ticks),
            'confidence': confidence,
            'heat_level': self._classify_heat(imbalance),
            'window_seconds': window_seconds
        }

        if symbol not in self.imbalance_history:
            self.imbalance_history[symbol] = deque(maxlen=1000)
        self.imbalance_history[symbol].append(result)

        logger.info(f"{symbol}: Imbalance={imbalance_pct:+.1f}% (B={buy_count}/S={sell_count}) "
                   f"Conf={confidence:.2f} → {result['heat_level']}")

        return result

    def get_multi_timeframe_imbalance(self, symbol: str) -> Dict:
        """Get robust imbalance using 3-timeframe median"""

        imb_30s = self.get_imbalance(symbol, 30)
        imb_60s = self.get_imbalance(symbol, 60)
        imb_120s = self.get_imbalance(symbol, 120)

        imbalances = [
            imb_30s.get('imbalance', 0),
            imb_60s.get('imbalance', 0),
            imb_120s.get('imbalance', 0)
        ]

        median_imbalance = float(np.median(imbalances))
        min_confidence = min(
            imb_30s.get('confidence', 0),
            imb_60s.get('confidence', 0),
            imb_120s.get('confidence', 0)
        )

        return {
            'timestamp': datetime.now(),
            'symbol': symbol,
            'imbalance_30s': imb_30s.get('imbalance', 0),
            'imbalance_60s': imb_60s.get('imbalance', 0),
            'imbalance_120s': imb_120s.get('imbalance', 0),
            'imbalance_median': median_imbalance,
            'imbalance_median_pct': median_imbalance * 100,
            'confidence': min_confidence,
            'heat_level': self._classify_heat(median_imbalance)
        }

    def _classify_heat(self, imbalance: float) -> str:
        """Classify imbalance intensity"""
        abs_imb = abs(imbalance)
        side = 'BUY' if imbalance > 0 else 'SELL'

        if abs_imb > 0.30:
            return f'EXTREME_{side}'
        elif abs_imb > 0.20:
            return f'VERY_STRONG_{side}'
        elif abs_imb > 0.10:
            return f'STRONG_{side}'
        elif abs_imb > 0.05:
            return f'MODERATE_{side}'
        else:
            return 'NEUTRAL'

    def _empty_result(self, symbol: str, window_seconds: int) -> Dict:
        """Return empty result when insufficient data"""
        return {
            'timestamp': datetime.now(),
            'symbol': symbol,
            'imbalance': 0.0,
            'imbalance_pct': 0.0,
            'buy_count': 0,
            'sell_count': 0,
            'total_count': 0,
            'buy_volume': 0.0,
            'sell_volume': 0.0,
            'sample_size': 0,
            'confidence': 0.0,
            'heat_level': 'INSUFFICIENT_DATA',
            'window_seconds': window_seconds
        }

# ============================================================================
# PANIC DETECTOR - WEIGHTED CRISIS DETECTION
# ============================================================================

class PanicDetector:
    """
    Detect panic/euphoria using weighted combination:
    - Order imbalance (40% weight)
    - Volatility (30% weight)
    - Drawdown (30% weight)
    - Correlation bonus (up to +15%)

    Output: 0.0 (calm) to 1.0 (panic)
    Trigger: > 0.7 → circuit breaker halt
    """

    def __init__(self):
        self.panic_history = deque(maxlen=1000)
        self.logger = logger

    def detect_panic(self, imbalance: float, volatility: float,
                     drawdown: float, correlation: float) -> Dict:
        """Calculate weighted panic score"""

        # Normalize components to 0-1
        imbalance_stress = min(abs(imbalance) / 0.3, 1.0)
        vol_stress = min(volatility / 0.05, 1.0)
        dd_stress = min(abs(drawdown) / 0.10, 1.0)

        # Weighted sum
        panic_score = (
            imbalance_stress * 0.40 +
            vol_stress * 0.30 +
            dd_stress * 0.30
        )

        # Correlation bonus (herd behavior = increased panic)
        if correlation > 0.85:
            panic_score += 0.15

        panic_score = min(panic_score, 1.0)

        # Classify severity
        if panic_score > 0.7:
            severity = 'EXTREME'
        elif panic_score > 0.5:
            severity = 'HIGH'
        elif panic_score > 0.3:
            severity = 'MODERATE'
        else:
            severity = 'LOW'

        result = {
            'timestamp': datetime.now(),
            'panic_score': panic_score,
            'severity': severity,
            'imbalance_stress': imbalance_stress,
            'vol_stress': vol_stress,
            'dd_stress': dd_stress,
            'correlation_factor': 0.15 if correlation > 0.85 else 0.0,
            'should_halt': panic_score > 0.7
        }

        self.panic_history.append(result)

        self.logger.info(f"Panic Score: {panic_score:.3f} ({severity}) | "
                        f"Imb={imbalance_stress:.2f} Vol={vol_stress:.2f} "
                        f"DD={dd_stress:.2f} Corr_bonus={result['correlation_factor']:.2f}")

        return result

# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

class OrderImbalanceEngine:
    """
    Complete order imbalance processing engine

    Pipeline:
    1. TickClassifier: Classify each tick as BUY/SELL
    2. ImbalanceCalculator: Calculate multi-timeframe imbalance
    3. PanicDetector: Detect crisis conditions

    Ready for integration with ECS and circuit breaker
    """

    def __init__(self):
        self.classifier = TickClassifier()
        self.calculator = ImbalanceCalculator()
        self.panic_detector = PanicDetector()
        self.logger = logger

    def process_tick(self, symbol: str, price: float, volume: int,
                     timestamp: Optional[datetime] = None) -> Dict:
        """Process single tick end-to-end"""

        if timestamp is None:
            timestamp = datetime.now()

        # Step 1: Classify
        classified = self.classifier.classify(symbol, price, volume, timestamp)

        # Step 2: Add to history
        self.calculator.add_tick(symbol, classified)

        # Step 3: Get imbalance
        imbalance_data = self.calculator.get_multi_timeframe_imbalance(symbol)

        return {
            'classified_tick': classified,
            'imbalance': imbalance_data
        }

    def get_ecs_signal(self, symbol: str) -> Dict:
        """Get imbalance signal ready for ECS"""
        imbalance_data = self.calculator.get_multi_timeframe_imbalance(symbol)

        return {
            'symbol': symbol,
            'imbalance': imbalance_data['imbalance_median'],
            'imbalance_pct': imbalance_data['imbalance_median_pct'],
            'confidence': imbalance_data['confidence'],
            'heat_level': imbalance_data['heat_level'],
            'timestamp': datetime.now()
        }

    def detect_crisis(self, imbalance: float, volatility: float,
                      drawdown: float, correlation: float) -> Dict:
        """Detect panic for circuit breaker"""
        return self.panic_detector.detect_panic(imbalance, volatility, drawdown, correlation)

    def get_stats(self) -> Dict:
        """Get processing statistics"""
        return {
            'tick_classification': self.classifier.stats,
            'imbalance_calculations': len(self.calculator.imbalance_history),
            'panic_events': len(self.panic_detector.panic_history)
        }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    engine = OrderImbalanceEngine()

    # Simulate tick stream
    prices = [1505.0, 1505.2, 1505.1, 1505.3, 1505.5, 1505.4, 1505.6]

    for i, price in enumerate(prices):
        result = engine.process_tick('INFY', price, volume=1000 + i*100,
                                     timestamp=datetime.now())
        print(f"Tick {i}: {result['imbalance']['heat_level']}")

    # Get final ECS signal
    signal = engine.get_ecs_signal('INFY')
    print(f"\nECS Signal: {signal['imbalance_pct']:.1f}%")
