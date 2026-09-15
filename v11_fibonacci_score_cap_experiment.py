"""EXPERIMENTAL, NOT VALIDATED - Fibonacci momentum-score cap.

Tests a rule motivated directly by the fold-2 attribution finding
(v11_yahoo_fold_attribution.py): every losing pick in that fold had an
extreme 12-1 score (130-264%). This script excludes any stock scoring
above a Fibonacci extension ratio (161.8%) from the selection pool
before taking the top 4, then re-runs the FULL 5-fold anchored walk-
forward (not just fold 2) with that one change.

WHY THIS IS LABELED EXPERIMENTAL, NOT A FIX: this rule was invented
after, and because of, seeing fold 2's loss. Testing it only on fold 2
would be circular - of course excluding what just hurt you looks good
on the data that showed you it hurt. Running it across all 5 folds is
the honest check: if the cap only helps fold 2 and hurts or does
nothing elsewhere, that is evidence this is curve-fitting, not a real
improvement. If it helps broadly, including folds it was NOT inspired
by, that is more interesting - but still only one broader-universe
history, still not proof.

Deliberately a full parallel copy of monthly_long_only()/
run_anchored_walk_forward() with ONE line changed (the score-cap
filter), not a modification of the original sealed functions - the
original files (external_model_comparison.py,
anchored_walk_forward_momentum_v11.py) are untouched.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from anchored_walk_forward_v10 import bootstrap_summary
from external_model_comparison import ComparisonConfig, _prior_month_date, month_ends
from walk_forward_v5 import WINDOWS

DATA_DIR = Path(__file__).parent / "historical_data_yahoo_daily"

FIBONACCI_SCORE_CAP = 1.618  # 161.8% - the golden ratio extension level

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


def monthly_long_only_fib_capped(
    name: str, closes: dict[str, dict[str, float]], cfg: ComparisonConfig,
    decision_window: tuple | None = None, score_cap: float = FIBONACCI_SCORE_CAP,
) -> dict:
    """Identical to external_model_comparison.monthly_long_only(require_positive=False)
    except: any stock whose 12-1 score exceeds `score_cap` is excluded from
    the ranking pool before taking the top cfg.positions. No cost_model
    param here (kept simple - same flat-bps cost as the original default)."""
    common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
    ends = month_ends(common_dates)
    warmup = cfg.formation_months + cfg.skip_months
    evaluation_start = ends[warmup]
    dates = [v for v in common_dates if v >= evaluation_start]
    if decision_window is not None:
        window_start, window_end = decision_window[0].isoformat(), decision_window[1].isoformat()
        dates = [v for v in dates if window_start <= v < window_end]
    cash = cfg.starting_capital
    quantities = {symbol: 0 for symbol in closes}
    curve: list[dict] = []
    turnover = 0.0
    rebalance_dates: list[str] = []
    friction = (cfg.cost_bps_per_side + cfg.slippage_bps_per_side) / 10_000
    excluded_for_extreme_score: list[tuple[str, str, float]] = []

    for day in dates:
        if day in ends:
            month_i = ends.index(day)
            recent = _prior_month_date(ends, month_i, cfg.skip_months)
            old = _prior_month_date(ends, month_i, warmup)
            if recent and old:
                scores = {symbol: closes[symbol][recent] / closes[symbol][old] - 1 for symbol in closes}
                eligible = {s: sc for s, sc in scores.items() if sc <= score_cap}
                for s, sc in scores.items():
                    if sc > score_cap:
                        excluded_for_extreme_score.append((day, s, sc))
                ranked = sorted(eligible, key=eligible.get, reverse=True)
                selected = ranked[:cfg.positions]
                equity = cash + sum(quantities[s] * closes[s][day] for s in closes)
                for symbol, quantity in quantities.items():
                    if quantity:
                        gross = quantity * closes[symbol][day]
                        proceeds = gross * (1 - friction)
                        cash += proceeds
                        turnover += gross
                        quantities[symbol] = 0
                allocation = equity / cfg.positions if selected else 0.0
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
                rebalance_dates.append(day)
        equity = cash + sum(quantities[s] * closes[s][day] for s in closes)
        curve.append({"timestamp": day, "equity": equity})

    def _performance(curve, start, turnover):
        peak = start
        drawdown = 0.0
        for row in curve:
            peak = max(peak, row["equity"])
            if peak:
                drawdown = max(drawdown, (peak - row["equity"]) / peak)
        final = curve[-1]["equity"] if curve else start
        return {"final_equity": final, "net_return": final / start - 1,
                "maximum_drawdown": drawdown, "turnover": turnover}

    return {
        "name": name, "evaluation_start": dates[0] if dates else evaluation_start,
        "method": f"12-1 cross-sectional, capped at {score_cap:.1%} score (Fibonacci 161.8%)",
        "metrics": _performance(curve, cfg.starting_capital, turnover),
        "rebalances": len(rebalance_dates), "curve": curve,
        "excluded_for_extreme_score": excluded_for_extreme_score,
    }


def run_walk_forward_capped(closes: dict[str, dict[str, float]], cfg: ComparisonConfig) -> dict:
    common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
    history_start = date.fromisoformat(common_dates[0])
    folds = []
    all_oos_returns: list[float] = []
    oos_net_pnls: list[float] = []

    for fold_start, fold_end in WINDOWS:
        test_window = (fold_start, fold_end)
        test_result = monthly_long_only_fib_capped(
            "CAPPED", closes, cfg, decision_window=test_window,
        )
        curve = test_result["curve"]
        returns = [c2["equity"] / c1["equity"] - 1 for c1, c2 in zip(curve, curve[1:]) if c1["equity"] > 0]
        all_oos_returns.extend(returns)
        oos_net_pnls.append(test_result["metrics"]["final_equity"] - cfg.starting_capital)
        folds.append({
            "fold_start": fold_start.isoformat(), "fold_end": fold_end.isoformat(),
            "test_metrics": test_result["metrics"], "test_rebalances": test_result["rebalances"],
            "excluded_count": len(test_result["excluded_for_extreme_score"]),
            "excluded_examples": test_result["excluded_for_extreme_score"][:5],
        })

    profitable_folds = sum(1 for pnl in oos_net_pnls if pnl > 0)
    return {
        "score_cap": FIBONACCI_SCORE_CAP,
        "history_start": history_start.isoformat(),
        "folds": folds,
        "profitable_folds": profitable_folds,
        "total_folds": len(folds),
        "aggregate_bootstrap_on_daily_returns": bootstrap_summary(all_oos_returns),
    }


def main() -> None:
    print("=== EXPERIMENTAL: Fibonacci (161.8%) momentum-score cap, all 5 folds ===")
    print("Rule invented AFTER seeing fold 2's loss - this run is the honest check,")
    print("not a validation. Comparing against the UNCAPPED baseline already on record.\n")

    closes = load_closes()
    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)
    report = run_walk_forward_capped(closes, cfg)

    print(f"{'Fold':<28}{'Capped return':>16}{'Excluded picks':>16}")
    baseline = [0.0, -0.2200, 0.2320, 0.0199, -0.0008]  # from the uncapped walk-forward already on record
    for i, f in enumerate(report["folds"]):
        label = f"{f['fold_start']} to {f['fold_end']}"
        capped_ret = f["test_metrics"]["net_return"]
        print(f"{label:<28}{capped_ret:>+15.2%}{f['excluded_count']:>16}   (uncapped was {baseline[i]:+.2%})")

    print(f"\nProfitable folds (capped): {report['profitable_folds']} / {report['total_folds']}  "
          f"(uncapped was 2/5)")
    b = report["aggregate_bootstrap_on_daily_returns"]
    print(f"Bootstrap probability of a NON-positive aggregate result (capped): {b['probability_total_non_positive']:.0%}  "
          f"(uncapped was 47%)")

    print("\nExample excluded picks (stock, day, score) from fold 2:")
    for day, symbol, score in report["folds"][1]["excluded_examples"]:
        print(f"  {day}  {symbol:<14} score={score:+.1%}")

    out_path = Path(__file__).parent / "brain_results_yahoo_broader" / "v11_fibonacci_score_cap_experiment.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report: {out_path}")

    print("\n=== HONEST READ ===")
    print("This is one experimental run, motivated by and partly tested against")
    print("the same broader-universe data the rule was inspired by. Even if every")
    print("fold improves, this is NOT independent proof the rule generalizes -")
    print("it would need testing on data neither the rule nor this session has")
    print("looked at yet before being treated as a real finding.")


if __name__ == "__main__":
    main()
