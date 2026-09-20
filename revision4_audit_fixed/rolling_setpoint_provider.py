"""
CRITICAL FIX #1: Unified Rolling Setpoint Provider

Purpose: Eliminate PID setpoint inconsistency between SimplePIDModelPredictiveControlBox
and ContinuousExitController by providing a single unified rolling baseline computation.

Issue Fixed: Both PIDs were computing baselines differently, causing asymmetric control behavior.

Author: Professional Code Audit (Critical Fix Implementation)
Date: September 20, 2026
"""

from collections import deque
from typing import Dict, Optional


class RollingSetpointProvider:
    """Unified rolling baseline computation for PID setpoints.

    This provider ensures that BOTH the entry PID and exit PID use IDENTICAL
    setpoint logic. A rolling mean of recent signal values is computed for each
    symbol, and both PIDs reference this same baseline.

    Key design: The baseline is computed from EXISTING history before adding
    the current value. This prevents circular dependency where the current
    reading influences its own setpoint.
    """

    def __init__(self, window_size: int = 3):
        """
        Args:
            window_size: Number of bars to include in rolling mean.
                         3 is standard (current + 2 prior bars).
        """
        self.window_size = window_size
        self._history: Dict[str, deque] = {}

    def update(self, symbol: str, current_value: float) -> float:
        """Get current rolling baseline and update history.

        CRITICAL: Returns baseline BEFORE adding current_value to history.
        This ensures the current bar's reading does NOT influence its own setpoint.

        Args:
            symbol: Trading symbol (e.g., 'INFY', 'TCS')
            current_value: Current confidence or signal value

        Returns:
            Rolling mean baseline (from history, not including current)

        Example:
            >>> provider = RollingSetpointProvider(window_size=3)
            >>> baseline1 = provider.update('INFY', 0.5)
            >>> baseline2 = provider.update('INFY', 0.55)
            >>> baseline3 = provider.update('INFY', 0.60)
            >>> baseline4 = provider.update('INFY', 0.65)
            # baseline4 = mean(0.55, 0.60) = 0.575 (most recent 2 prior bars)
        """
        # Initialize history for this symbol if first call
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self.window_size)

        history = self._history[symbol]

        # Compute baseline from EXISTING history (before adding current)
        if len(history) > 0:
            baseline = sum(history) / len(history)
        else:
            # First call: use current value as baseline (no prior data)
            baseline = float(current_value)

        # NOW add current value to history for NEXT call
        history.append(float(current_value))

        return float(baseline)

    def get_current_history(self, symbol: str) -> list:
        """Return current history for a symbol (for debugging/testing).

        Args:
            symbol: Trading symbol

        Returns:
            List of values in history (oldest to newest)
        """
        if symbol not in self._history:
            return []
        return list(self._history[symbol])

    def reset(self, symbol: str) -> None:
        """Clear history for a symbol (e.g., after trade exit).

        Args:
            symbol: Trading symbol to reset
        """
        self._history.pop(symbol, None)

    def reset_all(self) -> None:
        """Clear all histories."""
        self._history.clear()

    def __repr__(self) -> str:
        return f"RollingSetpointProvider(window_size={self.window_size}, symbols_tracked={len(self._history)})"
