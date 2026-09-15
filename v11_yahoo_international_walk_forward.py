"""V11 anchored walk-forward, real methodology, against the
international (US megacap) basket - the same rigorous 5-fold test
already run for the broader Nifty universe, not yet run for
international (only a single full-history number existed before this).

Reuses run_anchored_walk_forward() completely unmodified - same
function, same 5 fold windows, same training/selection/bootstrap logic.
Only the closes input changes.

Caveat worth restating plainly: only 12 symbols in this universe, and
the strategy always picks 4 of them - that's a third of the whole
basket every rebalance, a much smaller and more concentrated pool than
the 48-stock Nifty test. Treat any result here as even less statistically
meaningful than the Nifty walk-forward, not more.
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

INTERNATIONAL_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "JNJ", "V", "WMT", "BRK-B",
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
    print("=== V11 anchored walk-forward - international (US megacap) universe ===")
    print(f"Loading {len(INTERNATIONAL_SYMBOLS)} symbols from {DATA_DIR}...")
    print("CAVEAT: only 12 symbols total, picking 4 each time - a third of the whole")
    print("universe every rebalance. Treat this as less statistically meaningful than")
    print("the 48-stock Nifty walk-forward, not more.\n")

    closes = load_closes(INTERNATIONAL_SYMBOLS)
    print(f"{len(closes)} symbols loaded with real data.\n")

    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)
    report = run_anchored_walk_forward(closes, cfg)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "v11_yahoo_international_walk_forward.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"{'Fold':<28}{'Selected variant':<32}{'Test net return':>16}")
    for f in report["folds"]:
        label = f"{f['fold_start']} to {f['fold_end']}"
        print(f"{label:<28}{f['selected_variant']:<32}{f['test_metrics']['net_return']:>+15.2%}")

    print(f"\nProfitable folds: {report['profitable_folds']} / {report['total_folds']}")
    b = report["aggregate_bootstrap_on_daily_returns"]
    print(f"Bootstrap probability of a non-positive aggregate result: {b['probability_total_non_positive']:.0%}")
    print(f"\nFull report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
