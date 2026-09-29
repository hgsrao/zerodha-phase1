#!/usr/bin/env python3
"""Count PID quadrants; optional fixed-trajectory gain sensitivity (not a policy replay)."""
import argparse
import json
from collections import Counter
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('trace_dir', type=Path)
p.add_argument('--gains', nargs=3, type=float, metavar=('KP','KI','KD'))
a = p.parse_args()
rows = [json.loads(s) for s in (a.trace_dir/'pid_gate.jsonl').read_text().splitlines()]
eligible = [r for r in rows if r['eligible']]
result = dict(all_bars=len(rows), eligible_bars=len(eligible),
              all_quadrants=dict(Counter(f"A={int(r['A'])},B={int(r['B'])}" for r in rows)),
              eligible_quadrants=dict(Counter(f"A={int(r['A'])},B={int(r['B'])}" for r in eligible)),
              pid_vetoes=sum(r['pid_veto'] for r in eligible))
if a.gains:
    kp,ki,kd = a.gains
    # Same recorded error history and integral clamp; no alternate entries/exits simulated.
    changed = 0
    for r in eligible:
        u = kp*r['error'] + ki*r['integral'] + kd*r['derivative']
        # Recover target contribution from logged baseline threshold and inputs is ambiguous;
        # diagnostic runner stores runtime target explicitly in newer rows.
        target = r['gains']['target_r']
        threshold = max(abs(target), abs(ki*r['gains']['clamp']))
        changed += (r['A'] and u >= threshold) != r['pid_path_exit']
    result['fixed_trajectory_changed_path_decisions'] = changed
    result['limitation'] = 'Conditional on recorded trajectory; not changed-policy performance or future hold duration.'
print(json.dumps(result, indent=2))
