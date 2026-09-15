"""Run V10 (V9), V11 (momentum), and V12 (momentum + risk) against the
extended 2016-2026 history, on the 19-symbol universe (POLYCAB excluded -
see extended_history_windows.py), with 17 anchored folds instead of 5.

Reads only the corrected "_ready" CSV copies produced by
prepare_research_ohlcv.py - never the immutable raw "_extended" downloads
directly - so every anomalous OHLC tick this run sees has already been
deterministically corrected and logged to correction_audit.csv.

This module makes no broker calls and writes nothing but its own JSON
reports under brain_results_extended/.
"""

from __future__ import annotations

import json
from pathlib import Path

from anchored_walk_forward_momentum_v11 import run_anchored_walk_forward as run_v11
from anchored_walk_forward_v10 import run_anchored_walk_forward as run_v10
from anchored_walk_forward_v12 import run_anchored_walk_forward as run_v12
from brain_research_lab import load_candles_csv
from extended_history_windows import EXCLUDED_SYMBOLS, EXTENDED_WINDOWS
from external_model_comparison import ComparisonConfig, daily_closes
from momentum_risk_managed_v12 import resample_daily
from portfolio_brain_v9 import PortfolioConfig, SECTORS


EQUITIES_DIR = Path("historical_data_60minute_extended_ready")
MARKET_DIR = Path("historical_data_market_60minute_extended_ready")


def load_extended_universe() -> tuple[dict, list]:
    paths = sorted(EQUITIES_DIR.glob("NSE_*60minute*.csv"))
    series = {
        path.name.split("_", 2)[1]: load_candles_csv(path, 100)
        for path in paths
        if path.name.split("_", 2)[1] not in EXCLUDED_SYMBOLS
    }
    expected = set(SECTORS) - EXCLUDED_SYMBOLS
    if set(series) != expected:
        raise RuntimeError(
            f"extended universe mismatch: expected {sorted(expected)}, got {sorted(series)}"
        )
    market_path = next(MARKET_DIR.glob("NSE_NIFTY*60minute*.csv"))
    market = load_candles_csv(market_path, 100)
    return series, market


def main() -> int:
    series, market = load_extended_universe()
    closes = {symbol: daily_closes(bars) for symbol, bars in series.items()}
    daily_series = {symbol: resample_daily(bars) for symbol, bars in series.items()}

    portfolio_cfg = PortfolioConfig(starting_capital=100_000.0)
    comparison_cfg = ComparisonConfig(starting_capital=100_000.0)

    report_v10 = run_v10(series, market, portfolio_cfg, windows=EXTENDED_WINDOWS)
    report_v11 = run_v11(closes, comparison_cfg, windows=EXTENDED_WINDOWS)
    report_v12 = run_v12(daily_series, portfolio_cfg, windows=EXTENDED_WINDOWS)

    output = Path("brain_results_extended")
    output.mkdir(exist_ok=True)
    (output / "v10_extended.json").write_text(json.dumps(report_v10, indent=2), encoding="utf-8")
    (output / "v11_extended.json").write_text(json.dumps(report_v11, indent=2), encoding="utf-8")
    (output / "v12_extended.json").write_text(json.dumps(report_v12, indent=2), encoding="utf-8")

    summary = {
        "universe": sorted(series), "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
        "total_folds": len(EXTENDED_WINDOWS),
        "history": [EXTENDED_WINDOWS[0][0].isoformat(), EXTENDED_WINDOWS[-1][1].isoformat()],
        "V10_V9_ALONE": {
            "profitable_folds": report_v10["profitable_folds"],
            "aggregate_out_of_sample": report_v10["aggregate_out_of_sample"],
            "aggregate_bootstrap": report_v10["aggregate_bootstrap"],
        },
        "V11_MOMENTUM": {
            "profitable_folds": report_v11["profitable_folds"],
            "aggregate_out_of_sample_daily_returns": report_v11["aggregate_out_of_sample_daily_returns"],
            "aggregate_bootstrap_on_daily_returns": report_v11["aggregate_bootstrap_on_daily_returns"],
        },
        "V12_MOMENTUM_RISK_MANAGED": {
            "profitable_folds": report_v12["profitable_folds"],
            "aggregate_out_of_sample": report_v12["aggregate_out_of_sample"],
            "aggregate_bootstrap": report_v12["aggregate_bootstrap"],
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
