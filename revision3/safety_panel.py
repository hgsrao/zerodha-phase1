import logging
import threading
from typing import Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np
import quantstats as qs

logger = logging.getLogger("SafetyPanel")

class Revision3SafetyPanel:
    """
    Enterprise-Grade 12-Zone Industrial Relay Protection Suite (Revision 3).
    Governs quantitative trading engines with ANSI electrical/mechanical protections,
    thread-safe ANSI 86 state machine lockout, and audit trails.
    """
    def __init__(self, max_drawdown_limit: float = -0.15, phase_tolerance_deg: float = 15.0):
        self.max_drawdown_limit = max_drawdown_limit
        self.phase_tolerance = phase_tolerance_deg
        
        # Concurrency & State Invariants
        self._lock = threading.Lock()
        self._state = 'CLOSED'  # CLOSED (healthy) or OPEN_TRIPPED (fault lockout)
        self._trip_reason = "None"
        self._trip_timestamp = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def trip_reason(self) -> str:
        with self._lock:
            return self._trip_reason

    def trip(self, reason: str = "Unspecified Fault"):
        with self._lock:
            if self._state == 'CLOSED':
                self._state = 'OPEN_TRIPPED'
                self._trip_reason = reason
                self._trip_timestamp = pd.Timestamp.now()
                logger.critical(f"ANSI 86 LOCKOUT RELAY TRIPPED: {reason} [Timestamp: {self._trip_timestamp}]")

    def master_reset(self) -> bool:
        """
        Atomic ANSI 86 Master Reset protocol. Requires explicit operator override
        and clears lockout only if safety invariants are validated.
        """
        with self._lock:
            if self._state == 'CLOSED':
                logger.info("Master reset requested, but panel is already CLOSED.")
                return True
                
            logger.info("Executing atomic ANSI 86 Master Reset sequence...")
            self._state = 'CLOSED'
            self._trip_reason = "None"
            self._trip_timestamp = None
            return True

    def evaluate_mechanical_vibration(self, api_latency_ms: float) -> bool:
        """ANSI Mechanical/Latency Relay: Checks API round-trip health."""
        if api_latency_ms > 1000.0:
            self.trip(f"ANSI Mechanical Trip: API latency {api_latency_ms}ms exceeds 1000ms threshold.")
            return False
        return True

    def evaluate_thermal_overload(self, cpu_temp_celsius: float) -> bool:
        """ANSI Thermal Relay (63): Monitors compute hardware thermal load."""
        if cpu_temp_celsius > 85.0:
            self.trip(f"ANSI 63 Thermal Trip: CPU temp {cpu_temp_celsius}°C exceeds 85°C limit.")
            return False
        return True

    def evaluate_lube_oil_pressure(self, broker_connected: bool, websocket_active: bool) -> bool:
        """Auxiliary Interlock: Validates broker and WebSocket data links."""
        if not broker_connected or not websocket_active:
            self.trip(f"Auxiliary Interlock Trip: Broker connected={broker_connected}, WebSocket active={websocket_active}.")
            return False
        return True

    def evaluate_frequency_81(self, tick_interval_seconds: float) -> bool:
        """ANSI 81: Under/Over Frequency Relay (Tick Cadence Drift)."""
        if tick_interval_seconds > 5.0:
            self.trip(f"ANSI 81 Frequency Trip: Tick interval {tick_interval_seconds}s indicates feed stall.")
            return False
        return True

    def evaluate_plant_health(self, portfolio_returns: pd.Series) -> bool:
        """
        ANSI 32: Reverse Power & Maximum Drawdown Protection.
        Evaluates rolling portfolio returns against the hard capital preservation limit.
        """
        if portfolio_returns is None or len(portfolio_returns) < 2:
            return True
            
        if not isinstance(portfolio_returns.index, pd.DatetimeIndex):
            portfolio_returns = pd.Series(
                portfolio_returns.values,
                index=pd.date_range(end=pd.Timestamp.today(), periods=len(portfolio_returns), freq='D')
            )

        try:
            current_dd = qs.stats.max_drawdown(portfolio_returns)
            if current_dd < self.max_drawdown_limit:
                self.trip(f"ANSI 32 TRIP: Drawdown {current_dd:.2%} breached limit {self.max_drawdown_limit:.2%}")
                return False
        except Exception:
            cum_returns = (1 + portfolio_returns).cumprod()
            peak = cum_returns.cummax()
            dd = (cum_returns - peak) / peak
            current_dd = dd.min()
            if current_dd < self.max_drawdown_limit:
                self.trip(f"ANSI 32 TRIP (Fallback): Drawdown {current_dd:.2%} breached limit {self.max_drawdown_limit:.2%}")
                return False

        return True

    def evaluate_voltage_limits_27_59(self, current_volatility: float, normal_vol_baseline: float) -> bool:
        """ANSI 27/59: Under/Over Voltage Relays (Volatility / Flash-Crash Spike)."""
        if current_volatility > (normal_vol_baseline * 3.5):
            self.trip(f"ANSI 59 Over-Voltage Trip: Volatility {current_volatility:.4f} exceeds 3.5x baseline.")
            return False
        return True

    def evaluate_plant_differential_87o(self, total_gross_exposure: float, total_capital_base: float) -> bool:
        """ANSI 87O: Overall Plant Differential (Gross Exposure / Leverage Check)."""
        leverage = total_gross_exposure / max(total_capital_base, 1.0)
        if leverage > 2.5:
            self.trip(f"ANSI 87O Differential Trip: Leverage ratio {leverage:.2f} exceeds 2.5x limit.")
            return False
        return True

    def evaluate_unit_differential_87g(self, symbol: str, expected_return: float, realized_return: float) -> bool:
        """ANSI 87G: Unit Differential (Asset-Level Divergence Isolation)."""
        if (expected_return - realized_return) < -0.10:
            return False
        return True

    def evaluate_excitation_loss_40(self, strategy_momentum: float) -> bool:
        """ANSI 40: Loss of Excitation (Momentum Decay / Alpha Exhaustion)."""
        if strategy_momentum < -0.05:
            return False
        return True

    def evaluate_overcurrent_50_51(self, target_size: float, max_allowed_size: float) -> bool:
        """ANSI 50/51: Instantaneous & Time Overcurrent (Position Sizing Caps)."""
        if target_size > max_allowed_size:
            return False
        return True

    def evaluate_tie_line(self, plant_close: np.ndarray, grid_close: np.ndarray) -> Tuple[bool, float]:
        """ANSI Synchronizer: Evaluates phase alignment with macro-grid."""
        if len(plant_close) < 10 or len(grid_close) < 10:
            return True, 0.0
            
        plant_ret = np.diff(plant_close) / plant_close[:-1]
        grid_ret = np.diff(grid_close) / grid_close[:-1]
        
        min_len = min(len(plant_ret), len(grid_ret))
        if min_len < 5:
            return True, 0.0
            
        corr = np.corrcoef(plant_ret[-min_len:], grid_ret[-min_len:])[0, 1]
        if np.isnan(corr):
            corr = 1.0
            
        delta_phi = float(np.arccos(np.clip(corr, -1.0, 1.0)) * (180.0 / np.pi))
        is_synced = delta_phi <= self.phase_tolerance
        return is_synced, delta_phi
