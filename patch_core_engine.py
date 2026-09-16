import re

with open("alpha_engine_core.py", "r") as f:
    code = f.read()

gap_logic = '''
    def compute_daily_gaps(self) -> set:
        gapped_days = set()
        for sym, df in self.symbol_frames.items():
            daily = df.groupby('date_only').agg(
                first_open=('open', 'first'),
                last_close=('close', 'last')
            ).sort_index()
            daily['prev_close'] = daily['last_close'].shift(1)
            daily['gap_pct'] = (daily['first_open'] - daily['prev_close']) / daily['prev_close'] * 100.0
            for d in daily[daily['gap_pct'] <= -1.75].index:
                gapped_days.add((sym, d))
        return gapped_days
'''

if "def compute_daily_gaps" not in code:
    code = code.replace("class ExecutionEngine:", "class ExecutionEngine:" + gap_logic)
    code = code.replace("all_timestamps = sorted(", "gapped_days = self.compute_daily_gaps()\n        all_timestamps = sorted(")
    code = code.replace("if sym in self.active_positions or t not in df.index:", "if sym in self.active_positions or t not in df.index:\n                    continue\n                current_date = df.loc[t, 'date_only']\n                if (sym, current_date) in gapped_days:")
    with open("alpha_engine_core.py", "w") as f:
        f.write(code)
    print("[SUCCESS] Patched alpha_engine_core.py with permanent gap-down circuit breaker.")
else:
    print("[INFO] alpha_engine_core.py already contains gap-down logic.")
