with open("unified_dual_engine_orchestrator.py", "r") as f:
    code = f.read()

# Fix Engine A entry loop: check frames_15m containment
old_a = "for sym in ENGINE_A_UNIVERSE:"
new_a = "for sym in frames_15m.keys():"

# Fix Engine B entry loop: check frames_daily containment
old_b = "for sym in ENGINE_B_UNIVERSE:"
new_b = "for sym in frames_daily.keys():"

code = code.replace(old_a, new_a).replace(old_b, new_b)

with open("unified_dual_engine_orchestrator.py", "w") as f:
    f.write(code)

print("✓ Fixed key membership check in unified_dual_engine_orchestrator.py")
