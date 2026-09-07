"""Revision 3: Protected Engine Supervisor (ANSI Protection Relays)

Wraps any orchestrator in industrial safety constraints without external dependencies.
Self-contained implementation - no SafetyPanel or quantstats required.
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger("Revision3Supervisor")


@dataclass
class ProtectionState:
    """State of the protection relay system."""
    is_tripped: bool = False
    trip_reason: str = ""
    last_trip_zone: str = ""


class Revision3ProtectedSupervisor:
    """
    Self-contained protection relay wrapper.
    Enforces ANSI-inspired constraints without external dependencies.
    """

    def __init__(self, engine_instance: Any, engine_name: str = "Protected"):
        self.engine = engine_instance
        self.engine_name = engine_name
        self.protection_state = ProtectionState()
        self.is_running = True

    def _check_mechanical_integrity(self, telemetry: Dict[str, Any]) -> bool:
        """Check broker connectivity and API latency."""
        broker_ok = telemetry.get("broker_connected", True)
        api_latency_ms = telemetry.get("api_latency_ms", 0.0)

        if not broker_ok:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = "Broker disconnected"
            self.protection_state.last_trip_zone = "ANSI-81"
            return False

        if api_latency_ms > 500:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"API latency {api_latency_ms:.0f}ms exceeds 500ms"
            self.protection_state.last_trip_zone = "ANSI-81"
            return False

        return True

    def _check_thermal_limits(self, telemetry: Dict[str, Any]) -> bool:
        """Monitor CPU and memory pressure."""
        cpu_temp = telemetry.get("cpu_temp_celsius", 45.0)
        memory_pct = telemetry.get("memory_used_pct", 50.0)

        if cpu_temp > 85.0:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"CPU temp {cpu_temp:.1f}°C exceeds 85°C"
            self.protection_state.last_trip_zone = "ANSI-63"
            return False

        if memory_pct > 95.0:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"Memory {memory_pct:.1f}% exceeds 95%"
            self.protection_state.last_trip_zone = "ANSI-63"
            return False

        return True

    def _check_frequency_stability(self, telemetry: Dict[str, Any]) -> bool:
        """Monitor tick rate consistency."""
        tick_interval = telemetry.get("tick_interval_seconds", 1.0)

        if tick_interval > 2.0:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"Tick interval {tick_interval:.1f}s exceeds 2.0s"
            self.protection_state.last_trip_zone = "ANSI-81"
            return False

        return True

    def _check_voltage_limits(self, telemetry: Dict[str, Any], portfolio: Dict[str, Any]) -> bool:
        """Enforce portfolio exposure limits."""
        gross_exposure = portfolio.get("gross_exposure_fraction", 0.0)
        max_allowed = portfolio.get("max_gross_exposure_allowed", 2.0)

        if gross_exposure > max_allowed:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"Gross exposure {gross_exposure:.2f} exceeds {max_allowed:.2f}"
            self.protection_state.last_trip_zone = "ANSI-27/59"
            return False

        realized_pnl = portfolio.get("realized_pnl", 0.0)
        max_loss_allowed = portfolio.get("max_loss_fraction", -0.10)

        if realized_pnl < max_loss_allowed:
            self.protection_state.is_tripped = True
            self.protection_state.trip_reason = f"Loss {realized_pnl:.2%} exceeds max {max_loss_allowed:.2%}"
            self.protection_state.last_trip_zone = "ANSI-27/59"
            return False

        return True

    def guard_orchestrator_run(
        self,
        symbol_bars: Dict[str, Any],
        telemetry: Optional[Dict[str, Any]] = None,
        portfolio_state: Optional[Dict[str, Any]] = None,
        warmup: int = 60,
    ) -> Dict[str, Any]:
        """Execute orchestrator under protection relay supervision."""

        # Default telemetry
        if telemetry is None:
            telemetry = {
                "broker_connected": True,
                "api_latency_ms": 10.0,
                "cpu_temp_celsius": 45.0,
                "memory_used_pct": 50.0,
                "tick_interval_seconds": 1.0,
            }

        # Default portfolio
        if portfolio_state is None:
            portfolio_state = {
                "gross_exposure_fraction": 0.0,
                "max_gross_exposure_allowed": 2.0,
                "realized_pnl": 0.0,
                "max_loss_fraction": -0.10,
            }

        # Pre-flight checks
        if not self._check_mechanical_integrity(telemetry):
            logger.error(f"[{self.engine_name}] MECHANICAL TRIP: {self.protection_state.trip_reason}")
            return {}

        if not self._check_thermal_limits(telemetry):
            logger.error(f"[{self.engine_name}] THERMAL TRIP: {self.protection_state.trip_reason}")
            return {}

        if not self._check_frequency_stability(telemetry):
            logger.error(f"[{self.engine_name}] FREQUENCY TRIP: {self.protection_state.trip_reason}")
            return {}

        if not self._check_voltage_limits(telemetry, portfolio_state):
            logger.error(f"[{self.engine_name}] VOLTAGE TRIP: {self.protection_state.trip_reason}")
            return {}

        # Execute orchestrator
        try:
            report = self.engine.run(symbol_bars, warmup=warmup)
            logger.info(f"[{self.engine_name}] Completed: {len(report.get('trades', []))} trades")
            return report
        except Exception as e:
            logger.error(f"[{self.engine_name}] Execution exception: {str(e)}")
            return {}

    def reset_protection_state(self):
        """Clear trip flags."""
        self.protection_state = ProtectionState()
        self.is_running = True
        logger.info(f"[{self.engine_name}] Protection state reset")

    def get_protection_report(self) -> Dict[str, Any]:
        """Return current protection status."""
        return {
            "is_tripped": self.protection_state.is_tripped,
            "trip_reason": self.protection_state.trip_reason,
            "last_trip_zone": self.protection_state.last_trip_zone,
            "engine_running": self.is_running,
        }
