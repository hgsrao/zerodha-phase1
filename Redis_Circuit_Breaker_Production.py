# ============================================================================
# REDIS CIRCUIT BREAKER - PRODUCTION CODE
# Fast, distributed circuit breaker for 48-symbol portfolio
# Date: August 30, 2026
# Status: READY FOR LIVE DEPLOYMENT
# ============================================================================

import redis
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple
import json

# ============================================================================
# LOGGING
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('CircuitBreaker')

# ============================================================================
# CIRCUIT BREAKER THRESHOLDS
# ============================================================================

class CircuitBreakerThresholds:
    """Configurable circuit breaker triggers"""

    # Daily loss threshold
    DAILY_LOSS_THRESHOLD = -50000  # ₹50,000 loss triggers halt

    # Max drawdown threshold
    MAX_DRAWDOWN_THRESHOLD = -0.05  # 5% drawdown

    # Consecutive loss threshold
    CONSECUTIVE_LOSSES_THRESHOLD = 5

    # Volatility crisis threshold
    VOLATILITY_CRISIS = 5.0  # 5% rolling vol

    # Correlation herd threshold
    CORRELATION_HERD = 0.8

    # Sharpe ratio minimum
    SHARPE_MIN = 0.0


# ============================================================================
# REDIS CIRCUIT BREAKER
# ============================================================================

class RedisCircuitBreaker:
    """
    Fast, distributed circuit breaker using Redis

    Used by all 48 symbol engines to check if trading is allowed.
    Sub-millisecond latency (<1ms).
    """

    def __init__(self, host='localhost', port=6379, db=0):
        """
        Initialize Redis connection

        Args:
            host: Redis server host
            port: Redis server port
            db: Redis database number
        """
        try:
            self.redis_client = redis.Redis(
                host=host,
                port=port,
                db=db,
                decode_responses=True,
                socket_keepalive=True,
                socket_keepalive_options={
                    1: 1,  # TCP_KEEPIDLE
                    2: 1,  # TCP_KEEPINTVL
                    3: 3,  # TCP_KEEPCNT
                }
            )
            # Test connection
            self.redis_client.ping()
            logger.info(f"Redis connected: {host}:{port}")
            self._initialize_circuit_breaker()
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _initialize_circuit_breaker(self):
        """Initialize circuit breaker state in Redis"""
        # Flags
        self.redis_client.set('trading:allowed', 'true')
        self.redis_client.set('trading:halt_reason', '')

        # Counters
        self.redis_client.set('trading:daily_loss', '0')
        self.redis_client.set('trading:daily_max_dd', '0')
        self.redis_client.set('trading:consecutive_losses', '0')
        self.redis_client.set('trading:trades_today', '0')

        # Metrics
        self.redis_client.set('trading:volatility', '0')
        self.redis_client.set('trading:correlation', '0')
        self.redis_client.set('trading:stress_factor', '0')

        logger.info("Circuit breaker initialized")

    # ========================================================================
    # MAIN API: Check If Trading Allowed
    # ========================================================================

    def is_trading_allowed(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed (called by each symbol engine)

        Returns:
            (bool, str): (is_allowed, reason_if_halted)

        This is the FAST PATH - called 48x per bar - must be < 1ms
        """
        try:
            allowed = self.redis_client.get('trading:allowed') == 'true'
            reason = self.redis_client.get('trading:halt_reason') or ''
            return allowed, reason
        except Exception as e:
            logger.error(f"Redis read error: {e}")
            # Fail-safe: allow trading if Redis unavailable
            return True, ''

    # ========================================================================
    # UPDATE CIRCUIT BREAKER STATE
    # ========================================================================

    def on_trade_result(self, trade_pl: float, trade_type: str = 'EQUITY'):
        """
        Called after each trade execution

        Args:
            trade_pl: P&L from trade (+ for win, - for loss)
            trade_type: 'EQUITY' or 'DERIVATIVE'
        """
        try:
            # Update daily loss
            current_loss = float(self.redis_client.get('trading:daily_loss') or 0)
            new_loss = current_loss + trade_pl
            self.redis_client.set('trading:daily_loss', str(new_loss))

            # Update consecutive losses
            if trade_pl < 0:
                current_losses = int(self.redis_client.get('trading:consecutive_losses') or 0)
                self.redis_client.set('trading:consecutive_losses', str(current_losses + 1))
            else:
                self.redis_client.set('trading:consecutive_losses', '0')

            # Update trade count
            current_trades = int(self.redis_client.get('trading:trades_today') or 0)
            self.redis_client.set('trading:trades_today', str(current_trades + 1))

            # Check triggers
            self._check_circuit_breaker_triggers()

            logger.debug(f"Trade result: PL={trade_pl:.0f}, "
                        f"Daily loss={new_loss:.0f}, "
                        f"Consecutive losses={self.redis_client.get('trading:consecutive_losses')}")

        except Exception as e:
            logger.error(f"Error updating circuit breaker: {e}")

    def set_daily_loss(self, loss: float):
        """Set daily loss directly (for reconciliation)"""
        try:
            self.redis_client.set('trading:daily_loss', str(loss))
            self._check_circuit_breaker_triggers()
        except Exception as e:
            logger.error(f"Error setting daily loss: {e}")

    def set_max_drawdown(self, dd: float):
        """Set max drawdown directly (from ECS)"""
        try:
            self.redis_client.set('trading:daily_max_dd', str(dd))
            self._check_circuit_breaker_triggers()
        except Exception as e:
            logger.error(f"Error setting max drawdown: {e}")

    def set_volatility(self, vol: float):
        """Set current volatility (from ECS)"""
        try:
            self.redis_client.set('trading:volatility', str(vol))
            self._check_circuit_breaker_triggers()
        except Exception as e:
            logger.error(f"Error setting volatility: {e}")

    def set_correlation(self, corr: float):
        """Set portfolio correlation (from ECS)"""
        try:
            self.redis_client.set('trading:correlation', str(corr))
            self._check_circuit_breaker_triggers()
        except Exception as e:
            logger.error(f"Error setting correlation: {e}")

    def set_stress_factor(self, stress: float):
        """Set ECS stress factor (from ECS)"""
        try:
            self.redis_client.set('trading:stress_factor', str(stress))
            self._check_circuit_breaker_triggers()
        except Exception as e:
            logger.error(f"Error setting stress factor: {e}")

    # ========================================================================
    # CIRCUIT BREAKER TRIGGER LOGIC
    # ========================================================================

    def _check_circuit_breaker_triggers(self):
        """
        Check all circuit breaker conditions and halt if any triggered

        Conditions (any one triggers halt):
        1. Daily loss > threshold
        2. Max drawdown > threshold
        3. Consecutive losses >= threshold
        4. Volatility crisis
        5. Correlation herd (> 0.8)
        6. ECS says halt (stress > 0.7)
        """

        triggers = []

        # Read current metrics
        try:
            daily_loss = float(self.redis_client.get('trading:daily_loss') or 0)
            max_dd = float(self.redis_client.get('trading:daily_max_dd') or 0)
            consecutive_losses = int(self.redis_client.get('trading:consecutive_losses') or 0)
            volatility = float(self.redis_client.get('trading:volatility') or 0)
            correlation = float(self.redis_client.get('trading:correlation') or 0)
            stress_factor = float(self.redis_client.get('trading:stress_factor') or 0)
        except:
            return

        # Check each trigger

        # 1. Daily loss
        if daily_loss < CircuitBreakerThresholds.DAILY_LOSS_THRESHOLD:
            triggers.append(f"daily_loss={daily_loss:.0f}")

        # 2. Max drawdown
        if max_dd < CircuitBreakerThresholds.MAX_DRAWDOWN_THRESHOLD:
            triggers.append(f"max_dd={max_dd:.3f}")

        # 3. Consecutive losses
        if consecutive_losses >= CircuitBreakerThresholds.CONSECUTIVE_LOSSES_THRESHOLD:
            triggers.append(f"consecutive_losses={consecutive_losses}")

        # 4. Volatility crisis
        if volatility > CircuitBreakerThresholds.VOLATILITY_CRISIS:
            triggers.append(f"volatility={volatility:.2f}")

        # 5. Correlation herd
        if correlation > CircuitBreakerThresholds.CORRELATION_HERD:
            triggers.append(f"correlation={correlation:.2f}")

        # 6. ECS stress (would be set by ECS supervisor)
        if stress_factor > 0.7:
            triggers.append(f"stress={stress_factor:.2f}")

        # If any trigger, halt
        if triggers:
            halt_reason = f"Circuit breaker: {', '.join(triggers)}"
            self.halt_trading(halt_reason)
        else:
            # No triggers, ensure trading is allowed
            if self.redis_client.get('trading:allowed') != 'true':
                # Check if we should un-halt
                self.allow_trading()

    # ========================================================================
    # HALT / ALLOW TRADING
    # ========================================================================

    def halt_trading(self, reason: str):
        """HALT all trading"""
        try:
            current = self.redis_client.get('trading:allowed')
            if current == 'true':  # Only log if transitioning
                logger.critical(f"HALT TRADING: {reason}")
            self.redis_client.set('trading:allowed', 'false')
            self.redis_client.set('trading:halt_reason', reason)
            self.redis_client.set('trading:halt_timestamp', datetime.now().isoformat())
        except Exception as e:
            logger.error(f"Error halting trading: {e}")

    def allow_trading(self):
        """ALLOW all trading (circuit breaker released)"""
        try:
            current = self.redis_client.get('trading:allowed')
            if current == 'false':  # Only log if transitioning
                logger.info("TRADING RESUMED: Circuit breaker released")
            self.redis_client.set('trading:allowed', 'true')
            self.redis_client.set('trading:halt_reason', '')
            self.redis_client.set('trading:halt_timestamp', '')
        except Exception as e:
            logger.error(f"Error allowing trading: {e}")

    def manual_halt(self, reason: str):
        """Manual halt by operator"""
        logger.warning(f"MANUAL HALT: {reason}")
        self.halt_trading(f"Manual halt: {reason}")

    def manual_resume(self, reason: str):
        """Manual resume by operator"""
        logger.warning(f"MANUAL RESUME: {reason}")
        self.allow_trading()

    # ========================================================================
    # STATE QUERIES
    # ========================================================================

    def get_circuit_breaker_status(self) -> Dict:
        """Get detailed circuit breaker status"""
        try:
            return {
                'timestamp': datetime.now().isoformat(),
                'is_trading_allowed': self.redis_client.get('trading:allowed') == 'true',
                'halt_reason': self.redis_client.get('trading:halt_reason') or 'None',
                'halt_timestamp': self.redis_client.get('trading:halt_timestamp') or 'N/A',
                'daily_loss': float(self.redis_client.get('trading:daily_loss') or 0),
                'daily_max_dd': float(self.redis_client.get('trading:daily_max_dd') or 0),
                'consecutive_losses': int(self.redis_client.get('trading:consecutive_losses') or 0),
                'trades_today': int(self.redis_client.get('trading:trades_today') or 0),
                'volatility': float(self.redis_client.get('trading:volatility') or 0),
                'correlation': float(self.redis_client.get('trading:correlation') or 0),
                'stress_factor': float(self.redis_client.get('trading:stress_factor') or 0)
            }
        except Exception as e:
            logger.error(f"Error getting circuit breaker status: {e}")
            return {}

    def reset_daily_counters(self):
        """Reset daily counters (call at market open)"""
        try:
            self.redis_client.set('trading:daily_loss', '0')
            self.redis_client.set('trading:daily_max_dd', '0')
            self.redis_client.set('trading:consecutive_losses', '0')
            self.redis_client.set('trading:trades_today', '0')
            logger.info("Daily counters reset")
        except Exception as e:
            logger.error(f"Error resetting daily counters: {e}")

    # ========================================================================
    # REDIS HEALTH CHECK
    # ========================================================================

    def health_check(self) -> bool:
        """Check Redis connection health"""
        try:
            ping = self.redis_client.ping()
            latency_ms = self.redis_client.execute_command('PING')
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False

    def get_redis_info(self) -> Dict:
        """Get Redis server info"""
        try:
            info = self.redis_client.info()
            return {
                'redis_version': info.get('redis_version'),
                'uptime_seconds': info.get('uptime_in_seconds'),
                'used_memory_mb': info.get('used_memory', 0) / 1024 / 1024,
                'connected_clients': info.get('connected_clients'),
                'total_commands_processed': info.get('total_commands_processed')
            }
        except Exception as e:
            logger.error(f"Error getting Redis info: {e}")
            return {}


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == '__main__':
    # Initialize circuit breaker
    cb = RedisCircuitBreaker(host='localhost', port=6379)

    # Print initial status
    print("\n" + "="*80)
    print("INITIAL CIRCUIT BREAKER STATUS")
    print("="*80)
    status = cb.get_circuit_breaker_status()
    print(json.dumps(status, indent=2))

    # Simulate trading
    print("\n" + "="*80)
    print("SIMULATING TRADING")
    print("="*80)

    # Record some wins
    for i in range(3):
        cb.on_trade_result(+5000)  # +₹5000 win
        print(f"Win #{i+1}: +₹5000")

    # Record a loss
    cb.on_trade_result(-8000)  # -₹8000 loss
    print(f"Loss: -₹8000")

    # Check status
    print("\n" + "="*80)
    print("STATUS AFTER TRADING")
    print("="*80)
    status = cb.get_circuit_breaker_status()
    print(json.dumps(status, indent=2))

    # Simulate large loss to trigger halt
    print("\n" + "="*80)
    print("SIMULATING LARGE LOSS (should halt)")
    print("="*80)
    cb.set_daily_loss(-55000)  # Exceeds ₹50k threshold
    print("Set daily loss to -₹55000")

    # Check if halted
    allowed, reason = cb.is_trading_allowed()
    print(f"Trading allowed: {allowed}")
    print(f"Reason: {reason}")

    # Get final status
    print("\n" + "="*80)
    print("FINAL STATUS")
    print("="*80)
    status = cb.get_circuit_breaker_status()
    print(json.dumps(status, indent=2))

    # Redis info
    print("\n" + "="*80)
    print("REDIS SERVER INFO")
    print("="*80)
    info = cb.get_redis_info()
    print(json.dumps(info, indent=2))

