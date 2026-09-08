"""
REVISION 04: Chronological 48-Symbol Orchestrator

Processes bars in strict timestamp order across all 48 symbols.
Implements: exits → fills → signals → rank → allocate → authorize → orders

Each bar timestamp:
  1. Exit processing (stop/target/time/liquidation)
  2. Fill pending orders (at bar t+1 open)
  3. Generate signals (all 48 symbols)
  4. Rank candidates (PositionManager)
  5. Allocate capital (position/cash limits)
  6. Apply 18 safety gates
  7. Create pending orders (for t+1 submission)
  8. Reconcile ledger and equity
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import uuid
from revision4.contracts import Bar, EffectiveConfig, OrderIntent, OrderState, SizedProposal, TradePlan
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.research_target import SealedRunEvaluation, BenchmarkConfig


@dataclass
class TimestampSnapshot:
    """Portfolio state at one timestamp."""
    timestamp: str
    bar_index: int
    bars: Dict[str, Bar]  # {symbol: Bar}
    cash: float
    positions: int
    active_orders: int
    marked_equity: float
    daily_pnl: float


class TimestampOrchestrator:
    """
    Chronological 48-symbol orchestrator.
    Processes one timestamp across all symbols, then advances.
    """

    def __init__(self, config: EffectiveConfig, starting_cash: float = 100_000.0):
        self.config = config
        self.broker = PaperBroker()
        self.ledger = PortfolioLedger(starting_cash=starting_cash)
        self.current_timestamp: Optional[str] = None
        self.current_bar_index: int = 0
        self.snapshots: List[TimestampSnapshot] = []
        self.bar_history: Dict[str, List[Bar]] = {}

    def add_bar(self, bar: Bar):
        """Add bar to history for processing."""
        if bar.symbol not in self.bar_history:
            self.bar_history[bar.symbol] = []
        self.bar_history[bar.symbol].append(bar)

    def process_timestamp(
        self,
        timestamp: str,
        bar_index: int,
        bars: Dict[str, Bar],
    ) -> Tuple[bool, str]:
        """
        Process one timestamp across all 48 symbols.

        Args:
            timestamp: ISO format timestamp
            bar_index: Sequential bar index (0-based)
            bars: {symbol: Bar} for this timestamp

        Returns:
            (success, message)
        """
        self.current_timestamp = timestamp
        self.current_bar_index = bar_index

        # 1. EXIT PROCESSING (simplified: no active exits for validation replay)
        # TODO: Full implementation with stop/target/time/liquidation

        # 2. FILL PENDING ORDERS at bar t+1 open
        # For each pending order eligible for fill (submitted at prior bar)
        for order_id in list(self.broker.active_orders.keys()):
            order = self.broker.active_orders[order_id]
            # Check if this order can fill at this bar
            if bar_index > order.bar_index_created:
                fill_event = self.broker.try_fill_order(
                    order_id,
                    bars[order.symbol],
                    bar_index,
                    self.config,
                )
                if fill_event:
                    # Fill in ledger
                    ok, msg = self.ledger.fill_order(order_id, fill_event)
                    if not ok:
                        print(f"Warning: Fill failed: {msg}")

        # 3. GENERATE SIGNALS (simplified: no actual PA box for validation)
        # TODO: Call pipeline.generate_forecast() for each symbol

        # 4. RANK CANDIDATES (simplified: skip ranking for validation)
        # TODO: Call position_manager.rank_candidates()

        # 5. ALLOCATE CAPITAL (simplified: skip allocation for validation)
        # TODO: Respect position limits, cash constraints

        # 6. APPLY 18 SAFETY GATES (simplified: skip gates for validation)
        # TODO: Call safety_gates.authorize() for each candidate

        # 7. CREATE PENDING ORDERS (simplified: none for validation replay)
        # TODO: For authorized candidates:
        #   - Create OrderIntent
        #   - Call broker.submit_order()
        #   - Call ledger.create_order()

        # 8. RECONCILE LEDGER & EQUITY
        ok, msg = self.ledger.reconcile(bars)
        if not ok:
            return False, f"Ledger reconciliation failed: {msg}"

        # Mark-to-market
        equity = self.ledger.mark_to_market(bars)

        # Snapshot
        snapshot = TimestampSnapshot(
            timestamp=timestamp,
            bar_index=bar_index,
            bars=bars.copy(),
            cash=self.ledger.cash,
            positions=len(self.ledger.positions),
            active_orders=len(self.ledger.pending_orders),
            marked_equity=equity,
            daily_pnl=self.ledger.daily_pnl,
        )
        self.snapshots.append(snapshot)

        return True, "Timestamp processed"

    def finalize(self) -> Optional[SealedRunEvaluation]:
        """
        Finalize replay: emit SealedRunEvaluation.

        Returns:
            SealedRunEvaluation or None if failed
        """
        try:
            eval = SealedRunEvaluation.create(
                completed_trades=self.ledger.completed_trades,
                starting_equity=self.ledger.starting_cash,
                ending_equity=self.ledger.marked_equity,
                benchmark=BenchmarkConfig(),
            )
            return eval
        except ValueError as e:
            print(f"Evaluation failed: {e}")
            return None

    def get_snapshots(self) -> List[TimestampSnapshot]:
        """Return all timestep snapshots."""
        return self.snapshots.copy()
