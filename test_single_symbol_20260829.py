"""Run each diagnostic symbol in an isolated paper-trading system.

This intentionally creates a new CompleteIntegratedTradingSystem per symbol.
It records both calibration outputs and paper-trading metrics to make
cross-symbol comparison reproducible.  It never connects to a broker.
"""

import json
from datetime import datetime
from pathlib import Path
from collections import Counter

import pandas as pd

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import CompleteIntegratedTradingSystem


DATA_DIR = Path(
    "P01D_V2B_REGIME_TWO_PILLAR_20260816/"
    "DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES"
)
SYMBOLS = ("INFY", "TCS", "RELIANCE", "HDFCBANK", "SBIN")
OUTPUT_FILE = Path("SINGLE_SYMBOL_DIAGNOSTIC_RESULTS_20260829.json")


def load_symbol(symbol: str) -> pd.DataFrame:
    matches = sorted(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))
    if not matches:
        raise FileNotFoundError(f"No 15-minute data found for {symbol}")
    df = pd.read_csv(matches[-1])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(
        "Asia/Kolkata"
    )
    return df.sort_values("timestamp").reset_index(drop=True)


def main() -> None:
    report = {"timestamp": datetime.now().isoformat(), "symbols": {}}
    for symbol in SYMBOLS:
        df = load_symbol(symbol)
        system = CompleteIntegratedTradingSystem(verbose=False)
        initialization = system.initialize_from_data({symbol: df}, [symbol])
        result = system.run_paper_trading([symbol])
        metrics = result["metrics"]
        report["symbols"][symbol] = {
            "bars": len(df),
            "data_start": str(df["timestamp"].iloc[0]),
            "data_end": str(df["timestamp"].iloc[-1]),
            "thresholds": initialization["thresholds"][symbol],
            "starting_parameters": initialization["starting_params"][symbol],
            "metrics": metrics,
            "trade_diagnostics": {
                "exit_reasons": dict(
                    Counter(trade["exit_reason"] for trade in result["trades"])
                ),
                "hold_bars": {
                    "minimum": min((trade["hold_bars"] for trade in result["trades"]), default=0),
                    "maximum": max((trade["hold_bars"] for trade in result["trades"]), default=0),
                    "one_bar_trades": sum(
                        trade["hold_bars"] == 1 for trade in result["trades"]
                    ),
                },
            },
        }
        print(
            f"{symbol}: {metrics['win_rate']:.2%} win | "
            f"{metrics['total_trades']} trades | "
            f"P&L {metrics['total_pnl']:+,.2f}"
        )

    OUTPUT_FILE.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
