#!/usr/bin/env python3
"""
================================================================================
BACKTEST ENGINE - PHASE 2 PAPER TRADING
================================================================================

Complete backtest engine with:
- Bar-by-bar simulation (causal execution)
- Entry/exit logic with all 18 safety gates
- Position management and tracking
- P&L and risk calculations
- Performance metrics

================================================================================
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import json

from gates_framework import (
    EntryDecisionEngine, SafetyGateConfig, GateLogger,
    SystemState, EntrySignal
)
from position_manager import PositionManager, PositionConfig, Position
from data_loader import DataLoader


@dataclass
class Trade:
    """Single trade record"""
    symbol: str
    entry_time: datetime
    entry_price: float
    entry_qty: int
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    stop_loss: float = 0.0
    profit_target: float = 0.0
    pnl: float = 0.0
    pnl_percent: float = 0.0
    bars_held: int = 0
    exit_reason: str = "open"


@dataclass
class PortfolioMetrics:
    """Portfolio performance metrics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    total_pnl: float = 0.0
    max_pnl: float = 0.0
    min_pnl: float = 0.0

    max_drawdown: float = 0.0
    current_drawdown: float = 0.0

    gross_exposure: float = 0.0
    portfolio_lambda: float = 0.0

    final_capital: float = 0.0
    total_return: float = 0.0
    return_percent: float = 0.0

    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0


class BacktestEngine:
    """
    Complete backtesting engine for Phase 2 validation.
    """

    def __init__(self, initial_capital: float = 1000000.0):
        """
        Initialize backtest engine.

        Args:
            initial_capital: Starting portfolio value (₹)
        """

        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital

        # Components
        self.safety_config = SafetyGateConfig()
        self.gate_logger = GateLogger()
        self.entry_engine = EntryDecisionEngine(self.safety_config, self.gate_logger)
        self.position_manager = PositionManager(PositionConfig())

        # Tracking
        self.trades: List[Trade] = []
        self.open_trades: Dict[str, Trade] = {}
        self.portfolio_history: List[dict] = []

        # Metrics
        self.metrics = PortfolioMetrics()

        # Gates tracking
        self.gate_triggers = {
            'gate01_kill_switch': 0,
            'gate02_dd_halt': 0,
            'gate03_daily_loss_halt': 0,
            'gate11_lambda_derate': 0,
            'gate17_market_close': 0,
            'gate18_circuit_breaker': 0,
        }

    def run_backtest(self, data: Dict[str, pd.DataFrame]) -> PortfolioMetrics:
        """
        Run complete bar-by-bar backtest.

        Args:
            data: Symbol -> DataFrame with OHLCV data

        Returns:
            PortfolioMetrics with all performance statistics
        """

        symbols = list(data.keys())
        print(f"\n{'='*80}")
        print(f"STARTING BACKTEST: {len(symbols)} symbols")
        print(f"Initial Capital: ₹{self.initial_capital:,.0f}")
        print(f"{'='*80}\n")

        # Find common date range
        min_len = min(len(df) for df in data.values())

        # Bar-by-bar simulation
        for bar_idx in range(1, min_len):  # Start from bar 1 (need previous for comparison)

            # Update portfolio value
            self._update_portfolio_value(data, bar_idx)

            # Check for exits
            self._check_exits(data, bar_idx)

            # Check for entries (if capital available)
            if len(self.open_trades) < 5:  # Max 5 concurrent positions
                self._check_entries(data, bar_idx)

            # Log portfolio state every 1000 bars
            if bar_idx % 1000 == 0:
                print(f"Bar {bar_idx}: Capital ₹{self.current_capital:,.0f}, "
                      f"Positions: {len(self.open_trades)}, Trades: {len(self.trades)}")

        # Finalize metrics
        self._calculate_metrics()

        print(f"\n{'='*80}")
        print(f"BACKTEST COMPLETE")
        print(f"{'='*80}\n")

        return self.metrics

    def _update_portfolio_value(self, data: Dict[str, pd.DataFrame], bar_idx: int):
        """
        Update portfolio value based on current positions.

        Args:
            data: Historical data
            bar_idx: Current bar index
        """

        total_position_value = 0

        for symbol, trade in self.open_trades.items():
            bar = self._get_bar(data[symbol], bar_idx)
            if bar:
                # Update position P&L
                current_price = bar['close']
                pnl = (current_price - trade.entry_price) * trade.entry_qty
                trade.pnl = pnl
                trade.pnl_percent = (pnl / (trade.entry_price * trade.entry_qty)) * 100
                trade.bars_held += 1

                total_position_value += (current_price * trade.entry_qty)

        # Update capital
        self.current_capital = self.initial_capital + sum(t.pnl for t in self.open_trades.values())

        # Track peak for drawdown
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

    def _check_exits(self, data: Dict[str, pd.DataFrame], bar_idx: int):
        """
        Check for position exits (stop loss, profit target, or time-based).

        Args:
            data: Historical data
            bar_idx: Current bar index
        """

        symbols_to_exit = []

        for symbol, trade in self.open_trades.items():
            bar = self._get_bar(data[symbol], bar_idx)
            if not bar:
                continue

            current_price = bar['close']
            exit_signal = False
            exit_reason = ""

            # Check stop loss
            if current_price <= trade.stop_loss:
                exit_signal = True
                exit_reason = "stop_loss"

            # Check profit target
            elif current_price >= trade.profit_target:
                exit_signal = True
                exit_reason = "profit_target"

            # Check max hold time (100 bars = 1 trading day)
            elif trade.bars_held > 100:
                exit_signal = True
                exit_reason = "max_hold"

            if exit_signal:
                # Close trade
                trade.exit_time = bar['timestamp']
                trade.exit_price = current_price
                trade.exit_reason = exit_reason

                self.trades.append(trade)
                symbols_to_exit.append(symbol)

        # Remove exited positions
        for symbol in symbols_to_exit:
            del self.open_trades[symbol]

    def _check_entries(self, data: Dict[str, pd.DataFrame], bar_idx: int):
        """
        Check for entry opportunities on all symbols.

        Args:
            data: Historical data
            bar_idx: Current bar index
        """

        for symbol, df in data.items():
            if symbol in self.open_trades:
                continue  # Already in position

            bar = self._get_bar(df, bar_idx)
            prev_bar = self._get_bar(df, bar_idx - 1)

            if not bar or not prev_bar:
                continue

            # Simple entry signal: close > open (momentum)
            if bar['close'] > prev_bar['close']:

                # Create entry signal
                entry_price = bar['close']
                stop_loss = entry_price * 0.98  # 2% stop
                profit_target = entry_price * 1.03  # 3% profit target
                suggested_qty = 100

                signal = EntrySignal(
                    symbol=symbol,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss,
                    profit_target_price=profit_target,
                    confidence=0.6,  # Moderate confidence
                    suggested_quantity=suggested_qty,
                    position_notional=entry_price * suggested_qty,
                    risk_reward_ratio=((profit_target - entry_price) / (entry_price - stop_loss)) if (entry_price - stop_loss) > 0 else 1.0
                )

                # Create system state
                state = SystemState(
                    portfolio_value=self.current_capital,
                    current_dd_percent=self._calculate_drawdown() * 100,  # Convert to percentage
                    current_lambda=self.position_manager.calculate_portfolio_risk(),
                    daily_realized_loss=0,
                    daily_unrealized_loss=sum(t.pnl for t in self.open_trades.values() if t.pnl < 0),
                    open_positions_count=len(self.open_trades),
                    open_positions=[{'symbol': s, 'notional': t.entry_price * t.entry_qty} for s, t in self.open_trades.items()],
                    market_data_age_seconds=0,
                    broker_connected=True,
                    broker_offline_seconds=0,
                    kill_switch_active=False,
                    circuit_breaker_triggered=False,
                    last_broker_check_time=bar['timestamp'],
                    order_history=[]
                )

                # Check gates
                can_enter, final_size, reason = self.entry_engine.can_enter(signal, state, bar['timestamp'])

                if can_enter and self.current_capital > (entry_price * final_size):
                    # Open position
                    trade = Trade(
                        symbol=symbol,
                        entry_time=bar['timestamp'],
                        entry_price=entry_price,
                        entry_qty=final_size,
                        stop_loss=stop_loss,
                        profit_target=profit_target
                    )

                    self.open_trades[symbol] = trade

    def _calculate_metrics(self):
        """Calculate final portfolio metrics."""

        # Close out remaining open positions at last price
        for trade in self.open_trades.values():
            self.trades.append(trade)

        # Trade metrics
        self.metrics.total_trades = len(self.trades)
        self.metrics.winning_trades = sum(1 for t in self.trades if t.pnl > 0)
        self.metrics.losing_trades = sum(1 for t in self.trades if t.pnl <= 0)

        if self.metrics.total_trades > 0:
            self.metrics.win_rate = (self.metrics.winning_trades / self.metrics.total_trades) * 100

        # P&L metrics
        self.metrics.total_pnl = sum(t.pnl for t in self.trades)
        self.metrics.max_pnl = max((t.pnl for t in self.trades), default=0)
        self.metrics.min_pnl = min((t.pnl for t in self.trades), default=0)

        # Drawdown
        self.metrics.max_drawdown = ((self.peak_capital - self.current_capital) / self.peak_capital) * 100

        # Return
        self.metrics.final_capital = self.current_capital
        self.metrics.total_return = self.current_capital - self.initial_capital
        self.metrics.return_percent = (self.metrics.total_return / self.initial_capital) * 100

        # Profit factor
        wins = sum(t.pnl for t in self.trades if t.pnl > 0)
        losses = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if losses > 0:
            self.metrics.profit_factor = wins / losses
        else:
            self.metrics.profit_factor = float('inf') if wins > 0 else 0

    def _get_bar(self, df: pd.DataFrame, idx: int) -> Optional[dict]:
        """Get a bar from dataframe."""
        if idx >= len(df):
            return None
        row = df.iloc[idx]
        return {
            'timestamp': row['timestamp'],
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': int(row['volume'])
        }

    def _calculate_drawdown(self) -> float:
        """Calculate current drawdown as fraction."""
        if self.peak_capital <= 0:
            return 0.0
        return (self.peak_capital - self.current_capital) / self.peak_capital

    def _calculate_exposure(self) -> float:
        """Calculate gross exposure as fraction of portfolio."""
        total_exposure = sum(t.entry_price * t.entry_qty for t in self.open_trades.values())
        return total_exposure / self.current_capital if self.current_capital > 0 else 0.0

    def print_summary(self):
        """Print backtest results summary."""

        print("\n" + "="*80)
        print("BACKTEST RESULTS SUMMARY")
        print("="*80)

        print(f"\nCapital Performance:")
        print(f"  Initial:  ₹{self.initial_capital:,.0f}")
        print(f"  Final:    ₹{self.metrics.final_capital:,.0f}")
        print(f"  Return:   ₹{self.metrics.total_return:,.0f} ({self.metrics.return_percent:.2f}%)")

        print(f"\nTrade Statistics:")
        print(f"  Total Trades:    {self.metrics.total_trades}")
        print(f"  Winning Trades:  {self.metrics.winning_trades}")
        print(f"  Losing Trades:   {self.metrics.losing_trades}")
        print(f"  Win Rate:        {self.metrics.win_rate:.1f}%")

        print(f"\nP&L Metrics:")
        print(f"  Total P&L:       ₹{self.metrics.total_pnl:,.0f}")
        print(f"  Max Trade P&L:   ₹{self.metrics.max_pnl:,.0f}")
        print(f"  Min Trade P&L:   ₹{self.metrics.min_pnl:,.0f}")
        print(f"  Profit Factor:   {self.metrics.profit_factor:.2f}")

        print(f"\nRisk Metrics:")
        print(f"  Max Drawdown:    {self.metrics.max_drawdown:.2f}%")

        print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    # Test the backtest engine
    loader = DataLoader()
    data = loader.load_all_symbols()

    engine = BacktestEngine(initial_capital=1000000)
    metrics = engine.run_backtest(data)
    engine.print_summary()
