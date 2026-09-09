"""Chronological, portfolio-aware replay coordination.

This module deliberately owns *time and state ordering*, not signal logic.
Revision 2 box integration supplies ranked order candidates through a typed
callback once its adapters are certified.  Keeping that boundary explicit
prevents a serial per-symbol loop from becoming an accidental strategy rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import pandas as pd

from revision4.contracts import Bar, EffectiveConfig, ExitEvent, FillEvent, OrderIntent, PortfolioSnapshot
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger


@dataclass(frozen=True)
class RankedOrderCandidate:
    """An already-authorized candidate with an explicit portfolio rank.

    Higher ranks are considered first.  The deterministic order-id tiebreaker
    prevents source dictionary order or symbol alphabetization from changing a
    portfolio allocation.
    """

    order: OrderIntent
    rank: float
    pa_confidence: Optional[float] = None
    id_risk_reward: Optional[float] = None


@dataclass(frozen=True)
class TimestampReplayResult:
    """Auditable result of one chronological replay pass."""

    timestamps_processed: int
    bars_processed: int
    orders_submitted: Tuple[OrderIntent, ...]
    fills: Tuple[FillEvent, ...]
    exits: Tuple[ExitEvent, ...]
    event_log: Tuple[Tuple[str, str, str], ...]


CandidateProvider = Callable[[PortfolioSnapshot, Mapping[str, Bar], int], Sequence[RankedOrderCandidate]]
ExitProvider = Callable[[PortfolioSnapshot, Mapping[str, Bar], int], Iterable[ExitEvent]]


class TimestampOrchestrator:
    """Execute all symbols at a timestamp against one shared portfolio state.

    Lifecycle per timestamp:
      1. close approved exits;
      2. fill orders submitted at earlier timestamps;
      3. freeze one portfolio snapshot;
      4. obtain globally ranked new candidates;
      5. reserve and submit new pending orders.

    Orders generated at timestamp *t* are intentionally created after the fill
    phase and therefore cannot fill until a later timestamp.
    """

    def __init__(
        self,
        config: EffectiveConfig,
        candidate_provider: CandidateProvider,
        exit_provider: Optional[ExitProvider] = None,
        ledger: Optional[PortfolioLedger] = None,
        broker: Optional[PaperBroker] = None,
        gate_evaluator=None,
        gate16_remediator=None,
        candidate_observer=None,
        exit_observer=None,
    ) -> None:
        if config is None:
            raise ValueError("TimestampOrchestrator requires canonical config")
        self.config = config
        self.candidate_provider = candidate_provider
        self.exit_provider = exit_provider or (lambda _snapshot, _bars, _index: ())
        self.ledger = ledger or PortfolioLedger()
        self.broker = broker or PaperBroker()
        self.gate_evaluator = gate_evaluator
        self.gate16_remediator = gate16_remediator
        self.candidate_observer = candidate_observer
        self.exit_observer = exit_observer
        self._current_date: Optional[str] = None

    @staticmethod
    def build_event_stream(bars_by_symbol: Mapping[str, Sequence[Bar]]) -> Dict[str, Dict[str, Bar]]:
        """Index bars once by timestamp and reject malformed symbol streams."""
        events: Dict[str, Dict[str, Bar]] = {}
        for symbol, bars in bars_by_symbol.items():
            previous_timestamp: Optional[str] = None
            for bar in bars:
                if bar.symbol != symbol:
                    raise ValueError(f"bar symbol {bar.symbol} does not match stream {symbol}")
                if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
                    raise ValueError(f"{symbol} bars must be strictly chronological")
                previous_timestamp = bar.timestamp
                timestamp_bars = events.setdefault(bar.timestamp, {})
                if symbol in timestamp_bars:
                    raise ValueError(f"duplicate bar for {symbol} at {bar.timestamp}")
                timestamp_bars[symbol] = bar
        if not events:
            raise ValueError("cannot replay an empty event stream")
        return events

    def _find_next_bar_timestamp(self, symbol: str, current_timestamp: str,
                                   sorted_timestamps: List[str], events: Dict[str, Dict[str, Bar]],
                                   event_index: int) -> Optional[str]:
        """Find the next bar timestamp for a specific symbol after current_timestamp.

        Searches forward through timestamps to find the next one containing this symbol.
        Returns None if no future bar exists for this symbol.
        """
        if event_index + 1 >= len(sorted_timestamps):
            return None

        # Look for next timestamp that contains this symbol
        for future_index in range(event_index + 1, len(sorted_timestamps)):
            future_ts = sorted_timestamps[future_index]
            future_bars = events.get(future_ts, {})

            # Check if this symbol has a bar at this timestamp
            if symbol in future_bars:
                return future_ts

        # No future bar found for this symbol
        return None

    def run(self, bars_by_symbol: Mapping[str, Sequence[Bar]]) -> TimestampReplayResult:
        events = self.build_event_stream(bars_by_symbol)
        orders: List[OrderIntent] = []
        fills: List[FillEvent] = []
        exits: List[ExitEvent] = []
        event_log: List[Tuple[str, str, str]] = []

        sorted_timestamps = sorted(events)

        for event_index, timestamp in enumerate(sorted_timestamps):
            bars = events[timestamp]
            ratios = self.candidate_observer.observe_bars(bars) if self.candidate_observer else None
            date = timestamp.split("T", 1)[0]
            if self._current_date is not None and date != self._current_date:
                self.ledger.reset_daily()
            self._current_date = date

            if self.gate16_remediator is not None:
                remediation_exits = self.gate16_remediator.flatten_next_bars(bars, event_index, self.ledger)
                exits.extend(remediation_exits)
                event_log.extend((timestamp, "GATE16_FLATTEN", event.exit_id) for event in remediation_exits)

            # Exits observe the state from prior timestamps, before new fills
            # or candidates at this timestamp can change it.
            pre_exit_snapshot = self.ledger.snapshot(timestamp, event_index, bars)
            for exit_event in self.exit_provider(pre_exit_snapshot, bars, event_index):
                ok, reason = self.ledger.close_position(exit_event, self.config)
                if not ok:
                    raise RuntimeError(f"exit {exit_event.exit_id} rejected: {reason}")
                exits.append(exit_event)
                event_log.append((timestamp, "EXIT", exit_event.exit_id))
                if self.exit_observer is not None:
                    self.exit_observer(exit_event)

            # Only an observed bar for the order's symbol can fill it.
            for order_id, order in self.broker.get_active_orders().items():
                bar = bars.get(order.symbol)
                if bar is None:
                    continue

                fill = self.broker.try_fill_order(order_id, bar, event_index, self.config)

                # Detect cross-session rejection: fill returned None, but dates mismatch
                if fill is None:
                    decision_date = pd.Timestamp(order.timestamp_created).date().isoformat()
                    bar_date = pd.Timestamp(bar.timestamp).date().isoformat()

                    if decision_date != bar_date:
                        # Cross-session fill attempt: cancel the order
                        ok, msg = self.broker.cancel_order(order_id, "CROSS_SESSION_PROHIBITED")
                        if not ok:
                            raise RuntimeError(f"Failed to cancel cross-session order {order_id}: {msg}")

                        # Release ledger reservation
                        ok, msg = self.ledger.cancel_order(order_id)
                        if not ok:
                            raise RuntimeError(f"Failed to release reservation for {order_id}: {msg}")

                        event_log.append((timestamp, "CANCEL_CROSS_SESSION", order_id))
                    continue

                ok, reason = self.ledger.fill_order(order_id, fill)
                if not ok:
                    raise RuntimeError(f"fill {fill.fill_id} cannot reconcile: {reason}")
                fills.append(fill)
                event_log.append((timestamp, "FILL", order_id))
                if self.gate_evaluator is not None:
                    ok, reason = self.gate_evaluator.evaluate_post_fill(order, fill)
                    if not ok:
                        if self.gate16_remediator is None:
                            raise RuntimeError(f"post-fill gate rejected {order_id}: {reason}")
                        violation = self.gate16_remediator.handle_breach(timestamp, order, fill, self.ledger, self.broker)
                        event_log.append((timestamp, "GATE16_QUARANTINE", violation.violation_id))
                        if self.gate16_remediator.trading_halted:
                            raise RuntimeError("Gate16 second breach: trading halted")
                        continue
                    ok, reason = self.gate_evaluator.evaluate_post_reconciliation(
                        order, fill, int(order.quantity), int(fill.quantity_filled),
                        fill.cost_paid, fill.cost_paid,
                    )
                    if not ok:
                        raise RuntimeError(f"post-reconciliation gate rejected {order_id}: {reason}")
            self.broker.retire_filled_orders()

            # This exact snapshot is shared by every candidate at timestamp t.
            snapshot = self.ledger.snapshot(timestamp, event_index, bars)
            candidates = self.candidate_provider(snapshot, bars, event_index)
            if self.candidate_observer:
                self.candidate_observer.observe_candidates(candidates, ratios)
            if self.gate16_remediator is not None and not self.gate16_remediator.entry_authorization_enabled:
                candidates = ()
            ranked = sorted(candidates, key=lambda candidate: (-candidate.rank, candidate.order.order_id))
            for candidate in ranked:
                order = candidate.order
                if order.timestamp_created != timestamp or order.bar_index_created != event_index:
                    raise ValueError("candidate order does not belong to the current timestamp")
                if self.gate_evaluator is not None:
                    if candidate.pa_confidence is None or candidate.id_risk_reward is None:
                        raise RuntimeError(f"candidate {order.order_id} lacks authoritative PA/ID gate inputs")

                    # Find next eligible bar for this symbol (for cross-session check)
                    next_bar_timestamp = self._find_next_bar_timestamp(
                        order.symbol, timestamp, sorted_timestamps, events, event_index
                    )

                    ok, reason = self.gate_evaluator.evaluate_pre_submission(
                        order, snapshot, candidate.pa_confidence, candidate.id_risk_reward,
                        timestamp, self.ledger.peak_equity, max(0.0, -self.ledger.daily_pnl),
                        self.config.require("kill_switch_enabled"), bars,
                        next_bar_timestamp=next_bar_timestamp,
                    )
                    if not ok:
                        event_log.append((timestamp, "GATE_REJECT", f"{order.order_id}:{reason}"))
                        continue
                ok, reason = self.ledger.create_order(order.order_id, order)
                if not ok:
                    event_log.append((timestamp, "LEDGER_REJECT", f"{order.order_id}:{reason}"))
                    continue
                ok, reason = self.broker.submit_order(order, self.config)
                if not ok:
                    # The broker must not disagree after a ledger reservation.
                    # Cancellation/rollback is intentionally a separate
                    # lifecycle capability, so stop rather than leave a
                    # misleading pending reservation.
                    raise RuntimeError(f"broker rejected reserved order {order.order_id}: {reason}")
                orders.append(order)
                event_log.append((timestamp, "SUBMIT", order.order_id))

            ok, reason = self.ledger.reconcile(bars)
            if not ok:
                raise RuntimeError(f"ledger invariant failed at {timestamp}: {reason}")

        return TimestampReplayResult(
            timestamps_processed=len(events),
            bars_processed=sum(len(bars) for bars in events.values()),
            orders_submitted=tuple(orders),
            fills=tuple(fills),
            exits=tuple(exits),
            event_log=tuple(event_log),
        )
