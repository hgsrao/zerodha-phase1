with open("alpha_engine_core.py", "r") as f:
    code = f.read()

# Replace baseline parameters with calibrated cluster
replacements = [
    ("base_r_target: float = 1.30", "base_r_target: float = 2.25"),
    ("base_z_target: float = 0.40", "base_z_target: float = 0.60"),
    ("stop_atr_multiplier: float = 0.80", "stop_atr_multiplier: float = 1.00"),
    ("decay_start_time: str = \"12:30:00\"", "decay_start_time: str = \"12:30:00\"")
]

for old, new in replacements:
    code = code.replace(old, new)

with open("alpha_engine_core.py", "w") as f:
    f.write(code)

print("✓ alpha_engine_core.py successfully updated with Calibrated Parameter Profile (R=2.25, ATR_Stop=1.0x, Z=0.60)")
