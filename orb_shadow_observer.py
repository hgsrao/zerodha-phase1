"""Pure, read-only Opening Range Breakout (ORB) observer - EXPLORATORY.

Distinct from external_momentum_shadow.py (validated via anchored walk-
forward, V11/V13/V14) and from shadow_strategy_evaluator.py (the existing
momentum_z/stability_z/OBI formula). This one has NOT been backtested at
all - it can't be, honestly: everything downloaded is 60-minute bars, and
ORB's whole premise (the first N minutes of a session set a range; a later
breakout of that range is a candidate) needs intraday resolution finer than
anything on disk. This module exists to observe the concept live, today,
as an exercise - not to claim a validated edge the way the momentum shadow
can.

Mechanics:
  1. For each symbol, the first `orb_minutes` of the session establish an
     "opening range" - snapshot the quote's own cumulative day-high/day-low
     (Kite's `ohlc.high`/`ohlc.low`) at the moment that window closes. Since
     nothing has happened yet beyond that window, the day's cumulative
     high/low AT that instant already IS the opening-range high/low - no
     need to hand-poll every tick during the window.
  2. Once established (once per symbol per trading day - never re-armed
     intraday), any later last_price crossing above the range high by more
     than `breakout_buffer_bps` is a breakout candidate. This project does
     not short, so only upside breakouts are tracked.
  3. Candidates are ranked by breakout strength (% above the range high);
     the strongest is reported, all are listed.

No broker SDK, runner, request_entry, or order-placement import here - a
caller supplies quotes it already fetched via read-only quote(). This
module has no capability to execute anything.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

MARKET_OPEN = dtime(9, 15)


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float
    established_at: str


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    import math
    return result if math.isfinite(result) and result > 0 else None


def load_state(path: Path) -> dict[str, Any]:
    if not Path(path).exists():
        return {"trading_day": None, "opening_ranges": {}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"trading_day": None, "opening_ranges": {}}
    if not isinstance(data, dict) or "opening_ranges" not in data:
        return {"trading_day": None, "opening_ranges": {}}
    return data


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    target = Path(path)
    if target.name.lower() in {"bot_state_v34.json", "bot_state_v34.lock"}:
        raise ValueError("Refusing to write a production state or lock file")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def _minutes_since_open(now: datetime) -> float:
    open_dt = now.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0)
    return (now - open_dt).total_seconds() / 60.0


def update_opening_ranges(
    state: dict[str, Any], *, quotes: Mapping[str, Mapping[str, Any]],
    universe: Iterable[str], now: datetime, orb_minutes: float = 15.0,
) -> dict[str, Any]:
    """Roll state over to a new trading day, then establish any opening
    ranges whose window has just closed. Never overwrites an already-
    established range - a symbol's range is fixed once per day."""
    today = now.date().isoformat()
    if state.get("trading_day") != today:
        state = {"trading_day": today, "opening_ranges": {}}

    elapsed = _minutes_since_open(now)
    if elapsed < orb_minutes:
        return state

    ranges = dict(state["opening_ranges"])
    for symbol in universe:
        if symbol in ranges:
            continue
        payload = quotes.get(f"NSE:{symbol}", quotes.get(symbol, {}))
        ohlc = payload.get("ohlc") if isinstance(payload, Mapping) else None
        if not isinstance(ohlc, Mapping):
            continue
        high, low = _finite(ohlc.get("high")), _finite(ohlc.get("low"))
        if high is None or low is None or low > high:
            continue
        ranges[symbol] = asdict(OpeningRange(high=high, low=low, established_at=now.isoformat()))
    state["opening_ranges"] = ranges
    return state


def evaluate_orb_breakouts(
    state: Mapping[str, Any], *, quotes: Mapping[str, Mapping[str, Any]],
    universe: Iterable[str], breakout_buffer_bps: float = 10.0,
) -> dict[str, Any]:
    ranges = state.get("opening_ranges", {})
    symbols = list(universe)
    candidates = []
    for symbol in symbols:
        orb = ranges.get(symbol)
        if orb is None:
            continue
        payload = quotes.get(f"NSE:{symbol}", quotes.get(symbol, {}))
        price = _finite(payload.get("last_price")) if isinstance(payload, Mapping) else None
        if price is None:
            continue
        breakout_level = orb["high"] * (1 + breakout_buffer_bps / 10_000)
        if price > breakout_level:
            candidates.append({
                "symbol": symbol, "last_price": price,
                "orb_high": orb["high"], "orb_low": orb["low"],
                "breakout_strength_pct": (price / orb["high"] - 1) * 100,
            })
    candidates.sort(key=lambda row: row["breakout_strength_pct"], reverse=True)

    if not ranges:
        decision = "ESTABLISHING_OPENING_RANGE"
    elif not candidates:
        decision = "NO_BREAKOUT_YET"
    else:
        decision = "HYPOTHETICAL_BUY"

    return {
        "mode": "READ_ONLY_SHADOW",
        "strategy_id": "ORB_EXPLORATORY",
        "authoritative_strategy": False,
        "backtested": False,
        "backtest_note": (
            "Not validated - only 60-minute historical bars exist for this "
            "project; ORB needs intraday resolution this project cannot "
            "honestly test. Live observation only."
        ),
        "decision": decision,
        "trading_day": state.get("trading_day"),
        "symbols_with_established_range": sorted(ranges),
        "candidates": candidates,
        "top_candidate": candidates[0] if candidates else None,
        "capabilities": {
            "broker_network": False,
            "order_api": False,
            "request_entry": False,
            "state_mutation": False,
        },
    }


def advance(
    state: dict[str, Any], *, quotes: Mapping[str, Mapping[str, Any]],
    universe: Iterable[str], now: datetime,
    orb_minutes: float = 15.0, breakout_buffer_bps: float = 10.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One poll cycle: roll/establish opening ranges, then evaluate
    breakouts against them. Returns (new_state, telemetry)."""
    universe = list(universe)
    new_state = update_opening_ranges(state, quotes=quotes, universe=universe, now=now, orb_minutes=orb_minutes)
    telemetry = evaluate_orb_breakouts(
        new_state, quotes=quotes, universe=universe, breakout_buffer_bps=breakout_buffer_bps,
    )
    return new_state, telemetry
