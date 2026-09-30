#!/usr/bin/env python3
"""Side-by-side comparison of Step 5 candidate results: V2 baseline vs Protocol V3.

Reads candidate output JSON (``run_r5_step5_candidate.py``) for the same stage and blocks and
prints, per engine: expectancy (R/trade), profit factor, win rate, mean hold (1-minute bars =
minutes), MTM drawdown, R-curve drawdown (depth and duration in trades), MFE captured at exit and
MFE given back.  R per trade = side * (exit - entry) / |entry - planned stop|.

    python scripts/protocol_v3/compare_baseline_vs_v3.py --baseline <V2 result.json> \
        --v3 outputs/protocol_v3_state/<V3 result.json> [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def _r(trade):
    risk = abs(float(trade["entry_price"]) - float(trade["planned_stop_price"]))
    if not risk:
        return None
    sign = 1.0 if trade["side"] == "BUY" else -1.0
    return sign * (float(trade["exit_price"]) - float(trade["entry_price"])) / risk


def _mean(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    return sum(values) / len(values) if values else None


def evaluate(result: dict) -> dict:
    blocks = sorted(result["blocks"], key=lambda b: int(b["block"]))
    trades = [t for b in blocks for t in b["trades"]]
    trades.sort(key=lambda t: (str(t["exit_timestamp"]), str(t.get("trade_id"))))
    net = [float(t["net_pnl"]) for t in trades]
    wins, losses = [x for x in net if x > 0], [x for x in net if x < 0]
    r = [_r(t) for t in trades]
    peak = depth = cum = 0.0
    duration = longest = 0
    for x in r:
        cum += x or 0.0
        if cum >= peak:
            peak, duration = cum, 0
        else:
            duration += 1
            longest = max(longest, duration)
        depth = max(depth, peak - cum)
    capture = [x / float(t["mfe_r"]) for t, x in zip(trades, r)
               if x is not None and t.get("mfe_r") is not None and float(t["mfe_r"]) > 0]
    giveback = [max(0.0, float(t["mfe_r"]) - x) for t, x in zip(trades, r)
                if x is not None and t.get("mfe_r") is not None]
    agg = result.get("aggregate", {})
    return {
        "score": agg.get("score"), "trades": len(trades), "net_pnl": sum(net),
        "gross_pnl": sum(float(t["pnl"]) for t in trades),
        "expectancy_r": _mean(r), "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "win_rate": len(wins) / len(trades) if trades else None,
        "mean_hold_minutes": _mean([t.get("bars_held") for t in trades]),
        "max_mtm_drawdown_fraction": agg.get("mtm_max_drawdown_fraction"),
        "r_drawdown_depth": depth, "r_drawdown_duration_trades": longest,
        "mfe_capture_fraction": _mean(capture), "mean_mfe_giveback_r": _mean(giveback),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--v3", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    if any("r5_step6" in str(p) for p in (args.baseline, args.v3)):
        parser.error("holdout quarantine: Step-6 results are not compared during V3 development")
    base, v3 = json.loads(args.baseline.read_text()), json.loads(args.v3.read_text())
    if base.get("stage") != v3.get("stage") or [b["block"] for b in base["blocks"]] != [b["block"] for b in v3["blocks"]]:
        parser.error("baseline and V3 must cover the same stage and blocks")
    rows = {"V2 baseline": evaluate(base), "Protocol V3": evaluate(v3)}
    keys = list(rows["V2 baseline"])
    print(f"{'metric':32s} {'V2 baseline':>16s} {'Protocol V3':>16s} {'delta':>12s}")
    for k in keys:
        a, b = rows["V2 baseline"][k], rows["Protocol V3"][k]
        fmt = lambda v: "n/a" if v is None else (f"{v:,.4f}" if isinstance(v, float) else str(v))
        d = (b - a) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        print(f"{k:32s} {fmt(a):>16s} {fmt(b):>16s} {fmt(d):>12s}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
