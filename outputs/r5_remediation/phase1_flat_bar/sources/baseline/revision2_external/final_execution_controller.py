"""One bounded decision surface joining the external engine's controllers.

This object does not manufacture an alpha or override safety.  It makes the
otherwise separate outputs explicit at the only two moments at which an order
decision can be made: before entry and after a completed held-position bar.

The target and horizon setpoints are initially the MPC plan because the
outcome ledger does not yet contain a validated, causal symbol/regime path
distribution.  They are deliberately labelled ``BOOTSTRAP_MPC`` rather than
silently pretending to be adaptive.  A later, sealed setpoint-provider study
may replace only those two fields; the decision interface remains unchanged.
"""

from __future__ import annotations

from typing import Any, Dict


def _target_r(entry_price: float, stop_price: float, target_price: float) -> float:
    risk = abs(float(entry_price) - float(stop_price))
    return abs(float(target_price) - float(entry_price)) / risk if risk > 0.0 else 0.0


class FinalExecutionController:
    """Translate already-approved sensor/planner outputs into named actions."""

    def entry_decision(
        self, *, side: str, pa_confidence: float, id_approved: bool,
        studies_direction: int, studies_confidence: float,
        entry_price: float, stop_price: float, target_price: float,
        maximum_hold_bars: int, entry_quality: Dict[str, Any],
        dynamics: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected_direction = 1 if side == "BUY" else -1
        if not id_approved:
            action, reason = "CANCEL", "ID_NOT_APPROVED"
        elif studies_direction == 0:
            action, reason = "DEFER", "STUDIES_NEUTRAL"
        elif studies_direction != expected_direction:
            action, reason = "CANCEL", "STUDIES_DIRECTION_CONFLICT"
        else:
            action, reason = "ADMIT", "PA_ID_STUDIES_ALIGNED"
        return {
            "action": action,
            "reason": reason,
            "pa_confidence": float(pa_confidence),
            "studies_direction": int(studies_direction),
            "studies_confidence": float(studies_confidence),
            "target_setpoint_r": _target_r(entry_price, stop_price, target_price),
            "target_setpoint_source": "BOOTSTRAP_MPC",
            "maximum_hold_setpoint_bars": int(maximum_hold_bars),
            "maximum_hold_setpoint_source": "BOOTSTRAP_MPC",
            "response_time_bars": dynamics.get("response_time_bars"),
            "entry_quality_samples": int(entry_quality.get("symbol_regime_samples", 0)),
            "entry_quality_derate": float(entry_quality.get("suggested_entry_derate", 1.0)),
        }

    def exit_decision(
        self, *, path: Dict[str, Any] | None, exit_pid: Dict[str, Any],
        held_bars: int, minimum_hold_bars: int, maximum_hold_bars: int, side: str = "BUY",
    ) -> Dict[str, Any]:
        saturation = bool(exit_pid.get("studies_clamped"))
        behind_path = bool((path or {}).get("behind_path"))
        if side not in {"BUY", "SELL"}:
            raise ValueError("invalid exit side")
        delta = float(exit_pid.get("stop_after", 0.0)) - float(exit_pid.get("stop_before", 0.0))
        stop_moved = delta > 0 if side == "BUY" else delta < 0
        eligible = int(held_bars) >= int(minimum_hold_bars)
        if int(held_bars) >= int(maximum_hold_bars):
            action, reason = "EXIT", "MAXIMUM_HOLD_REACHED"
        elif eligible and saturation:
            action, reason = "EXIT", "STUDIES_PID_SUSTAINED_LOW_CONFIDENCE"
        elif eligible and behind_path:
            action, reason = "EXIT_NEXT_BAR", "BEHIND_REFERENCE_PATH"
        elif stop_moved:
            action, reason = "PROTECT", "RATCHET_STOP_TIGHTENED"
        else:
            action, reason = "HOLD", "ON_PATH_OR_MINIMUM_HOLD"
        return {
            "action": action,
            "reason": reason,
            "held_bars": int(held_bars),
            "minimum_hold_bars": int(minimum_hold_bars),
            "maximum_hold_bars": int(maximum_hold_bars),
            "behind_path": behind_path,
            "studies_pid_output": exit_pid.get("studies_output"),
            "studies_tightness": exit_pid.get("studies_tightness"),
            "stop_before": exit_pid.get("stop_before"),
            "stop_after": exit_pid.get("stop_after"),
        }
