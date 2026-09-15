#!/usr/bin/env python3
"""Auto-run calibration without prompts"""
import subprocess
import sys

print("\n" + "="*80)
print("STARTING 48-SYMBOL CALIBRATION (500 iterations, 6-12 hours)")
print("="*80 + "\n")

# Run with auto-confirmed input
process = subprocess.Popen(
    [sys.executable, 'MASTER_DEPLOYMENT_SCRIPT_20260829.py', '--mode', 'calibration'],
    stdin=subprocess.PIPE,
    text=True
)

# Send 'yes' to the prompt
process.communicate(input='yes\n')
process.wait()

print("\n" + "="*80)
print("Calibration complete. Check output file for results.")
print("="*80 + "\n")
