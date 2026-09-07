"""Revision 3: Macro Grid Synchronizer

Grid-Tie-Line Synchronization for algorithmic trading. Maps electrical grid
synchronization principles directly to quantitative finance.

Evaluates three core parameters before permitting entry execution:

1. FREQUENCY (Volatility Stability): Grid (India VIX) must operate in safe band
2. VOLTAGE (Trend Alignment): Macro index trend must support trade direction
3. PHASE ANGLE (Momentum Lockstep): Stock and index cycles must be in sync (Δϕ ≤ 15°)

Uses John Ehlers' Hilbert Transform (TA-Lib's HT_DCPHASE) to extract dominant
cycle phase angles, treating price movement as sine waves with measurable
phase relationships.

Only when all three parameters align does the synchronizer close the breaker
and permit the trade to reach the broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np
import talib


@dataclass
class SyncResult:
    """Result of a grid synchronization check."""

    is_synchronized: bool      # True if all three parameters aligned
    delta_phi: float           # Phase difference in degrees (0-180)
    reason: str                # Human-readable verdict


class MacroGridSynchronizer:
    """
    Revision 3: Grid Tie-Line Synchronizer.

    Evaluates Voltage, Frequency, and Phase Angle alignment between
    a local symbol (Plant) and the broader market index (Grid).

    Implements fail-fast execution order:
    1. FREQUENCY (VIX check) - cheapest, scalar computation
    2. VOLTAGE (EMA trend) - moderate cost
    3. PHASE ANGLE (Hilbert Transform) - most expensive, checked last
    """

    def __init__(
        self,
        phase_tolerance_deg: float = 15.0,
        vix_operating_band: Tuple[float, float] = (10.0, 30.0),
        trend_ema_period: int = 50,
    ):
        """
        Initialize the synchronizer with tolerance thresholds.

        Args:
            phase_tolerance_deg: Maximum allowed phase delta (default 15°)
            vix_operating_band: (min, max) India VIX safe operating range
            trend_ema_period: EMA lookback for trend detection
        """
        self.phase_tolerance = phase_tolerance_deg
        self.vix_min, self.vix_max = vix_operating_band
        self.trend_ema_period = trend_ema_period

    def check_synchronization(
        self,
        plant_close: np.ndarray,
        grid_close: np.ndarray,
        current_vix: float,
        trade_direction: int = 1,
    ) -> SyncResult:
        """
        Master breaker logic. All three parameters must align to close the circuit.

        Args:
            plant_close: Price series of the local symbol (e.g., INFY)
            grid_close: Price series of the macro index (e.g., Nifty 50)
            current_vix: Current India VIX reading
            trade_direction: 1 for BUY, -1 for SHORT

        Returns:
            SyncResult with is_synchronized (bool), delta_phi (float), reason (str)
        """

        # 1. FREQUENCY MATCH: Check if Grid Volatility is stable
        # If VIX is outside operating band, market is either panicking or dead.
        if not (self.vix_min <= current_vix <= self.vix_max):
            return SyncResult(
                is_synchronized=False,
                delta_phi=0.0,
                reason=f"Frequency Trip: VIX ({current_vix:.1f}) outside operating band [{self.vix_min}, {self.vix_max}]",
            )

        # 2. VOLTAGE MATCH: Check Macro Trend Alignment
        # Grid must support the intended trade direction.
        if len(grid_close) > self.trend_ema_period:
            grid_ema = talib.EMA(grid_close, timeperiod=self.trend_ema_period)
            grid_slope = grid_close[-1] - grid_ema[-1]

            # If buying (1), grid EMA must be rising (positive slope).
            # If shorting (-1), grid EMA must be falling (negative slope).
            if (trade_direction == 1 and grid_slope < 0) or (
                trade_direction == -1 and grid_slope > 0
            ):
                return SyncResult(
                    is_synchronized=False,
                    delta_phi=0.0,
                    reason=f"Voltage Trip: Grid trend opposes trade direction ({trade_direction}). "
                    f"Grid slope: {grid_slope:.4f}",
                )

        # 3. PHASE ANGLE MATCH: Check Momentum Lockstep
        # Most expensive computation—only reached if VIX and trend passed.
        is_in_phase, delta_phi = self._check_phase_angle(plant_close, grid_close)
        if not is_in_phase:
            return SyncResult(
                is_synchronized=False,
                delta_phi=delta_phi,
                reason=f"Phase Angle Trip: Δϕ ({delta_phi:.1f}°) exceeds {self.phase_tolerance}° tolerance",
            )

        # All parameters synchronized. Breaker closed.
        return SyncResult(
            is_synchronized=True,
            delta_phi=delta_phi,
            reason=f"Grid Synchronized: Voltage + Frequency + Phase Angle aligned (Δϕ={delta_phi:.1f}°)",
        )

    def _check_phase_angle(
        self, plant_close: np.ndarray, grid_close: np.ndarray
    ) -> Tuple[bool, float]:
        """
        Apply John Ehlers' Hilbert Transform to extract dominant cycle phase angles.

        The Hilbert Transform (via TA-Lib's HT_DCPHASE) extracts the phase angle
        of the dominant cyclical component in price data. This treats market price
        movement as a sine wave with measurable phase (0° to 360°).

        Returns:
            (is_synchronized: bool, delta_phi: float)
            is_synchronized: True if Δϕ <= tolerance
            delta_phi: Absolute phase difference (0° to 180°, normalized for wrap-around)
        """

        # HT_DCPHASE requires warmup period (approximately 63 bars for stability)
        if len(plant_close) < 63 or len(grid_close) < 63:
            # Insufficient data for reliable phase detection; bypass check
            return True, 0.0

        # Extract Dominant Cycle Phase (0° to 360°)
        plant_phase = talib.HT_DCPHASE(plant_close)[-1]
        grid_phase = talib.HT_DCPHASE(grid_close)[-1]

        # Calculate Absolute Phase Difference
        delta_phi = abs(plant_phase - grid_phase)

        # Normalize for cyclical wrap-around
        # Example: 355° and 5° are only 10° apart, not 350°
        if delta_phi > 180.0:
            delta_phi = 360.0 - delta_phi

        is_synchronized = delta_phi <= self.phase_tolerance
        return is_synchronized, delta_phi
