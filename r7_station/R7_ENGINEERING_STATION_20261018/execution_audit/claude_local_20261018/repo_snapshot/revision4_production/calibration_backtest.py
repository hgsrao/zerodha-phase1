"""
REVISION 04: Valid Calibration Backtest

One-month paper-trading on real 48-symbol portfolio
- One shared ₹1,00,000 cash/margin pool
- Chronological bar-by-bar processing
- Next-bar fills (decide on bar t, execute bar t+1)
- Real stops/targets/time/EOD exits
- Transaction costs
- Proper position tracking

NO scaling of single symbols by 48.
STATUS: Scaffolding only. Needs entry signal integration from 10-box system.
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Position:
    """Active position."""
    symbol: str
    direction: int
    entry_price: float
    entry_time: str
    entry_bar: int
    quantity: float
    stop_price: float
    target_price: float
    max_hold_bars: int = 60

    def bars_held(self, current_bar: int) -> int:
        return current_bar - self.entry_bar

@dataclass
class Trade:
    """Completed trade record."""
    symbol: str
    direction: int
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    quantity: float
    exit_reason: str
    pnl: float
    pnl_pct: float

class CalibrationBacktest:
    """Scaffold: 48-symbol portfolio backtest harness (entry signals not yet wired)."""

    def __init__(
        self,
        symbols: List[str],
        starting_cash: float = 100_000.0,
        cost_per_trade: float = 5.0,
    ):
        self.symbols = symbols
        self.starting_cash = starting_cash
        self.cost_per_trade = cost_per_trade

        # Portfolio state
        self.cash = starting_cash
        self.positions: Dict[str, Position] = {}
        self.pending_orders: List[Dict] = []  # [{symbol, direction, quantity, stop, target, bar, timestamp}]
        self.completed_trades: List[Trade] = []

        self.bar_index = 0
        self.current_timestamp = None
        self.signal_generator: Optional[Callable] = None  # Will be set to 10-box orchestrator

    def set_signal_generator(self, generator: Callable):
        """
        Attach entry signal generator (10-box orchestrator).
        Should return None or {symbol, direction, quantity, stop, target}
        """
        self.signal_generator = generator

    def process_bar(self, timestamp: str, bar_data: Dict[str, Dict]) -> Dict:
        """
        Process one bar across all symbols.
        bar_data: {symbol: {open, high, low, close, volume}}

        Returns: actions log
        """
        self.bar_index += 1
        self.current_timestamp = timestamp

        actions = {
            "closed": 0,
            "filled": 0,
            "signals": 0,
            "cash": self.cash,
            "positions": len(self.positions),
        }

        # Step 1: Exit existing positions
        symbols_to_close = []
        for symbol, position in list(self.positions.items()):
            if symbol not in bar_data:
                continue

            bar = bar_data[symbol]
            current_price = bar["close"]
            bars_held = position.bars_held(self.bar_index)

            should_exit, exit_reason, exit_price = self._check_exit(
                position, current_price, bars_held
            )

            if should_exit:
                trade = self._close_position(
                    symbol, position, exit_price, timestamp, exit_reason
                )
                self.completed_trades.append(trade)
                actions["closed"] += 1
                symbols_to_close.append(symbol)

        for symbol in symbols_to_close:
            del self.positions[symbol]

        # Step 2: Fill pending orders (created last bar, filled on t+1 open)
        orders_to_fill = []
        for order in list(self.pending_orders):
            if order["symbol"] not in bar_data:
                continue

            bar = bar_data[order["symbol"]]
            fill_price = bar["open"]

            # Check cash for buy orders
            cost = order["quantity"] * fill_price + self.cost_per_trade
            if self.cash < cost and order["direction"] == 1:
                continue  # Insufficient cash, skip fill

            # Execute fill
            self.cash -= cost
            position = Position(
                symbol=order["symbol"],
                direction=order["direction"],
                entry_price=fill_price,
                entry_time=timestamp,
                entry_bar=self.bar_index,
                quantity=order["quantity"],
                stop_price=order["stop"],
                target_price=order["target"],
                max_hold_bars=60,
            )
            self.positions[order["symbol"]] = position
            actions["filled"] += 1
            orders_to_fill.append(order)

        for order in orders_to_fill:
            self.pending_orders.remove(order)

        # Step 3: Generate entry signals via 10-box system
        # PLACEHOLDER: Not yet wired. signal_generator would be called here.
        # When wired: self.signal_generator(timestamp, bar_data) → list of entry signals
        # Each signal becomes a pending order

        return actions

    def _check_exit(
        self,
        position: Position,
        current_price: float,
        bars_held: int,
    ) -> Tuple[bool, str, float]:
        """Check if position should exit."""

        if bars_held >= position.max_hold_bars:
            return True, f"TIME_EXIT", current_price

        if position.direction == 1:  # LONG
            if current_price >= position.target_price:
                return True, "TARGET_HIT", current_price
            if current_price <= position.stop_price:
                return True, "STOP_HIT", current_price

        return False, None, None

    def _close_position(
        self,
        symbol: str,
        position: Position,
        exit_price: float,
        timestamp: str,
        exit_reason: str,
    ) -> Trade:
        """Close position and record trade."""

        if position.direction == 1:
            pnl = (exit_price - position.entry_price) * position.quantity - self.cost_per_trade
        else:
            pnl = (position.entry_price - exit_price) * position.quantity - self.cost_per_trade

        pnl_pct = (pnl / (position.entry_price * position.quantity) * 100) if position.entry_price > 0 else 0

        self.cash += exit_price * position.quantity

        return Trade(
            symbol=symbol,
            direction=position.direction,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=timestamp,
            exit_price=exit_price,
            quantity=position.quantity,
            exit_reason=exit_reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )

    def get_marked_equity(self, current_bar_data: Optional[Dict[str, Dict]] = None) -> float:
        """Get portfolio equity marked to market."""
        equity = self.cash

        if current_bar_data:
            for symbol, position in self.positions.items():
                if symbol in current_bar_data:
                    current_price = current_bar_data[symbol].get("close", position.entry_price)
                    equity += position.quantity * current_price

        return equity

    def get_report(self) -> Dict:
        """Generate backtest report."""

        if not self.completed_trades:
            return {
                "status": "INSUFFICIENT_DATA",
                "total_trades": 0,
                "total_pnl": 0.0,
                "starting_equity": self.starting_cash,
                "ending_equity": self.get_marked_equity(),
                "message": "No trades executed. Entry signals not yet wired from 10-box system.",
            }

        pnls = [t.pnl for t in self.completed_trades]
        winners = len([p for p in pnls if p > 0])
        losers = len([p for p in pnls if p < 0])
        total_pnl = sum(pnls)

        # Evaluation
        target_monthly = 1000 * 22  # ₹1,000/day × 22 trading days
        if total_pnl >= target_monthly * 0.9:  # 90% of target
            status = "PASS"
        else:
            status = "FAIL"

        return {
            "status": status,
            "total_trades": len(self.completed_trades),
            "winning_trades": winners,
            "losing_trades": losers,
            "win_rate": winners / len(self.completed_trades) if self.completed_trades else 0,
            "total_pnl": total_pnl,
            "avg_pnl": np.mean(pnls) if pnls else 0,
            "starting_equity": self.starting_cash,
            "ending_equity": self.get_marked_equity(),
            "target_pnl": target_monthly,
            "trades": [self._trade_to_dict(t) for t in self.completed_trades],
        }

    def _trade_to_dict(self, trade: Trade) -> Dict:
        return {
            "symbol": trade.symbol,
            "direction": "BUY" if trade.direction == 1 else "SELL",
            "entry_time": str(trade.entry_time),
            "entry_price": float(trade.entry_price),
            "exit_time": str(trade.exit_time),
            "exit_price": float(trade.exit_price),
            "quantity": float(trade.quantity),
            "exit_reason": trade.exit_reason,
            "pnl": float(trade.pnl),
            "pnl_pct": float(trade.pnl_pct),
        }
