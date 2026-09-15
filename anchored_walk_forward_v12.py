"""Anchored walk-forward validation for V12 (momentum selection, V9-style risk
management), using the identical methodology as V10 and V11.

V12's in-sample, full-period headline return is positive but misleading on
its own: closed trades net a loss over the full period, and the positive
headline comes entirely from unrealized gains in positions still open at the
arbitrary end-of-data cutoff (see BRAIN_RESEARCH_SPEC_V12). That is exactly
the kind of artifact an honest out-of-sample split should catch, since each
fold's test window ends at a different date and can't all get lucky at the
same cutoff.

Reuses phase_trades / phase_stats / select_variant / bootstrap_summary from
anchored_walk_forward_v10.py unmodified - V12's trade dicts use the identical
schema to V9's, so there is nothing V12-specific to reimplement there.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from anchored_walk_forward_v10 import bootstrap_summary, phase_stats, phase_trades, select_variant
from momentum_risk_managed_v12 import load_daily_universe, simulate
from portfolio_brain_v9 import PortfolioConfig
from walk_forward_v5 import WINDOWS


VARIANTS = (
    ("V12_TOP4_SECTOR", 4, True),
    ("V12_TOP2_SECTOR", 2, True),
)
DEFAULT_VARIANT = "V12_TOP4_SECTOR"
MINIMUM_TRAINING_TRADES = 5


def run_anchored_walk_forward(
    daily_series: dict, cfg: PortfolioConfig, *, windows: tuple = WINDOWS,
) -> dict:
    all_dates = sorted(set.intersection(*(
        {c.timestamp.date() for c in bars} for bars in daily_series.values()
    )))
    history_start = all_dates[0]

    folds = []
    all_oos_trades: list[dict] = []

    for fold_start, fold_end in windows:
        training_window = (history_start, fold_start)
        test_window = (fold_start, fold_end)

        training_trades_by_variant = {}
        training_stats_by_variant = {}
        for name, max_pos, sector_control in VARIANTS:
            result = simulate(name, max_pos, sector_control, daily_series, cfg, decision_window=training_window)
            trades = phase_trades(result["trades"], training_window[0], training_window[1])
            training_trades_by_variant[name] = trades
            training_stats_by_variant[name] = phase_stats(trades, cfg.starting_capital)

        selected, reason = select_variant(
            training_trades_by_variant, cfg.starting_capital,
            minimum_trades=MINIMUM_TRAINING_TRADES, default=DEFAULT_VARIANT,
        )
        variant_spec = next(v for v in VARIANTS if v[0] == selected)
        test_result = simulate(*variant_spec, daily_series, cfg, decision_window=test_window)
        test_trades = phase_trades(test_result["trades"], test_window[0], test_window[1])
        test_stats = phase_stats(test_trades, cfg.starting_capital)

        training_return = training_stats_by_variant[selected]["net_return_on_starting_capital"] or 0.0
        test_return = test_stats["net_return_on_starting_capital"] or 0.0
        walk_forward_efficiency = test_return / training_return if training_return else None

        all_oos_trades.extend(test_trades)
        folds.append({
            "fold_start": fold_start.isoformat(), "fold_end": fold_end.isoformat(),
            "training_window": [training_window[0].isoformat(), training_window[1].isoformat()],
            "training_stats_by_variant": training_stats_by_variant,
            "selected_variant": selected, "selection_reason": reason,
            "test_stats": test_stats, "walk_forward_efficiency": walk_forward_efficiency,
        })

    aggregate_oos = phase_stats(all_oos_trades, cfg.starting_capital)
    aggregate_bootstrap = bootstrap_summary([t["net_pnl"] for t in all_oos_trades])
    profitable_folds = sum(1 for fold in folds if fold["test_stats"]["net_pnl"] > 0)

    return {
        "research_version": "V12_ANCHORED_WALK_FORWARD",
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
            "Closed-trade P&L only - unlike the naive full-period run, every "
            "fold's test window is scored on its own closed and mark-to-date "
            "trades, so no single fold's result depends on an arbitrary "
            "end-of-data unrealized position the way the full-period number "
            "does.",
            "Five folds is a very small number of independent out-of-sample "
            "periods.",
            "The 20-symbol universe and sector map are today's constituents, "
            "not a point-in-time investable universe; survivorship bias is "
            "not corrected for here.",
        ],
    }


def main() -> int:
    daily_series = load_daily_universe()
    cfg = PortfolioConfig(starting_capital=100_000.0)
    report = run_anchored_walk_forward(daily_series, cfg)
    output = Path("brain_results_v12")
    output.mkdir(exist_ok=True)
    (output / "anchored_walk_forward_v12.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {
        "profitable_folds": report["profitable_folds"], "total_folds": report["total_folds"],
        "aggregate_out_of_sample": report["aggregate_out_of_sample"],
        "aggregate_bootstrap": report["aggregate_bootstrap"],
        "fold_selections": [
            {"fold": f'{f["fold_start"]}_{f["fold_end"]}', "selected_variant": f["selected_variant"],
             "selection_reason": f["selection_reason"], "walk_forward_efficiency": f["walk_forward_efficiency"],
             "test_net_pnl": f["test_stats"]["net_pnl"]}
            for f in report["folds"]
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
