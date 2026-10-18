#!/usr/bin/env python3
"""
================================================================================
POSITION MANAGEMENT MODULE - R2 EXECUTION
================================================================================

Handles all position sizing, concentration limits, and portfolio exposure logic.

Key responsibilities:
1. Position sizing based on risk and capital allocation
2. Concentration limits per symbol and portfolio
3. Portfolio exposure (lambda) calculation
4. Leverage management
5. Position tracking and reconciliation

Status: EXECUTION IN PROGRESS
Created: 2026-09-01

================================================================================
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


# ============================================================================
# POSITION MANAGEMENT CONFIGURATION
# ============================================================================

@dataclass
class PositionConfig:
    """Configuration for position management"""

    # Capital allocation
    max_risk_per_trade_fraction: float = 0.02  # 2% of portfolio
    max_loss_per_trade_rupees: float = 5000  # Hard cap on loss

    # Position sizing limits
    min_position_quantity: int = 1
    max_position_quantity: Dict[str, int] = None  # Per-symbol max qty

    # Portfolio concentration
    max_gross_exposure_fraction: float = 0.50  # 50% max total exposure
    max_exposure_per_symbol: float = 0.15  # 15% per symbol max
    max_concurrent_positions: int = 5

    # Leverage management
    allow_margin: bool = False  # Cash only (no margin)
    max_leverage_ratio: float = 1.0  # 1x leverage (cash only)

    # Position holding
    min_hold_bars: int = 2  # Minimum bars to hold
    max_hold_bars: int = 60  # Maximum bars before forced exit

    def __post_init__(self):
        if self.max_position_quantity is None:
            # Default for 48 NIFTY stocks
            self.max_position_quantity = {
                'INFY': 5, 'TCS': 10, 'RELIANCE': 3, 'HDFC': 4,
                'SBIN': 8, 'ICICIBANK': 6, 'LT': 2, 'ITC': 15,
                'MARUTI': 2, 'ONGC': 20, 'BAJAJFINSV': 2, 'HINDUSTAN': 2,
                'ASIANPAINT': 3, 'DMARUTI': 1, 'BHARTIARTL': 15,
                'BRITANNIA': 2, 'COALINDIA': 30, 'DIVISLAB': 2,
                'GAIL': 50, 'GRASIM': 4, 'HCLTECH': 5, 'HEROMOTOCO': 2,
                'HINDALCO': 10, 'INFY': 5, 'IOPLUSN': 15, 'JSWSTEEL': 5,
                'KOTAKBANK': 4, 'LT': 2, 'LUPIN': 5, 'M&M': 3,
                'MARUTI': 2, 'NESTLEIND': 1, 'NTPC': 30, 'ONGC': 20,
                'POWERGRID': 20, 'RELIANCE': 3, 'SBIN': 8, 'SHREECEM': 1,
                'SUNPHARMA': 8, 'TATAMOTORS': 10, 'TATAPOWER': 30,
                'TATASTEEL': 5, 'TCS': 10, 'TECHM': 10, 'TITAN': 3,
                'TORNTPHARM': 3, 'UPL': 8, 'WIPRO': 10, 'YESBANK': 15,
            }


class PortfolioRisk(Enum):
    """Portfolio risk levels for position sizing"""
    LOW = 0.05  # < 5% portfolio exposure
    MEDIUM = 0.10  # 5-10%
    HIGH = 0.15  # 10-15%
    VERY_HIGH = 0.25  # 15-25%
    CRITICAL = 1.0  # > 25% (force halt)


# ============================================================================
# POSITION CLASS
# ============================================================================

@dataclass
class Position:
    """Single open position"""
    symbol: str
    quantity: int
    entry_price: float
    entry_time: int  # Timestamp
    stop_loss: float
    profit_target: float
    position_notional: float  # qty × entry_price
    unrealized_pnl: float
    bars_held: int = 0

    def update_pnl(self, current_price: float):
        """Update unrealized P&L"""
        self.unrealized_pnl = (current_price - self.entry_price) * self.quantity


# ============================================================================
# POSITION MANAGER CLASS
# ============================================================================

class PositionManager:
    """
    Manages all positions, sizing, and portfolio risk.
    """

    def __init__(self, config: PositionConfig = None):
        self.config = config or PositionConfig()
        self.logger = logging.getLogger("PositionManager")

        # Current portfolio state
        self.positions: Dict[str, Position] = {}  # symbol -> Position
        self.portfolio_value: float = 0
        self.cash_available: float = 0

    def set_portfolio_value(self, value: float):
        """Update current portfolio value"""
        self.portfolio_value = value
        self.cash_available = self._calculate_available_cash()

    def _calculate_available_cash(self) -> float:
        """Calculate available cash (portfolio value - gross exposure)"""
        gross_exposure = sum(p.position_notional for p in self.positions.values())
        return self.portfolio_value - gross_exposure

    # ========================================================================
    # POSITION SIZING
    # ========================================================================

    def calculate_position_size(self,
                               symbol: str,
                               entry_price: float,
                               stop_loss_price: float,
                               profit_target_price: float) -> Tuple[int, str]:
        """
        Calculate optimal position size based on risk limits.

        Returns:
            (quantity: int, reason: str)
        """

        # Calculate price range
        price_risk = entry_price - stop_loss_price
        price_reward = profit_target_price - entry_price

        if price_risk <= 0:
            return 0, "Stop loss must be below entry price"

        if price_reward <= 0:
            return 0, "Profit target must be above entry price"

        # Risk per share
        risk_per_share = price_risk

        # Max loss allowed
        max_loss_fraction = self.config.max_risk_per_trade_fraction
        max_loss_rupees = self.config.max_loss_per_trade_rupees
        max_loss = min(self.portfolio_value * max_loss_fraction, max_loss_rupees)

        # Size based on risk
        quantity_by_risk = int(max_loss / risk_per_share)

        # Size based on symbol limit
        max_qty_symbol = self.config.max_position_quantity.get(symbol, 10)

        # Size based on exposure limit
        max_exposure_value = self.portfolio_value * self.config.max_exposure_per_symbol
        quantity_by_exposure = int(max_exposure_value / entry_price)

        # Use most restrictive
        final_quantity = min(quantity_by_risk, max_qty_symbol, quantity_by_exposure)
        final_quantity = max(final_quantity, self.config.min_position_quantity)

        # Check if cash available
        notional = final_quantity * entry_price
        if notional > self.cash_available:
            available_qty = int(self.cash_available / entry_price)
            return available_qty, f"Limited by available cash: {available_qty} shares"

        return final_quantity, f"Sized at {final_quantity} shares"

    # ========================================================================
    # PORTFOLIO RISK CALCULATION (LAMBDA)
    # ========================================================================

    def calculate_portfolio_risk(self) -> float:
        """
        Calculate portfolio exposure risk (lambda).

        Lambda = Σ(position_notional) / portfolio_value

        Returns: float between 0.0 and 1.0
        """
        if self.portfolio_value <= 0:
            return 0.0

        total_exposure = sum(p.position_notional for p in self.positions.values())
        lambda_risk = total_exposure / self.portfolio_value
        return min(lambda_risk, 1.0)  # Cap at 100%

    def get_portfolio_risk_level(self) -> Tuple[PortfolioRisk, float]:
        """
        Get current portfolio risk level and lambda value.

        Returns:
            (risk_level: PortfolioRisk, lambda_value: float)
        """
        lambda_value = self.calculate_portfolio_risk()

        if lambda_value < 0.05:
            return PortfolioRisk.LOW, lambda_value
        elif lambda_value < 0.10:
            return PortfolioRisk.MEDIUM, lambda_value
        elif lambda_value < 0.15:
            return PortfolioRisk.HIGH, lambda_value
        elif lambda_value < 0.25:
            return PortfolioRisk.VERY_HIGH, lambda_value
        else:
            return PortfolioRisk.CRITICAL, lambda_value

    # ========================================================================
    # CONCENTRATION LIMITS
    # ========================================================================

    def check_symbol_concentration(self, symbol: str,
                                   new_notional: float) -> Tuple[bool, str]:
        """
        Check if adding new position would exceed symbol concentration limit.

        Returns:
            (allowed: bool, reason: str)
        """
        current_exposure = 0
        if symbol in self.positions:
            current_exposure = self.positions[symbol].position_notional

        total_exposure_after = current_exposure + new_notional
        max_allowed = self.portfolio_value * self.config.max_exposure_per_symbol

        if total_exposure_after > max_allowed:
            return False, (f"Would exceed symbol concentration limit: "
                          f"{total_exposure_after:.0f} > {max_allowed:.0f}")

        return True, "Symbol concentration OK"

    def check_portfolio_concentration(self, new_notional: float) -> Tuple[bool, str]:
        """
        Check if adding new position would exceed portfolio gross exposure limit.

        Returns:
            (allowed: bool, reason: str)
        """
        current_exposure = sum(p.position_notional for p in self.positions.values())
        total_exposure_after = current_exposure + new_notional
        max_allowed = self.portfolio_value * self.config.max_gross_exposure_fraction

        if total_exposure_after > max_allowed:
            return False, (f"Would exceed portfolio exposure limit: "
                          f"{total_exposure_after:.0f} > {max_allowed:.0f}")

        return True, "Portfolio concentration OK"

    def check_position_count_limit(self) -> Tuple[bool, str]:
        """
        Check if adding new position would exceed concurrent positions limit.

        Returns:
            (allowed: bool, reason: str)
        """
        if len(self.positions) >= self.config.max_concurrent_positions:
            return False, (f"Max concurrent positions reached: "
                          f"{len(self.positions)} >= {self.config.max_concurrent_positions}")

        return True, "Position count OK"

    # ========================================================================
    # POSITION TRACKING
    # ========================================================================

    def open_position(self, position: Position) -> bool:
        """Track new open position"""
        if position.symbol in self.positions:
            self.logger.warning(f"Position already exists for {position.symbol}")
            return False

        self.positions[position.symbol] = position
        self.logger.info(f"Opened position: {position.symbol} × {position.quantity}")
        return True

    def close_position(self, symbol: str) -> Optional[Position]:
        """Close a position"""
        if symbol not in self.positions:
            self.logger.warning(f"No position to close for {symbol}")
            return None

        position = self.positions.pop(symbol)
        self.logger.info(f"Closed position: {symbol}, PnL: {position.unrealized_pnl:.0f}")
        return position

    def update_position_price(self, symbol: str, current_price: float):
        """Update position with current market price"""
        if symbol in self.positions:
            self.positions[symbol].update_pnl(current_price)

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position by symbol"""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Get all open positions"""
        return list(self.positions.values())

    # ========================================================================
    # PORTFOLIO METRICS
    # ========================================================================

    def get_portfolio_stats(self) -> Dict:
        """Get current portfolio statistics"""
        positions = self.get_all_positions()
        total_notional = sum(p.position_notional for p in positions)
        total_pnl = sum(p.unrealized_pnl for p in positions)

        return {
            'portfolio_value': self.portfolio_value,
            'cash_available': self.cash_available,
            'gross_exposure': total_notional,
            'exposure_fraction': total_notional / self.portfolio_value if self.portfolio_value > 0 else 0,
            'lambda_risk': self.calculate_portfolio_risk(),
            'total_unrealized_pnl': total_pnl,
            'position_count': len(positions),
            'positions': positions,
        }

    # ========================================================================
    # DERATING (for safety gates)
    # ========================================================================

    def derate_position_size(self, size: int,
                            derate_multiplier: float = 0.80) -> int:
        """
        Reduce position size by applying multiplier.

        Used when gates trigger derating (high lambda, high drawdown).
        """
        derated_size = int(size * derate_multiplier)
        self.logger.info(f"Derated position: {size} × {derate_multiplier:.0%} = {derated_size}")
        return derated_size


# ============================================================================
# MAIN - TEST
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Setup
    config = PositionConfig()
    manager = PositionManager(config)
    manager.set_portfolio_value(1000000)

    print("\n" + "=" * 80)
    print("POSITION MANAGER TEST")
    print("=" * 80)

    # Test position sizing
    qty, reason = manager.calculate_position_size(
        symbol='INFY',
        entry_price=1500,
        stop_loss_price=1450,
        profit_target_price=1550
    )
    print(f"\nPosition size for INFY: {qty} shares ({reason})")

    # Test portfolio risk
    risk_level, lambda_val = manager.get_portfolio_risk_level()
    print(f"Portfolio risk: {risk_level.name} (λ = {lambda_val:.4f})")

    # Test concentration checks
    allowed, reason = manager.check_symbol_concentration('INFY', 1500 * qty)
    print(f"Symbol concentration: {allowed} ({reason})")

    allowed, reason = manager.check_portfolio_concentration(1500 * qty)
    print(f"Portfolio concentration: {allowed} ({reason})")

    # Create and open position
    position = Position(
        symbol='INFY',
        quantity=qty,
        entry_price=1500,
        entry_time=0,
        stop_loss=1450,
        profit_target=1550,
        position_notional=1500 * qty,
        unrealized_pnl=0
    )
    manager.open_position(position)

    # Get stats
    stats = manager.get_portfolio_stats()
    print(f"\nPortfolio stats:")
    print(f"  Value: ₹{stats['portfolio_value']:,.0f}")
    print(f"  Exposure: ₹{stats['gross_exposure']:,.0f} ({stats['exposure_fraction']:.1%})")
    print(f"  Lambda: {stats['lambda_risk']:.4f}")
    print(f"  Positions: {stats['position_count']}")

    print("\n" + "=" * 80 + "\n")

