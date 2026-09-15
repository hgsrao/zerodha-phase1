"""V11 anchored walk-forward, real methodology, TRUE out-of-sample test:
2015-01-01 to 2023-01-01 - a period with zero overlap with the
2023-08-14 to 2026-08-16 data every other test in this session used.
Neither this strategy's parameters nor anyone in this session has looked
at this period before now.

NOTHING is changed from the sealed methodology, per explicit instruction:
same run_anchored_walk_forward() (unmodified), same 12-month formation /
1-month skip / top-4 / equal-weight thresholds, same monthly rebalance
cadence, same cost/slippage assumptions. Only the input data and the
fold windows (necessarily - this is a different calendar period) differ.

14 anchored 6-month folds (Feb 2016 - Jan 2023) instead of the usual 5 -
more data was available, so more independent test periods were used,
which directly addresses this session's own repeated caveat about small
fold counts. This period spans genuinely different market regimes (the
2018 selloff, the 2020 COVID crash and recovery, the 2022 rate-hike bear
market) rather than one continuous bull run, unlike 2024-2026.

DOW and CTVA excluded from every universe that includes them (both are
2019 corporate spinoffs with no real trading history before then -
confirmed by their own row counts, ~955 and ~909 days respectively
instead of the ~2014 every other symbol has) - including them would
silently truncate the whole Materials sector's usable window from 8
years down to about 3.5, which is not what "go substantially backward"
asked for.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from anchored_walk_forward_momentum_v11 import run_anchored_walk_forward
from external_model_comparison import ComparisonConfig

DATA_DIR = Path(__file__).parent / "historical_data_yahoo_daily"
OUT_DIR = Path(__file__).parent / "brain_results_yahoo_broader"
FILE_SUFFIX = "2015-01-01_2023-01-01"

SECTORS = {
    "Information Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "AMD", "INTC"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BRK-B", "BLK", "AXP", "SCHW", "C"],
    "Healthcare": ["JNJ", "UNH", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "MAR"],
    "Consumer Staples": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MDLZ", "CL", "KMB", "GIS"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR"],
    "Industrials": ["HON", "UNP", "RTX", "CAT", "BA", "GE", "LMT", "DE", "UPS", "MMM"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DD", "NUE"],  # DOW, CTVA excluded - see module docstring
    "Real Estate": ["PLD", "AMT", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "AVB", "EQR"],
}


def build_fold_windows() -> tuple:
    windows = []
    cursor = date(2016, 2, 14)
    end = date(2023, 1, 1)
    while cursor < end:
        next_cursor = date(cursor.year + (1 if cursor.month > 6 else 0),
                            (cursor.month + 6 - 1) % 12 + 1, 14)
        if next_cursor > end:
            next_cursor = end
        windows.append((cursor, next_cursor))
        cursor = next_cursor
    return tuple(windows)


WINDOWS_2015_2023 = build_fold_windows()


def load_closes(symbols: list[str]) -> dict[str, dict[str, float]]:
    closes: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        path = DATA_DIR / f"{symbol}_daily_{FILE_SUFFIX}.csv"
        if not path.exists():
            continue
        series: dict[str, float] = {}
        with path.open("r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                series[row["timestamp"]] = float(row["close"])
        closes[symbol] = series
    return closes


def main() -> int:
    print(f"=== V11 anchored walk-forward, 2015-2023 (TRUE out-of-sample), per sector ===")
    print(f"{len(WINDOWS_2015_2023)} anchored folds (vs. the usual 5) - Feb 2016 to Jan 2023.")
    print("Nothing about the strategy changed: same thresholds, same top-4, same monthly cadence.\n")

    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)
    all_reports = {}

    print(f"{'Sector':<26}{'Profitable':>12}{'Bootstrap non-pos %':>22}")
    for sector, symbols in SECTORS.items():
        closes = load_closes(symbols)
        report = run_anchored_walk_forward(closes, cfg, windows=WINDOWS_2015_2023)
        all_reports[sector] = report
        b = report["aggregate_bootstrap_on_daily_returns"]
        print(f"{sector:<26}{report['profitable_folds']}/{report['total_folds']:>10}"
              f"{b['probability_total_non_positive']:>21.0%}")

    # Combined, all sectors, one universe - the same "let the choices be
    # made regardless of sector" test as before, now on the unseen period.
    all_symbols = sorted({s for symbols in SECTORS.values() for s in symbols})
    closes = load_closes(all_symbols)
    combined_report = run_anchored_walk_forward(closes, cfg, windows=WINDOWS_2015_2023)
    all_reports["ALL_SECTORS_COMBINED"] = combined_report
    b = combined_report["aggregate_bootstrap_on_daily_returns"]
    print(f"{'ALL SECTORS COMBINED':<26}{combined_report['profitable_folds']}/"
          f"{combined_report['total_folds']:>10}{b['probability_total_non_positive']:>21.0%}")

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "v11_historical_2015_2023_walk_forward.json"
    out_path.write_text(json.dumps(all_reports, indent=2), encoding="utf-8")
    print(f"\nFull reports: {out_path}")

    print("\n=== Fold-by-fold, combined universe (all sectors) ===")
    for f in combined_report["folds"]:
        print(f"  {f['fold_start']} to {f['fold_end']}: {f['test_metrics']['net_return']:+.2%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
