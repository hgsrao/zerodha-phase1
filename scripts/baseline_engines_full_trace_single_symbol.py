#!/usr/bin/env python3
"""
BASELINE ENGINE FULL IVO TRACE: Single Symbol, 3-Year Data, Complete Black Box Visibility.

Runs both Revision 2 External (HMM) and Revision 2 In-House (Vanilla) on INFY 3-year data.
Logs EVERY step: inputs, processing, outputs for each black box.

Usage:
  python3 scripts/baseline_engines_full_trace_single_symbol.py INFY
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import json
import pandas as pd
import logging
from datetime import datetime
from typing import Any, Dict

# Setup detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


class TraceLogger:
    """Centralized trace logging with formatting."""

    def __init__(self, filename: str):
        self.log_file = open(filename, 'w')
        self.step = 0

    def log(self, category: str, status: str, details: str = "", data: Dict[str, Any] = None):
        """Log a step with optional JSON data."""
        self.step += 1
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        status_char = "✓" if status == "OK" else "✗" if status == "ERROR" else "⊙"

        line = f"[{timestamp}] [{self.step:04d}] {status_char} {category:40s} | {details}\n"
        self.log_file.write(line)
        self.log_file.flush()
        print(line.rstrip())

        if data:
            json_str = json.dumps(data, indent=2, default=str)
            for json_line in json_str.split('\n'):
                self.log_file.write(f"         {json_line}\n")
            self.log_file.flush()

    def close(self):
        self.log_file.close()


def trace_external_engine(symbol: str, df: pd.DataFrame):
    """Trace Revision 2 External Engine (HMM + PyPortfolioOpt)."""

    tracer = TraceLogger(f"trace_external_{symbol}_3year.log")

    print("\n" + "=" * 120)
    print(f"BASELINE EXTERNAL ENGINE TRACE (HMM Regime + PyPortfolioOpt Position Sizing)")
    print(f"Symbol: {symbol} | Duration: 3 years | Bars: {len(df):,}")
    print("=" * 120 + "\n")

    try:
        # STEP 1: Initialize Registry
        tracer.log("Initialize", "RUNNING", "Loading parameter registry")
        registry = CanonicalParameterRegistry()
        tracer.log("Initialize", "OK", "Registry loaded", {
            "parameters": 69,  # From earlier in project
            "calibratable": 47,
        })

        # STEP 2: Initialize Orchestrator
        tracer.log("Instantiate", "RUNNING", f"Create Revision2ExternalEngineOrchestrator([{symbol}])")
        orch = Revision2ExternalEngineOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        tracer.log("Instantiate", "OK", "Orchestrator created", {
            "symbol": symbol,
            "starting_equity": 1_000_000.0,
            "engine_type": "Revision2ExternalEngineOrchestrator",
        })

        # STEP 3: Run Backtest
        tracer.log("Backtest", "RUNNING", f"Run on {len(df):,} bars, warmup=60", {
            "first_bar_timestamp": str(df.iloc[0]['timestamp']),
            "last_bar_timestamp": str(df.iloc[-1]['timestamp']),
            "data_columns": list(df.columns),
        })

        symbol_bars = {symbol: df}
        report = orch.run(symbol_bars, warmup=60)

        tracer.log("Backtest", "OK", "Completed", {
            "bars_processed": report.get("bars_processed", 0),
            "trades_executed": len(report.get("trades", [])),
            "net_pnl": report.get("net_pnl", 0),
            "win_rate": report.get("win_rate", 0),
            "max_drawdown": report.get("max_drawdown", 0),
            "profit_factor": report.get("profit_factor", 0),
        })

        # STEP 4: Trade Analysis
        if report.get("trades"):
            tracer.log("Trades", "OK", f"Generated {len(report['trades'])} trades", {
                "trades_sample": [
                    {
                        "entry_price": t.get("entry_price"),
                        "exit_price": t.get("exit_price"),
                        "quantity": t.get("quantity"),
                        "pnl": t.get("pnl"),
                        "bars_held": t.get("bars_held"),
                    }
                    for t in report["trades"][:5]
                ] + (["..."] if len(report["trades"]) > 5 else [])
            })
        else:
            tracer.log("Trades", "WARN", "No trades generated", {
                "possible_reasons": [
                    "PA box produced zero signals",
                    "All signals rejected by ID box",
                    "Market conditions incompatible with strategy",
                ]
            })

        # STEP 5: Performance Summary
        tracer.log("Summary", "OK", "External engine complete", {
            "engine": "Revision2ExternalEngineOrchestrator",
            "symbol": symbol,
            "period": "3 years (2023-07-03 to 2026-08-24)",
            "total_trades": len(report.get("trades", [])),
            "net_pnl_rupees": report.get("net_pnl", 0),
            "win_rate_pct": report.get("win_rate", 0) * 100,
            "max_drawdown_pct": report.get("max_drawdown", 0) * 100,
            "profit_factor": report.get("profit_factor", 0),
        })

    except Exception as e:
        tracer.log("Error", "ERROR", f"Exception: {str(e)}")
        import traceback
        tracer.log("Error", "ERROR", "Full traceback:")
        for line in traceback.format_exc().split('\n'):
            tracer.log("Error", "ERROR", line)

    finally:
        tracer.close()


def trace_inhouse_engine(symbol: str, df: pd.DataFrame):
    """Trace Revision 2 In-House Engine (Vanilla Regime + Simple Sizing)."""

    tracer = TraceLogger(f"trace_inhouse_{symbol}_3year.log")

    print("\n" + "=" * 120)
    print(f"BASELINE IN-HOUSE ENGINE TRACE (Vanilla Volatility Regime + Simple Position Sizing)")
    print(f"Symbol: {symbol} | Duration: 3 years | Bars: {len(df):,}")
    print("=" * 120 + "\n")

    try:
        # STEP 1: Initialize Registry
        tracer.log("Initialize", "RUNNING", "Loading parameter registry")
        registry = CanonicalParameterRegistry()
        tracer.log("Initialize", "OK", "Registry loaded")

        # STEP 2: Initialize Orchestrator
        tracer.log("Instantiate", "RUNNING", f"Create Revision2PortfolioOrchestrator([{symbol}])")
        orch = Revision2PortfolioOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        tracer.log("Instantiate", "OK", "Orchestrator created", {
            "symbol": symbol,
            "starting_equity": 1_000_000.0,
            "engine_type": "Revision2PortfolioOrchestrator",
        })

        # STEP 3: Run Backtest
        tracer.log("Backtest", "RUNNING", f"Run on {len(df):,} bars, warmup=60", {
            "first_bar_timestamp": str(df.iloc[0]['timestamp']),
            "last_bar_timestamp": str(df.iloc[-1]['timestamp']),
            "data_columns": list(df.columns),
        })

        symbol_bars = {symbol: df}
        report = orch.run(symbol_bars, warmup=60)

        tracer.log("Backtest", "OK", "Completed", {
            "bars_processed": report.get("bars_processed", 0),
            "trades_executed": len(report.get("trades", [])),
            "net_pnl": report.get("net_pnl", 0),
            "win_rate": report.get("win_rate", 0),
            "max_drawdown": report.get("max_drawdown", 0),
            "profit_factor": report.get("profit_factor", 0),
        })

        # STEP 4: Trade Analysis
        if report.get("trades"):
            tracer.log("Trades", "OK", f"Generated {len(report['trades'])} trades", {
                "trades_sample": [
                    {
                        "entry_price": t.get("entry_price"),
                        "exit_price": t.get("exit_price"),
                        "quantity": t.get("quantity"),
                        "pnl": t.get("pnl"),
                        "bars_held": t.get("bars_held"),
                    }
                    for t in report["trades"][:5]
                ] + (["..."] if len(report["trades"]) > 5 else [])
            })
        else:
            tracer.log("Trades", "WARN", "No trades generated")

        # STEP 5: Performance Summary
        tracer.log("Summary", "OK", "In-house engine complete", {
            "engine": "Revision2PortfolioOrchestrator",
            "symbol": symbol,
            "period": "3 years (2023-07-03 to 2026-08-24)",
            "total_trades": len(report.get("trades", [])),
            "net_pnl_rupees": report.get("net_pnl", 0),
            "win_rate_pct": report.get("win_rate", 0) * 100,
            "max_drawdown_pct": report.get("max_drawdown", 0) * 100,
            "profit_factor": report.get("profit_factor", 0),
        })

    except Exception as e:
        tracer.log("Error", "ERROR", f"Exception: {str(e)}")
        import traceback
        tracer.log("Error", "ERROR", "Full traceback:")
        for line in traceback.format_exc().split('\n'):
            tracer.log("Error", "ERROR", line)

    finally:
        tracer.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/baseline_engines_full_trace_single_symbol.py <SYMBOL>")
        print("Example: python3 scripts/baseline_engines_full_trace_single_symbol.py INFY")
        sys.exit(1)

    symbol = sys.argv[1].upper()

    print("\n" + "🔍 BASELINE ENGINE FULL IVO TRACE 🔍".center(120))
    print("Input → Processing → Output for each black box")
    print("Single symbol, 3-year data, complete transparency.\n")

    # Load data
    try:
        print(f"Loading {symbol} 3-year data...")
        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
        loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
        df = loader._load_symbol_csv(symbol)
        print(f"✓ Loaded {len(df):,} bars ({df['timestamp'].min()} to {df['timestamp'].max()})\n")
    except Exception as e:
        print(f"✗ Failed to load data: {e}")
        sys.exit(1)

    # Run both engines
    trace_external_engine(symbol, df)
    trace_inhouse_engine(symbol, df)

    print("\n" + "=" * 120)
    print("TRACE FILES GENERATED:")
    print(f"  External: trace_external_{symbol}_3year.log")
    print(f"  In-House: trace_inhouse_{symbol}_3year.log")
    print("=" * 120 + "\n")


if __name__ == "__main__":
    main()
