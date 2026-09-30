"""Exit-only paired bridge: shadow copies of every real position under alternative exit policies.

The orchestrator calls this observer (``orch.exit_shadow``) at three points and never reads it
back, so it cannot change an order, a fill or any engine state:

    on_entry   after a real fill      -> one shadow position per policy (same fill, qty, stop, target)
    on_bar     after the real exit step of every evaluated (symbol, bar)
    on_run_end before the real end-of-run reconciliation

Each shadow runs the engine's own full-authority exit stack in the same order as
``Revision2ExternalEngineOrchestrator._maybe_exit``: forced close time, drawdown halt, MiCOM trip,
the armed governor exit (with stop/target gap precedence at the open), the intrabar protective
stop and target, MIS session close, maximum hold, then the governor position step, which arms
an exit for the next open or ratchets the protective stop from the completed bar.  Each policy
has its own bay governors and inner-loop state.

Entries are therefore identical by construction; only the exit policy differs.  A shadow that
runs the ``legacy`` policy must reproduce every real exit exactly (``validate_legacy``): that
proves the shadow is a faithful copy of the engine's exit stack before any other policy's
numbers are used.  Shared inputs that depend on the real portfolio (the drawdown input to FSRT
and the drawdown halt) are the real engine's values, as in any exit-only comparison.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd

from revision2.transaction_costs import leg_cost, paper_fill_price
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor, PositionControlV3
from revision5.governor_authority import GovernorInputError, position_decision
from revision5.topology import BAY_IDS, SYMBOL_TO_BAY


class ShadowExitBridge:
    def __init__(self, policies: Dict[str, Optional[PositionControlV3]]):
        if not policies:
            raise ValueError("at least one shadow policy is required")
        self.policies = dict(policies)
        self._governors = {name: {bay: BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[bay]) for bay in BAY_IDS}
                           for name in self.policies}
        self._open: Dict[str, Dict[str, Dict[str, Any]]] = {name: {} for name in self.policies}
        self.closed: Dict[str, List[Dict[str, Any]]] = {name: [] for name in self.policies}

    # ------------------------------------------------------------------ orchestrator hooks

    def on_entry(self, orch, symbol: str, trade: Dict[str, Any], entry_bar_idx: int) -> None:
        for name in self.policies:
            trade_id = trade["trade_id"]
            self._open[name][trade_id] = {
                "symbol": symbol, "trade_id": trade_id, "side": trade["side"],
                "entry_price": float(trade["entry_price"]), "stop_price": float(trade["stop_price"]),
                "target_price": float(trade["target_price"]), "quantity": int(trade["quantity"]),
                "minimum_hold_bars": int(trade["minimum_hold_bars"]),
                "maximum_hold_bars": int(trade["maximum_hold_bars"]),
                "entry_timestamp": str(trade["entry_timestamp"]), "closed_loop": trade.get("closed_loop"),
                "entry_bar_idx": int(entry_bar_idx), "governor_stop_price": float(trade["stop_price"]),
                "mfe_r": 0.0, "mae_r": 0.0, "pending": None, "stop_moves": 0,
                "v3_activation": None, "first_stop_move": None,
            }
            bay = SYMBOL_TO_BAY.get(symbol)
            if bay is not None:
                self._governors[name][bay].begin_position(hard_stop_r=-1.0, position_id=trade_id)

    def on_bar(self, orch, symbol, timestamp, bar, signal, composite_result, bar_idx, session_last_bar) -> None:
        for name, policy in self.policies.items():
            for trade_id in [t for t, s in self._open[name].items() if s["symbol"] == symbol]:
                shadow = self._open[name][trade_id]
                held = int(bar_idx) - shadow["entry_bar_idx"]
                if held < 0:
                    continue
                self._step(orch, name, policy, shadow, timestamp, bar, signal, composite_result, held,
                           session_last_bar)

    def on_run_end(self, orch, symbol_bars) -> None:
        for name in self.policies:
            for trade_id in list(self._open[name]):
                shadow = self._open[name][trade_id]
                bars = symbol_bars[shadow["symbol"]]
                last = bars.iloc[len(bars) - 1]
                self._close(orch, name, shadow, last.get("timestamp", ""), float(last["close"]),
                            "end_of_run_reconciliation", None)

    # ------------------------------------------------------------------ exit stack

    def _step(self, orch, name, policy, s, timestamp, bar, signal, composite_result, held, session_last_bar):
        o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        buy = s["side"] == "BUY"
        if pd.Timestamp(timestamp).strftime("%H:%M") >= orch.entry_decision_engine.config.force_close_time:
            return self._close(orch, name, s, timestamp, o, "force_close_time", held)
        halt_dd = min(float(orch.config.require("drawdown_halt_threshold")),
                      float(orch.safety_contract.values["safety_drawdown_halt_threshold"]))
        if orch._current_drawdown() >= halt_dd:
            return self._close(orch, name, s, timestamp, c, "forced_close_drawdown_halt", held)
        if orch._micom_trip is not None and orch._micom_trip["ansi_code"] != "MICOM_INPUT_UNAVAILABLE":
            return self._close(orch, name, s, timestamp, c, f"micom_trip:{orch._micom_trip['ansi_code']}", held)
        stop, target = s["governor_stop_price"], s["target_price"]
        if s["pending"] is not None:
            if (buy and o <= stop) or (not buy and o >= stop):
                return self._close(orch, name, s, timestamp, o, "stop_gap", held)
            if (buy and o >= target) or (not buy and o <= target):
                return self._close(orch, name, s, timestamp, o, "target_gap", held)
            return self._close(orch, name, s, timestamp, o, s["pending"], held)
        if buy:
            hit = ((o, "stop_gap") if o <= stop else (o, "target_gap") if o >= target
                   else (stop, "stop") if l <= stop else (target, "target") if h >= target else None)
        else:
            hit = ((o, "stop_gap") if o >= stop else (o, "target_gap") if o <= target
                   else (stop, "stop") if h >= stop else (target, "target") if l <= target else None)
        if hit is not None:
            return self._close(orch, name, s, timestamp, hit[0], hit[1], held)
        if session_last_bar:
            return self._close(orch, name, s, timestamp, c, "mis_session_close", held)
        if held >= s["maximum_hold_bars"]:
            return self._close(orch, name, s, timestamp, c, "max_hold", held)
        self._position_step(orch, name, policy, s, bar, signal, composite_result, held)

    def _position_step(self, orch, name, policy, s, bar, signal, composite_result, held):
        """Mirror of ``_governor_position_step`` (full authority) on the shadow's own state."""
        symbol = s["symbol"]
        bay = SYMBOL_TO_BAY.get(symbol)
        if bay is None:
            s["pending"] = "governor_exit:GOVERNOR_UNMAPPED_SYMBOL"
            return
        governor = self._governors[name][bay]
        sign = 1.0 if s["side"] == "BUY" else -1.0
        entry = s["entry_price"]
        risk = abs(entry - s["stop_price"])
        valid_risk = math.isfinite(risk) and risk > 0.0
        favorable = float(bar["high"]) if sign > 0 else float(bar["low"])
        adverse = float(bar["low"]) if sign > 0 else float(bar["high"])
        measured_r = sign * (float(bar["close"]) - entry) / risk if valid_risk else math.nan
        mfe_r = max(s["mfe_r"], sign * (favorable - entry) / risk) if valid_risk else math.nan
        if valid_risk:
            s["mfe_r"] = mfe_r
            s["mae_r"] = min(s["mae_r"], sign * (adverse - entry) / risk)
        try:
            conviction = orch._governor_conviction(symbol, s["side"], signal, composite_result, "pa_exit")
        except (GovernorInputError, TypeError, ValueError):
            conviction = math.nan
        reference_r = math.nan
        if s["closed_loop"] is not None:
            reference_r = orch.closed_loop.observe_trade_path(s["closed_loop"], float(bar["close"]), held).expected_r
        telemetry = orch._governor_telemetry.get(symbol)
        noise_r = (telemetry.atr / risk if telemetry is not None and telemetry.available and valid_risk
                   else None)
        result = position_decision(
            governor, orch.governor_config, position_id=s["trade_id"], measured_r=measured_r,
            reference_r=reference_r, max_favorable_r=mfe_r, elapsed_bars=int(held),
            min_hold_bars=s["minimum_hold_bars"], max_hold_bars=s["maximum_hold_bars"],
            trade_target_r=abs(s["target_price"] - entry) / risk if valid_risk else math.nan,
            conviction=conviction, drawdown=orch._current_drawdown(),
            velocity=telemetry.velocity if telemetry is not None and telemetry.available else None,
            session_bar=orch._governor_session_bar(symbol, getattr(orch, "_current_bar_idx", None)),
            bay_exhaust_spread=orch._bay_exhaust_spread.get(bay), hard_stop_r=-1.0,
            path_noise_r=noise_r,
            position_control=policy)
        self._record_transfer(s, result, held, noise_r)
        if result["action"] == "EXIT":
            s["pending"] = f"governor_exit:{result['reason']}"
            s["pending_source"] = result["reason"]
        elif result.get("protected_r_floor") is not None:
            proposed = (entry + float(result["protected_r_floor"]) * risk if s["side"] == "BUY"
                        else entry - float(result["protected_r_floor"]) * risk)
            before = s["governor_stop_price"]
            s["governor_stop_price"] = max(before, proposed) if s["side"] == "BUY" else min(before, proposed)
            s["stop_moves"] += int(s["governor_stop_price"] != before)
            if s["stop_moves"] == 1 and s["first_stop_move"] is None:
                s["first_stop_move"] = self._inner_state(result, held, noise_r)

    @staticmethod
    def _inner_state(result, held, noise_r):
        inner = result.get("inner") or {}
        v3 = inner.get("v3") or {}
        return {"held_bars": int(held), "noise_r": noise_r, "integral_error": inner.get("integral_error"),
                "error": inner.get("error"), "derivative": inner.get("derivative"),
                "control_u": inner.get("control_u"), "protected_r_floor": inner.get("protected_r_floor"),
                "max_favorable_r": inner.get("max_favorable_r"), "measured_r": inner.get("measured_r"),
                "gap_r": v3.get("gap_r"), "gamma": v3.get("gamma")}

    def _record_transfer(self, s, result, held, noise_r):
        """Bumpless-transfer diagnostic: the inner-loop state on the bar the V3 trailing actuator
        first becomes eligible (the activation latch)."""
        v3 = (result.get("inner") or {}).get("v3")
        if v3 and v3.get("activated_this_bar") and s["v3_activation"] is None:
            s["v3_activation"] = self._inner_state(result, held, noise_r)

    def _close(self, orch, name, s, timestamp, market_price, reason, held):
        close_side = "SELL" if s["side"] == "BUY" else "BUY"
        fill = paper_fill_price(market_price, close_side, orch.broker.slippage_fraction)
        q = s["quantity"]
        pnl = (fill - s["entry_price"]) * q if s["side"] == "BUY" else (s["entry_price"] - fill) * q
        costs = leg_cost(s["entry_price"], q, s["side"]) + leg_cost(fill, q, close_side)
        risk = abs(s["entry_price"] - s["stop_price"])
        sign = 1.0 if s["side"] == "BUY" else -1.0
        self.closed[name].append({
            "trade_id": s["trade_id"], "symbol": s["symbol"], "side": s["side"],
            "entry_timestamp": s["entry_timestamp"], "exit_timestamp": str(timestamp),
            "entry_price": s["entry_price"], "exit_price": fill, "quantity": q, "reason": reason,
            "pnl": pnl, "costs": costs, "net_pnl": pnl - costs, "bars_held": held,
            "realized_r": sign * (fill - s["entry_price"]) / risk if risk > 0 else None,
            "mfe_r": s["mfe_r"], "mae_r": s["mae_r"], "stop_moves": s["stop_moves"],
            "final_stop_price": s["governor_stop_price"],
            "v3_activation": s["v3_activation"], "first_stop_move": s["first_stop_move"],
            **activation_fields(s["v3_activation"]),
        })
        del self._open[name][s["trade_id"]]
        bay = SYMBOL_TO_BAY.get(s["symbol"])
        if bay is not None:
            self._governors[name][bay].confirm_position_closed(s["trade_id"])


ACTIVATION_FIELDS = {
    "activation_integral_error": "integral_error",   # I_t on the first actuator-eligible bar
    "activation_control_u": "control_u",
    "activation_mfe_r": "max_favorable_r",
    "activation_noise_r": "noise_r",
    "activation_gap_r": "gap_r",
}


def activation_fields(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flat per-trade activation telemetry; all None when the V3 actuator never became eligible."""
    return {name: (state or {}).get(key) for name, key in ACTIVATION_FIELDS.items()}


def validate_legacy(real_trades: List[Dict[str, Any]], shadow_trades: List[Dict[str, Any]],
                    tolerance: float = 1e-9) -> Dict[str, Any]:
    """The legacy shadow must reproduce every real trade: entry/exit timestamps, reason, quantity,
    entry and exit fills, gross P&L, costs and net P&L."""
    real = {t["trade_id"]: t for t in real_trades}
    shadow = {t["trade_id"]: t for t in shadow_trades}
    mismatches = []
    for trade_id in sorted(set(real) | set(shadow), key=str):
        a, b = real.get(trade_id), shadow.get(trade_id)
        if a is None or b is None:
            mismatches.append({"trade_id": trade_id, "missing": "real" if a is None else "shadow"})
            continue
        diffs = {k: (a[k], b[k]) for k in ("exit_timestamp", "reason", "quantity", "entry_timestamp")
                 if str(a[k]) != str(b[k])}
        for k in ("entry_price", "exit_price", "pnl", "costs", "net_pnl"):
            if abs(float(a[k]) - float(b[k])) > tolerance:
                diffs[k] = (a[k], b[k])
        if diffs:
            mismatches.append({"trade_id": trade_id, **{k: list(v) for k, v in diffs.items()}})
    return {"trades": len(real), "exact": not mismatches, "mismatches": mismatches[:50]}
