"""Revision 3 In-House Engine: Vanilla Regime + ANSI Protection Relay

Wraps Revision2PortfolioOrchestrator in Revision3ProtectedSupervisor.
Combines:
- Vanilla volatility-only regime detection (no HMM)
- ANSI 12-zone protection relay (mechanical, thermal, frequency, voltage, differential, overcurrent)
- Simple concentration-cap position sizing

Used in parallel A/B test: Revision3InHouse vs Revision3External vs baseline (unwrapped).
"""

from __future__ import annotations
from typing import Dict, Any, Optional

from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
from revision3.integration_supervisor import Revision3ProtectedSupervisor


class Revision3PortfolioOrchestrator:
    """
    Revision 3 wrapper for in-house engine (Vanilla + Protection Relay).

    Delegates all logic to Revision2PortfolioOrchestrator, but guards execution
    through ProtectedSupervisor which enforces ANSI protection relay constraints.
    """

    def __init__(self, symbols: list, registry: Any, starting_equity: float = 1_000_000.0):
        """Initialize with wrapped in-house orchestrator and protection relay."""
        # Instantiate base engine
        self._base_engine = Revision2PortfolioOrchestrator(
            symbols, registry, starting_equity=starting_equity
        )

        # Wrap in protection relay
        self._supervisor = Revision3ProtectedSupervisor(
            self._base_engine,
            engine_name="Revision3InHouseEngine (Vanilla + Relay)"
        )

    def run(
        self,
        symbol_bars: Dict[str, Any],
        warmup: int = 60,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute bar-by-bar backtest under protection relay supervision.

        Args:
            symbol_bars: {symbol: DataFrame} of OHLCV data
            warmup: Bars to skip before trading begins
            telemetry: Optional system telemetry (latency, memory, etc.) for relay checks

        Returns:
            Orchestrator report with trades, P&L, metrics (or empty dict if relay tripped)
        """
        # Default telemetry if not provided
        if telemetry is None:
            telemetry = {
                "broker_connected": True,
                "api_latency_ms": 10.0,
                "cpu_temp_celsius": 45.0,
                "memory_used_pct": 50.0,
                "tick_interval_seconds": 1.0,
            }

        # Default portfolio state for pre-execution checks
        portfolio_state = {
            "gross_exposure_fraction": 0.0,
            "max_gross_exposure_allowed": 2.0,
            "realized_pnl": 0.0,
            "max_loss_fraction": -0.10,
            "positions": {},
            "max_qty_per_symbol": {s: 1000 for s in symbol_bars.keys()},
        }

        # Guard execution through relay
        return self._supervisor.guard_orchestrator_run(
            symbol_bars, telemetry, portfolio_state, warmup=warmup
        )

    def get_protection_status(self) -> Dict[str, Any]:
        """Return current ANSI protection relay status."""
        return self._supervisor.get_protection_report()

    def reset_protection(self):
        """Clear relay trip flags after manual inspection."""
        self._supervisor.reset_protection_state()
