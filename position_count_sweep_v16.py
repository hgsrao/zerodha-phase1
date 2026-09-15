"""Version 16: how many concurrent positions actually makes sense?

Answers this empirically, not from portfolio-theory folklore, by re-running
the exact same validated methodology (anchored_walk_forward_momentum_v11's
17-fold walk-forward, real Zerodha delivery costs) at several different
position counts and comparing the results - the same rigor already applied
to everything else in this research program, not a new standard invented
for this one question.

Hard constraints checked first, not assumed:
- The validated universe is 19 symbols (POLYCAB excluded) across 11 distinct
  sectors - sector-controlled position counts above 11 are impossible
  without duplicating a sector, and even approaching 11 means holding most
  of the entire universe simultaneously, which dilutes a strategy whose
  whole premise is ranking and choosing the best movers, not owning nearly
  everything.
- Smaller per-position allocations face a worse proportional cost drag from
  Zerodha's flat DP fee (established in V14) - so this sweep runs with real
  costs applied throughout, not the flat bps guess, specifically so
  over-diversification's cost penalty shows up honestly instead of being
  hidden.

This module makes no broker calls; it only reads already-downloaded CSV
data and writes its own JSON report.
"""

from __future__ import annotations

import json
from pathlib import Path

from anchored_walk_forward_momentum_v11 import run_anchored_walk_forward
from external_model_comparison import ComparisonConfig, daily_closes
from extended_history_windows import EXTENDED_WINDOWS
from portfolio_brain_v9 import SECTORS
from run_extended_walk_forward import load_extended_universe
import zerodha_delivery_costs as real_costs


POSITION_COUNTS_TO_TEST = (2, 3, 4, 5, 6, 8, 10, 11)


def universe_sector_ceiling(excluded_symbols: frozenset) -> int:
    """The number of distinct sectors in the validated universe - the hard
    ceiling on a sector-controlled position count without duplication."""
    universe = {s: sec for s, sec in SECTORS.items() if s not in excluded_symbols}
    return len(set(universe.values()))


def sweep_position_counts(
    closes: dict, *, position_counts: tuple = POSITION_COUNTS_TO_TEST,
    windows: tuple = EXTENDED_WINDOWS, starting_capital: float = 100_000.0,
) -> dict:
    results = []
    for positions in position_counts:
        cfg = ComparisonConfig(starting_capital=starting_capital, positions=positions)
        report = run_anchored_walk_forward(closes, cfg, windows=windows, cost_model=real_costs)
        results.append({
            "positions": positions,
            "approx_capital_per_position": round(starting_capital / positions, 2),
            "profitable_folds": report["profitable_folds"],
            "total_folds": report["total_folds"],
            "mean_daily_return": report["aggregate_out_of_sample_daily_returns"]["mean_daily_return"],
            "bootstrap_ci_95": [
                report["aggregate_bootstrap_on_daily_returns"]["ci_95_low"],
                report["aggregate_bootstrap_on_daily_returns"]["ci_95_high"],
            ],
            "probability_non_positive": report["aggregate_bootstrap_on_daily_returns"]["probability_total_non_positive"],
        })
    return {
        "research_version": "V16_POSITION_COUNT_SWEEP",
        "cost_model": "zerodha_real_delivery_schedule",
        "starting_capital": starting_capital,
        "results": results,
    }


def main() -> int:
    from extended_history_windows import EXCLUDED_SYMBOLS
    ceiling = universe_sector_ceiling(EXCLUDED_SYMBOLS)
    print(f"Universe sector ceiling (distinct sectors): {ceiling}")

    series, market = load_extended_universe()
    closes = {symbol: daily_closes(bars) for symbol, bars in series.items()}
    report = sweep_position_counts(closes)

    output = Path("brain_results_v16")
    output.mkdir(exist_ok=True)
    (output / "position_count_sweep_v16.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
