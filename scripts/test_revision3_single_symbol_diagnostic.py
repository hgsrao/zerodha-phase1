#!/usr/bin/env python3
"""
Single-Symbol Diagnostic Runner for Revision 3 Engines.
Traces every step: data loading, initialization, execution, outputs.
Helps identify where/why Revision 3 hangs or fails.

Run: python3 scripts/test_revision3_single_symbol_diagnostic.py INFY
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
import traceback
from datetime import datetime

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest


def log_step(step_num, stage, status, details=""):
    """Log each step with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    status_char = "✓" if status == "OK" else "✗" if status == "ERROR" else "⊙"
    print(f"[{timestamp}] [{step_num:02d}] {status_char} {stage:40s} | {details}")


def test_revision3_external(symbol: str):
    """Test Revision 3 External (HMM + Relay) with single symbol."""
    print("\n" + "=" * 100)
    print(f"REVISION 3 EXTERNAL ENGINE DIAGNOSTIC (HMM + ANSI Relay)")
    print(f"Symbol: {symbol} | Full 3-year data")
    print("=" * 100 + "\n")

    step = 1

    # ========================================================================
    # STEP 1: Load Data
    # ========================================================================
    try:
        log_step(step, "Load data manifest", "RUNNING")
        step += 1

        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
        log_step(step, "Manifest loaded", "OK", f"{len(manifest.files)} symbols available")
        step += 1

        loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
        log_step(step, f"Load {symbol} CSV", "RUNNING")
        step += 1

        df = loader._load_symbol_csv(symbol)
        log_step(step, f"Loaded {symbol}", "OK", f"{len(df)} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")
        step += 1

        print(f"\n  Data shape: {df.shape}")
        print(f"  Columns: {list(df.columns)}")
        print(f"  First bar: {df.iloc[0].to_dict()}")
        print(f"  Last bar: {df.iloc[-1].to_dict()}")

    except Exception as e:
        log_step(step, "Data loading", "ERROR", str(e))
        traceback.print_exc()
        return

    # ========================================================================
    # STEP 2: Load Registry
    # ========================================================================
    try:
        log_step(step, "Load parameter registry", "RUNNING")
        step += 1

        registry = CanonicalParameterRegistry()
        log_step(step, "Registry loaded", "OK", f"Registry ready")
        step += 1

    except Exception as e:
        log_step(step, "Registry loading", "ERROR", str(e))
        traceback.print_exc()
        return

    # ========================================================================
    # STEP 3: Try to instantiate Revision 3 External Orchestrator
    # ========================================================================
    try:
        log_step(step, "Import Revision3ExternalEngineOrchestrator", "RUNNING")
        step += 1

        from revision3_external.orchestrator import Revision3ExternalEngineOrchestrator
        log_step(step, "Import successful", "OK")
        step += 1

        log_step(step, "Instantiate Revision3External([symbol])", "RUNNING", f"symbol={symbol}")
        step += 1

        # This is where it likely fails - trying to wrap external engine with protection relay
        orch = Revision3ExternalEngineOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        log_step(step, "Orchestrator instantiated", "OK")
        step += 1

    except Exception as e:
        log_step(step, "Orchestrator instantiation", "ERROR", str(e))
        print(f"\nFull traceback:")
        traceback.print_exc()
        print(f"\nThis is likely where Revision 3 fails: protection relay wrapper issue.")
        return

    # ========================================================================
    # STEP 4: Try to run orchestrator on single symbol
    # ========================================================================
    try:
        log_step(step, f"Run orchestrator ({symbol}, 3-year data, warmup=60)", "RUNNING")
        step += 1

        symbol_bars = {symbol: df}

        report = orch.run(symbol_bars, warmup=60)

        log_step(step, "Orchestrator.run() completed", "OK", f"report keys: {list(report.keys())}")
        step += 1

        # ========================================================================
        # STEP 5: Analyze Results
        # ========================================================================
        print(f"\n  Report Summary:")
        print(f"    Trades executed: {len(report.get('trades', []))}")
        print(f"    Net P&L: ₹{report.get('net_pnl', 0):,.2f}")
        print(f"    Win rate: {report.get('win_rate', 0):.2%}")
        print(f"    Max drawdown: {report.get('max_drawdown', 0):.2%}")
        print(f"    Bars processed: {report.get('bars_processed', 0)}")

        if len(report.get('trades', [])) > 0:
            print(f"\n  First 3 trades:")
            for i, trade in enumerate(report['trades'][:3]):
                print(f"    Trade {i+1}: Entry={trade.get('entry_price')}, Exit={trade.get('exit_price')}, P&L={trade.get('pnl')}")

        log_step(step, "Results analyzed", "OK")
        step += 1

    except Exception as e:
        log_step(step, "Orchestrator execution", "ERROR", str(e))
        print(f"\nFull traceback:")
        traceback.print_exc()
        print(f"\nThis is where execution fails: likely missing methods or data format mismatches.")
        return

    # ========================================================================
    # STEP 6: Protection Status
    # ========================================================================
    try:
        log_step(step, "Get protection relay status", "RUNNING")
        step += 1

        if hasattr(orch, 'get_protection_status'):
            status = orch.get_protection_status()
            log_step(step, "Protection status retrieved", "OK", f"tripped={status.get('is_tripped')}")
            step += 1
            print(f"\n  Protection Status:")
            print(f"    {json.dumps(status, indent=6)}")
        else:
            log_step(step, "get_protection_status() not found", "WARN", "method doesn't exist")
            step += 1

    except Exception as e:
        log_step(step, "Protection status retrieval", "ERROR", str(e))
        step += 1

    log_step(step, "DIAGNOSTIC COMPLETE", "OK", f"Total steps: {step}")
    step += 1


def test_revision3_inhouse(symbol: str):
    """Test Revision 3 In-House (Vanilla + Relay) with single symbol."""
    print("\n" + "=" * 100)
    print(f"REVISION 3 IN-HOUSE ENGINE DIAGNOSTIC (Vanilla + ANSI Relay)")
    print(f"Symbol: {symbol} | Full 3-year data")
    print("=" * 100 + "\n")

    step = 1

    # ========================================================================
    # STEP 1-3: Same as External (load data, registry)
    # ========================================================================
    try:
        log_step(step, "Load data manifest", "RUNNING")
        step += 1

        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
        log_step(step, "Manifest loaded", "OK", f"{len(manifest.files)} symbols available")
        step += 1

        loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
        log_step(step, f"Load {symbol} CSV", "RUNNING")
        step += 1

        df = loader._load_symbol_csv(symbol)
        log_step(step, f"Loaded {symbol}", "OK", f"{len(df)} bars")
        step += 1

        log_step(step, "Load parameter registry", "RUNNING")
        step += 1

        registry = CanonicalParameterRegistry()
        log_step(step, "Registry loaded", "OK", f"Registry ready")
        step += 1

    except Exception as e:
        log_step(step, "Data/Registry loading", "ERROR", str(e))
        traceback.print_exc()
        return

    # ========================================================================
    # STEP 4: Instantiate Revision 3 In-House
    # ========================================================================
    try:
        log_step(step, "Import Revision3PortfolioOrchestrator", "RUNNING")
        step += 1

        from revision3.portfolio_orchestrator import Revision3PortfolioOrchestrator
        log_step(step, "Import successful", "OK")
        step += 1

        log_step(step, "Instantiate Revision3InHouse([symbol])", "RUNNING", f"symbol={symbol}")
        step += 1

        orch = Revision3PortfolioOrchestrator(
            [symbol],
            registry,
            starting_equity=1_000_000.0
        )
        log_step(step, "Orchestrator instantiated", "OK")
        step += 1

    except Exception as e:
        log_step(step, "Orchestrator instantiation", "ERROR", str(e))
        print(f"\nFull traceback:")
        traceback.print_exc()
        print(f"\nThis is likely where Revision 3 fails: protection relay wrapper issue.")
        return

    # ========================================================================
    # STEP 5: Run orchestrator
    # ========================================================================
    try:
        log_step(step, f"Run orchestrator ({symbol}, 3-year data, warmup=60)", "RUNNING")
        step += 1

        symbol_bars = {symbol: df}

        report = orch.run(symbol_bars, warmup=60)

        log_step(step, "Orchestrator.run() completed", "OK", f"report keys: {list(report.keys())}")
        step += 1

        print(f"\n  Report Summary:")
        print(f"    Trades executed: {len(report.get('trades', []))}")
        print(f"    Net P&L: ₹{report.get('net_pnl', 0):,.2f}")
        print(f"    Win rate: {report.get('win_rate', 0):.2%}")
        print(f"    Bars processed: {report.get('bars_processed', 0)}")

        if len(report.get('trades', [])) > 0:
            print(f"\n  First trade: {report['trades'][0]}")

        log_step(step, "Results analyzed", "OK")
        step += 1

    except Exception as e:
        log_step(step, "Orchestrator execution", "ERROR", str(e))
        print(f"\nFull traceback:")
        traceback.print_exc()
        return

    log_step(step, "DIAGNOSTIC COMPLETE", "OK", f"Total steps: {step}")
    step += 1


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/test_revision3_single_symbol_diagnostic.py <SYMBOL>")
        print("Example: python3 scripts/test_revision3_single_symbol_diagnostic.py INFY")
        sys.exit(1)

    symbol = sys.argv[1].upper()

    print("\n" + "🔧 REVISION 3 SINGLE-SYMBOL DIAGNOSTIC SUITE 🔧".center(100))
    print("This script traces every step of Revision 3 execution to identify failures.\n")

    # Test both engines
    test_revision3_external(symbol)
    test_revision3_inhouse(symbol)

    print("\n" + "=" * 100)
    print("DIAGNOSTIC SUITE COMPLETE")
    print("=" * 100)
    print("\nIf either engine failed:")
    print("  1. Check the error message and traceback above")
    print("  2. Look for missing methods/attributes on orchestrator classes")
    print("  3. Verify Revision 3 wrappers correctly delegate to base engines")
    print("  4. Check if protection relay is trying to call non-existent SafetyPanel")


if __name__ == "__main__":
    main()
