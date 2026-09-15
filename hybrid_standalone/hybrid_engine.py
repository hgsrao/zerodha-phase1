#!/usr/bin/env python3
"""
Standalone Hybrid Trading Engine
=================================
Independent implementation of hybrid trend+mean reversion strategy.
No dependencies on R2 orchestrator - validates concept in isolation.

Components:
1. HybridPABox - Signal generation
2. SignalFilter - Simple approval logic
3. TradeBuilder - Entry/exit price calculation
4. PositionSizer - Position sizing
5. ExecutionEngine - Trade execution
6. ResultsTracker - P&L tracking and reporting
"""

import sys
sys.path.insert(0, '/home/shrinivas/ECS_Project')

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path

from revision2.boxes_hybrid_strategy_v2 import HybridPredictiveAnalyticsBoxV2
from revision2.contracts import MarketSnapshot

# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Signal:
    """PA signal with regime information"""
    timestamp: pd.Timestamp
    symbol: str
    direction: int  # 1=LONG, -1=SHORT, 0=NONE
    confidence: float
    volatility: float
    quality_band: str
    price: float


@dataclass
class Trade:
    """Executed trade with P&L"""
    trade_id: int
    timestamp_entry: pd.Timestamp
    timestamp_exit: Optional[pd.Timestamp]
    symbol: str
    direction: int
    entry_price: float
    exit_price: Optional[float]
    quantity: int
    stop_loss: float
    profit_target: float
    status: str  # 'open', 'closed_win', 'closed_loss', 'closed_sl', 'closed_tp'
    pnl: float = 0.0
    pnl_bps: float = 0.0
    bars_held: int = 0

    def close(self, exit_price: float, exit_timestamp: pd.Timestamp, status: str):
        """Close the trade"""
        self.exit_price = exit_price
        self.timestamp_exit = exit_timestamp
        self.status = status

        gross_pnl = (exit_price - self.entry_price) * self.quantity * self.direction
        costs = 100 + abs(gross_pnl) * 0.001  # Brokerage + taxes
        self.pnl = gross_pnl - costs
        self.pnl_bps = (self.pnl / (self.entry_price * self.quantity)) * 10000


@dataclass
class EngineConfig:
    """Engine configuration"""
    starting_equity: float = 100000.0
    max_risk_per_trade_pct: float = 0.02  # 2% of equity per trade
    max_position_quantity: int = 2  # Max shares per trade
    min_holding_bars: int = 1
    max_holding_bars: int = 30  # Reduced from 60 - let stops/targets work
    confidence_threshold: float = 0.20  # Min confidence to trade
    green_confidence_threshold: float = 0.75  # High-confidence requirement
    use_stops: bool = True
    use_targets: bool = True
    trailing_stop_percent: float = 0.005  # 0.5% trailing stop for winners


# ============================================================================
# SIGNAL FILTER - Simple approval
# ============================================================================

class SignalFilter:
    """Filter signals based on confidence and quality"""

    def __init__(self, config: EngineConfig):
        self.config = config

    def approve(self, signal: Signal) -> Tuple[bool, str]:
        """Approve signal for trading"""

        # Reject if no direction
        if signal.direction == 0:
            return False, "no_direction"

        # Reject if confidence too low
        if signal.confidence < self.config.confidence_threshold:
            return False, f"low_confidence_{signal.confidence:.2f}"

        # Red quality signals rejected
        if signal.quality_band == "red":
            return False, "red_quality"

        return True, f"approved_{signal.quality_band}_{signal.confidence:.2f}"


# ============================================================================
# TRADE BUILDER - Entry/exit price calculation
# ============================================================================

class TradeBuilder:
    """Build trade plan with stops and targets"""

    def __init__(self, config: EngineConfig):
        self.config = config

    def build_trade(self, signal: Signal, atr: float) -> Trade:
        """Build a trade from signal with improved stop/target logic"""

        # Position sizing based on confidence
        quantity = self._calculate_quantity(signal)

        # Stop loss and profit target based on signal quality and confidence
        if signal.quality_band == "green":
            # High confidence: Wider stops, more aggressive targets
            stop_distance = atr * 2.0  # 2x ATR
            target_distance = atr * 3.5  # 3.5x ATR (1.75:1 RR)
        elif signal.quality_band == "amber":
            # Medium confidence: Moderate stops
            stop_distance = atr * 1.5  # 1.5x ATR
            target_distance = atr * 2.5  # 2.5x ATR
        else:
            # Low confidence: Tight stops
            stop_distance = atr * 1.0  # 1x ATR
            target_distance = atr * 1.5  # 1.5x ATR

        # Ensure minimum distances
        stop_distance = max(stop_distance, signal.price * 0.003)  # At least 0.3%
        target_distance = max(target_distance, signal.price * 0.005)  # At least 0.5%

        if signal.direction == 1:  # LONG
            stop_loss = signal.price - stop_distance
            profit_target = signal.price + target_distance
        else:  # SHORT
            stop_loss = signal.price + stop_distance
            profit_target = signal.price - target_distance

        trade = Trade(
            trade_id=0,  # Set by engine
            timestamp_entry=signal.timestamp,
            timestamp_exit=None,
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.price,
            exit_price=None,
            quantity=quantity,
            stop_loss=stop_loss,
            profit_target=profit_target,
            status="open",
        )

        return trade

    def _calculate_quantity(self, signal: Signal) -> int:
        """Calculate position quantity based on confidence"""
        # Higher confidence = larger position
        if signal.quality_band == "green":
            qty = 2
        elif signal.quality_band == "amber":
            qty = 1
        else:
            qty = 1

        return min(qty, self.config.max_position_quantity)


# ============================================================================
# EXECUTION ENGINE - Trade management
# ============================================================================

class ExecutionEngine:
    """Execute and manage trades"""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.equity = config.starting_equity
        self.open_positions: Dict[str, Trade] = {}  # symbol -> Trade
        self.closed_trades: List[Trade] = []
        self.trade_id_counter = 1

    def get_portfolio_stats(self) -> Dict:
        """Get current portfolio statistics"""
        open_pnl = sum(t.pnl for t in self.open_positions.values() if t.pnl != 0)
        closed_pnl = sum(t.pnl for t in self.closed_trades)

        return {
            'equity': self.equity,
            'open_trades': len(self.open_positions),
            'closed_trades': len(self.closed_trades),
            'open_pnl': open_pnl,
            'closed_pnl': closed_pnl,
            'total_pnl': open_pnl + closed_pnl,
        }

    def enter_trade(self, trade: Trade) -> bool:
        """Enter a new trade"""
        symbol = trade.symbol

        # Already have open position in this symbol
        if symbol in self.open_positions:
            return False

        trade.trade_id = self.trade_id_counter
        self.trade_id_counter += 1
        self.open_positions[symbol] = trade
        return True

    def update_open_trades(self, bar: pd.Series, atr: float):
        """Update open positions with current bar"""
        symbols_to_close = []

        for symbol, trade in self.open_positions.items():
            price = bar['close']

            # Update bars held
            trade.bars_held += 1

            # Check stop loss
            if trade.direction == 1:  # LONG
                if price <= trade.stop_loss:
                    symbols_to_close.append((symbol, price, 'closed_sl'))
                elif price >= trade.profit_target:
                    symbols_to_close.append((symbol, price, 'closed_tp'))
            else:  # SHORT
                if price >= trade.stop_loss:
                    symbols_to_close.append((symbol, price, 'closed_sl'))
                elif price <= trade.profit_target:
                    symbols_to_close.append((symbol, price, 'closed_tp'))

            # Check max holding bars
            if trade.bars_held >= self.config.max_holding_bars:
                symbols_to_close.append((symbol, price, 'closed_max_hold'))

        # Close trades
        for symbol, price, status in symbols_to_close:
            self.close_trade(symbol, price, bar['timestamp'], status)

    def close_trade(self, symbol: str, price: float, timestamp: pd.Timestamp, status: str):
        """Close a trade"""
        if symbol not in self.open_positions:
            return

        trade = self.open_positions.pop(symbol)
        trade.close(price, timestamp, status)
        self.closed_trades.append(trade)
        self.equity += trade.pnl


# ============================================================================
# MAIN HYBRID ENGINE
# ============================================================================

class HybridStandaloneEngine:
    """Main standalone hybrid trading engine"""

    def __init__(self, symbol: str, config: EngineConfig = None):
        self.symbol = symbol
        self.config = config or EngineConfig()

        self.pa_box = HybridPredictiveAnalyticsBoxV2()
        self.signal_filter = SignalFilter(self.config)
        self.trade_builder = TradeBuilder(self.config)
        self.execution_engine = ExecutionEngine(self.config)

        self.signals_generated: List[Signal] = []
        self.signals_approved: List[Signal] = []

    def run(self, data: pd.DataFrame, warmup_bars: int = 60) -> Dict:
        """Run backtest on data"""

        print(f"\n{'='*80}")
        print(f"HYBRID STANDALONE ENGINE - {self.symbol}")
        print(f"{'='*80}")
        print(f"Data: {len(data)} bars ({data['timestamp'].min()} to {data['timestamp'].max()})")
        print(f"Warmup: {warmup_bars} bars")

        # Calibrate PA box
        warmup_data = data.iloc[:warmup_bars]
        self.pa_box.calibrate(self.symbol, warmup_data)
        print(f"✓ PA box calibrated")

        # Process bars
        for idx in range(warmup_bars, len(data)):
            bar = data.iloc[idx]

            # Get signal
            hist_bars = data.iloc[:idx+1].reset_index(drop=True)
            snapshot = MarketSnapshot(
                symbol=self.symbol,
                timestamp=str(bar['timestamp']),
                bars=hist_bars
            )

            pa_signal, _ = self.pa_box.evaluate(snapshot, {})

            # Calculate ATR for this bar
            atr_window = hist_bars['high'].tail(20) - hist_bars['low'].tail(20)
            atr = float(atr_window.mean()) if len(atr_window) > 0 else bar['close'] * 0.01

            # Convert to Signal
            signal = Signal(
                timestamp=bar['timestamp'],
                symbol=self.symbol,
                direction=pa_signal.direction,
                confidence=pa_signal.confidence,
                volatility=pa_signal.volatility,
                quality_band=pa_signal.quality_band,
                price=bar['close'],
            )

            self.signals_generated.append(signal)

            # Filter signal
            approved, reason = self.signal_filter.approve(signal)

            if approved:
                self.signals_approved.append(signal)

                # Build and enter trade
                trade = self.trade_builder.build_trade(signal, atr)
                self.execution_engine.enter_trade(trade)

            # Update open positions
            self.execution_engine.update_open_trades(bar, atr)

        # Close any remaining open trades at last price
        last_price = data.iloc[-1]['close']
        last_timestamp = data.iloc[-1]['timestamp']
        for symbol in list(self.execution_engine.open_positions.keys()):
            self.execution_engine.close_trade(symbol, last_price, last_timestamp, 'closed_end_of_period')

        return self._generate_report()

    def _generate_report(self) -> Dict:
        """Generate backtest report"""

        trades = self.execution_engine.closed_trades
        stats = self.execution_engine.get_portfolio_stats()

        # Calculate metrics
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.pnl > 0)
        losing_trades = sum(1 for t in trades if t.pnl < 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        total_pnl = sum(t.pnl for t in trades)
        gross_pnl = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))

        avg_win = (gross_pnl / winning_trades) if winning_trades > 0 else 0
        avg_loss = (gross_loss / losing_trades) if losing_trades > 0 else 0
        profit_factor = (gross_pnl / gross_loss) if gross_loss > 0 else float('inf')

        # Max drawdown (simplified)
        equity_curve = [self.config.starting_equity]
        for trade in trades:
            equity_curve.append(equity_curve[-1] + trade.pnl)

        peak_equity = max(equity_curve)
        max_dd = 0
        for eq in equity_curve:
            if eq < peak_equity:
                dd = (peak_equity - eq) / peak_equity * 100
                max_dd = max(max_dd, dd)

        report = {
            'symbol': self.symbol,
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate_pct': win_rate,
            'total_pnl': total_pnl,
            'gross_pnl': gross_pnl,
            'gross_loss': gross_loss,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown_pct': max_dd,
            'signals_generated': len(self.signals_generated),
            'signals_approved': len(self.signals_approved),
            'approval_rate_pct': (len(self.signals_approved) / len(self.signals_generated) * 100)
                                if len(self.signals_generated) > 0 else 0,
            'trades': trades,
        }

        return report
