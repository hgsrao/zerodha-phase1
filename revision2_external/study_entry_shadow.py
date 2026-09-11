"""Causal, non-executing outcome feedback for chart-study reversal setups.

This module is deliberately separate from the order path.  It creates a
shadow candidate only from information available at a completed bar, fills it
at the next bar's adverse paper price, and resolves target/stop only on later
bars.  Its feedback PID is observational: it reports a bounded suggested
derate but cannot place, resize, approve, or reject an actual order.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List

from simple_pid import PID


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class _Candidate:
    candidate_id: str
    symbol: str
    side: str
    signal_index: int
    fill_index: int
    expiry_index: int
    setup_extreme: float
    atr: float
    studies: Dict[str, Any]
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    entry_timestamp: str | None = None


class StudyEntryShadowLedger:
    """Outcome ledger for a conservative study-only reversal hypothesis."""

    def __init__(self, max_hold_bars: int = 60, minimum_history: int = 20, outcome_listener: Any | None = None) -> None:
        self.max_hold_bars = int(max_hold_bars)
        self.minimum_history = int(minimum_history)
        self._sequence = 0
        self._pending: List[_Candidate] = []
        self._open: List[_Candidate] = []
        self.resolved: List[Dict[str, Any]] = []
        self.observations: List[Dict[str, Any]] = []
        self._outcomes: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        self._pids: Dict[tuple[str, str], PID] = {}
        self._outcome_listener = outcome_listener

    @staticmethod
    def _leg_cost(price: float, side: str) -> float:
        turnover = abs(price)
        return min(20.0, 0.0003 * turnover) + 0.0000345 * turnover + (0.00025 * turnover if side == "SELL" else 0.0)

    @staticmethod
    def _fill_price(market_price: float, side: str) -> float:
        return float(market_price) * (1.0005 if side == "BUY" else 0.9995)

    def _profile(self, symbol: str, pattern: str, required_probability: float) -> Dict[str, float | int]:
        rows = self._outcomes[(symbol, pattern)]
        wins = sum(bool(row["target_before_stop"]) for row in rows)
        # Neutral Bayesian prior prevents one early result from changing an
        # adaptive output. It is not a profitability claim.
        probability = (wins + 0.5 * self.minimum_history) / (len(rows) + self.minimum_history)
        pid = self._pids.setdefault((symbol, pattern), PID(Kp=0.15, Ki=0.05, Kd=0.05, setpoint=required_probability, sample_time=None, output_limits=(0.0, 0.5)))
        pid.setpoint = required_probability
        deficit = float(pid(probability, dt=1)) if len(rows) >= self.minimum_history else 0.0
        return {
            "resolved_samples": len(rows), "target_before_stop_probability": probability,
            "required_break_even_probability": required_probability,
            "pid_deficit_output": deficit,
            "suggested_shadow_derate": 1.0 - deficit,
        }

    def observe(
        self, symbol: str, index: int, timestamp: object, bar: Any, history: Any,
        studies: Dict[str, Any], atr: float,
    ) -> None:
        """Record a causal study snapshot and schedule any reversal setup."""
        high, low, close, volume = (float(bar[key]) for key in ("high", "low", "close", "volume"))
        bar_range = max(high - low, 1e-12)
        close_location = (close - low) / bar_range
        trailing = history.iloc[max(0, len(history) - 10):]
        volume_mean = float(trailing["volume"].mean()) if len(trailing) else volume
        volume_ratio = volume / max(volume_mean, 1e-12)
        votes = dict(studies["votes"])
        # A reversal setup is deliberately strict and is NOT an order rule:
        # a trailing extreme, rejection close, elevated volume, and the one
        # fast study (stochastic) already turning in the proposed direction.
        side = None
        extreme = None
        if low <= float(trailing["low"].min()) and close_location >= 0.75 and volume_ratio >= 1.2 and votes.get("stochastic") == 1:
            side, extreme = "BUY", low
        elif high >= float(trailing["high"].max()) and close_location <= 0.25 and volume_ratio >= 1.2 and votes.get("stochastic") == -1:
            side, extreme = "SELL", high
        observation = {
            "timestamp": str(timestamp), "symbol": symbol, "index": index,
            "composite_direction": int(studies["direction"]), "composite_confidence": float(studies["confidence"]),
            "votes": votes, "weights": dict(studies["weights"]), "hit_rates": dict(studies["hit_rates"]),
            "close_location": close_location, "volume_ratio": volume_ratio,
            "reversal_setup_side": side,
        }
        self.observations.append(observation)
        if side is None:
            return
        self.schedule(
            symbol=symbol, index=index, side=side, setup_extreme=float(extreme),
            atr=atr, observation=observation,
        )

    def schedule(
        self, *, symbol: str, index: int, side: str, setup_extreme: float,
        atr: float, observation: Dict[str, Any],
    ) -> None:
        """Queue a generic causal shadow candidate for next-bar paper fill.

        This deliberately does not know why the candidate was found.  It lets
        independent research sensors use the same conservative fill, cost and
        terminal-bar rules without touching the production order path.
        """
        self._sequence += 1
        self._pending.append(_Candidate(
            candidate_id=f"study-shadow-{self._sequence}", symbol=symbol, side=side,
            signal_index=index, fill_index=index + 1, expiry_index=index + 1 + self.max_hold_bars,
            setup_extreme=float(setup_extreme), atr=max(float(atr), 1e-6), studies=observation,
        ))

    def advance(self, symbol: str, index: int, timestamp: object, bar: Any) -> None:
        """Fill prior candidates and resolve only on bars after their fill."""
        for candidate in list(self._pending):
            if candidate.symbol != symbol or candidate.fill_index != index:
                continue
            entry_side = candidate.side
            candidate.entry_price = self._fill_price(float(bar["open"]), entry_side)
            candidate.entry_timestamp = str(timestamp)
            buffer = 0.25 * candidate.atr
            candidate.stop_price = candidate.setup_extreme - buffer if entry_side == "BUY" else candidate.setup_extreme + buffer
            risk = abs(candidate.entry_price - candidate.stop_price)
            candidate.target_price = candidate.entry_price + 1.5 * risk if entry_side == "BUY" else candidate.entry_price - 1.5 * risk
            self._pending.remove(candidate)
            self._open.append(candidate)
        for candidate in list(self._open):
            if candidate.symbol != symbol or index <= candidate.fill_index:
                continue  # terminal fill bar remains intrabar-order-unknown
            high, low, close, open_price = (float(bar[key]) for key in ("high", "low", "close", "open"))
            hit_stop = low <= candidate.stop_price if candidate.side == "BUY" else high >= candidate.stop_price
            hit_target = high >= candidate.target_price if candidate.side == "BUY" else low <= candidate.target_price
            reason = None
            if hit_stop and hit_target:
                reason = "intrabar_order_unknown_stop_precedence"
            elif hit_stop:
                reason = "stop"
            elif hit_target:
                reason = "target"
            elif index >= candidate.expiry_index:
                reason = "expiry"
            if reason is None:
                continue
            if reason.startswith("stop"):
                market_exit = min(open_price, candidate.stop_price) if candidate.side == "BUY" else max(open_price, candidate.stop_price)
            elif reason == "target":
                market_exit = max(open_price, candidate.target_price) if candidate.side == "BUY" else min(open_price, candidate.target_price)
            else:
                market_exit = close
            exit_side = "SELL" if candidate.side == "BUY" else "BUY"
            exit_price = self._fill_price(market_exit, exit_side)
            gross = exit_price - candidate.entry_price if candidate.side == "BUY" else candidate.entry_price - exit_price
            costs = self._leg_cost(candidate.entry_price, candidate.side) + self._leg_cost(exit_price, exit_side)
            reward = abs(candidate.target_price - candidate.entry_price)
            loss = abs(candidate.entry_price - candidate.stop_price)
            required_probability = (loss + costs) / max(reward + loss, 1e-12)
            outcome = {
                "candidate_id": candidate.candidate_id, "symbol": symbol, "pattern": "study_reversal",
                "signal_timestamp": candidate.studies["timestamp"], "entry_timestamp": candidate.entry_timestamp,
                "resolution_timestamp": str(timestamp), "side": candidate.side, "entry_price": candidate.entry_price,
                "stop_price": candidate.stop_price, "target_price": candidate.target_price, "exit_price": exit_price,
                "exit_reason": reason, "target_before_stop": reason == "target", "gross_pnl_per_share": gross,
                "costs_per_share": costs, "net_pnl_per_share": gross - costs, "required_break_even_probability": required_probability,
                "setup": candidate.studies,
            }
            self._outcomes[(symbol, "study_reversal")].append(outcome)
            outcome["feedback"] = self._profile(symbol, "study_reversal", required_probability)
            self.resolved.append(outcome)
            if self._outcome_listener is not None:
                self._outcome_listener.record_outcome(outcome)
            self._open.remove(candidate)

    def finalize(self, symbol: str, timestamp: object, last_bar: Any) -> None:
        """Resolve remaining filled shadows at end of the observed dataset."""
        for candidate in list(self._open):
            if candidate.symbol != symbol:
                continue
            self.advance(symbol, candidate.expiry_index, timestamp, last_bar)

    def summary(self) -> Dict[str, Any]:
        rows = self.resolved
        return {
            "observations": len(self.observations), "setups": self._sequence,
            "resolved": len(rows), "pending_unresolved": len(self._pending) + len(self._open),
            "target_before_stop": sum(bool(row["target_before_stop"]) for row in rows),
            "net_pnl_per_share": sum(float(row["net_pnl_per_share"]) for row in rows),
            "outcomes": rows,
            "note": "Shadow-only. PID derate output is recorded but cannot alter orders, sizes, gates, stops, or targets.",
        }
