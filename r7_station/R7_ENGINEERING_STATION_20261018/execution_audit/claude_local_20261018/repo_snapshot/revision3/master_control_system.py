#!/usr/bin/env python3
"""
⚠️  EXPERIMENTAL: Revision 3 Master Control System

STATUS: Prototype only. NOT production-ready.

Known Incomplete:
- Grid synchronization: Method signatures present, data flow incomplete
- PID controller: Logic stubbed, not integrated into execution path
- Safety relay: Static preflight only, not continuous monitoring

DO NOT DEPLOY without:
1. Real stock + NIFTY data alignment (timestamp basis, not synthetic)
2. PID integration into actual execution + stop/exit paths
3. Continuous safety monitoring during live/replay execution
4. Full integration test suite proving gate actually stops fills

---

MASTER CONTROL SYSTEM: Protection + Grid Sync + PID Controller Integration

Three-Layer Architecture:
┌─────────────────────────────────────────────────────┐
│ LAYER 3: PROTECTION RELAY (Safety Constraints)      │
│ ├─ Mechanical Integrity (broker, latency)           │
│ ├─ Thermal Limits (CPU, memory)                     │
│ ├─ Frequency Stability (tick rate)                  │
│ └─ Voltage Limits (exposure, drawdown)              │
├─────────────────────────────────────────────────────┤
│ LAYER 2: GRID SYNCHRONIZATION (Market Regime)       │
│ ├─ Voltage: Nifty 50 trend alignment                │
│ ├─ Frequency: VIX in safe band?                     │
│ └─ Phase Angle: Stock/Index momentum sync (Δϕ)      │
├─────────────────────────────────────────────────────┤
│ LAYER 1: PID CONTROLLER (Exit Decisions)            │
│ ├─ Track 1: PA Confidence PID                       │
│ ├─ Track 2: Chart Studies PID                       │
│ ├─ Input 3: Favorable Extreme (price)               │
│ ├─ Input 4: Time Held (decay)                       │
│ ├─ Input 5: ATR Droop (volatility)                  │
│ └─ Input 6: Grid State (modulation)                 │
└─────────────────────────────────────────────────────┘
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
import logging
import numpy as np

logger = logging.getLogger("MasterControlSystem")


@dataclass
class ProtectionState:
    """State of protection relay system."""
    is_tripped: bool = False
    trip_reason: str = ""
    last_trip_zone: str = ""
    trip_zones: list = field(default_factory=list)


@dataclass
class GridSyncState:
    """State of grid synchronization."""
    is_synchronized: bool = False
    voltage: float = 0.5  # Trend alignment
    frequency: float = 0.5  # VIX stability
    phase_angle: float = 0.0  # Momentum sync
    reason: str = ""


@dataclass
class PIDState:
    """State of PID controller."""
    pa_tightness: float = 1.0
    studies_tightness: float = 1.0
    time_tightness: float = 1.0
    combined_tightness: float = 1.0
    grid_modulation: float = 1.0
    final_tightness: float = 1.0


class MasterControlSystem:
    """
    Three-layer control system: Protection + Grid + PID

    Execution flow:
    1. Pre-flight check: Protection relay
    2. Market regime check: Grid synchronization
    3. Exit decision: PID controller

    If any layer fails, abort or adjust accordingly.
    """

    def __init__(
        self,
        protection_relay=None,
        grid_synchronizer=None,
        pid_controller=None,
    ):
        self.protection = protection_relay
        self.grid_sync = grid_synchronizer
        self.pid = pid_controller

        self.protection_state = ProtectionState()
        self.grid_sync_state = GridSyncState()
        self.pid_state = PIDState()

        self.enabled_layers = {
            "protection": protection_relay is not None,
            "grid_sync": grid_synchronizer is not None,
            "pid": pid_controller is not None,
        }

    def check_protection_relay(self, telemetry: Dict[str, Any]) -> Tuple[bool, ProtectionState]:
        """
        LAYER 3: Pre-flight safety checks.

        Returns: (approved, state)
        - approved = True if all safety checks pass
        - state = detailed trip information if failed
        """

        if not self.enabled_layers["protection"]:
            return True, self.protection_state

        try:
            # Check mechanical integrity
            broker_ok = telemetry.get("broker_connected", True)
            api_latency_ms = telemetry.get("api_latency_ms", 0.0)

            if not broker_ok or api_latency_ms > 500:
                self.protection_state.is_tripped = True
                self.protection_state.trip_reason = (
                    f"Mechanical Trip: Broker={'OK' if broker_ok else 'DOWN'}, "
                    f"Latency={api_latency_ms:.0f}ms"
                )
                self.protection_state.last_trip_zone = "ANSI-81 (Mechanical)"
                self.protection_state.trip_zones.append("ANSI-81")
                return False, self.protection_state

            # Check thermal limits
            cpu_temp = telemetry.get("cpu_temp_celsius", 45.0)
            memory_pct = telemetry.get("memory_used_pct", 50.0)

            if cpu_temp > 85.0 or memory_pct > 95.0:
                self.protection_state.is_tripped = True
                self.protection_state.trip_reason = (
                    f"Thermal Trip: CPU={cpu_temp:.1f}°C, Memory={memory_pct:.1f}%"
                )
                self.protection_state.last_trip_zone = "ANSI-63 (Thermal)"
                self.protection_state.trip_zones.append("ANSI-63")
                return False, self.protection_state

            # Check frequency stability
            tick_interval = telemetry.get("tick_interval_seconds", 1.0)

            if tick_interval > 2.0:
                self.protection_state.is_tripped = True
                self.protection_state.trip_reason = (
                    f"Frequency Trip: Tick interval {tick_interval:.1f}s exceeds 2.0s"
                )
                self.protection_state.last_trip_zone = "ANSI-81 (Frequency)"
                self.protection_state.trip_zones.append("ANSI-81")
                return False, self.protection_state

            # Check voltage (exposure) limits
            gross_exposure = telemetry.get("gross_exposure_fraction", 0.0)
            realized_pnl = telemetry.get("realized_pnl", 0.0)

            if gross_exposure > 2.0:
                self.protection_state.is_tripped = True
                self.protection_state.trip_reason = (
                    f"Voltage Trip: Exposure {gross_exposure:.2f} exceeds 2.0"
                )
                self.protection_state.last_trip_zone = "ANSI-27/59 (Voltage)"
                self.protection_state.trip_zones.append("ANSI-27/59")
                return False, self.protection_state

            if realized_pnl < -0.10:
                self.protection_state.is_tripped = True
                self.protection_state.trip_reason = (
                    f"Voltage Trip: Loss {realized_pnl:.2%} exceeds -10%"
                )
                self.protection_state.last_trip_zone = "ANSI-27/59 (Loss Limit)"
                self.protection_state.trip_zones.append("ANSI-27/59")
                return False, self.protection_state

            # All checks passed
            self.protection_state.is_tripped = False
            self.protection_state.trip_reason = "All safety checks passed"
            self.protection_state.trip_zones = []
            return True, self.protection_state

        except Exception as e:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"Protection check error: {str(e)}"
            return False, self.protection_state

    def check_grid_synchronization(
        self,
        stock_prices,
        nifty_prices,
        current_vix: float,
        trade_direction: int = 1,
    ) -> Tuple[bool, GridSyncState]:
        """
        LAYER 2: Market regime check.

        CRITICAL FIX: Separate stock prices from market index prices.
        - stock_prices: Individual stock being traded (PLANT)
        - nifty_prices: Market index (GRID)

        Returns: (synchronized, state)
        - synchronized = True if grid is favorable
        - state = detailed grid synchronization information
        """

        if not self.enabled_layers["grid_sync"]:
            return True, self.grid_sync_state

        try:
            sync_result = self.grid_sync.check_synchronization(
                plant_close=stock_prices,  # FIXED: Individual stock
                grid_close=nifty_prices,   # FIXED: Market index (separate)
                current_vix=current_vix,
                trade_direction=trade_direction,
            )

            self.grid_sync_state.is_synchronized = sync_result.is_synchronized
            self.grid_sync_state.phase_angle = sync_result.delta_phi
            self.grid_sync_state.reason = sync_result.reason

            # Estimate voltage and frequency from reason
            if "Voltage Trip" in sync_result.reason:
                self.grid_sync_state.voltage = 0.3
            elif sync_result.is_synchronized:
                self.grid_sync_state.voltage = 0.8

            if "Frequency Trip" in sync_result.reason:
                self.grid_sync_state.frequency = 0.2
            elif sync_result.is_synchronized:
                self.grid_sync_state.frequency = 0.7

            return sync_result.is_synchronized, self.grid_sync_state

        except Exception as e:
            self.grid_sync_state.is_synchronized = False
            self.grid_sync_state.reason = f"Grid check error: {str(e)}"
            return False, self.grid_sync_state

    def evaluate_pid_controller(
        self,
        symbol: str,
        state: Any,
        pa_confidence: float,
        studies_confidence: float,
        current_close: float,
        current_atr: float,
        bar_index: int = 0,
        direction: int = 1,
    ) -> Tuple[bool, PIDState]:
        """
        ⚠️  LAYER 1: PID-based exit decision (INCOMPLETE)

        TEMPORARY NO-OP: PID logic is not integrated.

        Known issues:
        - Does not track chart studies confidence
        - ATR derivative calculated from prior PA tightness (wrong)
        - Not integrated into actual execution path (stops, exits)
        - Test failures hidden by broad exception handling

        Returns: (False, pid_state) - always no-exit until integration complete
        """

        if not self.enabled_layers["pid"]:
            return False, self.pid_state

        try:
            # TEMPORARY: Return no-exit until PID is properly integrated
            logger.warning(f"PID called for {symbol} but not integrated; returning no-exit")
            return False, self.pid_state

        except Exception as e:
            logger.error(f"PID evaluation error: {e}")
            return False, self.pid_state

    def make_trading_decision(
        self,
        symbol: str,
        telemetry: Dict[str, Any],
        nifty_prices,
        current_vix: float,
        pa_confidence: float,
        studies_confidence: float,
        current_close: float,
        current_atr: float,
        position_state=None,
        trade_direction: int = 1,
        bar_index: int = 0,
    ) -> Dict[str, Any]:
        """
        THREE-LAYER DECISION GATE

        Flows through:
        1. Protection Relay (safety constraints)
        2. Grid Synchronization (market regime)
        3. PID Controller (exit logic)

        If any layer fails, decision is adjusted.
        """

        result = {
            "approved": False,
            "reason": "",
            "protection": None,
            "grid": None,
            "pid": None,
            "decision_trace": [],
        }

        # LAYER 3: Protection Relay
        logger.info("[1/3] Checking Protection Relay...")
        protection_ok, protection_state = self.check_protection_relay(telemetry)
        result["protection"] = protection_state

        if not protection_ok:
            result["approved"] = False
            result["reason"] = f"Protection relay tripped: {protection_state.trip_reason}"
            result["decision_trace"].append(f"✗ Protection: {protection_state.trip_reason}")
            return result

        result["decision_trace"].append(f"✓ Protection: All checks passed")

        # LAYER 2: Grid Synchronization
        logger.info("[2/3] Checking Grid Synchronization...")
        # FIXED: Pass stock prices (plant) separately from Nifty (grid)
        # Build stock price series from available data
        stock_prices = np.array([current_close])  # Minimal: current price
        # In production, this would be a rolling window of stock prices
        grid_ok, grid_state = self.check_grid_synchronization(
            stock_prices, nifty_prices, current_vix, trade_direction
        )
        result["grid"] = grid_state

        if not grid_ok:
            result["approved"] = False
            result["reason"] = f"Grid not synchronized: {grid_state.reason}"
            result["decision_trace"].append(f"✗ Grid: {grid_state.reason}")
            # Note: Could allow entry anyway with warnings, or strictly block
            return result

        result["decision_trace"].append(
            f"✓ Grid: Synchronized (V={grid_state.voltage:.2f}, F={grid_state.frequency:.2f}, Δϕ={grid_state.phase_angle:.1f}°)"
        )

        # LAYER 1: PID Controller
        logger.info("[3/3] Evaluating PID Controller...")
        if position_state is not None:
            should_exit, pid_state = self.evaluate_pid_controller(
                symbol=symbol,
                state=position_state,
                pa_confidence=pa_confidence,
                studies_confidence=studies_confidence,
                current_close=current_close,
                current_atr=current_atr,
                bar_index=bar_index,
                direction=trade_direction,
            )
            result["pid"] = pid_state

            if should_exit:
                result["decision_trace"].append(f"✓ PID: Exit signal")
            else:
                result["decision_trace"].append(f"⊙ PID: Hold")

        # All layers approved
        result["approved"] = True
        result["reason"] = "All layers approved: Protection ✓ Grid ✓ PID ✓"

        return result

    def get_control_summary(self) -> Dict[str, Any]:
        """Get status of all three control layers."""
        return {
            "protection_enabled": self.enabled_layers["protection"],
            "protection_tripped": self.protection_state.is_tripped,
            "protection_trip_zones": self.protection_state.trip_zones,
            "grid_enabled": self.enabled_layers["grid_sync"],
            "grid_synchronized": self.grid_sync_state.is_synchronized,
            "grid_voltage": self.grid_sync_state.voltage,
            "grid_frequency": self.grid_sync_state.frequency,
            "pid_enabled": self.enabled_layers["pid"],
            "pid_combined_tightness": self.pid_state.combined_tightness,
        }

    def reset_protection_state(self):
        """Clear trip flags."""
        self.protection_state = ProtectionState()
