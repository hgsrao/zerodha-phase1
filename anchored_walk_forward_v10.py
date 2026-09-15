"""Version 10: anchored (expanding-window) walk-forward validation.

Why this exists
----------------
walk_forward_v5.py (and the window_returns field of portfolio_brain_v9.py's
own report) both run ONE full-period backtest and then slice its trades into
calendar windows for reporting. That tells you how a strategy that already
"knows" the whole 2023-2026 sample performed in each sub-period - it cannot
tell you how the strategy would have behaved on data it had never seen,
because the variant (TOP1 / TOP2_SECTOR / TOP4_SECTOR) was picked by looking
at full-period results in the first place.

This module instead does anchored/expanding walk-forward selection:

  For each of the five 6-month folds in WINDOWS:
    1. TRAINING: every V9 variant is re-run with new-entry decisions
       restricted to [start_of_history, fold_start) - a window that only
       grows across folds and never includes a single bar from, or after,
       the fold under test.
    2. SELECTION: whichever variant had the best training-slice net return
       (subject to a minimum trade count) is chosen for this fold. If no
       variant clears the minimum, the pre-registered default
       (TOP4_SECTOR, the same variant portfolio_brain_v9.md called "best")
       is used instead of an ad hoc pick.
    3. TEST: the selected variant is re-run with new-entry decisions
       restricted to [fold_start, fold_end) only. Its trades are this
       fold's genuinely out-of-sample result.

All three runs call portfolio_brain_v9.simulate() unmodified (via its
decision_window gate) - there is no second, divergent implementation of the
trading logic for this module to get subtly wrong.

Outputs
-------
- Per-fold training (in-sample) and test (out-of-sample) statistics.
- The aggregated out-of-sample trade set across all five folds.
- A walk-forward efficiency ratio (OOS return / training return) as a
  standard overfitting indicator - WFE well below 1 means the training-slice
  edge did not survive into unseen data.
- A bootstrap confidence interval and a one-sided p-value-style estimate
  (fraction of resamples with a non-positive total) on the aggregated
  out-of-sample net P&L, since the raw trade count is too small for a
  point estimate alone to mean much.

This module makes no broker calls, imports no runner/engine code, and writes
nothing but its own JSON report.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import date
from pathlib import Path
from statistics import fmean

from brain_research_lab import load_candles_csv
from portfolio_brain_v9 import PortfolioConfig, SECTORS, simulate
from walk_forward_v5 import WINDOWS


VARIANTS = (
    ("TOP1", 1, 1, False),
    ("TOP2_SECTOR", 2, 2, True),
    ("TOP4_SECTOR", 4, 4, True),
)

DEFAULT_VARIANT = "TOP4_SECTOR"
MINIMUM_TRAINING_TRADES = 5
BOOTSTRAP_ITERATIONS = 5000
BOOTSTRAP_SEED = 1337


def phase_trades(trades: list[dict], start: date, end: date) -> list[dict]:
    """Trades whose entry falls in [start, end)."""
    result = []
    for trade in trades:
        entry_date = date.fromisoformat(trade["entry_time"][:10])
        if start <= entry_date < end:
            result.append(trade)
    return result


def phase_stats(trades: list[dict], starting_capital: float) -> dict:
    pnls = [trade["net_pnl"] for trade in trades]
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    return {
        "trades": len(trades),
        "net_pnl": sum(pnls),
        "net_return_on_starting_capital": sum(pnls) / starting_capital if starting_capital else None,
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
    }


def select_variant(
    training_trades_by_variant: dict[str, list[dict]],
    starting_capital: float,
    *,
    minimum_trades: int = MINIMUM_TRAINING_TRADES,
    default: str = DEFAULT_VARIANT,
) -> tuple[str, str]:
    """Return (selected_variant_name, reason). Selection uses TRAINING data only."""
    eligible = {
        name: phase_stats(trades, starting_capital)
        for name, trades in training_trades_by_variant.items()
        if len(trades) >= minimum_trades
    }
    if not eligible:
        return default, f"INSUFFICIENT_TRAINING_TRADES_FELL_BACK_TO_{default}"
    best = max(eligible, key=lambda name: eligible[name]["net_return_on_starting_capital"])
    return best, "BEST_TRAINING_NET_RETURN"


def bootstrap_summary(
    pnls: list[float],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Nonparametric bootstrap over trade-level P&L.

    Returns the point estimate, a 95% percentile CI on the total P&L, and the
    fraction of resamples whose total was <= 0 - a one-sided estimate of how
    often this exact out-of-sample trade set would fail to show a positive
    edge under resampling. This is deliberately a resampling check, not a
    claim of statistical independence between trades (cross-sectional trades
    on the same day are correlated) - see the "limitations" field.
    """
    if not pnls:
        return {
            "trade_count": 0, "point_estimate_total": 0.0,
            "ci_95_low": None, "ci_95_high": None,
            "probability_total_non_positive": None,
        }
    rng = random.Random(seed)
    n = len(pnls)
    totals = []
    for _ in range(iterations):
        resample = [pnls[rng.randrange(n)] for _ in range(n)]
        totals.append(sum(resample))
    totals.sort()
    lo = totals[int(0.025 * iterations)]
    hi = totals[int(0.975 * iterations) - 1]
    non_positive = sum(1 for value in totals if value <= 0) / iterations
    return {
        "trade_count": n,
        "point_estimate_total": sum(pnls),
        "ci_95_low": lo,
        "ci_95_high": hi,
        "probability_total_non_positive": non_positive,
        "iterations": iterations,
    }


def run_anchored_walk_forward(
    series: dict, market: list, cfg: PortfolioConfig,
    *, windows: tuple = WINDOWS,
) -> dict:
    history_start = market[0].timestamp.date()
    folds = []
    all_oos_trades: list[dict] = []

    for fold_start, fold_end in windows:
        training_window = (history_start, fold_start)
        test_window = (fold_start, fold_end)

        training_trades_by_variant = {}
        training_stats_by_variant = {}
        for name, max_pos, cand_per_day, sector_control in VARIANTS:
            result = simulate(
                name, max_pos, cand_per_day, sector_control, series, market, cfg,
                decision_window=training_window,
            )
            trades = phase_trades(result["trades"], training_window[0], training_window[1])
            training_trades_by_variant[name] = trades
            training_stats_by_variant[name] = phase_stats(trades, cfg.starting_capital)

        selected, selection_reason = select_variant(
            training_trades_by_variant, cfg.starting_capital,
        )
        variant_spec = next(v for v in VARIANTS if v[0] == selected)
        test_result = simulate(
            *variant_spec, series, market, cfg, decision_window=test_window,
        )
        test_trades = phase_trades(test_result["trades"], test_window[0], test_window[1])
        test_stats = phase_stats(test_trades, cfg.starting_capital)

        training_return = training_stats_by_variant[selected]["net_return_on_starting_capital"] or 0.0
        test_return = test_stats["net_return_on_starting_capital"] or 0.0
        walk_forward_efficiency = (
            test_return / training_return if training_return not in (0, None) else None
        )

        all_oos_trades.extend(test_trades)
        folds.append({
            "fold_start": fold_start.isoformat(),
            "fold_end": fold_end.isoformat(),
            "training_window": [training_window[0].isoformat(), training_window[1].isoformat()],
            "training_stats_by_variant": training_stats_by_variant,
            "selected_variant": selected,
            "selection_reason": selection_reason,
            "test_stats": test_stats,
            "walk_forward_efficiency": walk_forward_efficiency,
        })

    aggregate_oos = phase_stats(all_oos_trades, cfg.starting_capital)
    aggregate_bootstrap = bootstrap_summary([t["net_pnl"] for t in all_oos_trades])
    profitable_folds = sum(1 for fold in folds if fold["test_stats"]["net_pnl"] > 0)

    return {
        "research_version": "V10_ANCHORED_WALK_FORWARD",
        "config": asdict(cfg),
        "history_start": history_start.isoformat(),
        "minimum_training_trades": MINIMUM_TRAINING_TRADES,
        "default_fallback_variant": DEFAULT_VARIANT,
        "folds": folds,
        "profitable_folds": profitable_folds,
        "total_folds": len(folds),
        "aggregate_out_of_sample": aggregate_oos,
        "aggregate_bootstrap": aggregate_bootstrap,
        "limitations": [
            "Five folds is a very small number of independent out-of-sample "
            "periods; treat fold-count-derived confidence as weak regardless "
            "of the bootstrap result below it.",
            "The bootstrap resamples individual trades as if independent, but "
            "trades opened on the same cross-sectional decision day share "
            "market exposure; the reported CI is therefore optimistic "
            "(narrower than the true uncertainty).",
            "Early folds train on as little as ~5-6 months of history; their "
            "variant selection is the least reliable of the five.",
            "The 20-symbol universe and sector map are today's constituents, "
            "not a point-in-time investable universe; survivorship bias is "
            "not corrected for here.",
        ],
    }


def main() -> int:
    paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    series = {path.name.split("_", 2)[1]: load_candles_csv(path, 100) for path in paths}
    if set(series) != set(SECTORS):
        raise RuntimeError("frozen sector map and equity universe differ")
    market_path = next(Path("historical_data_market_60minute").glob("NSE_NIFTY*60minute*.csv"))
    market = load_candles_csv(market_path, 100)

    cfg = PortfolioConfig()
    report = run_anchored_walk_forward(series, market, cfg)

    output = Path("brain_results_v10")
    output.mkdir(exist_ok=True)
    (output / "anchored_walk_forward_v10.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )
    summary = {
        "profitable_folds": report["profitable_folds"],
        "total_folds": report["total_folds"],
        "aggregate_out_of_sample": report["aggregate_out_of_sample"],
        "aggregate_bootstrap": report["aggregate_bootstrap"],
        "fold_selections": [
            {"fold": f'{f["fold_start"]}_{f["fold_end"]}',
             "selected_variant": f["selected_variant"],
             "selection_reason": f["selection_reason"],
             "walk_forward_efficiency": f["walk_forward_efficiency"],
             "test_net_pnl": f["test_stats"]["net_pnl"]}
            for f in report["folds"]
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
