"""
REVISION 04: Backtesting Engine

Runs causal backtest (not post-hoc analysis):
1. Iterate through bars chronologically
2. At each bar: Check if entry signal meets criteria
3. If yes: ENTER position
4. Check exit conditions
5. If met: EXIT position
6. Track P&L

All decisions made with data available at that bar.
No peeking ahead.
"""

import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from .strategy import Revision04Strategy, TradeSignal

logger = logging.getLogger(__name__)

class Trade:
    """Individual trade record."""

    def __init__(
        self,
        symbol: str,
        entry_bar: int,
        entry_price: float,
        entry_time: str,
        direction: int,
        target_price: float,
        stop_price: float,
    ):
        self.symbol = symbol
        self.entry_bar = entry_bar
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.direction = direction
        self.target_price = target_price
        self.stop_price = stop_price

        self.exit_bar: Optional[int] = None
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[str] = None
        self.exit_reason: Optional[str] = None
        self.pnl: float = 0.0
        self.pnl_pct: float = 0.0

    def close(self, bar: int, price: float, time: str, reason: str):
        """Close the trade."""
        self.exit_bar = bar
        self.exit_price = price
        self.exit_time = time
        self.exit_reason = reason

        if self.direction == 1:  # BUY
            self.pnl = price - self.entry_price
            self.pnl_pct = (price - self.entry_price) / self.entry_price * 100
        else:  # SELL
            self.pnl = self.entry_price - price
            self.pnl_pct = (self.entry_price - price) / self.entry_price * 100

    def is_open(self) -> bool:
        return self.exit_bar is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": "BUY" if self.direction == 1 else "SELL",
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "bars_held": self.exit_bar - self.entry_bar if self.exit_bar else 0,
        }

class Revision04Backtest:
    """Backtesting engine for Revision 04 strategy."""

    def __init__(self, strategy: Revision04Strategy, starting_equity: float = 100_000.0):
        self.strategy = strategy
        self.starting_equity = starting_equity
        self.equity = starting_equity
        self.trades: List[Trade] = []
        self.open_positions: Dict[str, Trade] = {}

    def run(self, symbol: str, bars: pd.DataFrame) -> Dict[str, Any]:
        """
        Run backtest on a symbol.

        Args:
            symbol: Stock symbol
            bars: DataFrame with columns: timestamp, open, high, low, close, volume

        Returns:
            Report with trades, P&L, metrics
        """

        logger.info(f"\nBacktesting {symbol}...")

        self.trades = []
        self.open_positions = {}

        # Prepare data
        bars = bars.reset_index(drop=True)
        timestamps = bars["timestamp"].values
        closes = bars["close"].values
        highs = bars["high"].values
        lows = bars["low"].values

        # Iterate through bars
        for bar_idx in range(60, len(bars)):  # Skip warmup
            ts = str(timestamps[bar_idx])
            # Parse timestamp: format is ISO (2023-07-03T09:15:00)
            try:
                if 'T' in ts:
                    time_part = ts.split('T')[1]
                    hour = int(time_part.split(':')[0])
                    minute = int(time_part.split(':')[1])
                else:
                    parts = ts.split(':')
                    hour = int(parts[0]) if len(parts) > 0 else 10
                    minute = int(parts[1]) if len(parts) > 1 else 0
            except:
                hour = 10
                minute = 0

            # Get data up to this bar (no lookahead)
            close_data = closes[: bar_idx + 1]
            high_data = highs[: bar_idx + 1]
            low_data = lows[: bar_idx + 1]
            current_price = closes[bar_idx]

            # Check open positions for exit
            positions_to_close = []
            for position_id, position in self.open_positions.items():
                minutes_held = (bar_idx - position.entry_bar)
                should_exit, reason, exit_price = self.strategy.check_exit(
                    current_price=current_price,
                    entry_price=position.entry_price,
                    target_price=position.target_price,
                    stop_price=position.stop_price,
                    minutes_held=minutes_held,
                    direction=position.direction,
                )

                if should_exit:
                    position.close(bar_idx, exit_price, ts, reason)
                    self.equity += position.pnl
                    positions_to_close.append(position_id)

            # Remove closed positions
            for position_id in positions_to_close:
                del self.open_positions[position_id]

            # EOD: Close remaining positions
            if self.strategy.on_bar_end(hour, minute):
                for position_id, position in list(self.open_positions.items()):
                    position.close(bar_idx, current_price, ts, "EOD_CLOSE")
                    self.equity += position.pnl
                    del self.open_positions[position_id]

            # Check for new entry
            # Simplified: PA confidence from baseline (80% of bars, then random)
            pa_confidence = 0.8 if bar_idx % 5 != 0 else np.random.uniform(0.5, 0.95)

            signal = self.strategy.generate_entry_signal(
                symbol=symbol,
                close_prices=close_data,
                high_prices=high_data,
                low_prices=low_data,
                pa_confidence=pa_confidence,
                signal_direction=1,  # Always BUY for now
                current_hour=hour,
                current_minute=minute,
            )

            if signal and len(self.open_positions) < 5:  # Max 5 concurrent
                position = Trade(
                    symbol=symbol,
                    entry_bar=bar_idx,
                    entry_price=current_price,
                    entry_time=ts,
                    direction=signal.direction,
                    target_price=signal.target_price,
                    stop_price=signal.stop_price,
                )
                self.open_positions[f"{symbol}_{bar_idx}"] = position

        # Close any remaining positions
        for position in self.open_positions.values():
            position.close(len(bars) - 1, closes[-1], str(timestamps[-1]), "BACKTEST_END")
            self.equity += position.pnl
            self.trades.append(position)

        # Add already closed trades
        self.trades.extend([p for p in self.open_positions.values() if not p.is_open()])

        # Generate report
        return self._generate_report(symbol)

    def _generate_report(self, symbol: str) -> Dict[str, Any]:
        """Generate backtest report."""

        if not self.trades:
            return {
                "symbol": symbol,
                "trades": [],
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "equity": self.equity,
            }

        pnls = [t.pnl for t in self.trades]
        winning = len([p for p in pnls if p > 0])
        losing = len([p for p in pnls if p < 0])

        return {
            "symbol": symbol,
            "trades": [t.to_dict() for t in self.trades],
            "total_trades": len(self.trades),
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": winning / len(self.trades) if self.trades else 0.0,
            "total_pnl": sum(pnls),
            "avg_pnl": np.mean(pnls) if pnls else 0.0,
            "best_trade": max(pnls),
            "worst_trade": min(pnls),
            "equity": self.equity,
        }
