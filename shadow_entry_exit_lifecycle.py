"""Separate, non-production shadow BUY/SELL lifecycle state."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


PROTECTED = {"bot_state_v34.json", "bot_state_v34.lock", "bot_production.log"}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    if path.name.lower() in PROTECTED:
        raise ValueError("refusing production state/log path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def advance(
    *, telemetry: Mapping[str, Any], quotes: Mapping[str, Mapping[str, Any]],
    state_path: Path, stop_loss_pct: Decimal = Decimal("0.02"),
) -> dict[str, Any] | None:
    """Open on hypothetical BUY; close on the production-style protective stop."""
    if stop_loss_pct <= 0 or stop_loss_pct >= 1:
        raise ValueError("invalid shadow stop loss percentage")
    state = {"status": "FLAT", "position": None}
    if state_path.exists():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid shadow lifecycle state")
        state = loaded

    if state.get("status") == "FLAT":
        candidate = telemetry.get("candidate") or {}
        if telemetry.get("decision") != "HYPOTHETICAL_BUY":
            return None
        entry = Decimal(str(candidate["last_price"]))
        quantity = int(candidate["target_qty"])
        stop = (entry * (Decimal("1") - stop_loss_pct)).quantize(Decimal("0.05"))
        position = {
            "symbol": str(candidate["symbol"]), "quantity": quantity,
            "entry_price": str(entry), "stop_price": str(stop),
        }
        _write(state_path, {"status": "OPEN", "position": position})
        return {"intent": "HYPOTHETICAL_BUY", **position,
                "execution_posture": "PHYSICALLY_UNAVAILABLE"}

    if state.get("status") != "OPEN" or not isinstance(state.get("position"), dict):
        raise ValueError("invalid shadow lifecycle state")
    position = state["position"]
    symbol = str(position["symbol"])
    quote = quotes.get(f"NSE:{symbol}", quotes.get(symbol, {}))
    ltp = Decimal(str(quote.get("last_price", "0"))) if isinstance(quote, Mapping) else Decimal("0")
    if ltp <= 0:
        return None
    stop = Decimal(str(position["stop_price"]))
    if ltp > stop:
        return None
    event = {
        "intent": "HYPOTHETICAL_SELL", "symbol": symbol,
        "quantity": int(position["quantity"]), "reference_price": str(ltp),
        "reason": "PROTECTIVE_STOP_TRIGGERED", "stop_price": str(stop),
        "execution_posture": "PHYSICALLY_UNAVAILABLE",
    }
    _write(state_path, {"status": "FLAT", "position": None})
    return event

