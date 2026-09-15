"""V11 anchored walk-forward, real methodology, across ALL 11 sectors
combined into one single universe - no sector bias, no cherry-picking
which sectors to include based on how they individually performed.

This directly answers "let us spread it across all sectors, whichever
is stronger or weaker" - one unified ~106-stock pool, the real top-4
selection picks purely on momentum, wherever it comes from. Same
unmodified run_anchored_walk_forward() as every other walk-forward this
session.

EA excluded (delisted - see v11_sector_walk_forward.py's docstring for
the evidence). No other exclusions or sector weighting of any kind.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from anchored_walk_forward_momentum_v11 import run_anchored_walk_forward
from external_model_comparison import ComparisonConfig

DATA_DIR = Path(__file__).parent / "historical_data_yahoo_daily"
OUT_DIR = Path(__file__).parent / "brain_results_yahoo_broader"

SECTORS = {
    "Information Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "AMD", "INTC"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BRK-B", "BLK", "AXP", "SCHW", "C"],
    "Healthcare": ["JNJ", "UNH", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "MAR"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MDLZ", "CL", "KMB", "GIS"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR"],  # EA excluded - delisted
    "Industrials": ["HON", "UNP", "RTX", "CAT", "BA", "GE", "LMT", "DE", "UPS", "MMM"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DD", "DOW", "NUE", "CTVA"],
    "Real Estate": ["PLD", "AMT", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "AVB", "EQR"],
}

ALL_SYMBOLS = sorted({s for symbols in SECTORS.values() for s in symbols})


def load_closes(symbols: list[str]) -> dict[str, dict[str, float]]:
    closes: dict[str, dict[str, float]] = {}
    missing = []
    for symbol in symbols:
        matches = list(DATA_DIR.glob(f"{symbol}_daily_*.csv"))
        if not matches:
            missing.append(symbol)
            continue
        series: dict[str, float] = {}
        with matches[0].open("r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                series[row["timestamp"]] = float(row["close"])
        closes[symbol] = series
    if missing:
        print(f"  (skipped, no file: {missing})")
    return closes


def sector_of(symbol: str) -> str:
    for sector, symbols in SECTORS.items():
        if symbol in symbols:
            return sector
    return "?"


def main() -> int:
    print(f"=== V11 anchored walk-forward - ALL {len(ALL_SYMBOLS)} sector stocks combined, one universe ===")
    print("No sector bias, no cherry-picking - the real top-4 selection picks purely")
    print("on momentum score across the whole combined pool.\n")

    closes = load_closes(ALL_SYMBOLS)
    print(f"{len(closes)} symbols loaded with real data.\n")

    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)
    report = run_anchored_walk_forward(closes, cfg)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "v11_all_sectors_combined_walk_forward.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"{'Fold':<28}{'Selected variant':<32}{'Test net return':>16}")
    for f in report["folds"]:
        label = f"{f['fold_start']} to {f['fold_end']}"
        print(f"{label:<28}{f['selected_variant']:<32}{f['test_metrics']['net_return']:>+15.2%}")

    print(f"\nProfitable folds: {report['profitable_folds']} / {report['total_folds']}")
    b = report["aggregate_bootstrap_on_daily_returns"]
    print(f"Bootstrap probability of a non-positive aggregate result: {b['probability_total_non_positive']:.0%}")

    # Which sector did the actual picks come from, fold by fold? Purely
    # informational - shows whether the combined universe naturally
    # gravitated toward certain sectors on its own, without being told to.
    print("\n=== Which sectors did the real picks actually come from? (informational only) ===")
    from external_model_comparison import _prior_month_date, month_ends
    common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
    ends = month_ends(common_dates)
    warmup = cfg.formation_months + cfg.skip_months
    for f in report["folds"]:
        fold_start, fold_end = f["fold_start"], f["fold_end"]
        dates_in_fold = [d for d in common_dates if fold_start <= d < fold_end]
        rebalance_dates = [d for d in dates_in_fold if d in ends]
        picks_this_fold = []
        for day in rebalance_dates:
            month_i = ends.index(day)
            recent = _prior_month_date(ends, month_i, cfg.skip_months)
            old = _prior_month_date(ends, month_i, warmup)
            if recent and old:
                scores = {s: closes[s][recent] / closes[s][old] - 1 for s in closes}
                ranked = sorted(scores, key=scores.get, reverse=True)[:cfg.positions]
                picks_this_fold.extend(ranked)
        sector_counts: dict[str, int] = {}
        for p in picks_this_fold:
            sec = sector_of(p)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
        summary = ", ".join(f"{sec}={n}" for sec, n in sorted(sector_counts.items(), key=lambda x: -x[1]))
        print(f"  {fold_start} to {fold_end}: {summary if summary else '(no picks - insufficient data)'}")

    print(f"\nFull report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
