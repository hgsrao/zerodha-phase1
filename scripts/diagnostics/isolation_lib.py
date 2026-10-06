"""Pure helpers for the Block 1 isolation harness: exit quality, friction ledger, null baseline.

Diagnostic only.  Nothing here imports or modifies engine state; the frozen friction functions
(``revision2.transaction_costs``) are the only engine code it reuses.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence

FORWARD_BARS = 15
EXIT_CLASSES = ("DEFENSIVE_SAVE", "PREMATURE_CHOKE", "NOISE_CHURN", "CENSORED")

# Frozen replay friction (revision2/transaction_costs.py), itemized.  The model has no separate
# exchange-vs-SEBI split: "turnover" is its 0.00345% turnover-proportional leg, "stt" its
# sell-side 0.025% leg, "brokerage" min(Rs 20, 0.03% of turnover).
BROKERAGE_RATE, BROKERAGE_CAP = 0.0003, 20.0
TURNOVER_RATE = 0.0000345
STT_SELL_RATE = 0.00025


def leg_components(price: float, quantity: int, side: str) -> Dict[str, float]:
    turnover = float(price) * int(quantity)
    return {"brokerage": min(BROKERAGE_CAP, BROKERAGE_RATE * turnover),
            "turnover_charge": TURNOVER_RATE * turnover,
            "stt": STT_SELL_RATE * turnover if side == "SELL" else 0.0}


def slippage_per_share(fill_price: float, side: str, slippage_fraction: float) -> float:
    """Adverse paper-fill slippage in Rs/share, recovered from the filled price.

    ``paper_fill_price`` fills BUY at m*(1+s) and SELL at m*(1-s), so the market price is
    m = fill/(1+s) or fill/(1-s) and the slippage is |fill - m|."""
    s = float(slippage_fraction)
    return fill_price * s / (1.0 + s) if side == "BUY" else fill_price * s / (1.0 - s)


def trade_ledger_row(trade: Dict[str, Any], slippage_fraction: float) -> Dict[str, Any]:
    side = trade["side"]
    exit_side = "SELL" if side == "BUY" else "BUY"
    qty = int(trade["quantity"])
    entry, exit_ = float(trade["entry_price"]), float(trade["exit_price"])
    sign = 1.0 if side == "BUY" else -1.0
    risk = abs(float(trade["planned_entry_price"]) - float(trade["planned_stop_price"]))
    legs = (leg_components(entry, qty, side), leg_components(exit_, qty, exit_side))
    fees = {k: legs[0][k] + legs[1][k] for k in ("brokerage", "turnover_charge", "stt")}
    slippage = qty * (slippage_per_share(entry, side, slippage_fraction)
                      + slippage_per_share(exit_, exit_side, slippage_fraction))
    fee_total = sum(fees.values())
    gross_fill = sign * (exit_ - entry) * qty            # fill-to-fill: slippage already inside
    gross_frictionless = gross_fill + slippage
    friction = fee_total + slippage
    notional = entry * qty
    return {
        "trade_id": trade.get("trade_id"), "symbol": trade["symbol"], "side": side, "quantity": qty,
        "entry_timestamp": str(trade["entry_timestamp"]), "exit_timestamp": str(trade["exit_timestamp"]),
        "exit_reason": trade.get("reason"), "bars_held": trade.get("bars_held"),
        "entry_price": entry, "exit_price": exit_, "initial_risk_per_share": risk,
        "gross_r_fill": sign * (exit_ - entry) / risk if risk > 0 else None,
        "gross_r_frictionless": (gross_frictionless / (risk * qty)) if risk > 0 else None,
        "gross_pnl_fill_inr": gross_fill, "gross_pnl_frictionless_inr": gross_frictionless,
        "brokerage_inr": fees["brokerage"], "stt_inr": fees["stt"],
        "turnover_charge_inr": fees["turnover_charge"], "slippage_inr": slippage,
        "fees_inr": fee_total, "total_friction_inr": friction,
        "total_friction_bps": friction / notional * 1e4 if notional > 0 else None,
        "fees_bps": fee_total / notional * 1e4 if notional > 0 else None,
        "slippage_bps": slippage / notional * 1e4 if notional > 0 else None,
        "net_pnl_inr": gross_frictionless - friction,
        "engine_net_pnl_inr": trade.get("net_pnl"), "engine_costs_inr": trade.get("costs"),
        # Reconciles the itemized fees against the engine's own booked cost.
        "fee_reconciliation_error_inr": (fee_total - float(trade["costs"])) if trade.get("costs") is not None else None,
        "net_r": (gross_frictionless - friction) / (risk * qty) if risk > 0 else None,
    }


def summarize_ledger(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"trades": 0}
    tot = lambda k: sum(float(r[k]) for r in rows)
    notional = sum(float(r["entry_price"]) * int(r["quantity"]) for r in rows)
    mean = lambda k: sum(float(r[k]) for r in rows if r[k] is not None) / max(1, sum(r[k] is not None for r in rows))
    return {
        "trades": n,
        "gross_pnl_frictionless_inr": tot("gross_pnl_frictionless_inr"),
        "gross_pnl_fill_inr": tot("gross_pnl_fill_inr"),
        "brokerage_inr": tot("brokerage_inr"), "stt_inr": tot("stt_inr"),
        "turnover_charge_inr": tot("turnover_charge_inr"), "slippage_inr": tot("slippage_inr"),
        "total_friction_inr": tot("total_friction_inr"),
        "total_friction_bps_of_entry_notional": tot("total_friction_inr") / notional * 1e4 if notional else None,
        "net_pnl_inr": tot("net_pnl_inr"),
        "mean_gross_r_frictionless": mean("gross_r_frictionless"), "mean_net_r": mean("net_r"),
        "mean_friction_r": mean("gross_r_frictionless") - mean("net_r"),
        "win_rate_net": sum(r["net_pnl_inr"] > 0 for r in rows) / n,
        "max_fee_reconciliation_error_inr": max(abs(float(r["fee_reconciliation_error_inr"]))
                                                for r in rows if r["fee_reconciliation_error_inr"] is not None)
        if any(r["fee_reconciliation_error_inr"] is not None for r in rows) else None,
    }


# ----------------------------------------------------------------------------- exit quality

def classify_exit(side: str, stop: float, target: float, forward_bars: Sequence[Dict[str, float]],
                  horizon: int = FORWARD_BARS) -> Dict[str, Any]:
    """Counterfactual outcome of the ORIGINAL plan over up to ``horizon`` bars after the exit.

    ``forward_bars`` are the bars strictly after the exit bar, same session, in order.  A stop
    breach is high >= stop (SELL) / low <= stop (BUY); a target hit mirrors it.  If a single bar
    touches both, the order is unknowable from OHLC and the stop is assumed first (conservative:
    it credits the exit as a save, never as a choke).  A bar that reaches neither level for the
    whole horizon is NOISE_CHURN; fewer than ``horizon`` bars available with no touch is CENSORED."""
    buy = side == "BUY"
    bars = list(forward_bars)[:horizon]
    for i, bar in enumerate(bars, start=1):
        stop_hit = float(bar["low"]) <= stop if buy else float(bar["high"]) >= stop
        target_hit = float(bar["high"]) >= target if buy else float(bar["low"]) <= target
        if stop_hit:
            return {"exit_class": "DEFENSIVE_SAVE", "bars_to_event": i, "same_bar_ambiguous": target_hit,
                    "forward_bars_available": len(bars)}
        if target_hit:
            return {"exit_class": "PREMATURE_CHOKE", "bars_to_event": i, "same_bar_ambiguous": False,
                    "forward_bars_available": len(bars)}
    cls = "NOISE_CHURN" if len(bars) >= horizon else "CENSORED"
    return {"exit_class": cls, "bars_to_event": None, "same_bar_ambiguous": False,
            "forward_bars_available": len(bars)}


def exit_kind(reason: Optional[str]) -> str:
    """HARD for mechanical exits (stop/target/force-close/max-hold/risk trips), else the family."""
    text = str(reason or "")
    if text.startswith("governor_exit:"):
        body = text.split(":", 1)[1]
        if body.startswith("FSR_BELOW_EXIT") or body.startswith("FSRN"):
            return "CONVICTION_FSRN" if body.endswith("FSRN") or body.startswith("FSRN") else "LIMITER"
        return "GOVERNOR_" + body.split(":")[0]
    return "MECHANICAL:" + text.split(":")[0]


def summarize_exit_quality(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_class: Dict[str, int] = {c: 0 for c in EXIT_CLASSES}
    by_kind: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_class[r["exit_class"]] += 1
        by_kind.setdefault(r["exit_kind"], {c: 0 for c in EXIT_CLASSES})[r["exit_class"]] += 1
    return {"n": len(rows), "by_class": by_class, "by_exit_kind": by_kind}


# ----------------------------------------------------------------------------- null baseline

def wilder_atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14):
    out: List[Optional[float]] = [None] * len(closes)
    trs = []
    for i in range(len(closes)):
        pc = closes[i - 1] if i else closes[i]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    if len(closes) < period:
        return out
    atr = sum(trs[:period]) / period
    out[period - 1] = atr
    for i in range(period, len(closes)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def random_entry_null(sessions: Dict[str, List[List[Dict[str, float]]]], *, n_entries: int, seed: int,
                      stop_atr_mult: float, target_atr_mult: float, max_hold_bars: int,
                      risk_budget_inr: float, slippage_fraction: float, entry_window: tuple) -> List[Dict[str, Any]]:
    """Single-pass zero-alpha baseline: random side and bar, identical stop/target geometry.

    ``sessions[symbol]`` is a list of sessions, each a list of bar dicts (timestamp, open, high, low,
    close).  An entry fills at the NEXT bar's open with the frozen adverse slippage; the stop and
    target are ``stop_atr_mult``/``target_atr_mult`` x ATR14 from the planned (market) entry, sized
    to ``risk_budget_inr`` per trade.  The exit is the first of stop touch, target touch, max hold,
    or session end (stop wins an ambiguous bar).  Positions are exclusive per symbol.  All costs go
    through ``trade_ledger_row`` so the friction is the same frozen model as the engine's."""
    from revision2.transaction_costs import leg_cost, paper_fill_price
    rng = random.Random(seed)
    lo, hi = entry_window
    candidates = []
    for symbol, sess_list in sessions.items():
        for si, bars in enumerate(sess_list):
            atr = wilder_atr([b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars])
            for i in range(len(bars) - 2):
                if atr[i] is None or not (lo <= i <= hi):
                    continue
                candidates.append((symbol, si, i))
    rng.shuffle(candidates)
    busy: Dict[str, List[tuple]] = {}
    rows: List[Dict[str, Any]] = []
    for symbol, si, i in candidates:
        if len(rows) >= n_entries:
            break
        bars = sessions[symbol][si]
        atr = wilder_atr([b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars])[i]
        side = rng.choice(("BUY", "SELL"))
        sign = 1.0 if side == "BUY" else -1.0
        entry_bar = bars[i + 1]
        market = float(entry_bar["open"])
        stop_dist, target_dist = stop_atr_mult * atr, target_atr_mult * atr
        if stop_dist <= 0:
            continue
        fill = paper_fill_price(market, side, slippage_fraction)
        qty = int(risk_budget_inr // stop_dist)
        if qty < 1:
            continue
        stop, target = market - sign * stop_dist, market + sign * target_dist
        exit_i, exit_px, reason = len(bars) - 1, float(bars[-1]["close"]), "session_end"
        for j in range(i + 1, len(bars)):
            b = bars[j]
            held = j - (i + 1)
            if j > i + 1:
                stop_hit = float(b["low"]) <= stop if sign > 0 else float(b["high"]) >= stop
                tgt_hit = float(b["high"]) >= target if sign > 0 else float(b["low"]) <= target
                if stop_hit:
                    exit_i, exit_px, reason = j, stop, "stop"
                    break
                if tgt_hit:
                    exit_i, exit_px, reason = j, target, "target"
                    break
            if held >= max_hold_bars:
                exit_i, exit_px, reason = j, float(b["close"]), "max_hold"
                break
        spans = busy.setdefault(symbol, [])
        if any(si_ == si and not (exit_i < a or i + 1 > b_) for si_, a, b_ in spans):
            continue
        spans.append((si, i + 1, exit_i))
        exit_side = "SELL" if side == "BUY" else "BUY"
        exit_fill = paper_fill_price(exit_px, exit_side, slippage_fraction)
        trade = {"trade_id": f"null-{len(rows) + 1}", "symbol": symbol, "side": side, "quantity": qty,
                 "entry_price": fill, "exit_price": exit_fill, "planned_entry_price": market,
                 "planned_stop_price": stop, "planned_target_price": target,
                 "entry_timestamp": str(entry_bar.get("timestamp")), "exit_timestamp": str(bars[exit_i].get("timestamp")),
                 "reason": reason, "bars_held": exit_i - (i + 1),
                 "costs": leg_cost(fill, qty, side) + leg_cost(exit_fill, qty, exit_side),
                 "net_pnl": None}
        trade["net_pnl"] = sign * (exit_fill - fill) * qty - trade["costs"]
        rows.append(trade_ledger_row(trade, slippage_fraction))
    return rows
