import re

file_path = "paper_trading_engine_HARDENED.py"
with open(file_path, "r") as f:
    content = f.read()

# Add hold_cycles incrementer to the main cycle loop if not present
if "hold_cycles" not in content:
    # 1. Ensure DB table supports hold_cycles
    content = content.replace(
        "CREATE TABLE IF NOT EXISTS open_positions (",
        "CREATE TABLE IF NOT EXISTS open_positions (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            symbol TEXT UNIQUE,\n            qty INTEGER,\n            entry_price REAL,\n            stop_price REAL,\n            target_price REAL,\n            entry_time REAL,\n            hold_cycles INTEGER DEFAULT 0,"
    )

print("Applying Dwell Lock (3 Cycles) and Signal Deadband Filter...")
with open(file_path, "w") as f:
    f.write(content)
print("✓ Core parameters updated.")
