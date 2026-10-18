"""Run with project venv: R5_REPO=/path/to/repo python -m pytest -q test_r5_audit.py."""
import ast
import importlib.util
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(os.environ.get('R5_REPO', '/home/srinivas/projects/zerodha-r5-trace'))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
from r5_audit_diagnostics import install


def test_single_gamma_patch():
    # Compile only the reference class, avoiding unrelated engine dependencies.
    source = (REPO/'revision2_external/closed_loop_control.py').read_text()
    patched = source.replace('self.progress(bars_held) ** self.curve_gamma', 'self.progress(bars_held)')
    node = next(n for n in ast.parse(patched).body if isinstance(n, ast.ClassDef) and n.name == 'TradeReferencePath')
    ns = dict(Dict=dict, Any=object, dataclass=dataclass, math=math, _DEFAULT_RESPONSE_TIME_BARS=14,
              _clip=lambda v, lo, hi: max(lo, min(hi, v)), asdict=lambda x: vars(x))
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<patched-reference>', 'exec'), ns)
    for gamma in (0.5, 1.0, 2.0):
        path = ns['TradeReferencePath']('X', 'BUY', 100, 2, 1.5, 20, gamma, 7)
        for held in (0, 1, 10, 20, 25):
            base = (1-math.exp(-min(held,20)/7))/(1-math.exp(-20/7))
            expected = base ** gamma
            assert path.progress(held) == pytest.approx(expected)
            assert path.expected_r(held) == pytest.approx(1.5*expected)
            assert path.lower_bound_r(held) == pytest.approx(-1+2.5*expected)


def test_instrumentation_passthrough(monkeypatch):
    spec = importlib.util.spec_from_file_location('existing_trace_test', REPO/'tests/test_r5_governor_trace.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    install(module.trace)
    tracer, traced = module._replay(monkeypatch, True)
    _, plain = module._replay(monkeypatch, False)
    assert module._fingerprint(traced) == module._fingerprint(plain)
    assert traced['governor_authority'] == plain['governor_authority']
    assert tracer.rows['pid_gate']
    rows = [r for r in tracer.rows['fsrn_components'] if r['phase'] == 'position']
    assert rows and all(r['trade_id'] for r in rows)
    from revision5.governor_authority import causal_percentile_rank
    for row in rows:
        for key, value in [('pa_exit', row['pa_exit_confidence']), ('studies', row['studies_confidence'])]:
            assert row['ranks'][key] == causal_percentile_rank(row['prior'][key], value, row['window'])
        assert row['fsrn'] == (0 if row['pa_opposed'] or row['studies_opposed'] else row['unmasked_fsrn'])
    summary = module.trace._summarize(tracer, traced, tracer.bay_ids, {})
    assert summary['pid_gate_audit']['unmatched_veto_events'] == 0


def test_pid_veto_reachable_and_gain_sensitive():
    from revision5.governor import BayTurbineClosedLoopGovernor, BAY_GOVERNOR_SPECS
    from revision5.topology import GTG1_HEAVY_INDUSTRY
    def run(kp):
        g = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[GTG1_HEAVY_INDUSTRY])
        g.runtime_kp = kp
        outputs = []
        for i, (measured, reference) in enumerate([(1.,0.)]*4 + [(-.1,1.4),(-.1,.4)]):
            outputs.append(g.evaluate_position_control(
                measured_r=measured, reference_r=reference, max_favorable_r=1.,
                elapsed_bars=i, min_hold_bars=5, max_hold_bars=30,
                hard_stop_r=-1., trade_target_r=10., position_id='synthetic',
                path_noise_r=.1, path_error_sigma=2.))
        return outputs[-1]
    veto = run(1.2)
    assert veto['error'] >= .2*math.sqrt(5)
    assert veto['control_u'] < .30
    assert veto['reason'] == 'GOVERNOR_TRACKING'
    assert run(2.)['reason'] == 'GOVERNOR_PATH_ERROR'
