#!/usr/bin/env python3
"""Compare deterministic unpolicied synthetic replays against a Git commit, all five bays.
Does not switch any checkout. A temporary git archive is removed on exit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]


def worker(root):
    sys.path.insert(0, str(root))
    import pytest
    from dataclasses import replace
    from revision5.topology import BAY_IDS, FLEET_TOPOLOGY
    result = {}
    for bay in BAY_IDS:
        symbol = FLEET_TOPOLOGY[bay][0]
        path = root/'tests/test_r5_governor_trace.py'
        source = path.read_text().replace('GTG2_TECH_TELECOM',bay).replace('"INFY"',repr(symbol))
        fixture = types.ModuleType('parity_fixture')
        fixture.__file__ = str(path)
        exec(compile(source,str(path),'exec'),fixture.__dict__)
        original_signal = fixture.signal
        fixture.signal = lambda: replace(original_signal(),symbol=symbol)
        with pytest.MonkeyPatch.context() as patch:
            tracer, report = fixture._replay(patch, True)
        assert all(g.position_policy is None for g in tracer.orch._bay_governors.values())
        # Compare full deterministic result plus every captured row, excluding wall time.
        payload = {'report':report, 'rows':dict(tracer.rows)}
        digest = hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()
        result[bay] = {'sha256':digest, 'trades':len(report['trades']),
                       'position_rows':len(tracer.rows['inner'])}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', default='fa9edd9')
    p.add_argument('--output', type=Path)
    p.add_argument('--worker-root', type=Path, help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.worker_root:
        print(json.dumps(worker(a.worker_root.resolve()),sort_keys=True))
        return
    (ROOT/'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='r5-parity-',dir=ROOT/'work') as temporary:
        temporary = Path(temporary)
        archive = temporary/'reference.tar'
        subprocess.run(['git','archive','--format=tar',f'--output={archive}',a.reference],cwd=ROOT,check=True)
        reference = temporary/'reference'
        reference.mkdir()
        subprocess.run(['tar','-xf',str(archive),'-C',str(reference)],check=True)
        results = []
        for root in (reference,ROOT):
            run = subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker-root',str(root)],
                                 cwd=root,text=True,capture_output=True,check=True,
                                 env={**os.environ,'PYTHONHASHSEED':'0'})
            results.append(json.loads(run.stdout.splitlines()[-1]))
        before,after = results
        output = {'reference':subprocess.check_output(['git','rev-parse',a.reference],cwd=ROOT,text=True).strip(),
                  'fixture':'90 synthetic bars, one canonical symbol per bay; full report and trace rows',
                  'before':before,'after':after,'match':before==after}
        if a.output:
            a.output.write_text(json.dumps(output,indent=2)+'\n')
        print(json.dumps(output,indent=2))
        if not output['match']:
            raise SystemExit('DEFAULT_PARITY_FAILED')

if __name__ == '__main__':
    main()
