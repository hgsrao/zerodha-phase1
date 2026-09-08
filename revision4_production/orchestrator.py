"""
REVISION 04: Integrated 10-Box Orchestrator

Orchestrates all 10 boxes into a complete trading system.
Target: ₹1,000/day on ₹1,00,000 investment
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime

from .boxes import (
    DataInputBox,
    PABox,
    ChartStudiesBox,
    EntryValidatorBox,
    RiskManagerBox,
    GridSyncBox,
    PositionManagerBox,
    Position,
    ExitDecisionBox,
    MPCBox,
    PerformanceTrackerBox,
)

logger = logging.getLogger(__name__)

class Revision04Orchestrator:
    """Complete 10-box trading orchestrator."""

    def __init__(self, equity: float = 100_000.0):
        """Initialize all 10 boxes."""
        self.equity = equity
        self.starting_equity = equity

        # Box 1: Data Input
        self.data_box = DataInputBox()

        # Box 2: PA (Predictive Analytics)
        self.pa_box = PABox(lookback=20)

        # Box 3: Chart Studies
        self.chart_box = ChartStudiesBox()

        # Box 4: Entry Validator
        self.entry_validator = EntryValidatorBox(
            min_pa_confidence=0.50,  # Lowered from 0.75 (data-driven)
            min_chart_confidence=0.45,  # Lowered from 0.6
        )

        # Box 5: Risk Manager
        self.risk_manager = RiskManagerBox(
            max_risk_per_trade=500,      # ₹500 risk per trade
            max_position_size=2083,      # ₹2,083 per symbol
        )

        # Box 6: Grid Sync
        self.grid_sync = GridSyncBox(vix_min=10.0, vix_max=30.0)

        # Box 7: Position Manager
        self.position_manager = PositionManagerBox(max_positions=5)

        # Box 8: Exit Decision
        self.exit_decision = ExitDecisionBox()

        # Box 9: MPC
        self.mpc_box = MPCBox()

        # Box 10: Performance Tracker
        self.perf_tracker = PerformanceTrackerBox()

        # State
        self.trades_completed: List[Dict] = []
        self.daily_pnl = 0.0
        self.current_date = None

    def process_bar(
        self,
        symbol: str,
        timestamp,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        closes_history: np.ndarray,
        volumes_history: np.ndarray,
        nifty_trend: float,
        vix: float,
    ) -> Dict:
        """
        Process one bar through all 10 boxes.

        Returns: {trades_entered, trades_exited, pnl_today}
        """

        # Convert timestamp to string if needed
        timestamp_str = str(timestamp)
        bar_date = timestamp_str.split()[0]
        if bar_date != self.current_date:
            self.current_date = bar_date
            self.daily_pnl = 0.0

        result = {
            "entries": [],
            "exits": [],
            "daily_pnl": self.daily_pnl,
        }

        # Parse time
        time_part = timestamp_str.split("T")[1] if "T" in timestamp_str else "10:00"
        hour = int(time_part.split(":")[0])
        minute = int(time_part.split(":")[1])

        # BOX 1: Data Input
        market_data = self.data_box.process(timestamp_str, open_price, high, low, close, volume)
        if not market_data:
            return result

        # Check exits on open positions
        for position in self.position_manager.get_open_positions():
            bars_held = 0  # Placeholder
            should_exit, exit_reason, exit_price = self.exit_decision.check_exit(
                position, close, bars_held, max_hold=60
            )

            if should_exit:
                pnl = (exit_price - position.entry_price) * position.shares
                if position.direction == -1:
                    pnl = (position.entry_price - exit_price) * position.shares

                self.equity += pnl
                self.daily_pnl += pnl
                self.perf_tracker.record_trade(bar_date, pnl)

                result["exits"].append({
                    "symbol": position.symbol,
                    "exit_price": exit_price,
                    "reason": exit_reason,
                    "pnl": pnl,
                })

                self.position_manager.positions.remove(position)

        # Check for new entry
        # BOX 2: PA Box
        pa_confidence = self.pa_box.calculate_confidence(closes_history, volumes_history)

        # BOX 3: Chart Studies
        chart_confidence = self.chart_box.calculate_confidence(closes_history)

        # BOX 4: Entry Validator
        entry_valid, entry_reason = self.entry_validator.validate_entry(
            pa_confidence, chart_confidence, hour, minute
        )

        if entry_valid:
            # BOX 6: Grid Sync
            grid_ok, grid_reason = self.grid_sync.check_synchronization(vix, nifty_trend)

            if grid_ok:
                # BOX 5: Risk Manager
                atr = max(0.001, np.std(closes_history[-20:]) if len(closes_history) >= 20 else 0.001)
                shares = self.risk_manager.calculate_position_size(atr, close)

                if shares > 0:
                    # BOX 9: MPC - Adjust for daily loss
                    mpc_size = self.mpc_box.calculate_optimal_size(
                        self.equity, max_daily_loss=2000, current_daily_pnl=self.daily_pnl
                    )

                    if mpc_size > 0:
                        # Calculate stops/targets
                        stop_price = close - (atr * 1.0)
                        target_price = close + (atr * 2.5)

                        # BOX 7: Position Manager
                        position = Position(
                            symbol=symbol,
                            direction=1,  # BUY
                            entry_price=close,
                            entry_bar=0,
                            stop_price=stop_price,
                            target_price=target_price,
                            shares=shares,
                        )

                        if self.position_manager.add_position(position):
                            result["entries"].append({
                                "symbol": symbol,
                                "entry_price": close,
                                "stop": stop_price,
                                "target": target_price,
                                "pa_confidence": pa_confidence,
                                "chart_confidence": chart_confidence,
                            })

        return result

    def get_summary(self) -> Dict:
        """Get trading summary."""
        stats = self.perf_tracker.get_stats()
        daily_pnl = self.perf_tracker.get_daily_summary()

        return {
            "starting_equity": self.starting_equity,
            "current_equity": self.equity,
            "total_pnl": self.equity - self.starting_equity,
            "daily_breakdown": daily_pnl,
            **stats,
        }
