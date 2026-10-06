#!/usr/bin/env python3
"""
Enhanced ContinuousExitController with Grid Synchronization as 6th Input.

Adds MacroGridSynchronizer (Voltage/Frequency/Phase) to the exit controller.
Grid state modulates the exit tightness and can gate entries entirely.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple
import numpy as np

from simple_pid import PID

# Import grid synchronizer from Revision 3
try:
    from revision3.macro_grid_synchronizer import MacroGridSynchronizer, SyncResult
    GRID_SYNC_AVAILABLE = True
except ImportError:
    GRID_SYNC_AVAILABLE = False
    print("⚠️  MacroGridSynchronizer not available - grid sync disabled")


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class ExitControllerState:
    """One open position's continuously-updated exit state."""

    side: str
    entry_price: float
    original_target_distance: float
    current_stop_price: float
    current_target_price: float
    favorable_extreme: float

    max_hold_bars: int
    bars_held: int = 0
    consecutive_bars_at_low_confidence_extreme: int = 0
    consecutive_bars_at_low_studies_extreme: int = 0
    adjustment_history: list = field(default_factory=list)
    studies_adjustment_history: list = field(default_factory=list)
    grid_state_history: list = field(default_factory=list)  # NEW: track grid state


class ContinuousExitControllerWithGrid:
    """
    Enhanced exit controller with Grid Synchronization as 6th input.

    New Input #6: GRID STATE (Voltage/Frequency/Phase)
    - Voltage: Nifty 50 trend alignment
    - Frequency: VIX in safe band?
    - Phase Angle: Stock/Index momentum sync?

    Effect on exit:
    - Grid strong → relax exit (let winners run)
    - Grid weak → tighten exit (exit faster)
    - Grid rejected → prevent entry (gating)
    """

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        clamp: float,
        atr_droop_mult: float,
        baseline_window: int = 10,
        saturation_exit_bars: int = 4,
        disable_saturation_exit: bool = False,
        grid_synchronizer: MacroGridSynchronizer = None,
        nifty_price_series: np.ndarray = None,
        vix_series: np.ndarray = None,
    ) -> None:
        self.kp, self.ki, self.kd = kp, ki, kd
        self.clamp = abs(clamp)
        self.atr_droop_mult = abs(atr_droop_mult)
        self.baseline_window = max(1, int(baseline_window))
        self.saturation_exit_bars = saturation_exit_bars
        self.disable_saturation_exit = disable_saturation_exit

        # Grid synchronization (NEW)
        self.grid_sync = grid_synchronizer
        self.nifty_prices = nifty_price_series
        self.vix_data = vix_series
        self.grid_enabled = grid_synchronizer is not None and GRID_SYNC_AVAILABLE

        # PIDs per symbol
        self._pids: Dict[str, PID] = {}
        self._confidence_history: Dict[str, Deque[float]] = {}
        self._studies_pids: Dict[str, PID] = {}
        self._studies_history: Dict[str, Deque[float]] = {}

    def _get_pid_from(self, store: Dict[str, PID], symbol: str) -> PID:
        if symbol not in store:
            store[symbol] = PID(
                Kp=self.kp, Ki=self.ki, Kd=self.kd, setpoint=0.5,
                sample_time=None, output_limits=(-self.clamp, self.clamp)
            )
        return store[symbol]

    def _baseline_from(self, store: Dict[str, Deque[float]], symbol: str, current_value: float) -> float:
        history = store.setdefault(symbol, deque(maxlen=self.baseline_window))
        baseline = (sum(history) / len(history)) if history else current_value
        history.append(current_value)
        return baseline

    def _get_grid_state(self, symbol: str, bar_index: int, direction: int = 1) -> Optional[SyncResult]:
        """Get grid synchronization state at current bar."""
        if not self.grid_enabled or self.nifty_prices is None:
            return None

        try:
            # Get price windows
            if bar_index < 63:
                return None  # Not enough data for Hilbert Transform

            nifty_window = self.nifty_prices[max(0, bar_index - 500):bar_index + 1]
            current_vix = self.vix_data[bar_index] if self.vix_data is not None else 20.0

            # Check grid state
            sync_result = self.grid_sync.check_synchronization(
                plant_close=np.array(nifty_window),
                grid_close=np.array(nifty_window),  # Using Nifty as proxy for both
                current_vix=float(current_vix),
                trade_direction=direction
            )

            return sync_result

        except Exception as e:
            return None

    def _grid_tightness_adjustment(self, grid_state: Optional[SyncResult]) -> float:
        """
        Calculate tightness adjustment based on grid state.

        Returns: 0.5 to 1.5 multiplier
        - 0.5: Grid strong → relax stop (wider band, let winners run)
        - 1.0: Grid neutral → normal stop
        - 1.5: Grid weak → tighten stop (narrow band, exit faster)
        """
        if grid_state is None or not self.grid_enabled:
            return 1.0  # No adjustment

        if not grid_state.is_synchronized:
            # Grid rejected: exit quickly
            return 1.5  # Tighten by 50%
        else:
            # Grid accepted: relax and let trade breathe
            return 0.7  # Loosen by 30%

    def open_position(
        self, side: str, entry_price: float, stop_price: float, target_price: float, max_hold_bars: int,
    ) -> ExitControllerState:
        return ExitControllerState(
            side=side, entry_price=entry_price, initial_stop_price=stop_price,
            original_target_distance=abs(target_price - entry_price),
            current_stop_price=stop_price, current_target_price=target_price,
            favorable_extreme=entry_price, max_hold_bars=max(1, int(max_hold_bars)),
        )

    def update(
        self,
        symbol: str,
        state: ExitControllerState,
        current_confidence: float,
        current_chart_studies_confidence: float,
        current_close: float,
        current_atr: float,
        bar_index: int = 0,  # NEW: bar index for grid sync
        direction: int = 1,  # NEW: trade direction for grid sync
    ) -> ExitControllerState:
        """
        Updated to include Grid Synchronization as 6th input.

        Inputs:
        1. PA Confidence (existing)
        2. Chart Studies Confidence (existing)
        3. Favorable Extreme (existing)
        4. Time Held (existing)
        5. ATR Droop (existing)
        6. Grid State (NEW) ← Voltage/Frequency/Phase
        """
        state.bars_held += 1

        # TRACK 1: PA Confidence PID
        baseline = self._baseline_from(self._confidence_history, symbol, current_confidence)
        pid = self._get_pid_from(self._pids, symbol)
        pid.setpoint = baseline
        adjustment = pid(current_confidence, dt=1)
        confidence_tightness = _clip(1.0 - abs(adjustment), 0.5, 1.0)
        state.adjustment_history.append(adjustment)

        # TRACK 2: Chart Studies Confidence PID
        studies_baseline = self._baseline_from(self._studies_history, symbol, current_chart_studies_confidence)
        studies_pid = self._get_pid_from(self._studies_pids, symbol)
        studies_pid.setpoint = studies_baseline
        studies_adjustment = studies_pid(current_chart_studies_confidence, dt=1)
        studies_tightness = _clip(1.0 - abs(studies_adjustment), 0.5, 1.0)
        state.studies_adjustment_history.append(studies_adjustment)

        # Saturation tracking
        at_low_confidence_extreme = adjustment >= self.clamp - 1e-9
        state.consecutive_bars_at_low_confidence_extreme = (
            state.consecutive_bars_at_low_confidence_extreme + 1 if at_low_confidence_extreme else 0
        )
        at_low_studies_extreme = studies_adjustment >= self.clamp - 1e-9
        state.consecutive_bars_at_low_studies_extreme = (
            state.consecutive_bars_at_low_studies_extreme + 1 if at_low_studies_extreme else 0
        )

        # INPUT #3: Price Curve (Favorable Extreme)
        if state.side == "BUY":
            state.favorable_extreme = max(state.favorable_extreme, current_close)
        else:
            state.favorable_extreme = min(state.favorable_extreme, current_close)

        # INPUT #4: Time Held
        time_fraction = _clip(state.bars_held / state.max_hold_bars, 0.0, 1.0)
        time_tightness = 1.0 - 0.5 * time_fraction

        # INPUT #5 + #6: Combine with Grid State (NEW)
        grid_state = self._get_grid_state(symbol, bar_index, direction)
        state.grid_state_history.append(grid_state)

        grid_tightness_mult = self._grid_tightness_adjustment(grid_state)

        # Combine all tightness sources (take minimum = most conservative)
        base_tightness = min(confidence_tightness, studies_tightness, time_tightness)

        # Apply grid adjustment
        combined_tightness = base_tightness * grid_tightness_mult
        combined_tightness = _clip(combined_tightness, 0.5, 1.5)  # Allow wider range with grid

        # INPUT #5: ATR Droop (existing)
        droop_distance = current_atr * self.atr_droop_mult
        stop_distance_now = droop_distance * combined_tightness

        if state.side == "BUY":
            candidate_stop = state.favorable_extreme - stop_distance_now
            state.current_stop_price = max(state.current_stop_price, candidate_stop)
        else:
            candidate_stop = state.favorable_extreme + stop_distance_now
            state.current_stop_price = min(state.current_stop_price, candidate_stop)

        return state

    def saturation_exit_reason(self, state: ExitControllerState) -> Optional[str]:
        """Returns which track has sustained saturation."""
        if self.disable_saturation_exit:
            return None
        if state.consecutive_bars_at_low_confidence_extreme >= self.saturation_exit_bars:
            return "saturation_exit_pa"
        if state.consecutive_bars_at_low_studies_extreme >= self.saturation_exit_bars:
            return "saturation_exit_studies"
        return None

    def forget(self, symbol: str) -> None:
        """Clear state for a symbol."""
        self._pids.pop(symbol, None)
        self._confidence_history.pop(symbol, None)
        self._studies_pids.pop(symbol, None)
        self._studies_history.pop(symbol, None)
