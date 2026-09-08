#!/usr/bin/env python3
"""Run one fixed-parameter three-year validation on five frozen symbols.

This is deliberately a single default-config evaluation, not an optimizer.
It provides an honest baseline before any train/validation/test calibration.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

SYMBOLS = ["INFY", "HDFCBANK", "RELIANCE", "SUNPHARMA", "TCS"]
ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"
OUTPUT_PATH = ROOT / "output" / "five_symbol_three_year_default_validation.json"


def main() -> None:
    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    integrity = verify_manifest(manifest)
    if not integrity.valid:
        raise SystemExit(f"dataset manifest validation failed: {integrity.message}")

    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = {}
    for symbol in SYMBOLS:
        frame = loader._load_symbol_csv(symbol)
        if frame is None or not loader._validate_dataframe(frame):
            raise SystemExit(f"invalid real historical data for {symbol}")
        bars[symbol] = frame

    print(json.dumps({
        "status": "started", "symbols": SYMBOLS,
        "rows": {symbol: len(frame) for symbol, frame in bars.items()},
        "manifest_hash": manifest.manifest_hash,
    }), flush=True)

    engine = Revision2PortfolioOrchestrator(SYMBOLS, CanonicalParameterRegistry(), starting_equity=1_000_000.0)
    report = engine.run(bars, warmup=60)
    trades = report["trades"]
    result = {
        "status": "completed",
        "symbols": SYMBOLS,
        "manifest_hash": manifest.manifest_hash,
        "window": {
            symbol: [str(frame["timestamp"].iloc[0]), str(frame["timestamp"].iloc[-1])]
            for symbol, frame in bars.items()
        },
        "report": report,
        "summary": {
            "exit_reasons": dict(Counter(trade["reason"] for trade in trades)),
            "trades_by_symbol": dict(Counter(trade["symbol"] for trade in trades)),
            "same_entry_exit_bar": sum(
                trade.get("entry_bar_idx") == trade.get("exit_bar_idx") for trade in trades
            ),
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": "completed", **result["summary"], **{
        key: report[key] for key in ("completed_trades", "gross_pnl", "net_pnl", "ending_equity", "mtm_max_drawdown_fraction")
    }, "output": str(OUTPUT_PATH)}, default=str), flush=True)


if __name__ == "__main__":
    main()
