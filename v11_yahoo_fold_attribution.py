"""Per-stock attribution for the two losing/flat folds found by
v11_yahoo_broader_universe_walk_forward.py (fold 2: Aug 2024-Feb 2025,
-22.0%; fold 5: Feb 2026-Aug 2026, -0.1%).

Reuses external_model_comparison's OWN month_ends()/_prior_month_date()
helpers - the exact same date bookkeeping monthly_long_only() itself
uses - so the picks this script reports are guaranteed to be the same
picks the real backtest made, not a re-derived approximation that could
silently disagree with it. Diagnostic only - does not change or re-run
the strategy, just decomposes an already-computed fold's result down to
which specific stock, at which specific rebalance, contributed what.

Both fold 2 and fold 5 tested with EXTERNAL_CROSS_SECTIONAL_12_1
(require_positive=False) - confirmed from the walk-forward JSON's own
"selected_variant" field for each fold, not assumed.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from external_model_comparison import _prior_month_date, month_ends

DATA_DIR = Path(__file__).parent / "historical_data_yahoo_daily"

NIFTY_SYMBOLS = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "BAJFINANCE",
    "LT", "HINDUNILVR", "INFY", "SUNPHARMA", "TITAN", "MARUTI", "M&M", "ADANIENT",
    "KOTAKBANK", "ADANIPORTS", "AXISBANK", "HCLTECH", "ITC", "ULTRACEMCO", "NTPC",
    "BAJAJFINSV", "BAJAJ-AUTO", "JSWSTEEL", "ETERNAL", "BEL", "ONGC", "SHRIRAMFIN",
    "ASIANPAINT", "COALINDIA", "POWERGRID", "HINDALCO", "TATASTEEL", "EICHERMOT",
    "GRASIM", "INDIGO", "WIPRO", "SBILIFE", "JIOFIN", "TECHM", "TRENT", "APOLLOHOSP",
    "CIPLA", "HDFCLIFE", "TATACONSUM", "DRREDDY", "MAXHEALTH",
]

FORMATION_MONTHS = 12
SKIP_MONTHS = 1
WARMUP = FORMATION_MONTHS + SKIP_MONTHS
POSITIONS = 4

FOLDS = {
    "Fold 2 (Aug 2024 - Feb 2025, -22.0% portfolio result)": (date(2024, 8, 14), date(2025, 2, 14)),
    "Fold 5 (Feb 2026 - Aug 2026, -0.1% portfolio result)": (date(2026, 2, 14), date(2026, 8, 14)),
}


def load_closes() -> dict[str, dict[str, float]]:
    closes: dict[str, dict[str, float]] = {}
    for symbol in NIFTY_SYMBOLS:
        matches = list(DATA_DIR.glob(f"{symbol}_daily_*.csv"))
        if not matches:
            continue
        series: dict[str, float] = {}
        with matches[0].open("r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                series[row["timestamp"]] = float(row["close"])
        closes[symbol] = series
    return closes


def attribute_fold(closes: dict[str, dict[str, float]], fold_start: date, fold_end: date) -> list[dict]:
    common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
    ends = month_ends(common_dates)
    dates_in_fold = [d for d in common_dates if fold_start.isoformat() <= d < fold_end.isoformat()]
    rebalance_dates = [d for d in dates_in_fold if d in ends]

    rows = []
    for i, day in enumerate(rebalance_dates):
        month_i = ends.index(day)
        recent = _prior_month_date(ends, month_i, SKIP_MONTHS)
        old = _prior_month_date(ends, month_i, WARMUP)
        if not (recent and old):
            continue
        scores = {s: closes[s][recent] / closes[s][old] - 1 for s in closes}
        ranked = sorted(scores, key=scores.get, reverse=True)
        selected = ranked[:POSITIONS]  # require_positive=False, confirmed from walk-forward JSON

        # Exit date: the next rebalance date, or the last common date in the fold (mark-to-market at fold end).
        if i + 1 < len(rebalance_dates):
            exit_date = rebalance_dates[i + 1]
        else:
            exit_date = dates_in_fold[-1]

        for symbol in selected:
            entry_price = closes[symbol][day]
            exit_price = closes[symbol].get(exit_date)
            if exit_price is None:
                continue
            holding_return = exit_price / entry_price - 1
            rows.append({
                "rebalance_date": day, "exit_date": exit_date, "symbol": symbol,
                "selection_score_12_1": scores[symbol], "holding_period_return": holding_return,
            })
    return rows


def main() -> None:
    closes = load_closes()
    print(f"Loaded {len(closes)} symbols.\n")

    for label, (fold_start, fold_end) in FOLDS.items():
        print(f"=== {label} ===")
        rows = attribute_fold(closes, fold_start, fold_end)
        rows_sorted = sorted(rows, key=lambda r: r["holding_period_return"])
        print(f"{'rebalance':<12} {'exit':<12} {'symbol':<14} {'12-1 score':>12} {'holding return':>16}")
        for r in rows_sorted:
            print(f"{r['rebalance_date']:<12} {r['exit_date']:<12} {r['symbol']:<14} "
                  f"{r['selection_score_12_1']:>+11.2%} {r['holding_period_return']:>+15.2%}")
        # Aggregate per symbol (a symbol can be picked more than once across rebalances in a fold).
        by_symbol: dict[str, float] = {}
        for r in rows:
            by_symbol.setdefault(r["symbol"], 0.0)
            by_symbol[r["symbol"]] += r["holding_period_return"]
        print("\n  Worst 5 individual holding-period contributions in this fold:")
        for r in rows_sorted[:5]:
            print(f"    {r['symbol']:<14} {r['holding_period_return']:>+.2%}  "
                  f"(picked {r['rebalance_date']}, held to {r['exit_date']})")
        print()


if __name__ == "__main__":
    main()
