"""Pre-Entry Deferral & Confirmation Gate
===========================================

Moves feedback authority from post-entry (when capital is already deployed)
to pre-entry (before the broker order is submitted).

Architecture:
  Signal at bar t
     ↓
  Candidate armed (no order submitted yet)
     ↓
  Observe next completed bar t+1
     ↓
  Compare actual R-progress vs expected R-progress
     ↓
  ADMIT_NEXT_OPEN / DEFER / CANCEL decision

This eliminates the friction tax (0.27R) on entries that lack immediate
structural follow-through.

Expected R-progress: ~0.077R (typical after first bar on strong confirms)
Decision rule: Actual_progress >= Expected_progress → ADMIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, List, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class CandidateState:
    """State of a provisional entry candidate."""
    symbol: str
    armed_at_bar_index: int
    entry_px: float
    atr: float
    direction: int  # +1 for long, -1 for short
    expected_r_progress: float

    def __repr__(self) -> str:
        return (
            f"Candidate({self.symbol} @ {self.entry_px:.2f}, "
            f"ATR={self.atr:.4f}, dir={self.direction:+d}, "
            f"expected_R={self.expected_r_progress:.4f})"
        )


class PreEntryDeferralController:
    """
    Pre-entry confirmation gate.

    One bar's observation before submitting the market order, measuring
    whether immediate follow-through (velocity) matches the expected
    minimum R-progress for the next bar.
    """

    def __init__(self, expected_r_progress_pct: float = 0.077, enable_logging: bool = True) -> None:
        """
        Initialize the deferral controller.

        Args:
            expected_r_progress_pct: Expected R-progress after first bar (default 0.077R)
            enable_logging: Log all decisions
        """
        self.expected_r_progress_pct = float(expected_r_progress_pct)
        self.enable_logging = bool(enable_logging)

        # Provisional candidates awaiting next-bar evaluation
        self._provisional_candidates: Dict[str, CandidateState] = {}

        # Audit trail of all deferral decisions
        self.decision_history: List[Dict[str, Any]] = []

        self._decision_sequence = 0

    def arm_candidate(
        self,
        symbol: str,
        current_bar_index: int,
        entry_px: float,
        atr: float,
        direction: int,
    ) -> bool:
        """
        Arm a candidate signal. NO order is submitted yet.

        Args:
            symbol: Ticker symbol
            current_bar_index: Index of the bar where signal arrived
            entry_px: Planned entry price
            atr: Current ATR (defines the risk unit)
            direction: +1 for long, -1 for short

        Returns:
            False (do not submit order; wait for next bar evaluation)
        """
        if symbol in self._provisional_candidates:
            logger.warning(f"Symbol {symbol} already has armed candidate; ignoring new one")
            return False

        candidate = CandidateState(
            symbol=symbol,
            armed_at_bar_index=current_bar_index,
            entry_px=float(entry_px),
            atr=float(atr),
            direction=int(direction),
            expected_r_progress=float(self.expected_r_progress_pct),
        )

        self._provisional_candidates[symbol] = candidate

        if self.enable_logging:
            logger.info(f"[DEFERRAL] Armed: {candidate}")

        return False  # Do not enter yet

    def evaluate_provisional(
        self,
        symbol: str,
        next_bar_index: int,
        next_bar_open: float,
        next_bar_high: float,
        next_bar_low: float,
        next_bar_close: float,
    ) -> str:
        """
        Evaluate the provisional candidate after the next bar closes.

        Compares actual R-progress vs expected R-progress on the provisional
        bar, and returns a decision: ADMIT_NEXT_OPEN / DEFER / CANCEL

        Args:
            symbol: Ticker symbol
            next_bar_index: Index of the bar being evaluated
            next_bar_open: Open price of the next bar
            next_bar_high: High price of the next bar
            next_bar_low: Low price of the next bar
            next_bar_close: Close price of the next bar

        Returns:
            Decision string: "ADMIT_NEXT_OPEN", "DEFER", or "CANCEL_CANDIDATE"
        """
        candidate = self._provisional_candidates.get(symbol)
        if not candidate:
            return "UNKNOWN"

        # Calculate actual R-progress from entry price to next bar close
        r_unit = candidate.atr  # For a long: 1R = 1 ATR

        if candidate.direction > 0:  # Long
            actual_progress_r = (next_bar_close - candidate.entry_px) / r_unit
            bar_high = next_bar_high
            bar_low = next_bar_low
        else:  # Short
            actual_progress_r = (candidate.entry_px - next_bar_close) / r_unit
            bar_high = next_bar_high
            bar_low = next_bar_low

        expected_progress_r = candidate.expected_r_progress
        error_r = expected_progress_r - actual_progress_r

        # Decision logic
        if error_r <= 0:
            # Actual progress >= expected; candidate passed the test
            decision = "ADMIT_NEXT_OPEN"
        elif error_r <= 0.05:
            # Close call; marginally below expected but within 0.05R
            # For now, DEFER and re-evaluate next bar
            decision = "DEFER"
        else:
            # Clear miss; actual progress is well below expected
            decision = "CANCEL_CANDIDATE"

        # Record decision
        record = {
            "sequence": self._decision_sequence,
            "symbol": symbol,
            "armed_at_bar": candidate.armed_at_bar_index,
            "evaluated_at_bar": next_bar_index,
            "entry_px": candidate.entry_px,
            "atr": candidate.atr,
            "direction": candidate.direction,
            "next_bar_open": next_bar_open,
            "next_bar_high": bar_high,
            "next_bar_low": bar_low,
            "next_bar_close": next_bar_close,
            "expected_r_progress": expected_progress_r,
            "actual_r_progress": actual_progress_r,
            "error_r": error_r,
            "decision": decision,
        }

        self.decision_history.append(record)
        self._decision_sequence += 1

        # Remove from provisional after evaluation
        del self._provisional_candidates[symbol]

        if self.enable_logging:
            logger.info(
                f"[DEFERRAL] {symbol} @ bar {next_bar_index}: "
                f"actual={actual_progress_r:.4f}R, expected={expected_progress_r:.4f}R, "
                f"error={error_r:.4f}R → {decision}"
            )

        return decision

    def get_pending_symbols(self) -> List[str]:
        """Return list of symbols awaiting next-bar evaluation."""
        return list(self._provisional_candidates.keys())

    def get_decision_summary(self) -> Dict[str, Any]:
        """Return summary statistics of all deferral decisions made."""
        if not self.decision_history:
            return {
                "total_decisions": 0,
                "admitted": 0,
                "deferred": 0,
                "cancelled": 0,
            }

        decisions = [d["decision"] for d in self.decision_history]

        return {
            "total_decisions": len(decisions),
            "admitted": decisions.count("ADMIT_NEXT_OPEN"),
            "deferred": decisions.count("DEFER"),
            "cancelled": decisions.count("CANCEL_CANDIDATE"),
            "mean_error_r": np.mean([d["error_r"] for d in self.decision_history]),
        }

    def export_decision_history_json(self) -> List[Dict[str, Any]]:
        """Export decision history as JSON-serializable list."""
        return self.decision_history


# For inline use in testing
import numpy as np
