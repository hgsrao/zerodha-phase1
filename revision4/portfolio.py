"""
REVISION 04 (Proper): Shared Portfolio Ledger

One ₹1,00,000 pool for all 48 symbols.
Single source of truth. Immutable state snapshots.
Full reconciliation after every bar.
"""

from typing import Dict, Tuple, Optional
from dataclasses import dataclass, field
from revision4.contracts import (
    Position, PortfolioSnapshot, OrderIntent, FillEvent,
    ExitEvent, ExitReason, Bar
)
from revision4.research_target import CompletedTrade


class PortfolioLedger:
    """
    Shared ₹1,00,000 portfolio for all 48 symbols.
    Maintains all state: cash, positions, orders, P&L.
    """

    def __init__(self, starting_cash: float = 100_000.0):
        self.starting_cash = starting_cash

        # Current state (mutable during replay)
        self.cash = starting_cash
        self.reserved_cash = 0.0  # Committed to pending orders
        self.positions: Dict[str, Position] = {}  # {symbol: Position}
        self.pending_orders: Dict[str, OrderIntent] = {}  # {order_id: OrderIntent}

        self.realized_pnl = 0.0
        self.total_costs = 0.0
        self.completed_trades: list[CompletedTrade] = []

        # Daily tracking
        self.daily_pnl = 0.0
        self.daily_start_cash = starting_cash

        # Equity tracking
        self.marked_equity = starting_cash
        self.peak_equity = starting_cash
        self.max_drawdown = 0.0

    def create_order(self, order_id: str, order: OrderIntent) -> Tuple[bool, str]:
        """
        Attempt to create pending order.
        Checks: duplicate orders, cash availability, position limits.
        """
        # Check for duplicate order
        if order_id in self.pending_orders:
            return False, "Duplicate order ID"

        # Check position limit
        existing_positions = len(self.positions)
        pending_same_symbol = len([o for o in self.pending_orders.values() if o.symbol == order.symbol])

        if existing_positions + pending_same_symbol >= 5:
            return False, "Position limit reached"

        # Reserve enough cash for either direction.  This is intentionally
        # conservative for shorts until a broker-specific margin model is
        # introduced: a short may not create leverage merely because it will
        # later receive sale proceeds.
        cost_estimate = order.quantity * order.proposal.plan.entry_price + order.proposal.cost_estimate
        if self.cash - self.reserved_cash < cost_estimate:
            return False, f"Insufficient cash: need {cost_estimate:.0f}, have {self.cash - self.reserved_cash:.0f}"

        # Create order
        self.pending_orders[order_id] = order
        self.reserved_cash += order.quantity * order.proposal.plan.entry_price
        return True, "Order created"

    def cancel_order(self, order_id: str) -> Tuple[bool, str]:
        """
        Cancel a pending order and release its cash reservation.

        Atomically removes order from pending and unreserves cash.
        Used for cross-session rejections or other cancellations.
        """
        if order_id not in self.pending_orders:
            return False, f"Order {order_id} not found in pending orders"

        order = self.pending_orders[order_id]

        # Release reserved cash
        reserved_for_order = order.quantity * order.proposal.plan.entry_price
        self.reserved_cash -= reserved_for_order

        # Remove from pending
        del self.pending_orders[order_id]

        return True, f"Order {order_id} cancelled and reservation released"

    def fill_order(self, order_id: str, fill_event: FillEvent) -> Tuple[bool, str]:
        """
        Fill a pending order.
        Create position from FillEvent.
        """
        if order_id not in self.pending_orders:
            return False, "Order not found"

        order = self.pending_orders[order_id]

        # Execute fill.  Long entries pay cash; short entries receive sale
        # proceeds and carry a negative marked position value.
        entry_notional = fill_event.quantity_filled * fill_event.fill_price
        if order.direction == 1:
            self.cash -= entry_notional + fill_event.cost_paid
        else:
            self.cash += entry_notional - fill_event.cost_paid
        self.reserved_cash -= fill_event.quantity_filled * order.proposal.plan.entry_price
        self.total_costs += fill_event.cost_paid

        # Create position
        position = Position(
            symbol=order.symbol,
            direction=order.direction,
            entry_price=fill_event.fill_price,
            entry_bar_index=fill_event.bar_index_filled,
            entry_bar_timestamp=fill_event.timestamp_filled,
            quantity=fill_event.quantity_filled,
            stop_price=order.stop_price,
            target_price=order.target_price,
            cost_paid=fill_event.cost_paid,
            fill_id=fill_event.fill_id,
        )

        self.positions[order.symbol] = position
        del self.pending_orders[order_id]

        return True, "Order filled"

    def close_position(self, exit_event: ExitEvent, config) -> Tuple[bool, str]:
        """
        Close a position. Record exit event.
        Update cash, P&L, and deduct exit costs.

        Args:
            exit_event: ExitEvent with exit prices and reason
            config: Mandatory canonical config for transaction costs.
        """
        if exit_event.symbol not in self.positions:
            return False, "Position not found"
        if config is None:
            raise ValueError("close_position requires config")

        position = self.positions[exit_event.symbol]
        if exit_event.direction != position.direction:
            return False, "Exit direction does not match position"
        if exit_event.quantity != position.quantity:
            return False, "Exit quantity does not match position"

        # The exit side depends on the position: a long exits by selling;
        # a short exits by buying back.  This determines the applicable STT.
        from revision4.config_access import calculate_transaction_cost
        exit_side = "SELL" if position.direction == 1 else "BUY"
        exit_cost = calculate_transaction_cost(
            exit_event.exit_price,
            exit_event.quantity,
            side=exit_side,
        )
        if abs(exit_event.exit_cost_paid - exit_cost) > 0.01:
            return False, "Exit cost does not match the canonical cost model"
        if abs(exit_event.entry_cost_paid - position.cost_paid) > 0.01:
            return False, "Entry cost does not match the authoritative fill"

        if position.direction == 1:
            gross_pnl = (exit_event.exit_price - position.entry_price) * position.quantity
        else:
            gross_pnl = (position.entry_price - exit_event.exit_price) * position.quantity
        net_pnl = gross_pnl - position.cost_paid - exit_cost
        if abs(exit_event.pnl_realized - net_pnl) > 0.01:
            return False, "Exit P&L does not reconcile with prices and costs"

        # Long exits receive sale proceeds.  Short exits pay the buy-to-cover
        # amount.  In both cases the exit-side transaction cost is deducted.
        exit_notional = exit_event.exit_price * exit_event.quantity
        if position.direction == 1:
            self.cash += exit_notional - exit_cost
        else:
            self.cash -= exit_notional + exit_cost
        self.total_costs += exit_cost

        self.realized_pnl += net_pnl
        self.daily_pnl += net_pnl

        self.completed_trades.append(CompletedTrade(
            trade_id=exit_event.exit_id,
            symbol=position.symbol,
            direction=position.direction,
            entry_timestamp=position.entry_bar_timestamp,
            entry_price=position.entry_price,
            entry_cost=position.cost_paid,
            exit_timestamp=exit_event.timestamp_exit,
            exit_price=exit_event.exit_price,
            exit_cost=exit_cost,
            exit_reason=exit_event.exit_reason,
            quantity=position.quantity,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
        ))

        # Remove position
        del self.positions[exit_event.symbol]

        return True, "Position closed"

    def mark_to_market(self, bar_data: Dict[str, Bar]) -> float:
        """
        Calculate current portfolio value.
        Returns: marked equity.
        """
        equity = self.cash

        for symbol, position in self.positions.items():
            if symbol in bar_data:
                current_price = bar_data[symbol].close
                position_value = position.marked_value(current_price)
                equity += position_value

        self.marked_equity = equity

        # Update max drawdown
        if equity > self.peak_equity:
            self.peak_equity = equity
        drawdown = self.peak_equity - equity
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

        return equity

    def reset_daily(self):
        """Reset daily P&L at EOD."""
        self.daily_pnl = 0.0
        self.daily_start_cash = self.cash

    def reconcile(self, bar_data: Optional[Dict[str, Bar]] = None) -> Tuple[bool, str]:
        """
        Verify ledger invariants.
        Returns: (valid, message)
        """
        errors = []

        # Cash check
        if self.cash < 0:
            errors.append(f"Cash negative: {self.cash:.2f}")

        # Reserved cash check
        if self.reserved_cash < 0:
            errors.append(f"Reserved cash negative: {self.reserved_cash:.2f}")

        # Position count check
        if len(self.positions) > 5:
            errors.append(f"More than 5 positions: {len(self.positions)}")

        # Pending order check
        if len(self.pending_orders) > 5:
            errors.append(f"More than 5 pending orders: {len(self.pending_orders)}")

        # Exposure check
        if bar_data:
            exposure = 0
            for symbol, position in self.positions.items():
                if symbol in bar_data:
                    current_price = bar_data[symbol].close
                    exposure += abs(position.quantity * current_price)

            if exposure > self.starting_cash * 2:
                errors.append(f"Exposure exceeds 2x: {exposure:.2f}")

        # Daily loss check
        if abs(self.daily_pnl) > 2000:
            errors.append(f"Daily loss exceeds ₹2,000: {self.daily_pnl:.2f}")

        if errors:
            return False, "; ".join(errors)
        return True, "OK"

    def snapshot(self, timestamp: str, bar_index: int, bar_data: Dict[str, Bar]) -> PortfolioSnapshot:
        """
        Create immutable snapshot of current state.
        """
        equity = self.mark_to_market(bar_data)
        unrealized = equity - self.cash - self.starting_cash - self.realized_pnl

        # Sector exposure (simplified)
        sector_exposure = {}

        return PortfolioSnapshot(
            timestamp=timestamp,
            bar_index=bar_index,
            cash=self.cash,
            reserved_cash=self.reserved_cash,
            positions=self.positions.copy(),
            pending_orders=self.pending_orders.copy(),
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            total_costs=self.total_costs,
            daily_pnl=self.daily_pnl,
            marked_equity=equity,
            exposure=sum(abs(pos.marked_value(bar_data[pos.symbol].close)) for pos in self.positions.values() if pos.symbol in bar_data),
            sector_exposure=sector_exposure,
        )
