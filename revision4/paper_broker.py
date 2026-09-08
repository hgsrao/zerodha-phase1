"""
REVISION 04 (Proper): Paper Broker

Order state machine with proper fill timing.
Bar t: create order (PENDING)
Bar t+1: fill at open (FILLED)
Tracks: decision_time, submit_time, fill_time
"""

import uuid
from typing import Dict, List, Optional, Tuple
from revision4.contracts import (
    OrderIntent, FillEvent, OrderState, Bar
)


class PaperBroker:
    """
    Simulated broker for paper trading.
    Manages order lifecycle and fills.
    """

    def __init__(self):
        self.order_history: Dict[str, OrderIntent] = {}  # All orders ever created
        self.fill_history: List[FillEvent] = []
        self.active_orders: Dict[str, OrderIntent] = {}  # Pending + partial

    def submit_order(self, order_intent: OrderIntent, config) -> Tuple[bool, str]:
        """
        Submit order for trading.
        Order created at bar t, eligible for fill at bar t+1.

        MANDATORY: config with kill_switch_enabled parameter.
        NO BYPASS: Config must be provided; no None fallback allowed.

        Returns: (success, reason_or_message)
        """
        from revision4.config_access import get_kill_switch_status

        order_id = order_intent.order_id

        # CHECK 1: Kill switch (safety gate - blocks ALL orders if disabled)
        # MANDATORY: Config is required; fail-closed if missing
        if config is None:
            raise ValueError("submit_order requires config (no None bypass allowed)")

        kill_switch_enabled = get_kill_switch_status(config)
        if not kill_switch_enabled:
            return False, "Kill switch disabled: order submission blocked"

        # Check duplicate
        if order_id in self.order_history:
            return False, "Duplicate order ID"

        # Check same symbol already pending
        for existing in self.active_orders.values():
            if existing.symbol == order_intent.symbol and existing.state == OrderState.PENDING:
                return False, f"Order already pending for {order_intent.symbol}"

        # Store order
        self.order_history[order_id] = order_intent
        self.active_orders[order_id] = order_intent

        return True, "Order submitted"

    def try_fill_order(
        self,
        order_id: str,
        bar_at_fill_time: Bar,
        fill_bar_index: int,
        config,
    ) -> Optional[FillEvent]:
        """
        Attempt to fill order at bar t+1 open.
        Returns FillEvent if filled, None if can't fill yet.

        MANDATORY: config with kill_switch_enabled parameter.
        NO BYPASS: Config must be provided; no None fallback allowed.
        """
        from revision4.config_access import get_kill_switch_status

        if order_id not in self.active_orders:
            return None

        order = self.active_orders[order_id]

        # CHECK 1: Kill switch (safety gate - blocks ALL fills if disabled)
        # MANDATORY: Config is required; fail-closed if missing
        if config is None:
            raise ValueError("try_fill_order requires config (no None bypass allowed)")

        kill_switch_enabled = get_kill_switch_status(config)
        if not kill_switch_enabled:
            return None  # Can't fill while kill switch is disabled

        # Check eligibility: order created at bar t, fills at bar t+1+
        if fill_bar_index <= order.proposal.plan.bar_index:
            return None  # Not yet eligible

        # CHECK: Cross-session fill detection (pre-fill gate)
        # If fill date differs from decision date, reject unless explicitly authorized
        import pandas as pd
        decision_date = pd.Timestamp(order.timestamp_created).date().isoformat()
        fill_date = pd.Timestamp(bar_at_fill_time.timestamp).date().isoformat()
        is_cross_session = decision_date != fill_date

        if is_cross_session:
            authorized = config.require("authorized_cross_session") if config else False
            if not authorized:
                # Reject the fill: order will stay pending (expire after max_hold_bars)
                return None

        # Fill at open
        fill_price = bar_at_fill_time.open
        quantity_filled = order.quantity

        # Calculate cost for the actual entry side.  A long enters by buying;
        # a short enters by selling, which is the side on which STT applies.
        from revision4.config_access import calculate_transaction_cost
        entry_side = "BUY" if order.direction == 1 else "SELL"
        cost_paid = calculate_transaction_cost(fill_price, quantity_filled, side=entry_side)

        # Create fill event
        fill_event = FillEvent(
            fill_id=str(uuid.uuid4()),
            order_id=order_id,
            timestamp_filled=bar_at_fill_time.timestamp,
            bar_index_filled=fill_bar_index,
            symbol=order.symbol,
            direction=order.direction,
            quantity_filled=quantity_filled,
            fill_price=fill_price,
            cost_paid=cost_paid,
            timestamp_created=order.timestamp_created,
            timestamp_submitted=order.timestamp_created,  # Same for now
        )

        # Update order state
        updated_order = OrderIntent(
            order_id=order.order_id,
            timestamp_created=order.timestamp_created,
            bar_index_created=order.bar_index_created,
            symbol=order.symbol,
            direction=order.direction,
            quantity=order.quantity,
            stop_price=order.stop_price,
            target_price=order.target_price,
            proposal=order.proposal,
            state=OrderState.FILLED,
            rejection_reason=None,
        )

        self.order_history[order_id] = updated_order
        self.active_orders[order_id] = updated_order
        self.fill_history.append(fill_event)

        return fill_event

    def check_order_expiration(self, order_id: str, current_bar_index: int, max_hold_bars: int = 60):
        """
        Check if order has expired (unfilled after N bars).
        """
        if order_id not in self.active_orders:
            return

        order = self.active_orders[order_id]

        if current_bar_index - order.bar_index_created >= max_hold_bars:
            # Expire order
            expired_order = OrderIntent(
                order_id=order.order_id,
                timestamp_created=order.timestamp_created,
                bar_index_created=order.bar_index_created,
                symbol=order.symbol,
                direction=order.direction,
                quantity=order.quantity,
                stop_price=order.stop_price,
                target_price=order.target_price,
                proposal=order.proposal,
                state=OrderState.EXPIRED,
                rejection_reason=f"Unfilled after {max_hold_bars} bars",
            )

            self.order_history[order_id] = expired_order
            del self.active_orders[order_id]

    def get_active_orders(self) -> Dict[str, OrderIntent]:
        """Return copy of pending orders."""
        return self.active_orders.copy()

    def get_order_history(self) -> Dict[str, OrderIntent]:
        """Return all orders ever submitted."""
        return self.order_history.copy()

    def get_fill_history(self) -> List[FillEvent]:
        """Return all fills."""
        return self.fill_history.copy()

    def retire_filled_orders(self) -> int:
        """
        Remove filled orders from active_orders after reconciliation.
        Prevents unbounded memory growth and keeps order ledger auditable.

        Returns: Count of orders retired.
        """
        filled_order_ids = [
            oid for oid, order in self.active_orders.items()
            if order.state == OrderState.FILLED
        ]
        for oid in filled_order_ids:
            del self.active_orders[oid]
        return len(filled_order_ids)
