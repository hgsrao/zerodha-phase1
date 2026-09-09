#!/usr/bin/env python3
"""
PA Signal Debugging
===================

Shows EXACTLY which PA signals are generated and why ID rejects them.
Identifies the blocking gate for each signal.
"""

import os
from pathlib import Path
import pandas as pd
from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
from revision2.contracts import MarketSnapshot

MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60

loader = ManifestLoader(MANIFEST)
entry = loader.manifest.get_file(SYMBOL)
data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
path = data_dir / entry.filename
bars = pd.read_csv(path)

registry = CanonicalParameterRegistry()
orchestrator = Revision2PortfolioOrchestrator([SYMBOL], registry=registry, starting_equity=100_000.0)

print("="*120)
print("PA SIGNAL DEBUG: Why are signals being rejected?")
print("="*120)
print()

# Calibrate PA
print("[1] Calibrating PA on warmup bars...")
orchestrator.pa.calibrate(SYMBOL, bars.iloc[:WARMUP])
print(f"    ✓ PA calibrated on {WARMUP} bars")
print()

# Test signals on next 20 bars
print("[2] Testing PA signal generation on bars 60-80...")
print()

print(f"{'Bar':<5} {'Dir':<4} {'Conf':<7} {'Quality':<8} {'Volatility':<12} {'Rejection Reason':<60}")
print("-" * 120)

rejection_reasons = {}
passes = 0
fails = 0

for bar_idx in range(WARMUP, min(WARMUP + 20, len(bars))):
    bar = bars.iloc[bar_idx]

    # Build MarketSnapshot
    snapshot = MarketSnapshot(
        symbol=SYMBOL,
        timestamp=str(bar['timestamp']),
        bars=bars.iloc[0:bar_idx+1],  # All bars up to and including this one
        next_bar_open=None
    )

    # Get PA signal
    signal, _ = orchestrator.pa.evaluate(snapshot, orchestrator.config)

    # Get ID decision
    id_decision, _ = orchestrator.id_box.evaluate(signal, orchestrator.config)

    # Determine blocking reason
    if signal.direction == 0:
        reason = "PA: No direction"
        fails += 1
    elif signal.quality_band == "red":
        reason = "PA: Quality=RED"
        fails += 1
    elif signal.confidence < orchestrator.config.require('entry_confidence_threshold'):
        reason = f"ID: Confidence {signal.confidence:.3f} < threshold {orchestrator.config.require('entry_confidence_threshold')}"
        fails += 1
    elif (signal.volatility * 2.0) > orchestrator.config.require('slippage_guard_threshold'):
        reason = f"ID: Est.Slippage {signal.volatility * 2.0:.3f} > limit {orchestrator.config.require('slippage_guard_threshold')}"
        fails += 1
    elif id_decision.approved:
        reason = "✅ APPROVED"
        passes += 1
    else:
        reason = f"ID: {id_decision.rejection_reason}"
        fails += 1

    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    print(f"{bar_idx:<5} {signal.direction:<4} {signal.confidence:<7.3f} {signal.quality_band:<8} {signal.volatility:<12.4f} {reason:<60}")

print()
print("="*120)
print("REJECTION SUMMARY")
print("="*120)
print(f"Passed ID gates: {passes}")
print(f"Failed ID gates: {fails}")
print()

print("Blocking Reasons (ranked by frequency):")
for reason, count in sorted(rejection_reasons.items(), key=lambda x: -x[1]):
    print(f"  [{count:2d}x] {reason}")

print()
print("="*120)
print("CURRENT PARAMETER SETTINGS")
print("="*120)
print(f"entry_confidence_threshold:     {orchestrator.config.require('entry_confidence_threshold')}")
print(f"exit_confidence_threshold:      {orchestrator.config.require('exit_confidence_threshold')}")
print(f"slippage_guard_threshold:       {orchestrator.config.require('slippage_guard_threshold')}")
print(f"min_risk_reward_ratio:          {orchestrator.config.require('min_risk_reward_ratio')}")
print()

print("RECOMMENDED FIXES (if confidence too low):")
print("  Option 1: Lower entry_confidence_threshold (e.g., 0.5 → 0.2)")
print("  Option 2: Tune PA to generate higher-confidence signals")
print("  Option 3: Check if PA parameters need adjustment")
print()
