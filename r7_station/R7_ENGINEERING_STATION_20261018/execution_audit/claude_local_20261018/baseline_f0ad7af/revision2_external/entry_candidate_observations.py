"""Observer-only ledger for candidates created before execution constraints."""
from __future__ import annotations

from collections import Counter
from typing import Any


class EntryCandidateObservationLedger:
    """Record a candidate's causal state and its eventual execution disposition.

    This does not label future performance and must never be read by an order
    decision.  Its job is to expose selection bias between MPC-approved
    candidates and the smaller set that ultimately receives a paper fill.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def observe(self, candidate: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id", ""))
        if not candidate_id or candidate_id in self._rows:
            raise ValueError("candidate observation requires unique candidate_id")
        for field in ("symbol", "side", "timestamp", "pa_confidence", "id_confidence"):
            if field not in candidate:
                raise ValueError(f"candidate observation requires {field}")
        row = {**candidate, "execution_disposition": "PENDING", "execution_reason": None}
        self._rows[candidate_id] = row
        return dict(row)

    def dispose(self, candidate_id: str, disposition: str, reason: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self._rows.get(str(candidate_id))
        if row is None:
            raise ValueError(f"unknown candidate_id {candidate_id}")
        if row["execution_disposition"] != "PENDING":
            raise ValueError(f"candidate {candidate_id} already disposed")
        row["execution_disposition"] = str(disposition)
        row["execution_reason"] = str(reason)
        if details is not None:
            row["execution_details"] = dict(details)
        return dict(row)

    def finalize_pending(self) -> None:
        for row in self._rows.values():
            if row["execution_disposition"] == "PENDING":
                row["execution_disposition"] = "NOT_FILLED_UNCLASSIFIED"
                row["execution_reason"] = "runner_completed_without_disposition"

    def report(self) -> dict[str, Any]:
        rows = [dict(row) for _, row in sorted(self._rows.items())]
        return {
            "observed_candidates": len(rows),
            "dispositions": dict(sorted(Counter(row["execution_disposition"] for row in rows).items())),
            "rows": rows,
        }
