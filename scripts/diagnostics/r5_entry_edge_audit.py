#!/usr/bin/env python3
"""R5 entry-edge audit and loss decomposition (read-only diagnostic).

Question 1 -- do the entries have edge before any exit logic touches them?
For every completed trade in sealed V2 candidate results, measure the signed forward move from
the entry fill to the close of bar t+h (h = 1, 5, 15, 30; capped at the session's last bar), in
R (initial risk = |entry fill - planned stop|) and in basis points, plus the forward MFE/MAE over
the same bars.  Each trade is paired with matched controls: the same symbol, session and side,
entered at random bars of that session, measured the same way.  "Entry edge" is trade minus
control; if it is not positive, the entry timing adds nothing over being in that stock that day.

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
HORIZONS = (1, 5, 15, 30)
PRE_RUNS = (5, 15)
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

    def pre_run(self, idx: int, ref: float, sign: float, risk: float) -> dict:
        out = {}
        for k in PRE_RUNS:
            j = idx - k
            out[k] = (sign * (ref - self.close[j]) / risk) if j >= self.first[idx] else None
        return out

    def controls(self, idx: int, sign: float, risk: float, n: int, seed: int) -> dict:
        """Matched controls: random entry bars in the same session, same side and risk unit,
        entered at that bar's close.  Returns the mean control move per horizon, in R."""
        lo, hi = self.first[idx], self.last[idx]
        if hi <= lo or n <= 0:
            return {h: None for h in HORIZONS}
        out = {}
        for h in HORIZONS:
            # Controls get the full horizon where the session allows it, so a late-session control
            # is never compared on fewer bars than the trade.
            candidates = np.arange(lo, hi - h + 1) if hi - h >= lo else np.arange(lo, hi)
            picks = np.random.default_rng(seed + h).choice(candidates, size=n, replace=True)
            out[h] = float(np.mean([sign * (self.close[min(k + h, hi)] - self.close[k]) / risk for k in picks]))
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
    row = {"trade_id": trade.get("trade_id"), "symbol": trade["symbol"], "side": trade["side"],
           "entry_timestamp": str(trade["entry_timestamp"]), "exit_class": exit_class(trade["reason"]),
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
    row["forward"] = bars.forward(idx, market_entry, sign, risk)
    row["pre_run_r"] = bars.pre_run(idx, market_entry, sign, risk)
    seed = int(hashlib.sha256(f"{row['trade_id']}|{row['entry_timestamp']}|{row['symbol']}".encode())
               .hexdigest()[:8], 16)
    row["control_r"] = bars.controls(idx, sign, risk, controls, seed)
    # Friction of a round trip exiting at the horizon close: both legs' slippage + charges, in R.
    entry_slip_r = (entry - market_entry) * sign / risk
    charges_r = decomp["costs_model"] / (qty * risk)
    row["forward_net_r"] = {h: (f["r"] - entry_slip_r - slippage_fraction * f["exit_ref"] / risk - charges_r)
                            if f else None for h, f in row["forward"].items()}
    return row


# ------------------------------------------------------------------ aggregation

def _stats(values, seed=BOOTSTRAP_SEED, draws=2000) -> dict:
    x = np.array([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if len(x) == 0:
        return {"n": 0}
    rng = np.random.default_rng(seed)
    boots = rng.choice(x, size=(draws, len(x)), replace=True).mean(axis=1) if len(x) > 1 else x
    return {"n": int(len(x)), "mean": float(x.mean()), "median": float(np.median(x)),
            "share_positive": float((x > 0).mean()),
            "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]}


def summarize(rows: list) -> dict:
    ok = [r for r in rows if r.get("skip") is None]
    out = {"trades": len(rows), "measured": len(ok),
           "skipped": dict(pd.Series([r["skip"] for r in rows if r.get("skip")]).value_counts())
           if any(r.get("skip") for r in rows) else {}}
    out["risk_bps"] = _stats([r["risk_bps"] for r in ok])
    out["friction_bps"] = _stats([r["friction_bps"] for r in ok])
    out["friction_r"] = _stats([r["friction_r"] for r in ok])
    out["realized_r"] = _stats([r["realized_r"] for r in ok])
    out["entry_bar_close_r"] = _stats([r["entry_bar_close_r"] for r in ok])
    fwd = {}
    for h in HORIZONS:
        f = [r["forward"][h] for r in ok if r["forward"][h]]
        fwd[str(h)] = {
            "gross_r": _stats([x["r"] for x in f]),
            "net_r": _stats([r["forward_net_r"][h] for r in ok]),
            "control_r": _stats([r["control_r"][h] for r in ok]),
            "edge_vs_control_r": _stats([r["forward"][h]["r"] - r["control_r"][h] for r in ok
                                         if r["forward"][h] and r["control_r"][h] is not None]),
            "bps": _stats([x["bps"] for x in f]),
            "mfe_r": _stats([x["mfe_r"] for x in f]),
            "mae_r": _stats([x["mae_r"] for x in f]),
            "truncated_share": float(np.mean([x["truncated"] for x in f])) if f else None,
        }
    out["forward"] = fwd
    # Does the entry buy a move that already happened?  Forward 15-bar R by pre-entry 15-bar run.
    runs = [(r["pre_run_r"][15], r["forward"][15]["r"]) for r in ok
            if r["pre_run_r"][15] is not None and r["forward"][15]]
    if len(runs) >= 3:
        cut = np.quantile([a for a, _ in runs], [1 / 3, 2 / 3])
        buckets = {"low": [], "mid": [], "high": []}
        for a, b in runs:
            buckets["low" if a <= cut[0] else "mid" if a <= cut[1] else "high"].append((a, b))
        out["pre_run_15_terciles"] = {
            k: {"pre_run_r": _stats([a for a, _ in v]), "forward_15_r": _stats([b for _, b in v])}
            for k, v in buckets.items()}
        out["pre_run_15_forward_15_corr"] = float(np.corrcoef([a for a, _ in runs], [b for _, b in runs])[0, 1])
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
                frames, _, _ = worker.prepare_block(ROOT, protocol, _protocol_block(protocol, block["sessions"]))
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
    print(f"\n===== POOLED  trades {p['trades']}  measured {p['measured']}  skipped {p['skipped']}")
    print(f"risk unit (bps of price): {fmt(p['risk_bps'])}")
    print(f"round-trip friction bps : {fmt(p['friction_bps'])}")
    print(f"round-trip friction in R: {fmt(p['friction_r'])}")
    print(f"realized R (fills)      : {fmt(p['realized_r'])}")
    print(f"fill vs entry-bar close : {fmt(p['entry_bar_close_r'])}")
    for h, f in p["forward"].items():
        print(f"h={h:>2}  gross {fmt(f['gross_r'])}")
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
    print("by side      :", {k: (v["trades"], round(v["net"]), v["edge15_vs_control_mean"]) for k, v in p["by_side"].items()})
    print("by exit class:", {k: (v["trades"], round(v["net"])) for k, v in p["by_exit_class"].items()})
    print("by hour      :", {k: (v["trades"], round(v["net"]), v["fwd15_gross_r_mean"]) for k, v in p["by_entry_hour"].items()})


if __name__ == "__main__":
    raise SystemExit(main())
