#!/usr/bin/env python3
"""
Extract Best 42 Calibrated Parameters (Even If Gates Not Passed)
==================================================================

Re-run calibration and show the best candidate's parameters
ranked by guidance score, regardless of acceptance gate status.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.calibration_supervisor import (
    CalibrationSupervisor, CalibrationRunConfig, AcceptanceGates, trading_search_space
)

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60
BARS_FOR_ONE_DAY = 390

def load_symbol_1day():
    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename
    bars = pd.read_csv(path)
    return {SYMBOL: bars.iloc[:WARMUP + BARS_FOR_ONE_DAY].copy()}

print()
print("="*120)
print("EXTRACT BEST 42 CALIBRATED PARAMETERS (By Guidance Score)")
print("="*120)
print()

print("[LOAD] Loading SUNPHARMA...")
symbol_bars = load_symbol_1day()

print("[CALIBRATE] Running 69-parameter optimization...")
registry = CanonicalParameterRegistry()
search_space = trading_search_space(registry)

print(f"  Calibratable space: {len(search_space.names)} parameters")
print()

supervisor = CalibrationSupervisor(
    registry=registry,
    symbols=[SYMBOL],
    symbol_bars=symbol_bars,
    run_config=CalibrationRunConfig(phase1_trials=12, phase2_generations=3, phase3_iterations=6),
    gates=AcceptanceGates(min_trades=1, min_profit_factor=0.80, max_drawdown_fraction=0.30),
    warmup=WARMUP,
    starting_equity=100_000.0,
)

result = supervisor.run()

print(f"Candidates evaluated: {len(result.candidates)}")
print()

# Get best by guidance score
if not result.candidates:
    print("❌ No candidates")
    sys.exit(1)

best = max(result.candidates, key=lambda c: c.guidance_score)

print("="*120)
print(f"BEST CANDIDATE (Phase: {best.phase}, Guidance Score: {best.guidance_score:.4f})")
print("="*120)
print()

print(f"Metrics:")
print(f"  Trades: {best.metrics.get('completed_trades', 0)}")
print(f"  P&L: ₹{best.metrics.get('net_pnl', 0):.2f}")
print(f"  Sharpe: {best.metrics.get('sharpe', 0):.2f}")
print(f"  Max DD: {best.metrics.get('max_drawdown_fraction', 0):.2%}")
print()

print("="*120)
print(f"ALL {len(best.params)} CALIBRATED PARAMETERS")
print("="*120)
print()

for i, (param, value) in enumerate(sorted(best.params.items()), 1):
    print(f"{i:2d}. {param:50s} = {value}")

print()

# Save the parameters
output_path = Path("diagnostic_output/calibrated_42params_sunpharma_1day.json")
output_path.parent.mkdir(exist_ok=True)

report = {
    "timestamp": datetime.now().isoformat(),
    "symbol": SYMBOL,
    "phase": best.phase,
    "guidance_score": best.guidance_score,
    "metrics": best.metrics,
    "parameters": best.params,
    "parameter_count": len(best.params),
}

output_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
print(f"✅ Saved: {output_path}")
print()

