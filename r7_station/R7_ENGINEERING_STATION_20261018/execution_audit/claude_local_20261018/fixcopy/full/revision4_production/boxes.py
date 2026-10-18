"""
REVISION 04: 10-Box Modular Engine

Complete integrated system for ₹1,000/day intraday trading

Box Architecture:
  1. Data Input Box - Market data ingestion
  2. PA Box - Predictive Analytics signals
  3. Chart Studies Box - Technical indicators
  4. Entry Validator Box - Entry decision logic
  5. Risk Manager Box - Position sizing, stops
  6. Grid Sync Box - Market regime check
  7. Position Manager Box - Track open trades
  8. Exit Decision Box - Exit logic
  9. MPC Box - Model Predictive Control sizing
  10. Performance Tracker Box - Daily P&L metrics
"""

import logging
import numpy as np
import talib
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ========== BOX 1: DATA INPUT BOX ==========
@dataclass
class MarketData:
    """Market data for a bar."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int

class DataInputBox:
    """Box 1: Ingests and validates market data."""

    def process(self, timestamp: str, o: float, h: float, l: float, c: float, v: int) -> MarketData:
        """Validate and return market data."""
        if c <= 0 or h < l or o <= 0:
            logger.warning(f"Invalid data at {timestamp}")
            return None

        return MarketData(timestamp, o, h, l, c, v)

# ========== BOX 2: PA BOX (Predictive Analytics) ==========
class PABox:
    """Box 2: Predictive Analytics - Generate confidence scores."""

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def calculate_confidence(self, closes: np.ndarray, volumes: np.ndarray) -> float:
        """
        Calculate PA confidence (0-1) based on momentum and volume.

        High confidence = strong trend + high volume
        Low confidence = choppy price + low volume
        """
        try:
            if len(closes) < self.lookback:
                return 0.6  # Default to moderate confidence

            recent_closes = closes[-self.lookback:]
            recent_volumes = volumes[-self.lookback:]

            # Momentum: % change over lookback
            momentum = (recent_closes[-1] - recent_closes[0]) / (recent_closes[0] + 1e-6)
            momentum_strength = min(abs(momentum) * 20, 1.0)  # Increased sensitivity

            # Volume: Ratio of recent to average
            volume_ma = np.mean(recent_volumes)
            current_volume = recent_volumes[-1]
            volume_strength = min(current_volume / (volume_ma + 1e-6), 1.0)

            # Trend: Use price acceleration (2nd derivative)
            if len(closes) >= 3:
                diffs = np.diff(closes[-3:])
                trend_accel = min(abs(diffs[1] - diffs[0]) / (abs(diffs[0]) + 1e-6), 1.0)
            else:
                trend_accel = 0.5

            # Combined confidence: momentum (50%) + volume (30%) + acceleration (20%)
            confidence = (
                momentum_strength * 0.5 +
                volume_strength * 0.3 +
                trend_accel * 0.2
            )

            # Add randomness to avoid deterministic patterns
            noise = np.random.uniform(-0.05, 0.05)
            confidence = float(np.clip(confidence + noise, 0.3, 1.0))

            return confidence if not np.isnan(confidence) else 0.6
        except:
            return 0.6

# ========== BOX 3: CHART STUDIES BOX ==========
class ChartStudiesBox:
    """Box 3: Technical indicators and chart studies."""

    def calculate_rsi(self, closes: np.ndarray, period: int = 14) -> float:
        """Calculate RSI."""
        if len(closes) < period:
            return 50.0
        rsi = talib.RSI(closes, timeperiod=period)[-1]
        return float(np.nan_to_num(rsi, nan=50.0))

    def calculate_macd(self, closes: np.ndarray) -> Tuple[float, float, float]:
        """Calculate MACD."""
        if len(closes) < 26:
            return 0.0, 0.0, 0.0

        macd, signal, hist = talib.MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)
        return float(macd[-1]), float(signal[-1]), float(hist[-1])

    def calculate_confidence(self, closes: np.ndarray) -> float:
        """Generate chart studies confidence (0-1)."""
        try:
            rsi = self.calculate_rsi(closes)
            macd, signal, hist = self.calculate_macd(closes)

            # RSI: Score based on distance from 50
            rsi_distance = abs(rsi - 50) / 50  # 0-1 scale
            rsi_strength = min(rsi_distance * 0.6, 1.0)  # Cap at 1.0

            # MACD: Score based on histogram
            macd_value = abs(hist) if hist > 0 else 0.3
            macd_strength = min(macd_value / 0.1, 1.0)  # Normalize

            # RSI momentum: Has RSI changed direction?
            if len(closes) >= 3:
                rsi_mom = 0.5 if rsi > 50 else 0.5  # Placeholder
            else:
                rsi_mom = 0.5

            confidence = (
                rsi_strength * 0.4 +
                macd_strength * 0.4 +
                rsi_mom * 0.2
            )

            confidence = float(np.clip(confidence, 0.3, 1.0))
            return confidence if not np.isnan(confidence) else 0.5
        except:
            return 0.5

# ========== BOX 4: ENTRY VALIDATOR BOX ==========
class EntryValidatorBox:
    """Box 4: Decide if entry is valid."""

    def __init__(self, min_pa_confidence: float = 0.75, min_chart_confidence: float = 0.6):
        self.min_pa_confidence = min_pa_confidence
        self.min_chart_confidence = min_chart_confidence

    def validate_entry(
        self,
        pa_confidence: float,
        chart_confidence: float,
        hour: int,
        minute: int,
    ) -> Tuple[bool, str]:
        """Validate if entry should proceed."""

        # High PA confidence required
        if pa_confidence < self.min_pa_confidence:
            return False, f"PA confidence low: {pa_confidence:.2f}"

        # Chart studies should confirm
        if chart_confidence < self.min_chart_confidence:
            return False, f"Chart confidence low: {chart_confidence:.2f}"

        # Trading hours (9:15 AM - 2:00 PM)
        minutes = hour * 60 + minute
        if not (555 <= minutes < 840):  # 9:15 to 14:00
            return False, f"Outside trading hours: {hour:02d}:{minute:02d}"

        return True, "Entry valid"

# ========== BOX 5: RISK MANAGER BOX ==========
class RiskManagerBox:
    """Box 5: Position sizing and risk parameters."""

    def __init__(self, max_risk_per_trade: float = 500, max_position_size: float = 2083):
        self.max_risk_per_trade = max_risk_per_trade  # ₹500 max risk
        self.max_position_size = max_position_size    # ₹2,083 per symbol

    def calculate_position_size(self, atr: float, current_price: float) -> float:
        """Calculate safe position size based on risk."""
        stop_distance = atr * 1.0  # ATR × 1.0

        if stop_distance <= 0:
            return 0

        # How many shares can we buy with max risk?
        shares = self.max_risk_per_trade / stop_distance

        # Position value in rupees
        position_value = shares * current_price

        # Cap at max position size
        if position_value > self.max_position_size:
            shares = self.max_position_size / current_price

        return shares

# ========== BOX 6: GRID SYNC BOX ==========
class GridSyncBox:
    """Box 6: Market regime synchronization."""

    def __init__(self, vix_min: float = 10.0, vix_max: float = 30.0):
        self.vix_min = vix_min
        self.vix_max = vix_max

    def check_synchronization(self, vix: float, nifty_trend: float) -> Tuple[bool, str]:
        """Check if market regime is favorable."""

        # VIX check: Operating band
        if not (self.vix_min <= vix <= self.vix_max):
            return False, f"VIX out of band: {vix:.1f}"

        # Trend check: Should be trending
        if abs(nifty_trend) < 0.3:
            return False, f"Market not trending: {nifty_trend:.2f}"

        return True, "Grid synchronized"

# ========== BOX 7: POSITION MANAGER BOX ==========
@dataclass
class Position:
    """Active position."""
    symbol: str
    direction: int
    entry_price: float
    entry_bar: int
    stop_price: float
    target_price: float
    shares: float

class PositionManagerBox:
    """Box 7: Track and manage open positions."""

    def __init__(self, max_positions: int = 5):
        self.positions: List[Position] = []
        self.max_positions = max_positions

    def add_position(self, position: Position) -> bool:
        """Add new position if within limits."""
        if len(self.positions) >= self.max_positions:
            return False
        self.positions.append(position)
        return True

    def get_open_positions(self) -> List[Position]:
        return self.positions.copy()

# ========== BOX 8: EXIT DECISION BOX ==========
class ExitDecisionBox:
    """Box 8: Determine if position should exit."""

    def check_exit(
        self,
        position: Position,
        current_price: float,
        bars_held: int,
        max_hold: int = 60,
    ) -> Tuple[bool, str, float]:
        """Check if position should exit."""

        # Time-based exit
        if bars_held >= max_hold:
            return True, "TIME_EXIT", current_price

        # BUY position
        if position.direction == 1:
            # Target hit
            if current_price >= position.target_price:
                pnl = current_price - position.entry_price
                return True, f"TARGET_HIT (+₹{pnl:.0f})", current_price

            # Stop hit
            if current_price <= position.stop_price:
                pnl = current_price - position.entry_price
                return True, f"STOP_HIT (₹{pnl:.0f})", current_price

        return False, None, None

# ========== BOX 9: MPC BOX (Model Predictive Control) ==========
class MPCBox:
    """Box 9: Model Predictive Control - Adjust position sizing."""

    def calculate_optimal_size(
        self,
        equity: float,
        max_daily_loss: float = 2000,
        current_daily_pnl: float = 0,
    ) -> float:
        """Adjust position size based on running loss."""

        remaining_risk = max_daily_loss - abs(min(current_daily_pnl, 0))

        if remaining_risk <= 0:
            return 0  # Stop trading, stop loss hit

        # Scale position size by remaining risk
        scale = remaining_risk / max_daily_loss
        base_size = 2083 * scale

        return float(np.clip(base_size, 0, 2083))

# ========== BOX 10: PERFORMANCE TRACKER BOX ==========
class PerformanceTrackerBox:
    """Box 10: Track daily and overall performance."""

    def __init__(self):
        self.daily_pnl: Dict[str, float] = {}
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0

    def record_trade(self, date: str, pnl: float):
        """Record completed trade."""
        if date not in self.daily_pnl:
            self.daily_pnl[date] = 0

        self.daily_pnl[date] += pnl
        self.total_trades += 1

        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

    def get_daily_summary(self) -> Dict[str, float]:
        """Get daily P&L summary."""
        return self.daily_pnl.copy()

    def get_stats(self) -> Dict[str, any]:
        """Get overall statistics."""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.winning_trades / max(self.total_trades, 1),
            "daily_avg": np.mean(list(self.daily_pnl.values())) if self.daily_pnl else 0,
        }
