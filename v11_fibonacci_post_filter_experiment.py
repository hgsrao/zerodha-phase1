"""EXPERIMENTAL, NOT VALIDATED - Fibonacci golden-ratio filter applied
strictly OUTSIDE the momentum "black box," not inside it.

Different architecture from v11_fibonacci_score_cap_experiment.py (which
capped the score BEFORE ranking - the black box's own candidate pool was
touched). This version keeps the black box completely sealed: the real,
unmodified top-4 selection logic runs first and produces its normal
output, exactly as it always would. ONLY AFTER that output exists does
the Fibonacci golden-ratio rule (161.8%) get applied, as a pure post-
filter - any of the black box's own 4 picks that exceeds the cap is
simply dropped, with NO replacement pulled in from the rest of the
universe. A rebalance can therefore end up holding fewer than 4
positions that month, with the survivors getting a larger equal share
each - a genuinely different bet from the earlier "cap-then-select"
version, not just a relabeling of it.

Same honesty discipline as the other experiment: run across all 5
folds, not just the one that inspired it, and reported as exploratory,
not proof, regardless of outcome.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from anchored_walk_forward_v10 import bootstrap_summary
from external_model_comparison import ComparisonConfig, _prior_month_date, month_ends
from walk_forward_v5 import WINDOWS

DATA_DIR = Path(__file__).parent / "historical_data_yahoo_daily"
FIBONACCI_CAP = 1.618

NIFTY_SYMBOLS = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "SBIN", "TCS", "BAJFINANCE",
    "LT", "HINDUNILVR", "INFY", "SUNPHARMA", "TITAN", "MARUTI", "M&M", "ADANIENT",
    "KOTAKBANK", "ADANIPORTS", "AXISBANK", "HCLTECH", "ITC", "ULTRACEMCO", "NTPC",
    "BAJAJFINSV", "BAJAJ-AUTO", "JSWSTEEL", "ETERNAL", "BEL", "ONGC", "SHRIRAMFIN",
    "ASIANPAINT", "COALINDIA", "POWERGRID", "HINDALCO", "TATASTEEL", "EICHERMOT",
    "GRASIM", "INDIGO", "WIPRO", "SBILIFE", "JIOFIN", "TECHM", "TRENT", "APOLLOHOSP",
    "CIPLA", "HDFCLIFE", "TATACONSUM", "DRREDDY", "MAXHEALTH",
]


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


def run_fold_post_filtered(closes, fold_start: date, fold_end: date, cfg: ComparisonConfig) -> dict:
    common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
    ends = month_ends(common_dates)
    warmup = cfg.formation_months + cfg.skip_months
    dates = [v for v in common_dates if fold_start.isoformat() <= v < fold_end.isoformat()]

    cash = cfg.starting_capital
    quantities = {symbol: 0 for symbol in closes}
    curve: list[dict] = []
    turnover = 0.0
    rebalances = 0
    friction = (cfg.cost_bps_per_side + cfg.slippage_bps_per_side) / 10_000
    dropped_no_replacement: list[tuple[str, str, float]] = []

    for day in dates:
        if day in ends:
            month_i = ends.index(day)
            recent = _prior_month_date(ends, month_i, cfg.skip_months)
            old = _prior_month_date(ends, month_i, warmup)
            if recent and old:
                # STEP 1: the black box's REAL, unmodified top-4 - identical to the
                # production selection logic, no interference at all.
                scores = {s: closes[s][recent] / closes[s][old] - 1 for s in closes}
                ranked = sorted(scores, key=scores.get, reverse=True)
                black_box_top4 = ranked[:cfg.positions]

                # STEP 2: Fibonacci post-filter, strictly OUTSIDE the black box -
                # drop anything over the cap, no backfill.
                selected = []
                for s in black_box_top4:
                    if scores[s] > FIBONACCI_CAP:
                        dropped_no_replacement.append((day, s, scores[s]))
                    else:
                        selected.append(s)

                equity = cash + sum(quantities[s] * closes[s][day] for s in closes)
                for symbol, quantity in quantities.items():
                    if quantity:
                        gross = quantity * closes[symbol][day]
                        cash += gross * (1 - friction)
                        turnover += gross
                        quantities[symbol] = 0
                if selected:
                    allocation = equity / len(selected)  # fewer survivors -> bigger share each
                    for symbol in selected:
                        price_with_slippage = closes[symbol][day] * (1 + friction)
                        quantity = int(allocation // price_with_slippage)
                        if quantity <= 0:
                            continue
                        notional = quantity * price_with_slippage
                        if notional <= cash:
                            quantities[symbol] = quantity
                            cash -= notional
                            turnover += quantity * closes[symbol][day]
                rebalances += 1
        equity = cash + sum(quantities[s] * closes[s][day] for s in closes)
        curve.append({"timestamp": day, "equity": equity})

    peak = cfg.starting_capital
    drawdown = 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        if peak:
            drawdown = max(drawdown, (peak - row["equity"]) / peak)
    final = curve[-1]["equity"] if curve else cfg.starting_capital
    return {
        "final_equity": final, "net_return": final / cfg.starting_capital - 1,
        "maximum_drawdown": drawdown, "turnover": turnover, "rebalances": rebalances,
        "curve": curve, "dropped_no_replacement": dropped_no_replacement,
    }


def main() -> None:
    print("=== EXPERIMENTAL: Fibonacci post-filter (black box untouched), all 5 folds ===")
    print("The real top-4 selection runs unmodified; the 161.8% cap only removes")
    print("from that already-decided output, with NO replacement pulled in.\n")

    closes = load_closes()
    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)

    baseline = [0.0, -0.2200, 0.2320, 0.0199, -0.0008]
    all_returns: list[float] = []
    net_pnls: list[float] = []
    print(f"{'Fold':<28}{'Post-filtered':>15}{'Dropped (no replace)':>24}")
    fold_reports = []
    for i, (fold_start, fold_end) in enumerate(WINDOWS):
        result = run_fold_post_filtered(closes, fold_start, fold_end, cfg)
        label = f"{fold_start} to {fold_end}"
        print(f"{label:<28}{result['net_return']:>+14.2%}{len(result['dropped_no_replacement']):>24}   "
              f"(uncapped was {baseline[i]:+.2%})")
        curve = result["curve"]
        returns = [c2["equity"] / c1["equity"] - 1 for c1, c2 in zip(curve, curve[1:]) if c1["equity"] > 0]
        all_returns.extend(returns)
        net_pnls.append(result["final_equity"] - cfg.starting_capital)
        fold_reports.append({
            "fold_start": fold_start.isoformat(), "fold_end": fold_end.isoformat(),
            "net_return": result["net_return"], "maximum_drawdown": result["maximum_drawdown"],
            "rebalances": result["rebalances"], "dropped_no_replacement": result["dropped_no_replacement"],
        })

    profitable = sum(1 for p in net_pnls if p > 0)
    b = bootstrap_summary(all_returns)
    print(f"\nProfitable folds: {profitable} / 5  (uncapped was 2/5, cap-before-selection version was also 2/5)")
    print(f"Bootstrap probability of non-positive result: {b['probability_total_non_positive']:.0%}  "
          f"(uncapped 47%, cap-before-selection was 45%)")

    print("\nEvery drop, with what would have been held instead (no replacement):")
    for day, symbol, score in fold_reports[1]["dropped_no_replacement"]:
        print(f"  {day}  dropped {symbol:<14} score={score:+.1%}  -> that rebalance held one fewer position")

    out_path = Path(__file__).parent / "brain_results_yahoo_broader" / "v11_fibonacci_post_filter_experiment.json"
    out_path.write_text(json.dumps({"folds": fold_reports, "profitable_folds": profitable,
                                     "bootstrap": b}, indent=2), encoding="utf-8")
    print(f"\nFull report: {out_path}")

    print("\n=== HONEST READ ===")
    print("Real result, black box genuinely untouched. Still only tested on the")
    print("same broader-universe history that motivated it - not independent proof")
    print("either way, whatever the number says.")


if __name__ == "__main__":
    main()
