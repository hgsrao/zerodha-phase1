"""Grid Synchronization Box - Uses Nifty 50 State to Gate Entry/Exit Decisions

Nifty 50 acts as the "grid" (power system analogy):
  - Voltage   = Trend strength (momentum directional alignment)
  - Frequency = Volatility band (market condition)
  - Phase     = Momentum phase (Hilbert transform)

Entry decision: Only initiate trades when grid is STRONG
Exit decision: Tighten/loosen exit threshold based on grid state
"""

import pandas as pd
from typing import Dict, Tuple, Any, Optional
from dataclasses import dataclass


@dataclass
class GridState:
    """Representation of Nifty 50 state."""
    voltage: float        # Trend strength (0.0 to 1.0)
    frequency: float      # Volatility state (0.0 to 1.0)
    phase: float          # Momentum phase (-1.0 to 1.0)
    momentum: float       # Price momentum (-1.0 to 1.0)
    accepts_entry: bool   # Should we initiate new trades?
    exit_urgency: float   # 0.0 (relaxed) to 1.0 (aggressive exit)


class GridSynchronizationBox:
    """Reads Nifty 50 state and gates individual symbol decisions."""

    def __init__(self, lookback_bars: int = 50, volatility_percentile: float = 0.7):
        self.lookback_bars = lookback_bars
        self.volatility_percentile = volatility_percentile
        self._nifty_history: Optional[pd.DataFrame] = None
        self.grid_state: Optional[GridState] = None

    def load_nifty_data(self, nifty_df: pd.DataFrame) -> None:
        """Load Nifty 50 OHLCV data."""
        required = {"close", "high", "low", "volume"}
        if not required.issubset(nifty_df.columns):
            raise ValueError(f"Nifty data must have columns: {required}")
        self._nifty_history = nifty_df.copy()

    def update(self, current_bar_index: int) -> GridState:
        """Calculate current grid state at this bar."""
        if self._nifty_history is None or len(self._nifty_history) < self.lookback_bars:
            # Default to "accept everything" if grid data not available
            return GridState(
                voltage=0.5,
                frequency=0.5,
                phase=0.0,
                momentum=0.0,
                accepts_entry=True,
                exit_urgency=0.0,
            )

        # Window: from (current_bar_index - lookback) to current_bar_index
        start_idx = max(0, current_bar_index - self.lookback_bars)
        end_idx = min(len(self._nifty_history), current_bar_index + 1)

        if end_idx <= start_idx + 1:
            return GridState(
                voltage=0.5,
                frequency=0.5,
                phase=0.0,
                momentum=0.0,
                accepts_entry=True,
                exit_urgency=0.0,
            )

        window = self._nifty_history.iloc[start_idx:end_idx]

        # Component 1: VOLTAGE (Trend Strength)
        # Measured by: Momentum relative to ATR
        returns = window["close"].pct_change().dropna()
        if len(returns) > 0:
            momentum = returns.iloc[-1]  # Last bar's return
            volatility = returns.std()
            voltage = abs(momentum) / (volatility + 1e-8)  # Ratio normalized
            voltage = min(voltage, 1.0)  # Clip to [0, 1]
        else:
            voltage = 0.5
            momentum = 0.0

        # Component 2: FREQUENCY (Volatility State)
        # High volatility = choppy/risky, Low volatility = trending
        recent_volatility = returns.std() if len(returns) > 0 else 0.0
        all_volatility = self._nifty_history["close"].pct_change().std()
        if all_volatility > 0:
            volatility_ratio = recent_volatility / all_volatility
            frequency = min(volatility_ratio, 1.0)
        else:
            frequency = 0.5

        # Component 3: PHASE (Momentum Phase via Hilbert-like approximation)
        # Simple: compare recent momentum vs baseline momentum
        if len(returns) > 20:
            baseline_momentum = returns.iloc[:-5].mean()
            recent_momentum = returns.iloc[-5:].mean()
            phase = (recent_momentum - baseline_momentum) / (all_volatility + 1e-8)
            phase = max(-1.0, min(phase, 1.0))
        else:
            phase = 0.0

        # Decision Rules
        # Entry: Accept if voltage (trend) is STRONG AND frequency (volatility) is LOW
        accepts_entry = (voltage > 0.4) and (frequency < 0.7)

        # Exit Urgency: If frequency is HIGH or momentum is WEAK, increase urgency
        exit_urgency = frequency * (1.0 - voltage)  # Range [0, 1]

        self.grid_state = GridState(
            voltage=voltage,
            frequency=frequency,
            phase=phase,
            momentum=momentum,
            accepts_entry=accepts_entry,
            exit_urgency=exit_urgency,
        )

        return self.grid_state

    def get_pa_signal_gate(self) -> float:
        """
        Gate applied to PA box signals.
        Returns multiplier: 0.0 (reject all) to 1.0 (accept fully).
        """
        if self.grid_state is None:
            return 1.0

        # If grid rejects entry, PA signals are heavily attenuated (but not zero)
        if not self.grid_state.accepts_entry:
            return 0.2  # Allow only strongest PA signals

        # If grid accepts, scale by how strong the grid is
        return 0.5 + 0.5 * self.grid_state.voltage  # Range [0.5, 1.0]

    def get_exit_tightness_adjustment(self) -> float:
        """
        Adjustment to exit tightness based on grid urgency.
        Returns factor: 0.0 (disable exits) to 1.0+ (aggressive exits).

        Lower values = more relaxed trailing stop (wider band)
        Higher values = tighter trailing stop (narrower band)
        """
        if self.grid_state is None:
            return 1.0

        # If exit urgency is high, tighten the exit
        # If exit urgency is low, loosen the exit
        return 0.5 + self.grid_state.exit_urgency  # Range [0.5, 1.5]

    def get_entry_confidence_adjustment(self) -> float:
        """
        Adjustment factor applied to entry confidence threshold.
        Lower = easier to enter, Higher = harder to enter.
        """
        if self.grid_state is None:
            return 1.0

        # Require stronger signals when grid is unfavorable
        if not self.grid_state.accepts_entry:
            return 2.0  # Require 2x stronger PA signal

        # Favorable conditions: relax requirement
        return 0.7 + 0.3 * self.grid_state.voltage  # Range [0.7, 1.0]

    def get_state_description(self) -> str:
        """Human-readable grid state."""
        if self.grid_state is None:
            return "GRID_UNAVAILABLE"

        entry_status = "✓ ENTRY" if self.grid_state.accepts_entry else "✗ NO_ENTRY"
        voltage_bar = "█" * int(self.grid_state.voltage * 10)
        freq_bar = "█" * int(self.grid_state.frequency * 10)

        return (
            f"{entry_status} | "
            f"Voltage:{voltage_bar:10s} ({self.grid_state.voltage:.2f}) | "
            f"Freq:{freq_bar:10s} ({self.grid_state.frequency:.2f}) | "
            f"Exit_Urgency:{self.grid_state.exit_urgency:.2f}"
        )
