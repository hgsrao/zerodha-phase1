"""Revision 3: Macro Grid Synchronizer

Ensures algorithmic trading engine transitions from Island Mode (local symbol
evaluation only) to Grid-Tied Mode (synchronized with macro market conditions).

Maps electrical grid synchronization concepts directly to quantitative trading:

* Voltage (Directional Amplitude): Macro index trend must align with local symbol.
* Frequency (Volatility/Pace): India VIX must fall within engine's operating band.
* Phase Angle (Momentum Alignment): Stock and Nifty 50 cyclical momentum must be
  in lockstep (Δϕ ≈ 0°).

Uses TA-Lib's Hilbert Transform (HT_DCPHASE) to extract phase angles from price
cycles, treating market movement as sine waves with measurable phase relationships.

Only when all three synchronization parameters align does the synchronizer close
the breaker and permit entry signals to reach the broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import talib


@dataclass
class SynchronizerConfig:
    """Configuration for grid synchronization thresholds."""

    # Voltage (Trend Alignment)
    voltage_tolerance: float = 0.05  # Macro trend must be within 5% of local direction

    # Frequency (Volatility Band)
    vix_min: float = 10.0            # India VIX floor (panic floor)
    vix_max: float = 40.0            # India VIX ceiling (extreme volatility)

    # Phase Angle (Momentum Lockstep)
    phase_tolerance: float = 15.0    # Δϕ must be ≤ 15° for synchronization

    # Minimum data requirement
    min_bars_for_phase: int = 50     # Need at least 50 bars for Hilbert Transform


@dataclass
class SynchronizerState:
    """Real-time synchronization state."""

    # Voltage check result
    voltage_aligned: bool = False
    macro_trend: float = 0.0         # Nifty 50 recent direction (-1 to +1)
    local_trend: float = 0.0         # Stock recent direction (-1 to +1)
    trend_correlation: float = 0.0   # Correlation coefficient

    # Frequency check result
    frequency_stable: bool = False
    india_vix: float = 0.0
    vix_in_band: bool = False

    # Phase check result
    phase_aligned: bool = False
    stock_phase: float = 0.0         # 0° to 360°
    grid_phase: float = 0.0          # 0° to 360°
    phase_delta: float = 0.0         # Δϕ = |stock_phase - grid_phase|

    # Overall breaker state
    breaker_closed: bool = False
    last_failure_reason: Optional[str] = None
    timestamp: Optional[str] = None


class MacroGridSynchronizer:
    """
    Grid-tie synchronization engine for algorithmic trading.

    Implements three-parameter synchronization check (Voltage, Frequency, Phase)
    before permitting entry signals. Acts as the ultimate safety veto between
    the orchestrator's proposed trades and actual execution.
    """

    def __init__(self, config: SynchronizerConfig = None):
        self.config = config or SynchronizerConfig()
        self.state: Dict[str, SynchronizerState] = {}  # Per-symbol state

    def check_synchronization(
        self,
        symbol: str,
        stock_bars: pd.DataFrame,
        grid_bars: pd.DataFrame,  # Nifty 50 or Sensex
        vix_series: Optional[pd.Series] = None,
        current_close: float = None,
    ) -> Tuple[bool, SynchronizerState, str]:
        """
        Check if stock and macro grid are synchronized for entry.

        Returns:
            (breaker_closed: bool, state: SynchronizerState, verdict_reason: str)
            breaker_closed=True means all three checks passed, safe to enter.
            breaker_closed=False means at least one check failed, trade is blocked.
        """

        state = SynchronizerState(timestamp=str(pd.Timestamp.now()))

        # Check 1: Voltage (Trend Alignment)
        voltage_ok, voltage_reason = self._check_voltage(
            symbol, stock_bars, grid_bars, state
        )

        if not voltage_ok:
            state.breaker_closed = False
            state.last_failure_reason = voltage_reason
            return False, state, voltage_reason

        # Check 2: Frequency (Volatility Stability)
        frequency_ok, frequency_reason = self._check_frequency(
            symbol, vix_series, state
        )

        if not frequency_ok:
            state.breaker_closed = False
            state.last_failure_reason = frequency_reason
            return False, state, frequency_reason

        # Check 3: Phase Angle (Momentum Lockstep)
        phase_ok, phase_reason = self._check_phase_angle(
            symbol, stock_bars, grid_bars, state
        )

        if not phase_ok:
            state.breaker_closed = False
            state.last_failure_reason = phase_reason
            return False, state, phase_reason

        # All three checks passed
        state.breaker_closed = True
        state.last_failure_reason = None
        self.state[symbol] = state

        return True, state, "SYNCHRONIZATION OK: Voltage + Frequency + Phase aligned"

    def _check_voltage(
        self,
        symbol: str,
        stock_bars: pd.DataFrame,
        grid_bars: pd.DataFrame,
        state: SynchronizerState,
    ) -> Tuple[bool, str]:
        """
        Check Voltage (Directional Amplitude): Do stock and grid trends align?

        Computes the recent 20-bar trend direction for both stock and macro index.
        If they point in opposite directions, the generator and grid are 180° out
        of phase electrically—entering now means fighting the macro current.
        """

        if len(stock_bars) < 20 or len(grid_bars) < 20:
            return False, "VOLTAGE CHECK FAILED: Insufficient bars for trend"

        # Recent 20-bar trend direction
        stock_recent = stock_bars["close"].iloc[-20:].to_numpy()
        grid_recent = grid_bars["close"].iloc[-20:].to_numpy()

        stock_trend = float(stock_recent[-1] - stock_recent[0]) / stock_recent[0]
        grid_trend = float(grid_recent[-1] - grid_recent[0]) / grid_recent[0]

        # Normalize to [-1, 1]
        stock_trend = max(-1.0, min(1.0, stock_trend / 0.05))  # 5% = max normal move
        grid_trend = max(-1.0, min(1.0, grid_trend / 0.05))

        state.local_trend = stock_trend
        state.macro_trend = grid_trend

        # Correlation: do they move in the same direction?
        correlation = np.corrcoef(stock_recent, grid_recent)[0, 1]
        if np.isnan(correlation):
            correlation = 0.0

        state.trend_correlation = correlation
        state.voltage_aligned = correlation > 0.3  # Positive correlation required

        if not state.voltage_aligned:
            return (
                False,
                f"VOLTAGE MISMATCH: Stock trend {stock_trend:+.3f} vs Grid {grid_trend:+.3f} "
                f"(correlation {correlation:.3f}, need >0.3)"
            )

        return True, "Voltage check passed"

    def _check_frequency(
        self,
        symbol: str,
        vix_series: Optional[pd.Series],
        state: SynchronizerState,
    ) -> Tuple[bool, str]:
        """
        Check Frequency (Volatility/Pace): Is macro volatility within safe band?

        If India VIX is spiking (grid frequency collapsing), enter anyway and
        expect immediate, violent drawdowns as the market panic sells everything.
        If VIX is dead (grid frequency stuck), order books are empty—slippage kills.
        """

        if vix_series is None or len(vix_series) == 0:
            # No VIX data available, default to "stable"
            state.frequency_stable = True
            state.vix_in_band = True
            state.india_vix = np.nan
            return True, "Frequency check passed (no VIX data, assuming stable)"

        current_vix = float(vix_series.iloc[-1])
        state.india_vix = current_vix
        state.vix_in_band = self.config.vix_min <= current_vix <= self.config.vix_max
        state.frequency_stable = state.vix_in_band

        if not state.frequency_stable:
            if current_vix < self.config.vix_min:
                return (
                    False,
                    f"FREQUENCY ANOMALY: India VIX {current_vix:.1f} below floor "
                    f"(dead market, expect slippage)"
                )
            else:
                return (
                    False,
                    f"FREQUENCY ANOMALY: India VIX {current_vix:.1f} above ceiling "
                    f"(market panic, flash crash risk)"
                )

        return True, "Frequency check passed"

    def _check_phase_angle(
        self,
        symbol: str,
        stock_bars: pd.DataFrame,
        grid_bars: pd.DataFrame,
        state: SynchronizerState,
    ) -> Tuple[bool, str]:
        """
        Check Phase Angle (Δϕ): Are stock and grid momentum in lockstep?

        Uses Hilbert Transform (TA-Lib's HT_DCPHASE) to extract the dominant
        cycle phase angle from each price series. In electrical terms, this is
        the angle at which the sine wave crosses zero.

        If Δϕ = 0°, they are perfectly in phase—accelerating together.
        If Δϕ = 180°, they are anti-phase—stock up, grid down (disaster).
        If Δϕ ≈ 90°, they are in quadrature—one leads, one lags (unstable).
        """

        if len(stock_bars) < self.config.min_bars_for_phase:
            return (
                False,
                f"PHASE CHECK FAILED: Need {self.config.min_bars_for_phase} bars, "
                f"have {len(stock_bars)}"
            )

        if len(grid_bars) < self.config.min_bars_for_phase:
            return (
                False,
                f"PHASE CHECK FAILED: Need {self.config.min_bars_for_phase} bars "
                f"for grid, have {len(grid_bars)}"
            )

        # Extract close prices as float64 arrays
        stock_close = stock_bars["close"].to_numpy(dtype=np.float64)
        grid_close = grid_bars["close"].to_numpy(dtype=np.float64)

        try:
            # Compute Hilbert Transform dominant cycle phase (0° to 360°)
            stock_phase_array = talib.HT_DCPHASE(stock_close)
            grid_phase_array = talib.HT_DCPHASE(grid_close)

            # Get the current (most recent) phase
            stock_phase = float(stock_phase_array[-1])
            grid_phase = float(grid_phase_array[-1])

            state.stock_phase = stock_phase
            state.grid_phase = grid_phase

            # Calculate phase delta, accounting for 360° wrap-around
            delta_phi = abs(stock_phase - grid_phase)
            if delta_phi > 180:
                delta_phi = 360 - delta_phi

            state.phase_delta = delta_phi
            state.phase_aligned = delta_phi <= self.config.phase_tolerance

            if not state.phase_aligned:
                return (
                    False,
                    f"PHASE MISMATCH: Stock {stock_phase:.1f}° vs Grid {grid_phase:.1f}° "
                    f"(Δϕ={delta_phi:.1f}°, need ≤{self.config.phase_tolerance}°)"
                )

            return True, "Phase angle check passed"

        except Exception as e:
            return False, f"PHASE CHECK ERROR: {str(e)}"

    def get_state(self, symbol: str) -> Optional[SynchronizerState]:
        """Retrieve the last computed synchronization state for a symbol."""
        return self.state.get(symbol)

    def print_synchronizer_status(self, state: SynchronizerState) -> None:
        """Pretty-print the synchronization status."""

        print(f"\n{'='*90}")
        print(f"MACRO GRID SYNCHRONIZER STATUS [{state.timestamp}]")
        print(f"{'='*90}")

        # Voltage
        voltage_icon = "✅" if state.voltage_aligned else "❌"
        print(f"\n{voltage_icon} VOLTAGE (Trend Alignment)")
        print(f"   Local Trend:      {state.local_trend:+.3f}")
        print(f"   Macro Trend:      {state.macro_trend:+.3f}")
        print(f"   Correlation:      {state.trend_correlation:.3f} (need >0.3)")

        # Frequency
        frequency_icon = "✅" if state.frequency_stable else "❌"
        print(f"\n{frequency_icon} FREQUENCY (Volatility Band)")
        print(f"   India VIX:        {state.india_vix:.1f}")
        print(f"   In Band:          {state.vix_in_band}")

        # Phase
        phase_icon = "✅" if state.phase_aligned else "❌"
        print(f"\n{phase_icon} PHASE ANGLE (Momentum Lockstep)")
        print(f"   Stock Phase:      {state.stock_phase:.1f}°")
        print(f"   Grid Phase:       {state.grid_phase:.1f}°")
        print(f"   Δϕ (Delta Phi):   {state.phase_delta:.1f}° (need ≤15°)")

        # Breaker
        breaker_icon = "⚡ CLOSED" if state.breaker_closed else "🔌 OPEN"
        print(f"\n{breaker_icon}")
        if not state.breaker_closed and state.last_failure_reason:
            print(f"   Failure: {state.last_failure_reason}")

        print(f"{'='*90}\n")
