"""Run the framework against the exact frozen 48-symbol history only."""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import CompleteIntegratedTradingSystem
from l2_dataset_certifier_v2 import AUTHORITATIVE_UNIVERSE


FROZEN_DATA_DIR = Path(
    "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/"
    "DATA_1MIN_48_20230703_20260824"
)
EVALUATION_START = "2023-08-14"
EVALUATION_END = "2026-08-24 15:14:59"
OUTPUT_FILE = "FROZEN_48_15MIN_BASELINE_20260829.json"


def load_all_symbols(symbols: list[str], data_dir: Path) -> dict[str, pd.DataFrame]:
    """Causally aggregate complete 15-minute bars from immutable 1-minute CSVs."""
    data = {}
    for symbol in sorted(symbols):
        matches = sorted(data_dir.glob(f"NSE_{symbol}_minute_*.csv"))
        if not matches:
            raise FileNotFoundError(f"Frozen source missing for {symbol}")
        df = pd.read_csv(matches[-1])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(
            "Asia/Kolkata"
        )
        df = df.sort_values("timestamp").set_index("timestamp")
        df = df.between_time("09:15", "15:14:59")
        counts = df["close"].resample(
            "15min", label="left", closed="left", origin="start_day", offset="9h15min"
        ).count()
        bars = df.resample(
            "15min", label="left", closed="left", origin="start_day", offset="9h15min"
        ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        bars = bars[counts.eq(15)].dropna().reset_index()
        bars = bars.loc[
            (bars["timestamp"] >= pd.Timestamp(EVALUATION_START, tz="Asia/Kolkata"))
            & (bars["timestamp"] <= pd.Timestamp(EVALUATION_END, tz="Asia/Kolkata"))
        ].reset_index(drop=True)
        if bars.empty:
            raise ValueError(f"No complete frozen 15-minute bars for {symbol}")
        data[symbol] = bars
    return data


def main() -> None:
    logging.disable(logging.CRITICAL)
    data = load_all_symbols(list(AUTHORITATIVE_UNIVERSE), FROZEN_DATA_DIR)
    system = CompleteIntegratedTradingSystem(verbose=False)
    initialization = system.initialize_from_data(data, list(data))
    result = system.run_paper_trading(list(data))
    payload = {
        "timestamp": datetime.now().isoformat(),
        "mode": "paper_trading_baseline",
        "source": "frozen_1minute_history_resampled_to_complete_15minute_bars",
        "configured_symbol_count": len(AUTHORITATIVE_UNIVERSE),
        "evaluation_start": EVALUATION_START,
        "evaluation_end": EVALUATION_END,
        "loaded_symbols": list(data),
        "metrics": result["metrics"],
        "trades_by_symbol": result["trades_by_symbol"],
        "thresholds": initialization["thresholds"],
        "starting_parameters": initialization["starting_params"],
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, default=str)
    print(json.dumps(payload["metrics"], indent=2))


if __name__ == "__main__":
    main()
