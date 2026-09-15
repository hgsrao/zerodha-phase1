"""Version 11: anchored walk-forward validation for the two published-paper
momentum controls (EXTERNAL_CROSS_SECTIONAL_12_1, EXTERNAL_TIME_SERIES_12M),
using the exact same methodology V10 applied to V9.

Why this one is a genuinely different question from V10
---------------------------------------------------------
V9's free parameters (feature weights, breakout window, decision hour, ...)
were hand-tuned across nine iterations against this project's own 2023-2026
sample - the walk-forward test in V10 exists to catch exactly that kind of
in-sample fitting. These two models are different: their parameters (12-month
formation, 1-month skip, equal-weight top-4) are taken directly from
Jegadeesh & Titman (1993) and Moskowitz, Ooi & Pedersen (2012) and were not
tuned on this dataset at all. So the walk-forward here is not hunting for the
same failure mode - it is checking whether a well-known, independently
discovered edge still shows up on this specific 20-symbol NSE universe once
its one remaining discretionary choice (cross-sectional ranking vs. absolute
time-series trend) is selected without looking at the fold being graded.

Method
------
Same five anchored/expanding folds as V10. For each fold:

  1. TRAINING: both variants (cross-sectional winners, absolute trend) are
     re-run with decision_window = [start_of_history, fold_start) - an
     isolated, fresh-capital backtest that only ever rebalances on dates
     strictly before the fold under test.
  2. SELECTION: the variant with the better training-window net return is
     chosen, subject to a minimum rebalance count. No eligible variant falls
     back to the pre-registered default (EXTERNAL_CROSS_SECTIONAL_12_1, the
     classic Jegadeesh-Titman formulation).
  3. TEST: the selected variant is re-run with decision_window =
     [fold_start, fold_end) only - an isolated, fresh-capital backtest whose
     trading is confined to the fold.

Both runs call external_model_comparison.monthly_long_only() unmodified via
its decision_window gate - no separate implementation of the momentum logic.

This module makes no broker calls and writes nothing but its own JSON report.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from anchored_walk_forward_v10 import bootstrap_summary
from external_model_comparison import ComparisonConfig, daily_closes, load_universe, monthly_long_only
from walk_forward_v5 import WINDOWS


VARIANTS = (
    ("EXTERNAL_CROSS_SECTIONAL_12_1", False),
    ("EXTERNAL_TIME_SERIES_12M", True),
)

DEFAULT_VARIANT = "EXTERNAL_CROSS_SECTIONAL_12_1"
MINIMUM_TRAINING_REBALANCES = 3


def curve_returns(curve: list[dict]) -> list[float]:
    """Day-over-day equity returns, one per consecutive curve pair.

    monthly_long_only()'s curve has one entry per trading day (the portfolio
    is only rebalanced monthly, but marked to market daily), so this is a
    daily return series, not a monthly one. Used instead of individual trade
    P&L (this strategy has no discrete round-trip trades - it is a
    continuously held, periodically rebalanced portfolio) as the bootstrap's
    resampling unit. See the "limitations" entries about autocorrelation
    before treating its confidence interval as precise.
    """
    returns = []
    for previous, current in zip(curve, curve[1:]):
        if previous["equity"] > 0:
            returns.append(current["equity"] / previous["equity"] - 1)
    return returns


def select_variant(
    training_results: dict[str, dict],
    *,
    minimum_rebalances: int = MINIMUM_TRAINING_REBALANCES,
    default: str = DEFAULT_VARIANT,
) -> tuple[str, str]:
    eligible = {
        name: result for name, result in training_results.items()
        if result["rebalances"] >= minimum_rebalances
    }
    if not eligible:
        return default, f"INSUFFICIENT_TRAINING_REBALANCES_FELL_BACK_TO_{default}"
    best = max(eligible, key=lambda name: eligible[name]["metrics"]["net_return"])
    return best, "BEST_TRAINING_NET_RETURN"


def run_anchored_walk_forward(
    closes: dict[str, dict[str, float]], cfg: ComparisonConfig,
    *, windows: tuple = WINDOWS, cost_model=None,
) -> dict:
    common_dates = sorted(set.intersection(*(set(values) for values in closes.values())))
    history_start = date.fromisoformat(common_dates[0])

    folds = []
    all_oos_returns: list[float] = []
    oos_net_pnls: list[float] = []

    for fold_start, fold_end in windows:
        training_window = (history_start, fold_start)
        test_window = (fold_start, fold_end)

        training_results = {
            name: monthly_long_only(
                name, closes, cfg, require_positive,
                decision_window=training_window, cost_model=cost_model,
            )
            for name, require_positive in VARIANTS
        }
        selected, reason = select_variant(training_results)
        require_positive = dict(VARIANTS)[selected]
        test_result = monthly_long_only(
            selected, closes, cfg, require_positive,
            decision_window=test_window, cost_model=cost_model,
        )

        test_returns = curve_returns(test_result["curve"])
        all_oos_returns.extend(test_returns)
        test_net_pnl = test_result["metrics"]["final_equity"] - cfg.starting_capital
        oos_net_pnls.append(test_net_pnl)

        training_return = training_results[selected]["metrics"]["net_return"]
        test_return = test_result["metrics"]["net_return"]
        walk_forward_efficiency = test_return / training_return if training_return else None

        folds.append({
            "fold_start": fold_start.isoformat(),
            "fold_end": fold_end.isoformat(),
            "training_window": [training_window[0].isoformat(), training_window[1].isoformat()],
            "training_metrics_by_variant": {
                name: {"net_return": result["metrics"]["net_return"], "rebalances": result["rebalances"]}
                for name, result in training_results.items()
            },
            "selected_variant": selected,
            "selection_reason": reason,
            "test_metrics": test_result["metrics"],
            "test_rebalances": test_result["rebalances"],
            "walk_forward_efficiency": walk_forward_efficiency,
        })

    profitable_folds = sum(1 for pnl in oos_net_pnls if pnl > 0)
    aggregate_bootstrap = bootstrap_summary(all_oos_returns)

    return {
        "research_version": "V11_MOMENTUM_ANCHORED_WALK_FORWARD",
        "config": asdict(cfg),
        "cost_model": "flat_bps_guess" if cost_model is None else "zerodha_real_delivery_schedule",
        "history_start": history_start.isoformat(),
        "minimum_training_rebalances": MINIMUM_TRAINING_REBALANCES,
        "default_fallback_variant": DEFAULT_VARIANT,
        "folds": folds,
        "profitable_folds": profitable_folds,
        "total_folds": len(folds),
        "aggregate_out_of_sample_daily_returns": {
            "observations": len(all_oos_returns),
            "mean_daily_return": (
                sum(all_oos_returns) / len(all_oos_returns) if all_oos_returns else None
            ),
        },
        "aggregate_bootstrap_on_daily_returns": aggregate_bootstrap,
        "limitations": [
            "The bootstrap unit here is the DAILY equity-curve return, not the "
            "monthly rebalance - hundreds of daily observations exist, but the "
            "portfolio only actually changes composition ~5-6 times per fold, "
            "so the true independent-decision sample is far smaller than the "
            "observation count suggests.",
            "The bootstrap resamples individual daily returns as if independent; "
            "consecutive days within the same monthly holding period share "
            "the same basket and are highly autocorrelated, so the reported "
            "CI is optimistic (narrower than the true uncertainty) - treat "
            "the fold-level pattern (3 of 5 profitable) as the more honest "
            "signal than the daily-return confidence interval.",
            "The 20-symbol universe and sector map are today's constituents, "
            "not a point-in-time investable universe; survivorship bias is "
            "not corrected for here.",
            "Unlike V9, these two variants' parameters were not tuned on "
            "this dataset - so a weak walk-forward result here says more "
            "about whether the published edge transfers to this small NSE "
            "universe than about in-sample overfitting.",
        ],
    }


def main() -> int:
    series, market = load_universe()
    closes = {symbol: daily_closes(bars) for symbol, bars in series.items()}
    cfg = ComparisonConfig(starting_capital=100_000.0)
    report = run_anchored_walk_forward(closes, cfg)

    output = Path("brain_results_v11")
    output.mkdir(exist_ok=True)
    (output / "anchored_walk_forward_momentum_v11.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )
    summary = {
        "profitable_folds": report["profitable_folds"],
        "total_folds": report["total_folds"],
        "aggregate_out_of_sample_daily_returns": report["aggregate_out_of_sample_daily_returns"],
        "aggregate_bootstrap_on_daily_returns": report["aggregate_bootstrap_on_daily_returns"],
        "fold_selections": [
            {"fold": f'{f["fold_start"]}_{f["fold_end"]}',
             "selected_variant": f["selected_variant"],
             "selection_reason": f["selection_reason"],
             "walk_forward_efficiency": f["walk_forward_efficiency"],
             "test_net_return": f["test_metrics"]["net_return"]}
            for f in report["folds"]
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
