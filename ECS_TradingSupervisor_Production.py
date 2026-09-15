# ============================================================================
# ECS TRADING SUPERVISOR - PRODUCTION CODE
# Electrical Control System for Trading
# Date: August 30, 2026
# Status: READY FOR DEPLOYMENT
# ============================================================================

import numpy as np
import pandas as pd
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Dict, List, Tuple, Optional
from collections import deque
import json

# ============================================================================
# CONFIGURATION
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('ECS')

# ============================================================================
# ENUMS & DATA CLASSES
# ============================================================================

class OperatingMode(Enum):
    """7 Operating Modes based on power plant analogy"""
    BLACK_START_MODE = 1          # Crisis (stress > 0.7)
    VAR_SUPPORT_MODE = 2          # Divergent portfolio (DD > -2%, corr < 0.5)
    FREQUENCY_CONTROL_MODE = 3    # Choppy but winning (vol > 2.0, WR > 50%)
    LOAD_SHARING_MODE = 4         # Neutral (-0.2 < stress < 0.2)
    PLANT_FOLLOW_MODE = 5         # Trending (ADX > 25, stress < 0.3)
    ISOCHRONOUS_MODE = 6          # Concentrated (active_signals <= 2)
    ISLANDING_MODE = 7            # Herding (corr > 0.7, DD > -3%)


@dataclass
class MarketState:
    """Current market conditions input"""
    volatility: float              # Rolling 20-bar std
    drawdown: float                # Current drawdown % (-0.05 = -5%)
    correlation: float             # Avg correlation (0.0-1.0)
    trend_strength: float          # ADX or similar (0-100)
    win_rate: float                # Recent win rate (0.0-1.0)
    recent_trades: List[str]       # ['WIN', 'LOSS', 'WIN', ...]
    active_signals: int            # Number of symbols with valid PA scores
    timestamp: datetime            # When this state was measured


@dataclass
class ECSSignals:
    """Output signals to all 48 symbols"""
    speed_signal: float            # -100 to +100 (entry confidence)
    voltage_signal: float          # -100 to +100 (position size)
    mode: OperatingMode            # Which mode is active
    stress_factor: float           # -1.0 to +1.0 (mood)
    timestamp: datetime


@dataclass
class ECSState:
    """Internal ECS state tracking"""
    stress_factor_ema: float = 0.0
    speed_ema: float = 0.0
    voltage_ema: float = 0.0
    current_mode: OperatingMode = OperatingMode.LOAD_SHARING_MODE
    signal_history: deque = None
    mode_history: deque = None

    def __post_init__(self):
        if self.signal_history is None:
            self.signal_history = deque(maxlen=1440)  # 1 day of 1-min bars
        if self.mode_history is None:
            self.mode_history = deque(maxlen=1440)


# ============================================================================
# ECS TRADING SUPERVISOR
# ============================================================================

class ECS_TradingSupervisor:
    """
    Electrical Control System for Trading

    Hierarchical supervisor for 48 parallel trading engines.
    Generates SPEED RISE/LOWER and VOLTAGE RISE/LOWER signals.
    """

    def __init__(self, enable_redis: bool = False):
        """
        Initialize ECS Supervisor

        Args:
            enable_redis: If True, use Redis for circuit breaker state
                         If False, use in-memory flags (dev mode)
        """
        self.state = ECSState()
        self.enable_redis = enable_redis
        self.redis_client = None

        if enable_redis:
            try:
                import redis
                self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
                self.redis_client.ping()
                logger.info("Redis connected successfully")
            except Exception as e:
                logger.warning(f"Redis not available: {e}. Using in-memory mode.")
                self.enable_redis = False

        # In-memory circuit breaker state (if Redis unavailable)
        self.in_memory_state = {
            'is_trading_allowed': True,
            'daily_loss': 0.0,
            'daily_max_dd': 0.0,
            'halt_reason': None
        }

        logger.info("ECS Supervisor initialized")

    # ========================================================================
    # STRESS FACTOR CALCULATION
    # ========================================================================

    def calculate_stress_factor(self, market_state: MarketState) -> float:
        """
        Calculate unified stress factor from 4 components

        Returns:
            float: -1.0 (euphoric) to +1.0 (crisis)
        """

        # Component 1: Volatility
        baseline_vol = 2.5
        vol_ratio = market_state.volatility / baseline_vol
        vol_component = (vol_ratio - 1.0) / 1.5
        vol_component = np.clip(vol_component, -0.5, 1.0)

        # Component 2: Drawdown
        crisis_dd = 0.05  # 5% drawdown = crisis
        dd_component = abs(market_state.drawdown) / crisis_dd
        dd_component = np.clip(dd_component, -0.5, 1.0)

        # Component 3: Correlation
        corr_component = (market_state.correlation - 0.4) / 0.3
        corr_component = np.clip(corr_component, -0.5, 1.0)

        # Component 4: Streak (win/loss)
        recent = market_state.recent_trades[-20:] if len(market_state.recent_trades) > 0 else []
        wins = sum(1 for t in recent if t == 'WIN')
        losses = sum(1 for t in recent if t == 'LOSS')
        streak = wins - losses
        streak_component = streak / 20.0
        streak_component = np.clip(streak_component, -1.0, 1.0)

        # Weighted blend
        stress = (0.35 * vol_component +
                  0.35 * dd_component +
                  0.20 * corr_component +
                  0.10 * streak_component)

        # Smooth with EMA (prevent jitter)
        self.state.stress_factor_ema = 0.7 * self.state.stress_factor_ema + 0.3 * stress

        final_stress = np.clip(self.state.stress_factor_ema, -1.0, 1.0)

        logger.debug(f"Stress components: vol={vol_component:.3f}, dd={dd_component:.3f}, "
                    f"corr={corr_component:.3f}, streak={streak_component:.3f} → "
                    f"Final stress={final_stress:.3f}")

        return final_stress

    # ========================================================================
    # MODE SELECTION
    # ========================================================================

    def select_operating_mode(self,
                             market_state: MarketState,
                             stress_factor: float) -> OperatingMode:
        """
        Select one of 7 operating modes based on market conditions

        Priority-based selection (top to bottom)
        """

        corr = market_state.correlation
        dd = market_state.drawdown
        vol = market_state.volatility
        trend = market_state.trend_strength
        wr = market_state.win_rate
        signals = market_state.active_signals

        # Priority 1: Crisis mode (highest priority)
        if stress_factor > 0.7 and dd < -0.04:
            logger.warning(f"Crisis detected: stress={stress_factor:.2f}, dd={dd:.3f} → BLACK_START")
            return OperatingMode.BLACK_START_MODE

        # Priority 2: Concentrated opportunity
        if signals <= 2:
            logger.info(f"Few signals ({signals}) → ISOCHRONOUS (concentrated)")
            return OperatingMode.ISOCHRONOUS_MODE

        # Priority 3: Divergent portfolio
        if dd > -0.02 and corr < 0.5:
            logger.info(f"Divergent portfolio: dd={dd:.3f}, corr={corr:.2f} → VAR_SUPPORT")
            return OperatingMode.VAR_SUPPORT_MODE

        # Priority 4: Neutral/balanced
        if -0.2 < stress_factor < 0.2:
            logger.info(f"Neutral stress {stress_factor:.2f} → LOAD_SHARING")
            return OperatingMode.LOAD_SHARING_MODE

        # Priority 5: Trending market
        if trend > 25 and stress_factor < 0.3:
            logger.info(f"Trend detected: ADX={trend:.1f}, stress={stress_factor:.2f} → PLANT_FOLLOW")
            return OperatingMode.PLANT_FOLLOW_MODE

        # Priority 6: Choppy but winning
        if vol > 2.0 and wr > 0.50:
            logger.info(f"Choppy winning: vol={vol:.2f}, WR={wr:.2%} → FREQUENCY_CONTROL")
            return OperatingMode.FREQUENCY_CONTROL_MODE

        # Priority 7: Herding behavior (default)
        if corr > 0.7 and dd > -0.03:
            logger.info(f"Herding: corr={corr:.2f}, dd={dd:.3f} → ISLANDING")
            return OperatingMode.ISLANDING_MODE

        # Fallback
        logger.info(f"No clear mode, defaulting to LOAD_SHARING")
        return OperatingMode.LOAD_SHARING_MODE

    # ========================================================================
    # SIGNAL GENERATION
    # ========================================================================

    def calculate_speed_signal(self,
                              mode: OperatingMode,
                              stress_factor: float,
                              trend_strength: float) -> float:
        """
        Calculate SPEED RISE/LOWER signal (entry confidence adaptation)

        Range: -100 to +100
        -100 = very defensive (only strongest signals)
        0 = neutral
        +100 = very aggressive (lower threshold)
        """

        base_signal = {
            OperatingMode.BLACK_START_MODE: -75,
            OperatingMode.VAR_SUPPORT_MODE: -25,
            OperatingMode.FREQUENCY_CONTROL_MODE: 0,
            OperatingMode.LOAD_SHARING_MODE: 0,
            OperatingMode.PLANT_FOLLOW_MODE: +50,
            OperatingMode.ISOCHRONOUS_MODE: +75,
            OperatingMode.ISLANDING_MODE: +25,
        }[mode]

        # Stress adjustment
        stress_adj = stress_factor * 50

        # Trend bonus
        trend_bonus = (trend_strength - 20) * 2 if trend_strength > 20 else 0

        # Calculate raw signal
        speed = base_signal + stress_adj + trend_bonus
        speed = np.clip(speed, -100, +100)

        # Smooth with EMA
        self.state.speed_ema = 0.7 * self.state.speed_ema + 0.3 * speed

        logger.debug(f"SPEED signal: base={base_signal}, stress_adj={stress_adj:.1f}, "
                    f"trend_bonus={trend_bonus:.1f} → EMA={self.state.speed_ema:.1f}")

        return self.state.speed_ema

    def calculate_voltage_signal(self,
                                mode: OperatingMode,
                                stress_factor: float,
                                drawdown: float,
                                correlation: float) -> float:
        """
        Calculate VOLTAGE RISE/LOWER signal (position size adaptation)

        Range: -100 to +100
        -100 = minimum positions
        0 = normal size
        +100 = maximum positions
        """

        base_signal = {
            OperatingMode.BLACK_START_MODE: -100,
            OperatingMode.VAR_SUPPORT_MODE: +25,
            OperatingMode.FREQUENCY_CONTROL_MODE: -25,
            OperatingMode.LOAD_SHARING_MODE: 0,
            OperatingMode.PLANT_FOLLOW_MODE: +50,
            OperatingMode.ISOCHRONOUS_MODE: +75,
            OperatingMode.ISLANDING_MODE: +10,
        }[mode]

        # Drawdown adjustment (only reduce on DD)
        dd_adj = (abs(drawdown) - 0.02) * 500
        dd_adj = np.clip(dd_adj, -50, 0)

        # Correlation adjustment (high corr = reduce)
        corr_adj = (correlation - 0.4) * 100
        corr_adj = np.clip(corr_adj, -30, 30)

        # Calculate raw signal
        voltage = base_signal + dd_adj + corr_adj
        voltage = np.clip(voltage, -100, +100)

        # Smooth with EMA
        self.state.voltage_ema = 0.7 * self.state.voltage_ema + 0.3 * voltage

        logger.debug(f"VOLTAGE signal: base={base_signal}, dd_adj={dd_adj:.1f}, "
                    f"corr_adj={corr_adj:.1f} → EMA={self.state.voltage_ema:.1f}")

        return self.state.voltage_ema

    # ========================================================================
    # MAIN: GENERATE SIGNALS
    # ========================================================================

    def generate_signals(self, market_state: MarketState) -> ECSSignals:
        """
        Main entry point: Generate SPEED and VOLTAGE signals for all 48 symbols
        """

        # Step 1: Calculate stress factor
        stress = self.calculate_stress_factor(market_state)

        # Step 2: Select mode
        mode = self.select_operating_mode(market_state, stress)
        self.state.current_mode = mode

        # Step 3: Calculate signals
        speed = self.calculate_speed_signal(mode, stress, market_state.trend_strength)
        voltage = self.calculate_voltage_signal(mode, stress, market_state.drawdown,
                                               market_state.correlation)

        # Step 4: Package signals
        signals = ECSSignals(
            speed_signal=speed,
            voltage_signal=voltage,
            mode=mode,
            stress_factor=stress,
            timestamp=market_state.timestamp
        )

        # Step 5: Store history
        self.state.signal_history.append(signals)
        self.state.mode_history.append((mode, stress, signals.timestamp))

        # Step 6: Check circuit breaker
        self._check_circuit_breaker(market_state, stress)

        logger.info(f"ECS Signals: Mode={mode.name}, Stress={stress:.3f}, "
                   f"SPEED={speed:.1f}, VOLTAGE={voltage:.1f}")

        return signals

    # ========================================================================
    # CIRCUIT BREAKER (BLACK_START VALIDATION)
    # ========================================================================

    def _check_circuit_breaker(self, market_state: MarketState, stress_factor: float):
        """
        Validate circuit breaker triggers (BLACK_START_MODE conditions)
        """

        triggers = []

        if stress_factor > 0.7:
            triggers.append(f"stress={stress_factor:.2f}")

        if market_state.drawdown < -0.04:
            triggers.append(f"dd={market_state.drawdown:.3f}")

        if market_state.volatility > 5.0:
            triggers.append(f"vol={market_state.volatility:.2f}")

        if market_state.correlation > 0.8:
            triggers.append(f"corr={market_state.correlation:.2f}")

        if len(market_state.recent_trades) >= 5:
            last_5 = market_state.recent_trades[-5:]
            if all(t == 'LOSS' for t in last_5):
                triggers.append("5_losses")

        if triggers:
            logger.warning(f"Circuit breaker triggers: {', '.join(triggers)}")
            self.set_halt_trading(f"Circuit breaker: {', '.join(triggers)}")
        else:
            if not self.is_trading_allowed():
                logger.info("Circuit breaker released - stress normalized")
                self.allow_trading()

    # ========================================================================
    # CIRCUIT BREAKER STATE MANAGEMENT
    # ========================================================================

    def set_halt_trading(self, reason: str):
        """Signal all 48 symbols to HALT (circuit breaker triggered)"""
        if self.enable_redis and self.redis_client:
            try:
                self.redis_client.set('is_trading_allowed', 'false')
                self.redis_client.set('halt_reason', reason)
                logger.critical(f"HALT TRADING: {reason} (Redis)")
            except Exception as e:
                logger.warning(f"Redis set failed: {e}, using in-memory")
                self.in_memory_state['is_trading_allowed'] = False
                self.in_memory_state['halt_reason'] = reason
        else:
            self.in_memory_state['is_trading_allowed'] = False
            self.in_memory_state['halt_reason'] = reason
            logger.critical(f"HALT TRADING: {reason} (in-memory)")

    def allow_trading(self):
        """Signal all 48 symbols to RESUME trading"""
        if self.enable_redis and self.redis_client:
            try:
                self.redis_client.set('is_trading_allowed', 'true')
                self.redis_client.delete('halt_reason')
                logger.info("Trading RESUMED (Redis)")
            except Exception as e:
                logger.warning(f"Redis set failed: {e}, using in-memory")
                self.in_memory_state['is_trading_allowed'] = True
                self.in_memory_state['halt_reason'] = None
        else:
            self.in_memory_state['is_trading_allowed'] = True
            self.in_memory_state['halt_reason'] = None
            logger.info("Trading RESUMED (in-memory)")

    def is_trading_allowed(self) -> bool:
        """Check if trading is allowed (circuit breaker check)"""
        if self.enable_redis and self.redis_client:
            try:
                return self.redis_client.get('is_trading_allowed') == 'true'
            except:
                pass
        return self.in_memory_state['is_trading_allowed']

    def get_circuit_breaker_status(self) -> Dict:
        """Get detailed circuit breaker status"""
        if self.enable_redis and self.redis_client:
            try:
                return {
                    'is_trading_allowed': self.redis_client.get('is_trading_allowed') == 'true',
                    'halt_reason': self.redis_client.get('halt_reason'),
                    'backend': 'redis'
                }
            except:
                pass
        return {
            'is_trading_allowed': self.in_memory_state['is_trading_allowed'],
            'halt_reason': self.in_memory_state['halt_reason'],
            'backend': 'in_memory'
        }

    # ========================================================================
    # HISTORY & DIAGNOSTICS
    # ========================================================================

    def get_signal_history(self, last_n: int = 100) -> pd.DataFrame:
        """Return last N signals as DataFrame"""
        recent = list(self.state.signal_history)[-last_n:]
        return pd.DataFrame([{
            'timestamp': s.timestamp,
            'mode': s.mode.name,
            'stress': s.stress_factor,
            'speed': s.speed_signal,
            'voltage': s.voltage_signal
        } for s in recent])

    def get_mode_statistics(self) -> Dict:
        """Get statistics on mode usage"""
        if not self.state.mode_history:
            return {}

        modes = [m[0].name for m in self.state.mode_history]
        stresses = [m[1] for m in self.state.mode_history]

        stats = {}
        for mode in OperatingMode:
            count = modes.count(mode.name)
            if count > 0:
                mode_stresses = [s for m, s, _ in self.state.mode_history if m == mode]
                stats[mode.name] = {
                    'count': count,
                    'percent': count / len(modes) * 100,
                    'avg_stress': np.mean(mode_stresses),
                    'max_stress': np.max(mode_stresses)
                }

        return stats

    def export_state(self) -> Dict:
        """Export ECS state for analysis/debugging"""
        return {
            'current_mode': self.state.current_mode.name,
            'stress_factor_ema': self.state.stress_factor_ema,
            'speed_ema': self.state.speed_ema,
            'voltage_ema': self.state.voltage_ema,
            'signal_history_length': len(self.state.signal_history),
            'mode_history_length': len(self.state.mode_history),
            'circuit_breaker': self.get_circuit_breaker_status()
        }


# ============================================================================
# HELPER: Convert ECS Signals to Symbol Parameters
# ============================================================================

def convert_ecs_signals_to_symbol_params(ecs_signals: ECSSignals) -> Dict:
    """
    Convert ECS SPEED/VOLTAGE signals to individual symbol parameters

    Used by 6-stage engine to interpret ECS commands
    """

    # SPEED signal → Entry threshold adjustment
    # speed_signal = -100 to +100
    speed_impact = ecs_signals.speed_signal / 200  # -0.5 to +0.5
    base_entry_threshold = 0.75
    adaptive_entry_threshold = base_entry_threshold - speed_impact
    adaptive_entry_threshold = np.clip(adaptive_entry_threshold, 0.65, 0.85)

    # VOLTAGE signal → Position multiplier adjustment
    # voltage_signal = -100 to +100
    voltage_impact = ecs_signals.voltage_signal / 200  # -0.5 to +0.5
    base_position_mult = 1.0
    adaptive_position_mult = base_position_mult + voltage_impact
    adaptive_position_mult = np.clip(adaptive_position_mult, 0.85, 1.0)

    return {
        'entry_threshold': adaptive_entry_threshold,
        'position_multiplier': adaptive_position_mult,
        'speed_signal': ecs_signals.speed_signal,
        'voltage_signal': ecs_signals.voltage_signal,
        'mode': ecs_signals.mode.name,
        'stress_factor': ecs_signals.stress_factor,
        'timestamp': ecs_signals.timestamp
    }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    # Initialize ECS (without Redis for dev/backtest)
    ecs = ECS_TradingSupervisor(enable_redis=False)

    # Simulate market state
    market_state = MarketState(
        volatility=2.5,
        drawdown=-0.01,
        correlation=0.5,
        trend_strength=22.0,
        win_rate=0.52,
        recent_trades=['WIN', 'WIN', 'LOSS', 'WIN', 'WIN'] * 4,
        active_signals=48,
        timestamp=datetime.now()
    )

    # Generate signals
    signals = ecs.generate_signals(market_state)

    # Convert to symbol parameters
    symbol_params = convert_ecs_signals_to_symbol_params(signals)

    print("\n" + "="*80)
    print("ECS SIGNALS GENERATED")
    print("="*80)
    print(f"Mode: {signals.mode.name}")
    print(f"Stress Factor: {signals.stress_factor:.3f}")
    print(f"SPEED Signal: {signals.speed_signal:.1f}")
    print(f"VOLTAGE Signal: {signals.voltage_signal:.1f}")
    print("\nSymbol Parameters:")
    print(f"  Entry Threshold: {symbol_params['entry_threshold']:.3f}")
    print(f"  Position Multiplier: {symbol_params['position_multiplier']:.3f}")
    print("="*80)

    # Export state
    state = ecs.export_state()
    print("\nECS State:")
    print(json.dumps(state, indent=2, default=str))

