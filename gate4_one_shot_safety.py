"""Offline one-shot Gate 4 integration safety exercise.

The harness consumes only cached JSON, copies all scenario state in memory, and
records complete order intents.  Its final dispatch seam is permanently locked;
this module has no Kite dependency, credentials, or network implementation.
"""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


class Gate4Locked(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LockedDispatchRecorder:
    """Capture the proposed payload and prove that dispatch cannot continue."""

    def __init__(self) -> None:
        self.intents: list[dict[str, Any]] = []
        self.external_broker_calls = 0

    def dispatch(self, payload: Mapping[str, Any]) -> None:
        self.intents.append(copy.deepcopy(dict(payload)))
        raise Gate4Locked("GATE_4_LOCKED: LIVE_TRADING_ENABLED=False")


def _entry_intent(candidate: Mapping[str, Any]) -> dict[str, Any]:
    symbol = str(candidate["symbol"]).strip().upper()
    price = Decimal(str(candidate["last_price"]))
    quantity = int(candidate["target_qty"])
    if not symbol or price <= 0 or quantity <= 0:
        raise ValueError("cached candidate is not usable for an offline scenario")
    return {
        "exchange": "NSE", "tradingsymbol": symbol,
        "transaction_type": "BUY", "product": "MIS",
        "order_type": "LIMIT", "quantity": quantity,
        "price": str(price), "tag": "V3.4_ENTRY",
    }


def _exit_intent(entry: Mapping[str, Any], tick_size: Decimal) -> dict[str, Any]:
    price = Decimal(str(entry["price"]))
    trigger = price - tick_size
    if tick_size <= 0 or trigger <= 0:
        raise ValueError("invalid simulated exit tick/trigger")
    return {
        "exchange": "NSE", "tradingsymbol": entry["tradingsymbol"],
        "transaction_type": "SELL", "product": "MIS",
        "order_type": "SL-M", "quantity": int(entry["quantity"]),
        "trigger_price": str(trigger), "market_protection": "-1",
        "tag": "V3.4_EXIT",
    }


def run_one_shot(
    *, shadow_path: Path, production_state_path: Path,
    production_runner_path: Path, tick_size: Decimal = Decimal("0.05"),
) -> dict[str, Any]:
    runner_text = production_runner_path.read_text(encoding="utf-8")
    if "LIVE_TRADING_ENABLED = False" not in runner_text:
        raise Gate4Locked("exact production live-trading lock is absent")
    if "LIVE_TRADING_ENABLED = True" in runner_text:
        raise Gate4Locked("unsafe live-trading assignment found")

    state_before = _digest(production_state_path)
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    production_state = json.loads(production_state_path.read_text(encoding="utf-8"))

    # Preserve the real observation exactly.  This cached observation may quite
    # correctly produce no entry; it is reported, never rewritten.
    observed_decision = str(shadow.get("decision", "BLOCK")).upper()
    observed_entry_generated = observed_decision == "ENTER"

    # Exercise generation with a clearly labelled, in-memory authorization copy.
    simulated_shadow = copy.deepcopy(shadow)
    simulated_shadow["mode"] = "OFFLINE_SIMULATED_AUTHORIZATION"
    entry = _entry_intent(simulated_shadow["candidate"])
    simulated_position = {
        "symbol": entry["tradingsymbol"], "quantity": entry["quantity"],
        "reference_price": entry["price"], "source": "IN_MEMORY_ONLY",
    }
    exit_order = _exit_intent(entry, tick_size)

    recorder = LockedDispatchRecorder()
    blocked = []
    for intent in (entry, exit_order):
        try:
            recorder.dispatch(intent)
        except Gate4Locked as exc:
            blocked.append(str(exc))

    state_after = _digest(production_state_path)
    return {
        "mode": "OFFLINE_ONE_SHOT_GATE4",
        "cached_observation": {
            "source": shadow.get("source"), "decision": observed_decision,
            "entry_generated": observed_entry_generated,
        },
        "production_state_observed": production_state.get("status"),
        "production_state_unchanged": state_before == state_after,
        "simulated_position": simulated_position,
        "captured_intents": recorder.intents,
        "gate4_blocks": blocked,
        "live_trading_enabled": False,
        "runner_started": False,
        "external_broker_calls": recorder.external_broker_calls,
    }

