"""Cross-session order rejection policy for in-house engine.

Default policy: do not allow an intraday order to fill on another trading date.

Checks twice:
1. Pre-submission: locate next bar for symbol, reject if cross-date
2. Fill-time: verify decision/fill dates again

Cancelled orders release reservations and never leave stranded state.
"""

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd


class CrossSessionRejectionPolicy:
    """Validates and enforces cross-session order policy."""

    def __init__(self, allow_cross_session: bool = False):
        """
        Args:
            allow_cross_session: If True, allow orders to fill cross-session
                               (only with explicit authorization).
        """
        self.allow_cross_session = allow_cross_session

    def get_next_bar_for_symbol(
        self,
        symbol: str,
        current_bar_index: int,
        all_bars: Dict[str, List[pd.Series]],
    ) -> Optional[int]:
        """
        Find the next bar index for a specific symbol.

        Args:
            symbol: Symbol to search for
            current_bar_index: Current bar index (0-based)
            all_bars: Dict[symbol -> list of bars with timestamp column]

        Returns:
            Next bar index if found; None if no future bar exists.
        """
        if symbol not in all_bars:
            return None

        bars = all_bars[symbol]
        if current_bar_index + 1 >= len(bars):
            return None  # No future bar

        return current_bar_index + 1

    def check_pre_submission(
        self,
        symbol: str,
        decision_timestamp: str,  # ISO format
        current_bar_index: int,
        all_bars: Dict[str, List[pd.Series]],
    ) -> tuple[bool, Optional[str]]:
        """
        Pre-submission check: can this order fill on the next bar?

        Args:
            symbol: Symbol being ordered
            decision_timestamp: Decision bar timestamp (ISO)
            current_bar_index: Current bar index
            all_bars: All available bars

        Returns:
            (allowed, reason)
            - (True, None) if next bar exists and is same-date
            - (False, reason) if rejected
        """
        # Find next bar for this symbol
        next_bar_idx = self.get_next_bar_for_symbol(
            symbol, current_bar_index, all_bars
        )

        if next_bar_idx is None:
            return False, "NO_ELIGIBLE_FILL_BAR"

        # Check if next bar is same date
        bars = all_bars[symbol]
        decision_date = pd.Timestamp(decision_timestamp).date()
        next_bar = bars[next_bar_idx]
        next_bar_timestamp = pd.Timestamp(next_bar['timestamp'])
        next_bar_date = next_bar_timestamp.date()

        if decision_date != next_bar_date:
            if self.allow_cross_session:
                return True, None  # Allowed (only if explicitly authorized)
            return False, "CROSS_SESSION_REJECTED"

        return True, None

    def check_fill_time(
        self,
        order_decision_date: str,  # ISO date YYYY-MM-DD
        fill_timestamp: str,  # ISO timestamp
        cross_session_authorized: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """
        Fill-time check: verify decision/fill dates match (or authorized cross-session).

        Args:
            order_decision_date: Date when order was decided (ISO YYYY-MM-DD)
            fill_timestamp: When fill actually occurred (ISO timestamp)
            cross_session_authorized: Whether cross-session is explicitly authorized

        Returns:
            (allowed, reason)
        """
        fill_date = pd.Timestamp(fill_timestamp).date().isoformat()

        if order_decision_date != fill_date:
            if cross_session_authorized:
                return True, None  # Allowed
            return False, "CROSS_SESSION_FILL_REJECTED"

        return True, None

    def reject_pending_order(
        self,
        order_id: str,
        symbol: str,
        reserved_cash: float,
    ) -> Dict[str, any]:
        """
        Record rejection of a pending order: mark cancelled, release cash.

        Returns:
            {
                "order_id": order_id,
                "status": "CANCELLED",
                "reason": "CROSS_SESSION_REJECTED",
                "reserved_cash_released": reserved_cash,
            }
        """
        return {
            "order_id": order_id,
            "symbol": symbol,
            "status": "CANCELLED",
            "reason": "CROSS_SESSION_REJECTED",
            "reserved_cash_released": reserved_cash,
        }
