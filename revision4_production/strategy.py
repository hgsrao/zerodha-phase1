"""
REVISION 04: Production Intraday Strategy

TARGET: ₹1,000/day per symbol (₹48,000/day on 48 symbols)

DESIGN PRINCIPLES:
1. Simple (no complexity unless proven necessary)
2. Measurable (daily P&L, win rate, trade count)
3. Testable (clear entry/exit rules)
4. Scalable (same logic × 48 symbols)

ENTRY RULES:
- PA Confidence > 0.75 (high conviction only)
- Time: 9:15 AM - 2:00 PM (avoid market chop)
- No more than 5 concurrent positions

EXIT RULES:
- Time-based: 60 minutes hold OR market close
- Profit target: ATR × 2.5
- Stop loss: ATR × 1.0 (tight, defensive)
- Mandatory EOD close (no overnight)

MONEY MANAGEMENT:
- Position size: ₹2,083 per symbol (₹100k / 48)
- Max loss per day: ₹2,000 (2% of capital)
- Max loss overall: ₹10,000 (circuit breaker)
"""

import logging
import numpy as np
import talib
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class TradeSignal:
    """Entry signal for a trade."""
    symbol: str
    direction: int  # 1=BUY, -1=SELL
    confidence: float  # 0-1
    entry_price: float
    stop_price: float
    target_price: float
    timestamp: str
    reason: str

class Revision04Strategy:
    """
    Production strategy: High confidence entries + tight risk management.
    """

    def __init__(
        self,
        min_confidence: float = 0.75,
        atr_stop_mult: float = 1.0,
        atr_target_mult: float = 2.5,
        hold_minutes: int = 60,
        trading_start_hour: int = 9,
        trading_start_minute: int = 15,
        trading_end_hour: int = 14,
        trading_end_minute: int = 0,
    ):
        """
        Initialize strategy with parameters.

        Args:
            min_confidence: Minimum PA confidence to enter (default 0.75)
            atr_stop_mult: Stop distance as multiple of ATR (default 1.0 = tight)
            atr_target_mult: Target distance as multiple of ATR (default 2.5)
            hold_minutes: Maximum hold time (default 60 minutes)
            trading_start_hour: Market open hour (default 9:15)
            trading_start_minute: Market open minute
            trading_end_hour: Market close hour (default 2:00 PM)
            trading_end_minute: Market close minute
        """
        self.min_confidence = min_confidence
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.hold_minutes = hold_minutes
        self.trading_start_hour = trading_start_hour
        self.trading_start_minute = trading_start_minute
        self.trading_end_hour = trading_end_hour
        self.trading_end_minute = trading_end_minute

        logger.info(f"Strategy initialized:")
        logger.info(f"  Min confidence: {min_confidence}")
        logger.info(f"  Stop: ATR × {atr_stop_mult}")
        logger.info(f"  Target: ATR × {atr_target_mult}")
        logger.info(f"  Hold time: {hold_minutes} min")
        logger.info(f"  Trading hours: {trading_start_hour}:{trading_start_minute:02d} - {trading_end_hour}:{trading_end_minute:02d}")

    def is_trading_hours(self, hour: int, minute: int) -> bool:
        """Check if current time is within trading window."""
        start_minutes = self.trading_start_hour * 60 + self.trading_start_minute
        end_minutes = self.trading_end_hour * 60 + self.trading_end_minute
        current_minutes = hour * 60 + minute

        return start_minutes <= current_minutes < end_minutes

    def calculate_atr(self, close: np.ndarray, high: np.ndarray, low: np.ndarray, period: int = 14) -> float:
        """Calculate ATR, with floor to prevent zero volatility."""
        if len(close) < period:
            return 0.001

        atr = talib.ATR(high, low, close, timeperiod=period)[-1]

        # Floor: Prevent zero volatility that kills stops
        if atr < 0.001:
            atr = close[-1] * 0.005  # 0.5% of price

        return float(atr)

    def generate_entry_signal(
        self,
        symbol: str,
        close_prices: np.ndarray,
        high_prices: np.ndarray,
        low_prices: np.ndarray,
        pa_confidence: float,
        signal_direction: int = 1,
        current_hour: int = 10,
        current_minute: int = 0,
    ) -> Optional[TradeSignal]:
        """
        Generate entry signal if conditions are met.

        Returns:
            TradeSignal if entry criteria met, None otherwise
        """

        # RULE 1: High confidence only
        if pa_confidence < self.min_confidence:
            return None

        # RULE 2: Trading hours only
        if not self.is_trading_hours(current_hour, current_minute):
            return None

        # RULE 3: Calculate stops and targets
        current_price = close_prices[-1]
        atr = self.calculate_atr(close_prices, high_prices, low_prices)

        if atr < 0.001:
            logger.warning(f"{symbol}: ATR degenerate, skipping entry")
            return None

        # BUY: Long entry
        if signal_direction == 1:
            stop_price = current_price - (atr * self.atr_stop_mult)
            target_price = current_price + (atr * self.atr_target_mult)
            reason = f"BUY @ {current_price:.2f} | Conf={pa_confidence:.2f} | Stop={stop_price:.2f} | Target={target_price:.2f}"

        # SELL: Short entry
        else:
            stop_price = current_price + (atr * self.atr_stop_mult)
            target_price = current_price - (atr * self.atr_target_mult)
            reason = f"SELL @ {current_price:.2f} | Conf={pa_confidence:.2f} | Stop={stop_price:.2f} | Target={target_price:.2f}"

        return TradeSignal(
            symbol=symbol,
            direction=signal_direction,
            confidence=pa_confidence,
            entry_price=current_price,
            stop_price=stop_price,
            target_price=target_price,
            timestamp=f"{current_hour:02d}:{current_minute:02d}",
            reason=reason,
        )

    def check_exit(
        self,
        current_price: float,
        entry_price: float,
        target_price: float,
        stop_price: float,
        minutes_held: int,
        direction: int = 1,
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        Check if position should exit.

        Returns:
            (should_exit, reason, exit_price)
        """

        # Time-based exit
        if minutes_held >= self.hold_minutes:
            return True, "TIME_EXIT", current_price

        # BUY position
        if direction == 1:
            # Hit target
            if current_price >= target_price:
                pnl = current_price - entry_price
                return True, f"TARGET_HIT (+{pnl:.2f})", current_price

            # Hit stop
            if current_price <= stop_price:
                pnl = current_price - entry_price
                return True, f"STOP_HIT ({pnl:.2f})", current_price

        # SELL position
        else:
            # Hit target
            if current_price <= target_price:
                pnl = entry_price - current_price
                return True, f"TARGET_HIT (+{pnl:.2f})", current_price

            # Hit stop
            if current_price >= stop_price:
                pnl = entry_price - current_price
                return True, f"STOP_HIT ({pnl:.2f})", current_price

        return False, None, None

    def on_bar_end(self, hour: int, minute: int) -> bool:
        """Check if position must close at end of trading day."""
        # Mandatory close at market end
        if hour >= self.trading_end_hour and minute >= self.trading_end_minute:
            return True
        return False
