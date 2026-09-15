from pathlib import Path
src = Path("test_harness_v34_b13_convergence.py")
dst = Path("test_harness_v34_b14_convergence.py")
content = src.read_text(encoding="utf-8")

# Replace B1.3 hash with B1.4 hash
old_hash = 'EXPECTED_B12_SHA256 = "9A6DA05D3131FF6D66E727C758E14FA5453799B52618352498950DF4D06217DC"'
new_hash = 'EXPECTED_B12_SHA256 = "F23F043BBAA427D2DCC7C386E9656CCCD089000CAC580CEE543F3E08855BB671"'

if old_hash not in content:
    raise RuntimeError("Fail-closed: Expected B1.3 hash constant anchor not found.")

content = content.replace(old_hash, new_hash, 1)
content = content.replace("TestB13RestartConvergence", "TestB14RestartConvergence")
content = content.replace("B1.3 artifact identity mismatch", "B1.4 artifact identity mismatch")

dst.write_text(content, encoding="utf-8")
print("[SUCCESS] Generated test_harness_v34_b14_convergence.py")
