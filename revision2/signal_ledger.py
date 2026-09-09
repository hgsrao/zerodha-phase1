"""Per-signal ledger for complete audit trail and debugging.

Captures every signal from PA through ID, MPC, SafetyGates, and Execution.
Purpose: Enable deep inspection of decisions without needing to trace through box internals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum


class SignalStage(Enum):
    """Lifecycle stages for each signal."""
    PA_GENERATED = "PA_GENERATED"
    ID_EVALUATED = "ID_EVALUATED"
    MPC_PLANNED = "MPC_PLANNED"
    SAFETY_GATES_PRESIZING = "SAFETY_GATES_PRESIZING"
    SAFETY_GATES_POSTSIZING = "SAFETY_GATES_POSTSIZING"
    POSITION_MANAGER_SIZED = "POSITION_MANAGER_SIZED"
    ORDER_QUEUED = "ORDER_QUEUED"
    ORDER_FILLED = "ORDER_FILLED"
    TRADE_COMPLETED = "TRADE_COMPLETED"


@dataclass
class SignalLedgerEntry:
    """Single entry for one signal through its lifecycle."""
    # Identification
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    symbol: str = ""
    bar_index: int = 0
    bar_timestamp: str = ""

    # PA Signal Stage
    pa_direction: Optional[int] = None  # +1 BUY, -1 SELL
    pa_confidence: Optional[float] = None  # 0.0-1.0
    pa_entry_threshold: Optional[float] = None
    pa_parameters: Dict[str, Any] = field(default_factory=dict)

    # ID Decision Stage
    id_approved: Optional[bool] = None
    id_decision_reason: str = ""
    id_confidence: Optional[float] = None
    id_timing_quality: Optional[float] = None
    id_parameters: Dict[str, Any] = field(default_factory=dict)

    # Market Context at Decision
    atr: Optional[float] = None
    current_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    # MPC Plan Stage
    mpc_entry_price: Optional[float] = None
    mpc_stop_price: Optional[float] = None
    mpc_target_price: Optional[float] = None
    mpc_projected_profit_per_share: Optional[float] = None
    mpc_stop_distance: Optional[float] = None
    mpc_target_distance: Optional[float] = None
    mpc_pid_entry_adjustment: Optional[float] = None
    mpc_pid_exit_adjustment: Optional[float] = None
    mpc_entry_timing_multiplier: Optional[float] = None
    mpc_plan_rejected: bool = False
    mpc_rejection_reason: str = ""
    mpc_parameters: Dict[str, Any] = field(default_factory=dict)

    # Pre-Sizing Safety Gates
    safety_pre_approved: Optional[bool] = None
    safety_pre_reason: str = ""
    safety_pre_size_multiplier: Optional[float] = None

    # Position Manager Sizing
    pm_quantity: Optional[int] = None
    pm_available_equity: Optional[float] = None
    pm_size_multiplier_used: Optional[float] = None
    pm_capital_allocation: Optional[float] = None

    # Post-Sizing Safety Gates
    safety_post_approved: Optional[bool] = None
    safety_post_reason: str = ""
    safety_post_estimated_entry_cost: Optional[float] = None
    safety_post_estimated_exit_cost: Optional[float] = None
    safety_post_total_round_trip_cost: Optional[float] = None
    safety_post_required_net_profit: Optional[float] = None
    safety_post_target_profit_total: Optional[float] = None

    # Execution
    order_id: Optional[str] = None
    order_status: str = ""  # queued, filled, partial, rejected, cancelled
    order_fill_quantity: Optional[int] = None
    order_fill_price: Optional[float] = None
    order_slippage: Optional[float] = None

    # Trade Completion
    trade_exit_price: Optional[float] = None
    trade_exit_timestamp: Optional[str] = None
    trade_pnl: Optional[float] = None
    trade_pnl_percent: Optional[float] = None

    # Rejection Chain
    final_status: str = ""  # approved, rejected, cancelled
    rejection_stage: str = ""  # Which box rejected it
    rejection_reason: str = ""


class PerSymbolLedger:
    """Accumulator for all signals on a single symbol."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.entries: List[SignalLedgerEntry] = []

    def add_entry(self, entry: SignalLedgerEntry) -> None:
        """Add a new signal entry."""
        entry.symbol = self.symbol
        self.entries.append(entry)

    def get_stats(self) -> Dict[str, Any]:
        """Get summary statistics for this symbol."""
        total = len(self.entries)
        approved = sum(1 for e in self.entries if e.final_status == "approved")
        completed = sum(1 for e in self.entries if e.final_status == "traded")

        rejection_reasons = {}
        for entry in self.entries:
            if entry.final_status == "rejected" and entry.rejection_reason:
                key = entry.rejection_reason
                rejection_reasons[key] = rejection_reasons.get(key, 0) + 1

        return {
            "symbol": self.symbol,
            "total_signals": total,
            "pa_generated": sum(1 for e in self.entries if e.pa_confidence is not None),
            "id_approved": sum(1 for e in self.entries if e.id_approved is True),
            "mpc_plans": sum(1 for e in self.entries if e.mpc_entry_price is not None),
            "safety_approved": sum(1 for e in self.entries if e.safety_post_approved is True),
            "orders_queued": sum(1 for e in self.entries if e.order_id is not None),
            "orders_filled": sum(1 for e in self.entries if e.order_status == "filled"),
            "trades_completed": completed,
            "approvals": approved,
            "rejections": total - approved,
            "rejection_reasons": rejection_reasons,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "total_entries": len(self.entries),
            "entries": [asdict(e) for e in self.entries],
            "stats": self.get_stats(),
        }


class GlobalLedger:
    """Global ledger tracking all signals across all symbols."""

    def __init__(self):
        self.per_symbol: Dict[str, PerSymbolLedger] = {}

    def get_symbol_ledger(self, symbol: str) -> PerSymbolLedger:
        """Get or create ledger for a symbol."""
        if symbol not in self.per_symbol:
            self.per_symbol[symbol] = PerSymbolLedger(symbol)
        return self.per_symbol[symbol]

    def add_entry(self, entry: SignalLedgerEntry) -> None:
        """Add entry to appropriate symbol ledger."""
        ledger = self.get_symbol_ledger(entry.symbol)
        ledger.add_entry(entry)

    def get_stats(self) -> Dict[str, Any]:
        """Get portfolio-level statistics."""
        all_stats = []
        total_approved = 0
        total_rejected = 0

        for symbol, ledger in sorted(self.per_symbol.items()):
            stats = ledger.get_stats()
            all_stats.append(stats)
            total_approved += stats["approvals"]
            total_rejected += stats["rejections"]

        return {
            "total_symbols": len(self.per_symbol),
            "total_signals": sum(s["total_signals"] for s in all_stats),
            "total_approvals": total_approved,
            "total_rejections": total_rejected,
            "per_symbol": all_stats,
        }

    def save_to_file(self, filepath: str) -> None:
        """Save complete ledger to JSON file."""
        output = {
            "timestamp": datetime.utcnow().isoformat(),
            "stats": self.get_stats(),
            "per_symbol": {symbol: ledger.to_dict() for symbol, ledger in self.per_symbol.items()},
        }
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, default=str)
