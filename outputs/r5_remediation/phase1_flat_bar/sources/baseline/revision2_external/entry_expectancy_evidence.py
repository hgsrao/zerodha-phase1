"""Causal evidence ledger for future entry-admission research.

This is deliberately not an entry predictor.  It pairs an already-filled
candidate's pre-entry state with its completed, cost-adjusted outcome.  A
future frozen model may consume only these resolved rows, never an open
trade's future path.
"""
from __future__ import annotations

import math
from typing import Any


class CausalEntryExpectancyLedger:
    """Keep pre-entry observations separate from subsequently resolved P&L."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._resolved: list[dict[str, Any]] = []

    def observe_fill(self, candidate: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id", ""))
        if not candidate_id:
            raise ValueError("entry evidence requires candidate_id")
        if candidate_id in self._pending:
            raise ValueError(f"duplicate entry evidence for {candidate_id}")
        for field in ("symbol", "side", "timestamp", "pa_confidence", "id_confidence", "studies_confidence"):
            if field not in candidate:
                raise ValueError(f"entry evidence requires {field}")
        # Reject non-finite numeric inputs rather than letting a later model
        # silently learn from corrupt telemetry.
        for field in ("pa_confidence", "id_confidence", "studies_confidence", "atr_fraction", "target_r"):
            if not math.isfinite(float(candidate[field])):
                raise ValueError(f"entry evidence has non-finite {field}")
        row = dict(candidate)
        self._pending[candidate_id] = row
        return row

    def record_outcome(self, outcome: dict[str, Any]) -> dict[str, Any] | None:
        candidate_id = str(outcome.get("candidate_id", ""))
        if not candidate_id:
            raise ValueError("entry outcome requires candidate_id")
        candidate = self._pending.get(candidate_id)
        if candidate is None:
            return None
        # Validate the whole outcome BEFORE consuming the pending candidate: an invalid outcome
        # must be rejected without losing the pre-entry evidence it would have been paired with.
        for field in ("net_pnl", "pnl", "costs", "exit_reason", "bars_held"):
            if outcome.get(field) is None:
                raise ValueError(f"entry outcome requires {field}")
        numbers = {}
        for field in ("pnl", "costs", "net_pnl", "bars_held"):
            value = outcome[field]
            if isinstance(value, bool):
                raise ValueError(f"entry outcome has non-numeric {field}")
            try:
                numbers[field] = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"entry outcome has non-numeric {field}") from exc
            if not math.isfinite(numbers[field]):
                raise ValueError(f"entry outcome has non-finite {field}")
        if numbers["bars_held"] < 0 or not numbers["bars_held"].is_integer():
            raise ValueError("entry outcome bars_held must be a non-negative integer")
        if not isinstance(outcome["exit_reason"], str) or not outcome["exit_reason"].strip():
            raise ValueError("entry outcome requires a non-empty exit_reason")
        del self._pending[candidate_id]
        resolved = {
            **candidate,
            "trade_id": outcome.get("trade_id"),
            "exit_timestamp": outcome.get("exit_timestamp"),
            "exit_reason": outcome["exit_reason"],
            "bars_held": int(numbers["bars_held"]),
            "gross_pnl": numbers["pnl"],
            "costs": numbers["costs"],
            "net_pnl": numbers["net_pnl"],
            "mfe_r": outcome.get("mfe_r"),
            "mae_r": outcome.get("mae_r"),
            "terminal_bar_excursion": outcome.get("terminal_bar_excursion"),
        }
        self._resolved.append(resolved)
        return resolved

    def summary(self) -> dict[str, int]:
        return {"pending_candidates": len(self._pending), "resolved_candidates": len(self._resolved)}

    @property
    def resolved(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._resolved]
