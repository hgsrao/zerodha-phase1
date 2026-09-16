with open("unified_dual_engine_orchestrator.py", "r") as f:
    code = f.read()

# Replace brittle list comprehensions with robust datetime locator
old_snippet = """            t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'dt']][0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)"""

new_snippet = """            t_candidates = [c for c in raw.columns if any(k in str(c).lower() for k in ['date', 'time', 'timestamp', 'dt'])]
            t_col = t_candidates[0] if t_candidates else raw.columns[0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)"""

if old_snippet in code:
    code = code.replace(old_snippet, new_snippet)
    with open("unified_dual_engine_orchestrator.py", "w") as f:
        f.write(code)
    print("✓ Successfully patched unified_dual_engine_orchestrator.py")
else:
    # Alternative direct rewrite of loader sections
    import re
    pattern = r"t_col = \[c for c in raw\.columns if c\.lower\(\) in \['date', 'datetime', 'time', 'dt'\]\]\[0\]"
    replacement = "t_candidates = [c for c in raw.columns if any(k in str(c).lower() for k in ['date', 'time', 'timestamp', 'dt'])]; t_col = t_candidates[0] if t_candidates else raw.columns[0]"
    code_mod = re.sub(pattern, replacement, code)
    with open("unified_dual_engine_orchestrator.py", "w") as f:
        f.write(code_mod)
    print("✓ Successfully applied regex patch to unified_dual_engine_orchestrator.py")
