#!/usr/bin/env python3
"""R5 entry-edge audit and loss decomposition (read-only diagnostic).

Question 1 -- do the entries have edge before any exit logic touches them?
For every completed trade in sealed V2 candidate results, measure the signed forward move from
the pre-slippage entry to the close of bar t+h (h = 1, 5, 15, 30, 60 and the session close), in
R (initial risk = |entry fill - planned stop|) and in basis points, plus the forward MFE/MAE over
the same bars -- independent of when V2 actually exited.  Each trade is paired with matched
controls: the same symbol and side, entered at random bars within +/-30 minutes of the trade's
time of day on the block's OTHER sessions (at the bar close, the engine's measured fill convention;
the report checks this against the next bar's open), measured the same way.  Same-session bars are
never controls: entries follow a run in their own direction, so a same-day control near the entry
would ride the move that triggered it.  "Entry edge" is trade minus control; if it is not positive,
the entry timing adds nothing over holding that stock, on that side, at that time of day.

Also reported: the move over the 5 and 15 bars *before* entry in the trade's direction (does the
entry buy a move that already happened?) and the forward result split by that pre-entry run.

Question 2 -- where does the money go?
Net P&L = market move - slippage - brokerage - exchange charges - STT, per trade, using the
engine's own cost formula (revision2.transaction_costs.leg_cost) and slippage fraction.  The
round-trip friction is also expressed in R, which shows whether costs alone exceed the risk unit.

Inputs are the worker's candidate result JSONs (they carry every completed trade per block) and
the frozen 1-minute data, loaded through the sealed worker's own prepare_block.  Nothing is
written except outputs/diagnostics/<label>/report.json.  Step-6 verification state is refused.

    python scripts/diagnostics/r5_entry_edge_audit.py --label v2_stage_a \\
        --results outputs/r5_step5_stage_a_v2_state/results/trial_00*.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUTPUT_ROOT = ROOT / "outputs/diagnostics"
DEFAULT_PROTOCOL = ROOT / "revision5/step5_sealed_calibration_protocol_v2.json"
# 375 bars = a full NSE session, so that horizon always runs to the session close ("EOD").
HORIZONS = (1, 5, 15, 30, 60, 375)
CONTROL_WINDOW_BARS = 30
PRE_RUNS = (5, 15)
REGRET_BARS = (1, 3, 5, 10)
MFE_LADDER = (0.10, 0.30, 0.50, 1.00)
BOOTSTRAP_SEED = 20260930


# ------------------------------------------------------------------ costs (engine formula, split)

def leg_cost_parts(price: float, quantity: float, side: str) -> dict:
    """revision2.transaction_costs.leg_cost, itemized."""
    turnover = price * quantity
    return {"brokerage": min(20.0, 0.0003 * turnover), "exchange": 0.0000345 * turnover,
            "stt": 0.00025 * turnover if side == "SELL" else 0.0}


def decompose(trade: dict, slippage_fraction: float) -> dict:
    side, qty = trade["side"], float(trade["quantity"])
    entry, exit_ = float(trade["entry_price"]), float(trade["exit_price"])
    close_side = "SELL" if side == "BUY" else "BUY"
    parts = defaultdict(float)
    for price, leg_side in ((entry, side), (exit_, close_side)):
        for k, v in leg_cost_parts(price, qty, leg_side).items():
            parts[k] += v
    # Fills already include slippage (paper_fill_price: market*(1+s) to buy, market*(1-s) to sell);
    # recover the pre-slippage market move.
    def leg_slippage(fill, leg_side):
        return fill * slippage_fraction / ((1.0 + slippage_fraction) if leg_side == "BUY"
                                           else (1.0 - slippage_fraction))
    slippage = (leg_slippage(entry, side) + leg_slippage(exit_, close_side)) * qty
    pnl = float(trade["pnl"])
    return {"market": pnl + slippage, "slippage": slippage, **parts,
            "costs_recorded": float(trade["costs"]), "costs_model": sum(parts.values()),
            "net": float(trade["net_pnl"])}


# ------------------------------------------------------------------ forward edge

def _session_bounds(ts: pd.Series) -> np.ndarray:
    """For each row, the index of the last row of the same calendar session."""
    dates = ts.dt.date.to_numpy()
    last = np.empty(len(dates), dtype=int)
    end = len(dates) - 1
    for i in range(len(dates) - 1, -1, -1):
        if i < len(dates) - 1 and dates[i] != dates[i + 1]:
            end = i
        last[i] = end
    return last


def _session_first(ts: pd.Series) -> np.ndarray:
    dates = ts.dt.date.to_numpy()
    first = np.empty(len(dates), dtype=int)
    start = 0
    for i in range(len(dates)):
        if i > 0 and dates[i] != dates[i - 1]:
            start = i
        first[i] = start
    return first


class SymbolBars:
    def __init__(self, frame: pd.DataFrame):
        frame = frame.sort_values("timestamp").reset_index(drop=True)
        self.ts = pd.to_datetime(frame["timestamp"])
        self.open = frame["open"].to_numpy(float)
        self.high = frame["high"].to_numpy(float)
        self.low = frame["low"].to_numpy(float)
        self.close = frame["close"].to_numpy(float)
        self.last = _session_bounds(self.ts)
        self.first = _session_first(self.ts)
        self._ns = self.ts.astype("int64").to_numpy()
        self.session = np.cumsum(np.r_[0, (self.first[1:] != self.first[:-1]).astype(int)])
        self.tod = (self.ts.dt.hour * 60 + self.ts.dt.minute).to_numpy()

    def index_of(self, timestamp) -> int | None:
        stamp = pd.Timestamp(timestamp)
        if stamp.tzinfo is None and self.ts.dt.tz is not None:
            stamp = stamp.tz_localize(self.ts.dt.tz)
        elif self.ts.dt.tz is not None:
            stamp = stamp.tz_convert(self.ts.dt.tz)
        i = int(np.searchsorted(self._ns, stamp.value, side="left"))
        return i if i < len(self._ns) else None

    def forward(self, idx: int, ref: float, sign: float, risk: float) -> dict:
        """Signed forward move from ref (the fill) over bars idx+1..idx+h, capped at the session."""
        out = {}
        end = self.last[idx]
        for h in HORIZONS:
            j = min(idx + h, end)
            if j <= idx:
                out[h] = None
                continue
            move = sign * (self.close[j] - ref)
            fav = (self.high[idx + 1:j + 1] if sign > 0 else self.low[idx + 1:j + 1])
            adv = (self.low[idx + 1:j + 1] if sign > 0 else self.high[idx + 1:j + 1])
            out[h] = {"r": move / risk, "bps": move / ref * 1e4, "truncated": idx + h > end,
                      "mfe_r": float(np.max(sign * (fav - ref))) / risk,
                      "mae_r": float(np.min(sign * (adv - ref))) / risk,
                      "exit_ref": float(self.close[j])}
        return out

    def after_exit(self, ex: int, ref: float, sign: float, risk: float) -> dict:
        """Exit regret: what the position would have done over the k bars after the exit bar,
        from the pre-slippage exit price, capped at the session.  Positive = exited too early."""
        out = {}
        end = self.last[ex]
        for k in REGRET_BARS:
            j = min(ex + k, end)
            if j <= ex:
                out[k] = None
                continue
            fav = self.high[ex + 1:j + 1] if sign > 0 else self.low[ex + 1:j + 1]
            adv = self.low[ex + 1:j + 1] if sign > 0 else self.high[ex + 1:j + 1]
            out[k] = {"close_r": sign * (self.close[j] - ref) / risk,
                      "best_r": float(np.max(sign * (fav - ref))) / risk,
                      "worst_r": float(np.min(sign * (adv - ref))) / risk}
        return out

    def in_trade_excursion(self, idx: int, ex: int, ref: float, sign: float, risk: float) -> tuple:
        """MFE/MAE from the pre-slippage entry over the bars after entry up to the exit bar."""
        if ex <= idx:
            return 0.0, 0.0
        fav = self.high[idx + 1:ex + 1] if sign > 0 else self.low[idx + 1:ex + 1]
        adv = self.low[idx + 1:ex + 1] if sign > 0 else self.high[idx + 1:ex + 1]
        return (max(0.0, float(np.max(sign * (fav - ref))) / risk),
                min(0.0, float(np.min(sign * (adv - ref))) / risk))

    def pre_run(self, idx: int, ref: float, sign: float, risk: float) -> dict:
        out = {}
        for k in PRE_RUNS:
            j = idx - k
            out[k] = (sign * (ref - self.close[j]) / risk) if j >= self.first[idx] else None
        return out

    def controls(self, idx: int, sign: float, risk: float, n: int, seed: int,
                 window: int = CONTROL_WINDOW_BARS, hits: dict | None = None) -> dict:
        """Matched controls: same symbol, side and risk unit, entered at the bar close (the
        engine's measured fill convention) at random bars within +/-window minutes of the trade's
        time of day -- on the OTHER sessions in the frame.  Same-session bars are excluded: every
        entry follows a run in its own direction, so a same-day control near the entry would ride
        the very move that triggered it (look-ahead).  Returns the mean control move per horizon."""
        if n <= 0:
            return {h: None for h in HORIZONS}
        pool = np.flatnonzero((self.session != self.session[idx])
                              & (np.abs(self.tod - self.tod[idx]) <= window)
                              & (np.arange(len(self.close)) < self.last))
        if len(pool) == 0:
            return {h: None for h in HORIZONS}
        out = {}
        for h in HORIZONS:
            # Full horizon where that session allows it; the session-close horizon runs to each
            # control's own session close, as the trade's does.
            full = pool[pool + h <= self.last[pool]]
            candidates = full if len(full) else pool
            picks = np.random.default_rng(seed + h).choice(candidates, size=n, replace=True)
            out[h] = float(np.mean([sign * (self.close[min(k + h, self.last[k])] - self.close[k]) / risk
                                    for k in picks]))
            if hits is not None:
                mfe = []
                for k in picks:
                    j = min(k + h, self.last[k])
                    fav = self.high[k + 1:j + 1] if sign > 0 else self.low[k + 1:j + 1]
                    mfe.append(max(0.0, float(np.max(sign * (fav - self.close[k]))) / risk) if j > k else 0.0)
                hits[h] = {f">={r:.2f}R": float(np.mean([m >= r for m in mfe])) for r in MFE_LADDER}
        return out


def exit_class(reason: str) -> str:
    reason = str(reason)
    if reason.startswith("governor_exit:FSR"):
        return "FSR_EXIT"
    if reason.startswith("governor_exit:"):
        return "GOVERNOR_OTHER"
    if reason in ("stop", "stop_gap"):
        return "STOP"
    if reason in ("target", "target_gap"):
        return "TARGET"
    if reason.startswith("micom_trip"):
        return "MICOM"
    return "TIME_OR_SESSION"


def trade_row(trade: dict, bars: SymbolBars, slippage_fraction: float, controls: int) -> dict:
    sign = 1.0 if trade["side"] == "BUY" else -1.0
    entry = float(trade["entry_price"])
    stop = trade.get("planned_stop_price")
    risk = abs(entry - float(stop)) if stop is not None else float("nan")
    reason = str(trade["reason"])
    row = {"trade_id": trade.get("trade_id"), "symbol": trade["symbol"], "side": trade["side"],
           "entry_timestamp": str(trade["entry_timestamp"]), "exit_class": exit_class(reason),
           "exit_reason": reason, "exit_timestamp": str(trade.get("exit_timestamp")),
           "fsr_channel": reason.split("FSR_BELOW_EXIT:", 1)[1] if "FSR_BELOW_EXIT:" in reason else None,
           "recorded_mfe_r": trade.get("mfe_r"), "recorded_mae_r": trade.get("mae_r"),
           "bars_held": trade.get("bars_held"), "risk_bps": risk / entry * 1e4 if math.isfinite(risk) else None,
           "realized_r": None, "skip": None}
    decomp = decompose(trade, slippage_fraction)
    row["decomposition"] = decomp
    if not (math.isfinite(risk) and risk > 0):
        row["skip"] = "no_initial_risk"
        return row
    qty = float(trade["quantity"])
    row["realized_r"] = sign * (float(trade["exit_price"]) - entry) / risk
    row["friction_r"] = (decomp["slippage"] + decomp["costs_model"]) / (qty * risk)
    row["market_r"] = decomp["market"] / (qty * risk)      # pulse energy: pre-slippage move captured
    # Inputs for re-pricing the same trade at another slippage: pre-slippage leg prices over risk,
    # the engine's charges in R, and the rupee scale.
    sign_ = 1.0 if trade["side"] == "BUY" else -1.0
    row["market_entry_price"] = entry / (1.0 + sign_ * slippage_fraction)
    row["market_exit_price"] = float(trade["exit_price"]) / (1.0 - sign_ * slippage_fraction)
    row["risk"], row["quantity"] = risk, qty
    row["charges_r"] = decomp["costs_model"] / (qty * risk)
    row["friction_bps"] = (decomp["slippage"] + decomp["costs_model"]) / (qty * entry) * 1e4
    idx = bars.index_of(trade["entry_timestamp"])
    if idx is None:
        row["skip"] = "entry_bar_not_found"
        return row
    # Timing is measured from the pre-slippage market entry, exactly as the controls are; all
    # friction (both legs' slippage and charges) is charged only in forward_net_r.
    market_entry = entry / (1.0 + sign * slippage_fraction)
    row["entry_hour"] = int(bars.ts.iloc[idx].hour)
    row["entry_bar_close_r"] = sign * (bars.close[idx] - market_entry) / risk   # market entry vs its bar's close
    row["next_bar_open_r"] = (sign * (bars.open[idx + 1] - market_entry) / risk
                              if idx + 1 <= bars.last[idx] else None)            # ... vs the next bar's open
    row["forward"] = bars.forward(idx, market_entry, sign, risk)
    row["pre_run_r"] = bars.pre_run(idx, market_entry, sign, risk)
    ex = bars.index_of(trade["exit_timestamp"])
    if ex is not None and ex >= idx:
        market_exit = float(trade["exit_price"]) / (1.0 - sign * slippage_fraction)
        row["exit_regret"] = bars.after_exit(ex, market_exit, sign, risk)
        row["mfe_r_market"], row["mae_r_market"] = bars.in_trade_excursion(idx, ex, market_entry, sign, risk)
    else:
        row["exit_regret"], row["mfe_r_market"], row["mae_r_market"] = None, None, None
    seed = int(hashlib.sha256(f"{row['trade_id']}|{row['entry_timestamp']}|{row['symbol']}".encode())
               .hexdigest()[:8], 16)
    # Random timing also produces MFE that grows with the horizon (a random walk's running maximum
    # grows like sqrt(h)), so the fixed-horizon ladder is only meaningful against the controls'.
    row["control_mfe_hits"] = {}
    row["control_r"] = bars.controls(idx, sign, risk, controls, seed, hits=row["control_mfe_hits"])
    # Friction of a round trip exiting at the horizon close: both legs' slippage + charges, in R.
    entry_slip_r = (entry - market_entry) * sign / risk
    charges_r = decomp["costs_model"] / (qty * risk)
    row["forward_net_r"] = {h: (f["r"] - entry_slip_r - slippage_fraction * f["exit_ref"] / risk - charges_r)
                            if f else None for h, f in row["forward"].items()}
    return row


# ------------------------------------------------------------------ aggregation

def _stats(values, clusters=None, seed=BOOTSTRAP_SEED, draws=2000) -> dict:
    """Mean/median/share-positive with a bootstrap 95% CI.  With ``clusters`` (one key per value,
    the trading session), whole sessions are resampled: trades on the same day share the market's
    regime and shocks, and several candidates replaying the same days are not independent, so a
    trade-level bootstrap is overconfident.  ``ci95_trade`` keeps the naive interval for reference."""
    pairs = [(v, c) for v, c in zip(values, clusters if clusters is not None else [None] * len(values))
             if v is not None and math.isfinite(v)]
    if not pairs:
        return {"n": 0}
    x = np.array([v for v, _ in pairs], dtype=float)
    rng = np.random.default_rng(seed)
    trade_boot = rng.choice(x, size=(draws, len(x)), replace=True).mean(axis=1) if len(x) > 1 else x
    out = {"n": int(len(x)), "mean": float(x.mean()), "median": float(np.median(x)),
           "share_positive": float((x > 0).mean()),
           "ci95_trade": [float(np.percentile(trade_boot, 2.5)), float(np.percentile(trade_boot, 97.5))]}
    if clusters is None:
        out["ci95"] = out["ci95_trade"]
        return out
    keys = sorted({c for _, c in pairs}, key=str)
    index = {k: i for i, k in enumerate(keys)}
    sums, counts = np.zeros(len(keys)), np.zeros(len(keys))
    for v, c in pairs:
        sums[index[c]] += v
        counts[index[c]] += 1
    if len(keys) > 1:
        pick = rng.integers(0, len(keys), size=(draws, len(keys)))
        boots = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
        out["ci95"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
    else:
        out["ci95"] = [float("nan"), float("nan")]          # one session: no sampling distribution
    out["clusters"] = len(keys)
    return out


def _session_key(row: dict) -> str:
    return str(row["entry_timestamp"])[:10]


def _S(rows: list, fn) -> dict:
    """_stats over fn(row) for each row, clustered by trading session; rows where fn fails or
    returns a non-finite value are skipped."""
    values, clusters = [], []
    for r in rows:
        try:
            v = fn(r)
        except (KeyError, TypeError, IndexError):
            continue
        if v is None or not math.isfinite(v):
            continue
        values.append(float(v))
        clusters.append(_session_key(r))
    return _stats(values, clusters)


WIDTH_BUCKETS = ((1, 3), (4, 10), (11, 30), (31, 10_000))


def pulse_train(rows: list) -> dict:
    """The system's output seen as an exposure pulse train per symbol: leading edge (entry),
    width (bars held), trailing edge (exit), off-interval to the next pulse on the same symbol and
    session, and polarity.  Each pulse pays a switching loss (round-trip friction, in R); its
    energy is the pre-slippage market move captured (in R).  A pulse pays only if energy > loss."""
    if not rows:
        return {}
    widths = [r["bars_held"] for r in rows if r.get("bars_held") is not None]
    sessions = defaultdict(list)
    for r in rows:
        sessions[(r.get("source"), r["symbol"], r["entry_timestamp"][:10])].append(r)
    gaps, flips, pulses_per_symbol_session = [], 0, []
    for group_rows in sessions.values():
        group_rows.sort(key=lambda r: r["entry_timestamp"])
        pulses_per_symbol_session.append(len(group_rows))
        for a, b in zip(group_rows, group_rows[1:]):
            try:
                gaps.append((pd.Timestamp(b["entry_timestamp"]) - pd.Timestamp(a["exit_timestamp"])).total_seconds() / 60)
            except (ValueError, TypeError):
                continue
            flips += a["side"] != b["side"]
    by_width = {}
    for lo, hi in WIDTH_BUCKETS:
        sel = [r for r in rows if r.get("bars_held") is not None and lo <= r["bars_held"] <= hi]
        if sel:
            by_width[f"{lo}-{hi if hi < 10_000 else 'max'}"] = {
                "pulses": len(sel), "energy_r": _stats([r["market_r"] for r in sel]).get("mean"),
                "switching_loss_r": _stats([r["friction_r"] for r in sel]).get("mean"),
                "net_r": _stats([r["market_r"] - r["friction_r"] for r in sel]).get("mean"),
                "net_r_ci95": _S(sel, lambda r: r["market_r"] - r["friction_r"]).get("ci95"),
                "share_energy_gt_loss": float(np.mean([r["market_r"] > r["friction_r"] for r in sel]))}
    g = np.array(gaps) if gaps else np.array([np.nan])
    return {
        "pulses": len(rows),
        "polarity_share_sell": float(np.mean([r["side"] == "SELL" for r in rows])),
        "width_bars": {"median": float(np.median(widths)) if widths else None,
                       "p25": float(np.percentile(widths, 25)) if widths else None,
                       "p75": float(np.percentile(widths, 75)) if widths else None,
                       "share_le_3": float(np.mean([w <= 3 for w in widths])) if widths else None},
        "pulses_per_active_symbol_session": {"mean": float(np.mean(pulses_per_symbol_session)),
                                             "max": int(max(pulses_per_symbol_session))},
        "off_interval_minutes": {"n": len(gaps), "median": float(np.nanmedian(g)) if gaps else None,
                                 "share_le_5": float(np.mean(g <= 5)) if gaps else None,
                                 "share_le_15": float(np.mean(g <= 15)) if gaps else None},
        "polarity_flips_within_session": flips,
        "energy_r": _S(rows, lambda r: r["market_r"]),
        "switching_loss_r": _S(rows, lambda r: r["friction_r"]),
        "by_width_bars": by_width,
    }


SLIPPAGE_SWEEP_BPS = (0.0, 1.0, 2.0, 3.0, 5.0)
SWEEP_HORIZONS = (15, 30, 60, 375)


def friction_sweep(rows: list) -> dict:
    """Same signals and price paths, re-priced at other per-leg slippage (charges unchanged):
    separates a weak signal (loses even at 0 bps) from an execution problem (pays at 0-2 bps)
    and from a direction-only edge (only the session-close hold pays)."""
    out = {}
    for bps in SLIPPAGE_SWEEP_BPS:
        s = bps / 1e4
        entry = {
            "realized_net_r": _S(rows, lambda r: r["market_r"] - s * (r["market_entry_price"] + r["market_exit_price"])
                                 / r["risk"] - r["charges_r"]),
            "realized_net_rupees": float(sum(
                r["decomposition"]["market"] - s * (r["market_entry_price"] + r["market_exit_price"]) * r["quantity"]
                - r["decomposition"]["costs_model"] for r in rows if "market_entry_price" in r)),
            "fixed_hold_net_r": {},
        }
        for h in SWEEP_HORIZONS:
            entry["fixed_hold_net_r"][str(h)] = _S(
                rows, lambda r: r["forward"][h]["r"] - s * (r["market_entry_price"] + r["forward"][h]["exit_ref"])
                / r["risk"] - r["charges_r"])
        out[f"{bps:g}"] = entry
    return out


def summarize(rows: list) -> dict:
    ok = [r for r in rows if r.get("skip") is None]
    out = {"trades": len(rows), "measured": len(ok),
           "skipped": dict(pd.Series([r["skip"] for r in rows if r.get("skip")]).value_counts())
           if any(r.get("skip") for r in rows) else {}}
    out["sessions"] = len({_session_key(r) for r in ok})
    out["risk_bps"] = _S(ok, lambda r: r["risk_bps"])
    out["friction_bps"] = _S(ok, lambda r: r["friction_bps"])
    out["friction_r"] = _S(ok, lambda r: r["friction_r"])
    out["realized_r"] = _S(ok, lambda r: r["realized_r"])
    out["entry_bar_close_r"] = _S(ok, lambda r: r["entry_bar_close_r"])
    out["next_bar_open_r"] = _S(ok, lambda r: r.get("next_bar_open_r"))
    fwd = {}
    for h in HORIZONS:
        f = [r["forward"][h] for r in ok if r["forward"][h]]
        fwd[str(h)] = {
            "gross_r": _S(ok, lambda r: r["forward"][h]["r"]),
            "net_r": _S(ok, lambda r: r["forward_net_r"][h]),
            "control_r": _S(ok, lambda r: r["control_r"][h]),
            "edge_vs_control_r": _S(ok, lambda r: r["forward"][h]["r"] - r["control_r"][h]),
            "bps": _S(ok, lambda r: r["forward"][h]["bps"]),
            "mfe_r": _S(ok, lambda r: r["forward"][h]["mfe_r"]),
            "mae_r": _S(ok, lambda r: r["forward"][h]["mae_r"]),
            "truncated_share": float(np.mean([x["truncated"] for x in f])) if f else None,
        }
    out["forward"] = fwd
    # Does the entry buy a move that already happened?  Forward 15-bar R by pre-entry 15-bar run.
    runs = [(r["pre_run_r"][15], r["forward"][15]["r"], _session_key(r)) for r in ok
            if r["pre_run_r"][15] is not None and r["forward"][15]]
    if len(runs) >= 3:
        cut = np.quantile([a for a, _, _ in runs], [1 / 3, 2 / 3])
        buckets = {"low": [], "mid": [], "high": []}
        for a, b, c in runs:
            buckets["low" if a <= cut[0] else "mid" if a <= cut[1] else "high"].append((a, b, c))
        out["pre_run_15_terciles"] = {
            k: {"pre_run_r": _stats([a for a, _, _ in v], [c for _, _, c in v]),
                "forward_15_r": _stats([b for _, b, _ in v], [c for _, _, c in v])}
            for k, v in buckets.items()}
        out["pre_run_15_forward_15_corr"] = float(np.corrcoef([a for a, _, _ in runs], [b for _, b, _ in runs])[0, 1])
    out["pre_run_r"] = {str(k): _stats([r["pre_run_r"][k] for r in ok]) for k in PRE_RUNS}
    # Money decomposition over every trade (skipped ones still have P&L).
    keys = ("market", "slippage", "brokerage", "exchange", "stt", "net")
    out["decomposition_total"] = {k: float(sum(r["decomposition"][k] for r in rows)) for k in keys}
    out["costs_model_vs_recorded_max_abs_diff"] = float(max(
        (abs(r["decomposition"]["costs_model"] - r["decomposition"]["costs_recorded"]) for r in rows),
        default=0.0))

    def group(key):
        g = defaultdict(list)
        for r in ok:
            g[r.get(key)].append(r)
        return {str(k): {"trades": len(v), "net": float(sum(x["decomposition"]["net"] for x in v)),
                         "market": float(sum(x["decomposition"]["market"] for x in v)),
                         "fwd15_gross_r_mean": _stats([x["forward"][15]["r"] for x in v if x["forward"][15]]).get("mean"),
                         "edge15_vs_control_mean": _stats([x["forward"][15]["r"] - x["control_r"][15] for x in v
                                                           if x["forward"][15] and x["control_r"][15] is not None]).get("mean")}
                for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}

    # Did the entries ever have room?  Share of trades whose in-trade MFE reached each rung, on the
    # engine's recorded basis (from the fill, i.e. after entry slippage) and from the market entry.
    def ladder(values):
        v = [x for x in values if x is not None and math.isfinite(x)]
        if not v:
            return {"n": 0}
        return {"n": len(v), "median": float(np.median(v)), "zero_or_less": float(np.mean([x <= 0 for x in v])),
                **{f">={r:.2f}R": float(np.mean([x >= r for x in v])) for r in MFE_LADDER}}
    out["mfe_ladder_recorded"] = ladder([r.get("recorded_mfe_r") for r in rows])
    out["mfe_ladder_market"] = ladder([r.get("mfe_r_market") for r in ok])
    # Exit-independent opportunity: forward MFE over a fixed horizon, whatever V2 actually did.
    out["mfe_ladder_fixed"] = {str(h): ladder([max(0.0, r["forward"][h]["mfe_r"]) for r in ok if r["forward"][h]])
                               for h in HORIZONS}
    out["mfe_ladder_fixed_control"] = {
        str(h): {rung: float(np.mean([r["control_mfe_hits"][h][rung] for r in ok if r.get("control_mfe_hits", {}).get(h)]))
                 for rung in (f">={x:.2f}R" for x in MFE_LADDER)}
        if any(r.get("control_mfe_hits", {}).get(h) for r in ok) else {} for h in HORIZONS}
    out["mae_median_recorded"] = _stats([r.get("recorded_mae_r") for r in rows]).get("median")

    # Exit authority and regret, per exit reason (FSR exits carry their controlling channel).
    def regret(group_rows):
        res = {"trades": len(group_rows), "net": float(sum(x["decomposition"]["net"] for x in group_rows)),
               "mfe_r_market_median": _stats([x.get("mfe_r_market") for x in group_rows]).get("median")}
        for k in REGRET_BARS:
            vals = [x["exit_regret"][k] for x in group_rows if x.get("exit_regret") and x["exit_regret"][k]]
            res[f"k{k}"] = {"close_r": _S(group_rows, lambda x: x["exit_regret"][k]["close_r"]),
                            "best_r_median": _stats([v["best_r"] for v in vals]).get("median"),
                            "share_best_ge_0.30R": float(np.mean([v["best_r"] >= 0.30 for v in vals])) if vals else None,
                            "share_worst_le_-0.30R": float(np.mean([v["worst_r"] <= -0.30 for v in vals])) if vals else None}
        return res
    by_reason = defaultdict(list)
    for r in ok:
        by_reason[r["exit_reason"]].append(r)
    out["exit_authority"] = {k: regret(v) for k, v in sorted(by_reason.items(), key=lambda kv: -len(kv[1]))}
    fsr = [r for r in ok if r.get("fsr_channel")]
    out["fsr_channels"] = {ch: sum(1 for r in fsr if r["fsr_channel"] == ch)
                           for ch in sorted({r["fsr_channel"] for r in fsr})}
    out["pulse_train"] = pulse_train(ok)
    out["friction_sweep"] = friction_sweep(ok)
    out["by_side"] = group("side")
    out["by_entry_hour"] = group("entry_hour")
    out["by_exit_class"] = group("exit_class")
    by_symbol = group("symbol")
    out["worst_symbols"] = dict(sorted(by_symbol.items(), key=lambda kv: kv[1]["net"])[:10])
    return out


# ------------------------------------------------------------------ driver

def _worker():
    spec = importlib.util.spec_from_file_location("r5_step5_worker", ROOT / "scripts/run_r5_step5_candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _protocol_block(protocol: dict, sessions: list) -> dict:
    for stage in ("stage_a", "stage_b"):
        for block in protocol["sampling_plan"][stage]:
            if list(block["sessions"]) == list(sessions):
                return block
    raise SystemExit(f"BLOCK_NOT_IN_PROTOCOL: {sessions[0]}..{sessions[-1]}")


STAGE_C_EXPORT_KIND = "r5_stage_c_train_v1"


def _block_for(result: dict, protocol: dict, block: dict) -> dict:
    """Sealed Stage A/B blocks must match the protocol's sampling plan exactly.  Stage-C blocks are
    accepted only from a verified training export (scripts/diagnostics/export_stage_c_train.py);
    the window is re-checked here as a second, independent guard."""
    if result.get("export_kind") != STAGE_C_EXPORT_KIND:
        return _protocol_block(protocol, block["sessions"])
    start, end = result["window"]
    if not result.get("provenance", {}).get("source_sha256"):
        raise SystemExit("STAGE_C_EXPORT_WITHOUT_PROVENANCE")
    for s in block["sessions"]:
        if not (start <= str(s)[:10] < end):
            raise SystemExit(f"STAGE_C_EXPORT_SESSION_OUT_OF_WINDOW: {s}")
    return {"block": int(block["block"]), "sessions": list(block["sessions"])}


def _sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return _sanitize(float(obj))
    return obj


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--results", type=Path, nargs="+", required=True,
                        help="sealed worker candidate result JSONs (Stage A or B)")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL,
                        help="protocol whose sampling plan contains the result blocks (default: V2)")
    parser.add_argument("--label", required=True)
    parser.add_argument("--controls", type=int, default=20, help="random control entries per trade")
    parser.add_argument("--slippage-fraction", type=float, default=None,
                        help="per-leg slippage; default: registry slippage_cost_multiplier x mpc_base_slippage_fraction")
    args = parser.parse_args(argv)

    if any("r5_step6" in str(p) for p in args.results):
        parser.error("holdout quarantine: Step-6 verification state may not be read")
    out_dir = (OUTPUT_ROOT / args.label).resolve()
    if OUTPUT_ROOT.resolve() not in out_dir.parents:
        parser.error("output must stay under outputs/diagnostics/")
    if args.slippage_fraction is None:
        from canonical_parameter_registry import CanonicalParameterRegistry
        reg = CanonicalParameterRegistry()
        args.slippage_fraction = (float(reg.params["slippage_cost_multiplier"].default)
                                  * float(reg.params["mpc_base_slippage_fraction"].default))
    protocol = json.loads(args.protocol.read_text())
    worker = _worker()
    bars_cache: dict = {}
    per_source = {}
    all_rows = []
    for path in args.results:
        result = json.loads(path.read_text())
        overrides = {**result.get("fixed_parameters", {}), **result.get("params", {})}
        for key in ("slippage_cost_multiplier", "mpc_base_slippage_fraction"):
            if key in overrides:
                raise SystemExit(f"{path}: {key} was calibrated; pass --slippage-fraction explicitly")
        rows = []
        for block in result["blocks"]:
            key = tuple(block["sessions"])
            if key not in bars_cache:
                print(f"loading block {block['block']} {key[0]}..{key[-1]} ...", flush=True)
                frames, _, _ = worker.prepare_block(ROOT, protocol, _block_for(result, protocol, block))
                bars_cache[key] = {s: SymbolBars(f) for s, f in frames.items()}
            for trade in block["trades"]:
                row = trade_row(trade, bars_cache[key][trade["symbol"]], args.slippage_fraction, args.controls)
                row["source"] = path.name
                row["block"] = int(block["block"])
                rows.append(row)
        per_source[path.name] = summarize(rows)
        all_rows.extend(rows)
        s = per_source[path.name]
        print(f"{path.name}: trades {s['trades']}  net {s['decomposition_total']['net']:.0f}  "
              f"fwd15 edge vs control {s['forward']['15']['edge_vs_control_r'].get('mean')}", flush=True)
    pooled = summarize(all_rows)
    document = _sanitize({"label": args.label, "slippage_fraction": args.slippage_fraction,
                          "horizons_bars": list(HORIZONS), "controls_per_trade": args.controls,
                          "pooled": pooled, "per_source": per_source, "trades": all_rows})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(document, indent=2, allow_nan=False, default=str))
    print_summary(pooled)
    print(f"\nwrote {out_dir / 'report.json'}")
    return 0


def print_summary(p: dict) -> None:
    def fmt(s):
        if not s or not s.get("n"):
            return "n/a"
        return f"mean {s['mean']:+.3f} [{s['ci95'][0]:+.3f},{s['ci95'][1]:+.3f}] med {s['median']:+.3f} pos {s['share_positive']:.0%}"
    print(f"\n===== POOLED  trades {p['trades']}  measured {p['measured']}  sessions {p.get('sessions')}  "
          f"skipped {p['skipped']}   (CIs resample whole sessions)")
    print(f"risk unit (bps of price): {fmt(p['risk_bps'])}")
    print(f"round-trip friction bps : {fmt(p['friction_bps'])}")
    print(f"round-trip friction in R: {fmt(p['friction_r'])}")
    print(f"realized R (fills)      : {fmt(p['realized_r'])}")
    print(f"fill vs entry-bar close : {fmt(p['entry_bar_close_r'])}")
    print(f"fill vs next-bar open   : {fmt(p['next_bar_open_r'])}")
    for h, f in p["forward"].items():
        label = "EOD" if h == "375" else h
        print(f"h={label:>3}  gross {fmt(f['gross_r'])}")
        print(f"      control {fmt(f['control_r'])}")
        print(f"      edge    {fmt(f['edge_vs_control_r'])}")
        print(f"      net     {fmt(f['net_r'])}   mfe {f['mfe_r'].get('median')}  mae {f['mae_r'].get('median')}")
    if "pre_run_15_terciles" in p:
        print(f"pre-entry 15-bar run vs forward 15-bar R (corr {p['pre_run_15_forward_15_corr']:+.3f}):")
        for k, v in p["pre_run_15_terciles"].items():
            print(f"   {k:>4}: pre {v['pre_run_r'].get('mean'):+.3f}  fwd15 {fmt(v['forward_15_r'])}")
    d = p["decomposition_total"]
    print("money: " + "  ".join(f"{k} {v:,.0f}" for k, v in d.items())
          + f"   (cost model vs recorded max diff {p['costs_model_vs_recorded_max_abs_diff']:.4f})")
    ladders = [("recorded (from fill)", p["mfe_ladder_recorded"]), ("to exit, market entry", p["mfe_ladder_market"])]
    for name, lad in ladders:
        if lad.get("n"):
            print(f"MFE ladder {name:>21}: n {lad['n']}  median {lad['median']:+.3f}  <=0 {lad['zero_or_less']:.0%}  "
                  + "  ".join(f"{k} {v:.0%}" for k, v in lad.items() if k.startswith(">=")))
    print("fixed-horizon MFE ladder, trades vs random-timing controls (exit-independent):")
    for h, lad in p["mfe_ladder_fixed"].items():
        ctl = p["mfe_ladder_fixed_control"].get(h, {})
        if lad.get("n"):
            print(f"   {('EOD' if h == '375' else h + ' bars'):>8}: "
                  + "  ".join(f"{k} {lad[k]:.0%} vs {ctl.get(k, float('nan')):.0%}" for k in lad if k.startswith(">=")))
    print(f"FSR controlling channels: {p['fsr_channels']}")
    print("exit authority and regret (k bars after exit, from the pre-slippage exit price):")
    for reason, g in list(p["exit_authority"].items())[:8]:
        k5, k10 = g["k5"], g["k10"]
        print(f"   {reason[:40]:<40} n {g['trades']:>4} net {g['net']:>9,.0f}  mfe_med {g['mfe_r_market_median']}"
              f"  k5 close {k5['close_r'].get('mean')} best>=.3R {k5['share_best_ge_0.30R']}"
              f"  k10 close {k10['close_r'].get('mean')} worst<=-.3R {k10['share_worst_le_-0.30R']}")
    sweep = p.get("friction_sweep") or {}
    if sweep:
        print("friction sweep (same signals and paths; per-leg slippage re-priced, charges unchanged):")
        for bps, e in sweep.items():
            fh = e["fixed_hold_net_r"]
            print(f"   {bps:>3} bps: realized {fmt(e['realized_net_r'])}  rupees {e['realized_net_rupees']:,.0f}")
            print("            hold " + "  ".join(
                f"{'EOD' if h == '375' else h}: {fh[h].get('mean', float('nan')):+.3f} "
                f"[{(fh[h].get('ci95') or [float('nan')] * 2)[0]:+.3f},{(fh[h].get('ci95') or [float('nan')] * 2)[1]:+.3f}]"
                for h in fh))
    pt = p.get("pulse_train") or {}
    if pt:
        w, oi = pt["width_bars"], pt["off_interval_minutes"]
        print(f"pulse train: {pt['pulses']} pulses  SELL {pt['polarity_share_sell']:.0%}  width med {w['median']} "
              f"(p25 {w['p25']}, p75 {w['p75']}, <=3 bars {w['share_le_3']:.0%})  pulses/active symbol-session "
              f"{pt['pulses_per_active_symbol_session']['mean']:.2f} (max {pt['pulses_per_active_symbol_session']['max']})")
        print(f"   off-interval same symbol/session: n {oi['n']} median {oi['median']} min  <=5 min {oi['share_le_5']}  "
              f"<=15 min {oi['share_le_15']}  polarity flips {pt['polarity_flips_within_session']}")
        print(f"   energy {fmt(pt['energy_r'])}")
        print(f"   switching loss {fmt(pt['switching_loss_r'])}")
        for k, v in pt["by_width_bars"].items():
            print(f"   width {k:>7} bars: {v['pulses']:>5} pulses  energy {v['energy_r']:+.3f}R  loss {v['switching_loss_r']:.3f}R"
                  f"  net {v['net_r']:+.3f}R  energy>loss {v['share_energy_gt_loss']:.0%}")
    print("by side      :", {k: (v["trades"], round(v["net"]), v["edge15_vs_control_mean"]) for k, v in p["by_side"].items()})
    print("by exit class:", {k: (v["trades"], round(v["net"])) for k, v in p["by_exit_class"].items()})
    print("by hour      :", {k: (v["trades"], round(v["net"]), v["fwd15_gross_r_mean"]) for k, v in p["by_entry_hour"].items()})


if __name__ == "__main__":
    raise SystemExit(main())
