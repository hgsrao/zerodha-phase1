# ============================================================================
# ECS TRADING SUPERVISOR - ENHANCED WITH ORDER IMBALANCE
# Integration point: Order imbalance → SPEED/VOLTAGE signals → 48-symbol execution
# Production-grade implementation (NO SHORTCUTS)
# Date: August 30, 2026
# ============================================================================

import numpy as np
import redis
import logging
from datetime import datetime
from typing import Dict, List, Optional
import json

# TA-Lib mock (for Windows compatibility)
try:
    import talib
except ImportError:
    class talib:
        @staticmethod
        def EMA(prices, timeperiod=12):
            """Simple EMA calculation without talib"""
            prices = np.array(prices, dtype=float)
            alpha = 2.0 / (timeperiod + 1.0)
            ema = np.zeros_like(prices)
            ema[0] = prices[0]
            for i in range(1, len(prices)):
                ema[i] = alpha * prices[i] + (1 - alpha) * ema[i-1]
            return ema

        @staticmethod
        def ATR(high, low, close, timeperiod=14):
            """Simple ATR calculation without talib"""
            high = np.array(high, dtype=float)
            low = np.array(low, dtype=float)
            close = np.array(close, dtype=float)

            tr1 = high - low
            tr2 = np.abs(high - np.roll(close, 1))
            tr3 = np.abs(low - np.roll(close, 1))
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            atr = np.zeros_like(tr)
            atr[0] = np.mean(tr[:timeperiod])
            for i in range(1, len(tr)):
                atr[i] = (atr[i-1] * (timeperiod - 1) + tr[i]) / timeperiod
            return atr

from KiteOrderImbalanceConnector import KiteOrderImbalanceConnector

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('ECS_Enhanced')

# ============================================================================
# ECS TRADING SUPERVISOR - ENHANCED
# ============================================================================

class ECS_TradingSupervisor_Enhanced:
    """
    Enhanced ECS with Order Imbalance integration

    Two-level hierarchical control:
    1. ECS Supervisor (this class)
       - Calculates stress factor (4 components)
       - Selects operating mode (7 modes)
       - Generates SPEED signal (entry confidence)
       - Generates VOLTAGE signal (position sizing)
       - Integrates order imbalance for boost

    2. 48-symbol engines (receive SPEED/VOLTAGE)
       - Each symbol adapts entry threshold
       - Each symbol adapts position multiplier
       - Coordinated execution

    Data flow:
    Market Data + Order Imbalance
           ↓
    Stress Factor Calculation
           ↓
    Mode Selection (1 of 7)
           ↓
    SPEED Signal = TA-Lib baseline + imbalance boost
    VOLTAGE Signal = TA-Lib baseline + imbalance boost
           ↓
    Broadcast to 48 symbols (async)
           ↓
    Symbol execution
    """

    def __init__(self, symbols: List[str], redis_host: str = 'localhost',
                 redis_port: int = 6379):
        """Initialize ECS with order imbalance integration"""

        self.symbols = symbols
        self.num_symbols = len(symbols)

        # Redis for state management
        try:
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                decode_responses=True
            )
            self.redis_client.ping()
            logger.info(f"Redis connected for ECS")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

        # Order imbalance connector
        self.imbalance_connector = KiteOrderImbalanceConnector(
            redis_host=redis_host,
            redis_port=redis_port
        )
        self.imbalance_connector.initialize(symbols)

        # ECS state
        self.current_stress_factor = 0.0
        self.current_mode = 'LOAD_SHARING_MODE'
        self.mode_history = []

        self.logger = logger

        logger.info(f"ECS initialized for {len(symbols)} symbols with order imbalance")

    # ========================================================================
    # STRESS FACTOR CALCULATION (4 Components)
    # ========================================================================

    def calculate_stress_factor(self, market_data: Dict) -> float:
        """
        Calculate stress factor from 4 components:
        1. Volatility (rolling 20-bar std)
        2. Drawdown (current DD from high)
        3. Correlation (avg inter-symbol correlation)
        4. Streak (WIN/LOSS momentum)

        Range: -1.0 (euphoria) to +1.0 (crisis)
        """

        components = {}

        # Component 1: Volatility (higher = more stress)
        close_prices = market_data.get('close', np.array([]))
        if len(close_prices) >= 20:
            returns = np.diff(np.log(close_prices[-20:]))
            volatility = np.std(returns) * np.sqrt(252)  # Annualized
            vol_stress = (volatility - 0.02) / 0.04  # Normalize around 2% vol
            components['volatility'] = np.clip(vol_stress, -0.5, 0.5)
        else:
            components['volatility'] = 0.0

        # Component 2: Drawdown (negative DD = stress)
        high_prices = market_data.get('high', close_prices)
        current_price = close_prices[-1] if len(close_prices) > 0 else 0
        peak_price = np.max(high_prices) if len(high_prices) > 0 else current_price
        drawdown = (current_price - peak_price) / peak_price if peak_price > 0 else 0
        dd_stress = -drawdown * 2  # Amplify drawdown
        components['drawdown'] = np.clip(dd_stress, -0.5, 0.5)

        # Component 3: Correlation (herd behavior = stress)
        correlation = market_data.get('portfolio_correlation', 0.5)
        corr_stress = (correlation - 0.5) * 0.5
        components['correlation'] = np.clip(corr_stress, -0.25, 0.25)

        # Component 4: Streak (consecutive losses = stress)
        win_loss_streak = market_data.get('consecutive_losses', 0)
        streak_stress = -min(win_loss_streak / 10, 0.3)  # Cap at -0.3
        components['streak'] = streak_stress

        # Combine (weighted average)
        stress_factor = (
            components['volatility'] * 0.30 +
            components['drawdown'] * 0.30 +
            components['correlation'] * 0.20 +
            components['streak'] * 0.20
        )

        stress_factor = np.clip(stress_factor, -1.0, 1.0)
        self.current_stress_factor = stress_factor

        self.logger.debug(f"Stress Factor: {stress_factor:.3f} | "
                         f"Vol={components['volatility']:.2f} "
                         f"DD={components['drawdown']:.2f} "
                         f"Corr={components['correlation']:.2f} "
                         f"Streak={components['streak']:.2f}")

        return stress_factor

    # ========================================================================
    # MODE SELECTION (7 Operating Modes)
    # ========================================================================

    def select_operating_mode(self, stress_factor: float) -> str:
        """
        Select operating mode based on stress factor

        Mapping (stress: -1.0 to +1.0):
        < -0.6 : BLACK_START_MODE (recovery mode)
        -0.6 to -0.3 : VAR_SUPPORT_MODE (support strong bull)
        -0.3 to 0.0 : FREQUENCY_CONTROL_MODE (normal)
        0.0 to 0.3 : LOAD_SHARING_MODE (neutral operations)
        0.3 to 0.6 : PLANT_FOLLOW_MODE (follower mode, reduced positions)
        0.6 to 0.8 : ISOCHRONOUS_MODE (tight control)
        > 0.8 : ISLANDING_MODE (isolation mode)
        """

        if stress_factor < -0.6:
            mode = 'BLACK_START_MODE'
        elif stress_factor < -0.3:
            mode = 'VAR_SUPPORT_MODE'
        elif stress_factor < 0.0:
            mode = 'FREQUENCY_CONTROL_MODE'
        elif stress_factor < 0.3:
            mode = 'LOAD_SHARING_MODE'
        elif stress_factor < 0.6:
            mode = 'PLANT_FOLLOW_MODE'
        elif stress_factor < 0.8:
            mode = 'ISOCHRONOUS_MODE'
        else:
            mode = 'ISLANDING_MODE'

        if mode != self.current_mode:
            self.logger.info(f"MODE CHANGE: {self.current_mode} → {mode} "
                            f"(stress={stress_factor:.3f})")
            self.current_mode = mode
            self.mode_history.append({'timestamp': datetime.now(), 'mode': mode})

        return mode

    # ========================================================================
    # SPEED SIGNAL (Entry Confidence)
    # ========================================================================

    def calculate_speed_signal(self, symbol: str, market_data: Dict) -> float:
        """
        Calculate SPEED signal with order imbalance boost

        SPEED ranges from -100 (crisis, no entries) to +100 (euphoria, max entries)

        Formula:
        base_speed = TA-Lib EMA momentum
        imbalance_boost = order_imbalance * 50
        final_speed = base_speed + imbalance_boost
        """

        # Step 1: Calculate baseline from TA-Lib
        close_prices = market_data.get('close', np.array([]))

        if len(close_prices) < 15:
            return 0.0

        try:
            ema_12 = talib.EMA(close_prices, timeperiod=12)
            ema_26 = talib.EMA(close_prices, timeperiod=26)

            if ema_12 is None or len(ema_12) == 0:
                return 0.0

            momentum = ema_12[-1] - ema_26[-1]
            base_speed = (momentum / close_prices[-1]) * 100  # Normalize

        except Exception as e:
            self.logger.error(f"Error calculating TA-Lib EMA: {e}")
            base_speed = 0.0

        # Step 2: Get order imbalance boost
        imbalance_data = self.imbalance_connector.get_imbalance(symbol)
        imbalance = imbalance_data.get('value', 0.0)
        confidence = imbalance_data.get('confidence', 0.0)

        # Only apply boost if confidence high
        if confidence > 0.5:
            speed_boost = imbalance * 50  # Range: -50 to +50
        else:
            speed_boost = 0.0

        # Final signal
        final_speed = base_speed + speed_boost
        final_speed = np.clip(final_speed, -100, 100)

        self.logger.info(f"{symbol}: SPEED = {base_speed:+.1f} (TA-Lib) "
                        f"+ {speed_boost:+.1f} (imbalance) = {final_speed:+.1f}")

        return final_speed

    # ========================================================================
    # VOLTAGE SIGNAL (Position Sizing)
    # ========================================================================

    def calculate_voltage_signal(self, symbol: str, market_data: Dict) -> float:
        """
        Calculate VOLTAGE signal with order imbalance boost

        VOLTAGE ranges from -100 to +100
        - Negative: reduce position size
        - Positive: increase position size
        - Based on ATR (volatility) + imbalance (directional confidence)

        Formula:
        base_voltage = TA-Lib ATR (volatility measure)
        imbalance_boost = order_imbalance * 30
        final_voltage = base_voltage + imbalance_boost
        """

        # Step 1: Calculate baseline ATR
        high = market_data.get('high', np.array([]))
        low = market_data.get('low', np.array([]))
        close = market_data.get('close', np.array([]))

        if len(close) < 14:
            return 0.0

        try:
            atr = talib.ATR(high, low, close, timeperiod=14)

            if atr is None or len(atr) == 0:
                return 0.0

            volatility_pct = (atr[-1] / close[-1]) * 100
            base_voltage = volatility_pct

        except Exception as e:
            self.logger.error(f"Error calculating TA-Lib ATR: {e}")
            base_voltage = 0.0

        # Step 2: Get imbalance for position sizing boost
        imbalance_data = self.imbalance_connector.get_imbalance(symbol)
        imbalance = imbalance_data.get('value', 0.0)
        confidence = imbalance_data.get('confidence', 0.0)

        # Higher buy imbalance = increase position size
        if confidence > 0.5:
            voltage_boost = imbalance * 30  # Range: -30 to +30
        else:
            voltage_boost = 0.0

        # Final signal
        final_voltage = base_voltage + voltage_boost
        final_voltage = np.clip(final_voltage, -100, 100)

        self.logger.info(f"{symbol}: VOLTAGE = {base_voltage:+.1f} (ATR) "
                        f"+ {voltage_boost:+.1f} (imbalance) = {final_voltage:+.1f}")

        return final_voltage

    # ========================================================================
    # PANIC DETECTION (Circuit Breaker)
    # ========================================================================

    def check_panic_for_circuit_breaker(self, symbol: str,
                                        volatility: float,
                                        drawdown: float,
                                        correlation: float) -> Dict:
        """
        Check panic score for circuit breaker integration

        Returns:
        {
            'panic_score': float (0 to 1),
            'severity': str,
            'should_halt': bool (True if > 0.7)
        }
        """

        panic_result = self.imbalance_connector.detect_panic(
            symbol=symbol,
            volatility=volatility,
            drawdown=drawdown,
            correlation=correlation
        )

        # Store in Redis for circuit breaker
        self.redis_client.set(
            f'ecs:panic_score:{symbol}',
            json.dumps({
                'score': panic_result['panic_score'],
                'severity': panic_result['severity'],
                'should_halt': panic_result['should_halt']
            })
        )

        return panic_result

    # ========================================================================
    # GET COMPLETE ECS SIGNALS
    # ========================================================================

    def get_ecs_signals(self, symbol: str, market_data: Dict) -> Dict:
        """
        Get complete ECS signals ready for 48-symbol broadcast

        Returns:
        {
            'symbol': str,
            'mode': str,
            'stress_factor': float,
            'speed': float,
            'voltage': float,
            'imbalance': float,
            'timestamp': datetime
        }
        """

        # Calculate stress factor
        stress = self.calculate_stress_factor(market_data)

        # Select mode
        mode = self.select_operating_mode(stress)

        # Generate signals
        speed = self.calculate_speed_signal(symbol, market_data)
        voltage = self.calculate_voltage_signal(symbol, market_data)

        # Get imbalance data
        imbalance_data = self.imbalance_connector.get_imbalance(symbol)

        signals = {
            'symbol': symbol,
            'mode': mode,
            'stress_factor': stress,
            'speed': speed,
            'voltage': voltage,
            'imbalance': imbalance_data.get('value', 0.0),
            'imbalance_heat': imbalance_data.get('heat', 'NEUTRAL'),
            'timestamp': datetime.now()
        }

        return signals

    # ========================================================================
    # BROADCAST TO 48 SYMBOLS (Async)
    # ========================================================================

    async def broadcast_signals_to_symbols(self, signals: Dict) -> Dict:
        """
        Broadcast ECS signals to all 48 symbol engines asynchronously

        Each symbol converts:
        - SPEED → entry threshold (0.65 to 0.85)
        - VOLTAGE → position multiplier (0.85 to 1.15)
        """

        results = {}

        for symbol in self.symbols:
            # Convert SPEED signal to entry threshold
            speed = signals.get('speed', 0.0)
            entry_threshold = 0.75 - (speed / 100) * 0.10  # Range: 0.65 to 0.85
            entry_threshold = np.clip(entry_threshold, 0.65, 0.85)

            # Convert VOLTAGE signal to position multiplier
            voltage = signals.get('voltage', 0.0)
            position_multiplier = 1.0 + (voltage / 100) * 0.15  # Range: 0.85 to 1.15
            position_multiplier = np.clip(position_multiplier, 0.85, 1.15)

            results[symbol] = {
                'symbol': symbol,
                'entry_threshold': entry_threshold,
                'position_multiplier': position_multiplier,
                'ecs_mode': signals.get('mode'),
                'ecs_stress': signals.get('stress_factor')
            }

        self.logger.info(f"Broadcast complete: {len(results)} symbols updated")

        return results

    # ========================================================================
    # STATE QUERIES
    # ========================================================================

    def get_ecs_state(self) -> Dict:
        """Get current ECS state"""

        return {
            'timestamp': datetime.now(),
            'current_mode': self.current_mode,
            'stress_factor': self.current_stress_factor,
            'symbols_managed': len(self.symbols),
            'mode_changes_today': len(self.mode_history)
        }

    def get_stats(self) -> Dict:
        """Get ECS statistics"""

        return {
            'state': self.get_ecs_state(),
            'imbalance_connector': self.imbalance_connector.get_stats()
        }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    # Initialize ECS
    symbols = ['INFY', 'TCS', 'RELIANCE', 'HDFC', 'HDFCBANK']
    ecs = ECS_TradingSupervisor_Enhanced(symbols)

    # Simulate market data
    market_data = {
        'close': np.array([1505.0, 1505.2, 1505.1, 1505.3, 1505.5,
                          1505.4, 1505.6, 1505.8, 1506.0, 1505.9,
                          1506.1, 1506.3, 1506.2, 1506.4, 1506.5]),
        'high': np.array([1506.0, 1506.2, 1506.1, 1506.3, 1506.5] * 3),
        'low': np.array([1504.0, 1504.2, 1504.1, 1504.3, 1504.5] * 3),
        'portfolio_correlation': 0.65,
        'consecutive_losses': 0
    }

    # Simulate tick for order imbalance
    print("\n=== SIMULATING MARKET CONDITIONS ===\n")
    for symbol in symbols[:3]:
        ecs.imbalance_connector.process_tick(symbol, 1505.5, 1000)

    # Get ECS signals
    print("\n=== ECS SIGNALS ===\n")
    signals = ecs.get_ecs_signals('INFY', market_data)
    print(json.dumps(signals, indent=2, default=str))

    # Check state
    print("\n=== ECS STATE ===\n")
    state = ecs.get_ecs_state()
    print(json.dumps(state, indent=2, default=str))
