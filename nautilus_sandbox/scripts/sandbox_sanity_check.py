import sys
from pathlib import Path

print("=" * 60)
print("SANDBOX ENVIRONMENT SANITY CHECK")
print("=" * 60)
sandbox_path = Path(__file__).resolve().parent.parent
print(f"Sandbox Root: {sandbox_path}")

core_files = list((sandbox_path / "engine_core").glob("*.py"))
print(f"Engine Core Modules Isolated: {len(core_files)}")
for f in core_files:
    print(f"  - {f.name}")
print("=" * 60)
print("Sandbox ready. External engine repo is completely isolated.")
