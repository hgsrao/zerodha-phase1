"""Sealed 48-symbol shared-portfolio replay runner for in-house engine.

Integrates with Revision2PortfolioOrchestrator to produce exact reconciliation
with dataset/config hashes, Gate16 remediation, and cross-session rejection.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.cross_session_rejection import CrossSessionRejectionPolicy
from inhouse_validation.gate16_remediation import Gate16Remediator
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


@dataclass
class SealedReplayReport:
    """Immutable sealed report from a complete 48-symbol replay."""
    timestamp: str
    dataset_hash: str
    config_hash: str
    symbols_loaded: int
    bars_processed: int
    orders_submitted: int
    orders_filled: int
    gate16_breaches: int
    rejected_orders: int
    starting_equity: float
    ending_equity: float
    realized_pnl: float
    entry_costs: float
    exit_costs: float
    daily_pnl_series: Dict[str, float]
    reconciliation_exact: bool
    pending_orders_final: int
    reserved_cash_final: float
    open_positions_final: int
    audit_chain_valid: bool
    status: str  # PASSED, REMEDIATION_REQUIRED, FAILED


class SealedReplayRunner:
    """Orchestrates a sealed 48-symbol replay with full validation."""

    def __init__(
        self,
        manifest_path: str,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 100_000.0,
        nse_data_dir_override: Optional[str] = None,
        month_start: Optional[str] = None,
        month_end: Optional[str] = None,
        warmup_bars: int = 60,
    ):
        self.manifest_loader = ManifestLoader(manifest_path)
        self.dataset_hash = self.manifest_loader.get_dataset_hash()
        self.registry = CanonicalParameterRegistry()
        self.calibration_overrides = calibration_overrides or {}
        errors = self.registry.validate_calibration_payload(self.calibration_overrides)
        if errors:
            raise ValueError(f"invalid calibration overrides: {errors}")

        # Build config
        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(self.calibration_overrides)
        config_json = json.dumps(values, sort_keys=True, default=str)
        self.config_hash = hashlib.sha256(config_json.encode('utf-8')).hexdigest()

        self.starting_equity = starting_equity
        self.nse_data_dir_override = nse_data_dir_override
        self.month_start = month_start
        self.month_end = month_end
        self.warmup_bars = warmup_bars

        # Initialize components
        self.gate16_remediator = Gate16Remediator(
            tolerance_pct=0.15,  # V3 immutable threshold
        )
        self.cross_session_policy = CrossSessionRejectionPolicy(
            allow_cross_session=False
        )

        self.orchestrator = None
        self.completed_trades: List[Dict[str, Any]] = []
        self.daily_pnl_series: Dict[str, float] = {}
        self.bars_processed = 0

    def run(self) -> SealedReplayReport:
        """
        Execute the complete 48-symbol sealed replay.

        Calls Revision2PortfolioOrchestrator.run() with chronological merged bars,
        wires Gate16 and cross-session validation, and produces sealed report.

        Returns:
            SealedReplayReport with full metrics and audit trail.

        Raises:
            RuntimeError: If orchestrator.run() fails or reconciliation is incomplete.
        """
        try:
            # Load and verify all 48 files
            print("Loading 48-symbol manifest-verified dataset...")
            all_data = self.manifest_loader.verify_and_load(
                use_nse_data_dir=True, data_dir_override=self.nse_data_dir_override,
            )
            if (self.month_start is None) != (self.month_end is None):
                raise ValueError("month_start and month_end must be supplied together")
            if self.month_start is not None:
                start = pd.Timestamp(self.month_start, tz="UTC")
                end = pd.Timestamp(self.month_end, tz="UTC") + pd.Timedelta(days=1)
                windowed = {}
                for symbol, frame in all_data.items():
                    columns = {name.lower(): name for name in frame.columns}
                    if "timestamp" not in columns:
                        raise RuntimeError(f"{symbol}: timestamp column missing")
                    timestamps = pd.to_datetime(frame[columns["timestamp"]], utc=True)
                    live = frame.loc[(timestamps >= start) & (timestamps < end)]
                    history = frame.loc[timestamps < start]
                    if len(live) == 0:
                        raise RuntimeError(f"{symbol}: no bars in sealed period")
                    if len(history) < self.warmup_bars:
                        raise RuntimeError(f"{symbol}: requires {self.warmup_bars} warmup bars")
                    windowed[symbol] = pd.concat([history.iloc[-self.warmup_bars:], live], ignore_index=True)
                all_data = windowed
            symbols = list(all_data.keys())
            print(f"✓ Loaded {len(symbols)} symbols")

            # Initialize orchestrator with safety components
            print("Initializing Revision2PortfolioOrchestrator...")
            self.orchestrator = Revision2PortfolioOrchestrator(
                symbols=symbols,
                registry=self.registry,
                calibration_overrides=self.calibration_overrides,
                starting_equity=self.starting_equity,
            )

            # Wire Gate16 remediator and cross-session policy into orchestrator
            # (These are used during run() to validate each order)
            self.orchestrator.gate16_remediator = self.gate16_remediator
            self.orchestrator.cross_session_policy = self.cross_session_policy

            # CRITICAL: Call orchestrator.run() with all data
            print("Starting chronological orchestration...")
            result = self.orchestrator.run(all_data, warmup=self.warmup_bars)

            # Extract metrics from orchestrator state
            self.bars_processed = sum(len(df) for df in all_data.values())
            self.completed_trades = self.orchestrator.completed_trades

            # Calculate daily P&L from completed trades
            for trade in self.completed_trades:
                exit_date = pd.Timestamp(trade.get('exit_timestamp', '2026-08-01')).date().isoformat()
                net_pnl = float(trade.get('net_pnl', 0.0))
                self.daily_pnl_series[exit_date] = self.daily_pnl_series.get(exit_date, 0.0) + net_pnl

            # Calculate costs from trades
            total_entry_costs = sum(float(trade.get('entry_cost', 0.0)) for trade in self.completed_trades)
            total_exit_costs = sum(float(trade.get('exit_cost', 0.0)) for trade in self.completed_trades)
            net_pnl = sum(self.daily_pnl_series.values())
            gross_pnl = sum(float(trade.get('pnl', 0.0)) for trade in self.completed_trades)

            # Reconciliation checks (MUST ALL PASS)
            reconciliation_exact = (
                len(self.orchestrator.open_trades) == 0 and
                len(self.orchestrator.pending_entries) == 0 and
                abs(self.orchestrator.broker.realized_pnl - gross_pnl) < 0.01 and
                abs(net_pnl - (gross_pnl - total_entry_costs - total_exit_costs)) < 0.01
            )

            if not reconciliation_exact:
                raise RuntimeError(
                    f"Reconciliation failed: "
                    f"open_trades={len(self.orchestrator.open_trades)}, "
                    f"pending={len(self.orchestrator.pending_entries)}, "
                    f"pnl_mismatch={abs(self.orchestrator.broker.realized_pnl - sum(self.daily_pnl_series.values()))}"
                )

            # Verify audit chains
            audit_chain_valid = self.gate16_remediator.verify_chain()
            if not audit_chain_valid:
                raise RuntimeError("Gate16 audit chain verification failed")

            # Determine final status
            if len(self.completed_trades) == 0:
                final_status = "NO_EXECUTION"  # No trades executed
            elif self.gate16_remediator.violations and not reconciliation_exact:
                final_status = "FAILED"  # Breach without recovery
            elif self.gate16_remediator.violations and reconciliation_exact:
                final_status = "REMEDIATION_REQUIRED"  # Breach + recovered
            elif reconciliation_exact:
                final_status = "PASSED"  # Clean run
            else:
                final_status = "FAILED"

            # Build immutable report
            report = SealedReplayReport(
                timestamp=datetime.now(timezone.utc).isoformat(),
                dataset_hash=self.dataset_hash,
                config_hash=self.config_hash,
                symbols_loaded=len(symbols),
                bars_processed=self.bars_processed,
                orders_submitted=int(result.get('orders_submitted', 0)),
                orders_filled=int(result.get('fills', 0)),
                gate16_breaches=len(self.gate16_remediator.violations),
                rejected_orders=(int(result.get('safety_rejections', 0)) +
                                 int(result.get('gates_rejected', 0)) +
                                 int(result.get('cross_session_rejections', 0))),
                starting_equity=self.starting_equity,
                ending_equity=self.starting_equity + net_pnl,
                realized_pnl=net_pnl,
                entry_costs=total_entry_costs,
                exit_costs=total_exit_costs,
                daily_pnl_series=self.daily_pnl_series,
                reconciliation_exact=reconciliation_exact,
                pending_orders_final=len(self.orchestrator.pending_entries),
                reserved_cash_final=0.0,  # TODO: track reservations in broker
                open_positions_final=len(self.orchestrator.open_trades),
                audit_chain_valid=audit_chain_valid,
                status=final_status,
            )

            return report

        except Exception as e:
            print(f"❌ Replay failed: {e}")
            raise

    def save_report(self, report: SealedReplayReport, output_path: str) -> None:
        """Save sealed report as JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        report_dict = asdict(report)
        with open(path, 'w') as f:
            json.dump(report_dict, f, indent=2, default=str)

        print(f"✓ Report saved: {output_path}")
        print(f"  Status: {report.status}")
        print(f"  Dataset hash: {report.dataset_hash[:16]}...")
        print(f"  Config hash: {report.config_hash[:16]}...")
        print(f"  Symbols: {report.symbols_loaded}")
        print(f"  Bars: {report.bars_processed}")
        print(f"  Orders: {report.orders_submitted}")
        print(f"  Gate16 breaches: {report.gate16_breaches}")
        print(f"  Reconciliation: {'EXACT' if report.reconciliation_exact else 'INCOMPLETE'}")
