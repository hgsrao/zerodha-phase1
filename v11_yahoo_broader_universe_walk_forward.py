"""V11 anchored walk-forward, real methodology, against the broader
Yahoo-sourced Nifty universe.

Reuses `anchored_walk_forward_momentum_v11.run_anchored_walk_forward()`
UNCHANGED - the exact same sealed function that produced
`BRAIN_RESEARCH_SPEC_V11_MOMENTUM_WALK_FORWARD.md`'s 5-fold result. Only
the `closes` input changes: the new 48-symbol Yahoo-sourced Nifty
universe instead of the original ~20-symbol Zerodha-sourced one. Same
5 anchored fold windows (`walk_forward_v5.WINDOWS`), same training/
selection/test discipline, same bootstrap. Nothing about the strategy's
own logic or parameters is touched - this is new data through the old,
already-tested lens, not a new experiment design.

International basket deliberately NOT run through this walk-forward in
this pass - 12 symbols is a small universe for meaningful cross-
sectional ranking and mixing a proper walk-forward run for it in here
would double this script's scope; can be added as its own follow-up if
wanted.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from anchored_walk_forward_momentum_v11 import run_anchored_walk_forward
from external_model_comparison import ComparisonConfig
from walk_forward_v5 import WINDOWS

DATA_DIR = Path(__file__).parent / "historical_data_yahoo_daily"
OUT_DIR = Path(__file__).parent / "brain_results_yahoo_broader"

NIFTY_SYMBOLS = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "BAJFINANCE",
    "LT", "HINDUNILVR", "INFY", "SUNPHARMA", "TITAN", "MARUTI", "M&M", "ADANIENT",
    "KOTAKBANK", "ADANIPORTS", "AXISBANK", "HCLTECH", "ITC", "ULTRACEMCO", "NTPC",
    "BAJAJFINSV", "BAJAJ-AUTO", "JSWSTEEL", "ETERNAL", "BEL", "ONGC", "SHRIRAMFIN",
    "ASIANPAINT", "COALINDIA", "POWERGRID", "HINDALCO", "TATASTEEL", "EICHERMOT",
    "GRASIM", "INDIGO", "WIPRO", "SBILIFE", "JIOFIN", "TECHM", "TRENT", "APOLLOHOSP",
    "CIPLA", "HDFCLIFE", "TATACONSUM", "DRREDDY", "MAXHEALTH",
]


def load_closes(symbols: list[str]) -> dict[str, dict[str, float]]:
    closes: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        matches = list(DATA_DIR.glob(f"{symbol}_daily_*.csv"))
        if not matches:
            print(f"  SKIPPED (no file): {symbol}")
            continue
        series: dict[str, float] = {}
        with matches[0].open("r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                series[row["timestamp"]] = float(row["close"])
        closes[symbol] = series
    return closes


def main() -> int:
    print("=== V11 anchored walk-forward - broader Yahoo Nifty universe ===")
    print(f"Loading {len(NIFTY_SYMBOLS)} requested symbols from {DATA_DIR}...\n")
    closes = load_closes(NIFTY_SYMBOLS)
    print(f"\n{len(closes)} symbols loaded with real data.\n")

    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)
    print(f"Fold windows (same 5 anchored folds as the original V11 study): {WINDOWS}\n")
    print("Running run_anchored_walk_forward() - unmodified, same function as before...\n")

    report = run_anchored_walk_forward(closes, cfg)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "v11_yahoo_broader_universe_walk_forward.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Full report written to: {out_path}\n")

    print("=== FULL REPORT (real output, not summarized) ===")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
