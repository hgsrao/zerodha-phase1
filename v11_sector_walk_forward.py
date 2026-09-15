"""V11 anchored walk-forward, real methodology, run separately per GICS-
style sector universe.

Same unmodified run_anchored_walk_forward() as every other walk-forward
in this session - only the closes input changes, once per sector. Each
sector run independently (never blended into one universe) - a fair
"top-4 of the sector" test needs the ranking pool confined to that
sector, not diluted across all 110 stocks at once.

EA (Electronic Arts) excluded from Communication Services: its
downloaded data shows only 6 real trading days with volume dropping to
zero from 2026-08-05 onward - the signature of a stock taken private/
delisted, not a download error. Left in, it would collapse Communication
Services' common-dates intersection to almost nothing and break that
sector's whole test. Excluded explicitly, not silently.
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
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR"],  # EA excluded - see module docstring
    "Industrials": ["HON", "UNP", "RTX", "CAT", "BA", "GE", "LMT", "DE", "UPS", "MMM"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DD", "DOW", "NUE", "CTVA"],
    "Real Estate": ["PLD", "AMT", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "AVB", "EQR"],
}


def load_closes(symbols: list[str]) -> dict[str, dict[str, float]]:
    closes: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        matches = list(DATA_DIR.glob(f"{symbol}_daily_*.csv"))
        if not matches:
            continue
        series: dict[str, float] = {}
        with matches[0].open("r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                series[row["timestamp"]] = float(row["close"])
        closes[symbol] = series
    return closes


def main() -> int:
    print("=== V11 anchored walk-forward, per sector - real code, real new data ===\n")
    cfg = ComparisonConfig(starting_capital=100_000.0, positions=4)

    all_reports = {}
    print(f"{'Sector':<26}{'Profitable':>12}{'Bootstrap non-pos %':>22}{'Fold returns':>50}")
    for sector, symbols in SECTORS.items():
        closes = load_closes(symbols)
        report = run_anchored_walk_forward(closes, cfg)
        all_reports[sector] = report
        fold_str = " / ".join(f"{f['test_metrics']['net_return']:+.1%}" for f in report["folds"])
        b = report["aggregate_bootstrap_on_daily_returns"]
        print(f"{sector:<26}{report['profitable_folds']}/{report['total_folds']:>10}"
              f"{b['probability_total_non_positive']:>21.0%}   {fold_str}")

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "v11_sector_walk_forward.json"
    out_path.write_text(json.dumps(all_reports, indent=2), encoding="utf-8")
    print(f"\nFull per-sector reports: {out_path}")
    print("\nNote: Communication Services ran with 9 symbols (EA excluded - delisted, see module docstring),")
    print("every other sector ran with the full 10.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
