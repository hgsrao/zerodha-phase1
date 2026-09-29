#!/usr/bin/env python3
"""Read-only controller inventory and isolated numerical probes; no engine mutations."""
import argparse
import ast
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def audit():
    from simple_pid import PID
    from revision5.ccpp_unified_plant import DynamicBayLoadDispatcher
    from revision5.topology import BAY_IDS
    records = []
    for directory in ('revision2','revision2_external','revision5'):
        for path in sorted((ROOT/directory).glob('*.py')):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('PID','BoundedPID'):
                    records.append({'path':str(path.relative_to(ROOT)), 'line':node.lineno,
                                    'constructor':node.func.id,
                                    'output_limits':next((ast.unparse(k.value) for k in node.keywords if k.arg=='output_limits'),None)})
    pid = PID(1.,.2,.4,setpoint=1.,sample_time=None,output_limits=(-.5,.5))
    first = pid(0.,dt=1)
    first_derivative = pid.components[2]
    for _ in range(100):
        pid(0.,dt=1)
    integral_at_rail = pid.components[1]
    pid.setpoint = 2.
    pid(0.,dt=1)
    setpoint_derivative = pid.components[2]
    dispatch = DynamicBayLoadDispatcher()
    for _ in range(100):
        dispatch.register_trade(BAY_IDS[0],10.)
    return {'simple_pid_source':inspect.getfile(PID),
            'simple_pid_default_signature':str(inspect.signature(PID)),
            'simple_pid_probes':{'first_output':first,'first_derivative':first_derivative,
                                 'weighted_integral_after_100_errors':integral_at_rail,
                                 'derivative_after_setpoint_only_change':setpoint_derivative},
            'pid_constructor_inventory':records,
            'manual_pid_sources':['revision5/governor.py','revision5/ccpp_protection_cubicles.py','revision2/boxes.py'],
            'merit_dispatch_probe':{'weights':dispatch.weights,'configured_ceiling':dispatch.max_ceiling,
                                    'raw_merit_weight_exceeds_ceiling':max(dispatch.weights.values())>dispatch.max_ceiling},
            'scope':'Read-only static inspection and freshly created isolated objects; not live process telemetry.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    payload = json.dumps(audit(),indent=2)
    if args.output:
        args.output.write_text(payload+'\n')
    print(payload)

if __name__ == '__main__':
    main()
