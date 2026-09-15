"""Pure, read-only strategy calculations for observation-only telemetry.

This module deliberately has no broker, Kite, runner, credential, state-store,
request_entry, or order-placement imports.  It can calculate hypothetical
decisions from caller-supplied snapshots, but cannot execute a trade.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Dict, Iterable, Mapping, Optional


@dataclass(frozen=True)
class Checkpoint:
    name: str
    passed: bool
    actual: str
    reason: str = ""


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_quote(raw_quote: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    """Validate the quote fields used by the experimental scoring formula."""
    ltp = _finite(raw_quote.get("last_price"))
    change = _finite(raw_quote.get("net_change"))
    ohlc = raw_quote.get("ohlc")
    if ltp is None or change is None or not isinstance(ohlc, Mapping):
        return None
    high = _finite(ohlc.get("high"))
    low = _finite(ohlc.get("low"))
    if ltp <= 0 or high is None or low is None or low <= 0 or high < low:
        return None
    return {
        "last_price": ltp,
        "net_change": change,
        "high": high,
        "low": low,
        "daily_range": high - low,
    }


def _z_scores(values: Mapping[str, float]) -> Dict[str, float]:
    if not values:
        return {}
    population = list(values.values())
    mean = fmean(population)
    deviation = pstdev(population)
    if deviation == 0:
        return {key: 0.0 for key in values}
    return {key: (value - mean) / deviation for key, value in values.items()}


def order_book_imbalance(depth: Mapping[str, Any]) -> Optional[float]:
    """Return (buy-sell)/(buy+sell), or None for malformed/empty depth."""
    try:
        buy = depth.get("buy")
        sell = depth.get("sell")
        if not isinstance(buy, list) or not isinstance(sell, list):
            return None
        buy_qty = sum(float(level["quantity"]) for level in buy)
        sell_qty = sum(float(level["quantity"]) for level in sell)
    except (KeyError, TypeError, ValueError):
        return None
    total = buy_qty + sell_qty
    if buy_qty < 0 or sell_qty < 0 or total <= 0:
        return None
    return (buy_qty - sell_qty) / total


def evaluate_shadow_strategy(
    *,
    quotes: Mapping[str, Mapping[str, Any]],
    universe: Iterable[str],
    depth_by_symbol: Mapping[str, Mapping[str, Any]],
    trial_capital: float = 20_000.0,
    obi_threshold: float = 0.2,
    risk_allowed: bool = False,
    risk_reason: str = "RISK_AUTHORIZATION_NOT_SUPPLIED",
) -> Dict[str, Any]:
    """Calculate a hypothetical candidate without producing any side effect."""
    symbols = list(universe)
    valid: Dict[str, Dict[str, float]] = {}
    for symbol in symbols:
        raw = quotes.get(f"NSE:{symbol}", quotes.get(symbol, {}))
        clean = validate_quote(raw) if isinstance(raw, Mapping) else None
        if clean is not None:
            valid[symbol] = clean

    quote_reason = "" if valid else "No valid quotes"
    coverage_reason = ""
    if len(valid) < 2:
        coverage_reason = (
            "At least two valid symbols are required for cross-sectional scoring"
        )
    checkpoints = [
        Checkpoint(
            name="quote_integrity",
            passed=bool(valid),
            actual=f"{len(valid)}/{len(symbols)} valid",
            reason=quote_reason,
        ),
        Checkpoint(
            name="universe_coverage",
            passed=len(valid) >= 2,
            actual=f"{len(valid)} valid symbols",
            reason=coverage_reason,
        ),
    ]
    if len(valid) < 2:
        return _result("BLOCK", None, {}, checkpoints)

    momentum = {symbol: quote["net_change"] for symbol, quote in valid.items()}
    stability = {
        symbol: 1.0 / ((quote["daily_range"] / quote["last_price"]) + 1e-6)
        for symbol, quote in valid.items()
    }
    momentum_z = _z_scores(momentum)
    stability_z = _z_scores(stability)
    scores = {
        symbol: 0.6 * momentum_z[symbol] + 0.4 * stability_z[symbol]
        for symbol in valid
    }
    selected = max(scores, key=scores.get)
    checkpoints.extend([
        Checkpoint("momentum", True, f"z={momentum_z[selected]:.6f}"),
        Checkpoint("stability", True, f"z={stability_z[selected]:.6f}"),
    ])

    ltp = valid[selected]["last_price"]
    tranche_qty = int((trial_capital / 2.0) / ltp) if trial_capital > 0 else 0
    checkpoints.append(Checkpoint(
        "affordability", tranche_qty > 0, f"tranche_qty={tranche_qty}",
        "Candidate is unaffordable" if tranche_qty <= 0 else "",
    ))

    obi = order_book_imbalance(depth_by_symbol.get(selected, {}))
    obi_pass = obi is not None and obi >= obi_threshold
    checkpoints.append(Checkpoint(
        "obi", obi_pass, "unavailable" if obi is None else f"{obi:.6f}",
        "Malformed/empty depth or OBI below threshold" if not obi_pass else "",
    ))
    checkpoints.append(Checkpoint(
        "risk_authorization", bool(risk_allowed),
        "allowed" if risk_allowed else "blocked", "" if risk_allowed else risk_reason,
    ))

    decision = "HYPOTHETICAL_BUY" if all(item.passed for item in checkpoints) else "BLOCK"
    candidate = {
        "symbol": selected,
        "last_price": ltp,
        "score": scores[selected],
        "tranche_qty": tranche_qty,
        "target_qty": tranche_qty * 2,
        "obi": obi,
    }
    return _result(decision, candidate, scores, checkpoints)


def _result(decision: str, candidate: Optional[Dict[str, Any]], scores: Dict[str, float],
            checkpoints: Iterable[Checkpoint]) -> Dict[str, Any]:
    return {
        "mode": "READ_ONLY_SHADOW",
        "authoritative_strategy": False,
        "decision": decision,
        "candidate": candidate,
        "scores": scores,
        "checkpoints": [asdict(item) for item in checkpoints],
        "capabilities": {
            "broker_network": False,
            "order_api": False,
            "request_entry": False,
            "state_mutation": False,
        },
    }


def write_telemetry_atomic(path: Path, telemetry: Mapping[str, Any]) -> None:
    """Atomically write display telemetry; never write the bot's state file."""
    target = Path(path)
    if target.name.lower() in {"bot_state_v34.json", "bot_state_v34.lock"}:
        raise ValueError("Refusing to write a production state or lock file")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(telemetry, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)
