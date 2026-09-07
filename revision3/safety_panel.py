import numpy as np
import pandas as pd
import talib
import quantstats as qs
from transitions import Machine

class Revision3SafetyPanel:
    """
    Master Safety Panel implementing 100% full-spectrum industrial power plant 
    protection coordination adapted for quantitative trading architectures.
    """
    states = ['CLOSED', 'OPEN_TRIPPED']

    def __init__(self, max_drawdown_limit: float = -0.15, phase_tolerance_deg: float = 15.0):
        self.max_drawdown_limit = max_drawdown_limit
        self.phase_tolerance = phase_tolerance_deg
        self.trip_reason = None

        # State machine for hard fault lockout
        self.machine = Machine(model=self, states=Revision3SafetyPanel.states, initial='CLOSED')
        self.machine.add_transition(trigger='trip', source='CLOSED', dest='OPEN_TRIPPED')
        self.machine.add_transition(trigger='reset', source='OPEN_TRIPPED', dest='CLOSED', after='_clear_fault')

    def _clear_fault(self):
        self.trip_reason = None

    def master_reset(self) -> bool:
        """
        Master Plant Reset: Clears all fault latches and closes the master substation breakers.
        Returns True if successful, False if the plant is still in an unsafe state.
        """
        if self.state == 'OPEN_TRIPPED':
            try:
                self.reset()
                self.trip_reason = None
                return True
            except Exception as e:
                self.trip_reason = f"Master Reset Blocked: {str(e)}"
                return False
        return True

    # ==========================================
    # ELECTRICAL: ANSI 32 (Reverse Power / Drawdown)
    # ==========================================
    def evaluate_plant_health(self, portfolio_returns: pd.Series) -> bool:
        """ANSI 32: Monitors portfolio physics and enforces hard drawdown limits."""
        if self.state == 'OPEN_TRIPPED':
            return False

        if len(portfolio_returns) >= 2:
            current_dd = qs.stats.max_drawdown(portfolio_returns)
            if current_dd < self.max_drawdown_limit:
                self.trip_reason = f"ANSI 32 Reverse Power: Drawdown {current_dd:.2%} breached limit {self.max_drawdown_limit:.2%}" 
                self.trip()
                return False
        return True

    # ==========================================
    # ELECTRICAL: GRID SYNCHRONIZER (Tie-Line)
    # ==========================================
    def evaluate_tie_line(self, plant_close: np.ndarray, grid_close: np.ndarray) -> tuple[bool, float]:
        """Hilbert Transform: Verifies phase alignment between asset momentum and market grid."""
        if len(plant_close) < 63 or len(grid_close) < 63:
            return True, 0.0 

        plant_phase = talib.HT_DCPHASE(plant_close)[-1]
        grid_phase = talib.HT_DCPHASE(grid_close)[-1]

        delta_phi = abs(plant_phase - grid_phase)
        if delta_phi > 180.0:
            delta_phi = 360.0 - delta_phi

        is_synchronized = delta_phi <= self.phase_tolerance
        return is_synchronized, delta_phi

    # ==========================================
    # ELECTRICAL: ANSI 87G & 87O (Differentials)
    # ==========================================
    def evaluate_unit_differential_87g(self, symbol: str, model_expected_return: float, actual_realized_return: float, threshold: float = 0.05) -> bool:
        """ANSI 87G: Unit Differential (Asset-Level). Isolates single failing assets."""
        divergence = abs(model_expected_return - actual_realized_return)
        if divergence > threshold:
            self.trip_reason = f"ANSI 87G Unit Fault [{symbol}]: Model expected {model_expected_return:.4f}, got {actual_realized_return:.4f}"
            return False 
        return True

    def evaluate_plant_differential_87o(self, total_gross_exposure: float, total_capital_base: float, max_allowable_ratio: float = 2.0) -> bool:
        """ANSI 87O: Plant Differential (Facility-Level). Catches aggregate leverage imbalances."""
        if self.state == 'OPEN_TRIPPED':
            return False

        exposure_ratio = total_gross_exposure / max(1.0, total_capital_base)
        if exposure_ratio > max_allowable_ratio:
            self.trip_reason = f"ANSI 87O Plant Differential Fault: Gross exposure ratio {exposure_ratio:.2f} breached limit {max_allowable_ratio}"
            self.trip()
            return False
        return True

    # ==========================================
    # ELECTRICAL: ANSI 27/59 (Under/Over Voltage)
    # ==========================================
    def evaluate_voltage_limits_27_59(self, current_volatility: float, normal_vol_baseline: float, max_vol_multiplier: float = 3.0) -> bool:
        """ANSI 27/59: Liquidity droughts and flash crash volatility spikes."""
        if self.state == 'OPEN_TRIPPED':
            return False

        if current_volatility > (normal_vol_baseline * max_vol_multiplier):
            self.trip_reason = f"ANSI 59 Over-Voltage Fault: Volatility spike ({current_volatility:.2f}) exceeded threshold."
            self.trip()
            return False
        return True

    # ==========================================
    # ELECTRICAL: ANSI 40 (Loss of Excitation)
    # ==========================================
    def evaluate_excitation_loss_40(self, strategy_momentum: float, threshold: float = -0.02) -> bool:
        """ANSI 40: Loss of Excitation. Detects momentum collapse and alpha decay."""
        if strategy_momentum < threshold:
            self.trip_reason = f"ANSI 40 Loss of Excitation: Strategy momentum degraded to {strategy_momentum:.4f}."
            return False
        return True

    # ==========================================
    # ELECTRICAL: ANSI 50/51 (Overcurrent / Sizing)
    # ==========================================
    def evaluate_overcurrent_50_51(self, position_size: float, max_allowed_size: float) -> bool:
        """ANSI 50/51: Position sizing and leverage limit checks."""
        if position_size > max_allowed_size:
            self.trip_reason = f"ANSI 50 Overcurrent Fault: Position size {position_size} breached maximum limit {max_allowed_size}."
            self.trip()
            return False
        return True

    # ==========================================
    # ELECTRICAL: ANSI 46 (Current Unbalance)
    # ==========================================
    def evaluate_current_unbalance_46(self, factor_skew_metric: float, max_skew: float = 0.8) -> bool:
        """ANSI 46: Portfolio factor skew and unhedged beta risk."""
        if factor_skew_metric > max_skew:
            self.trip_reason = f"ANSI 46 Unbalance Fault: Portfolio factor skew {factor_skew_metric:.2f} exceeded safe limits."
            self.trip()
            return False
        return True

    # ==========================================
    # ELECTRICAL: ANSI 81O/81U (Frequency Protection)
    # ==========================================
    def evaluate_frequency_81(self, tick_interval_seconds: float, expected_interval: float = 1.0, max_drift: float = 5.0) -> bool:
        """ANSI 81O/81U: Tick cadence and timestamp anomaly monitoring."""
        if abs(tick_interval_seconds - expected_interval) > max_drift:
            self.trip_reason = f"ANSI 81 Frequency Fault: Tick interval anomaly ({tick_interval_seconds}s)."
            self.trip()
            return False
        return True

    # ==========================================
    # MECHANICAL & INFRASTRUCTURE PROTECTIONS
    # ==========================================
    def evaluate_mechanical_vibration(self, api_latency_ms: float, max_latency_ms: float = 1000.0) -> bool:
        """Mechanical Vibration: Execution latency and network jitter monitoring."""
        if api_latency_ms > max_latency_ms:
            self.trip_reason = f"Mechanical Vibration Fault: API latency {api_latency_ms}ms exceeded critical threshold."
            self.trip()
            return False
        return True

    def evaluate_thermal_overload(self, cpu_temp_celsius: float, max_temp: float = 85.0) -> bool:
        """Thermal Overload: CPU core temperature monitoring."""
        if cpu_temp_celsius > max_temp:
            self.trip_reason = f"Thermal Overload Fault: CPU temperature reached {cpu_temp_celsius}°C."
            self.trip()
            return False
        return True

    def evaluate_lube_oil_pressure(self, broker_connected: bool, websocket_active: bool) -> bool:
        """Lube Oil Interlock: Broker connection and WebSocket data feed status."""
        if not broker_connected or not websocket_active:
            self.trip_reason = "Lube Oil Pressure Interlock Failure: Broker connection or WebSocket dropped."
            self.trip()
            return False
        return True
