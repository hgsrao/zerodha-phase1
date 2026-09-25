"""Control-plane regressions: never run a market replay or contact a laptop."""
import copy
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.util.spec_from_file_location('stage_a', ROOT / 'scripts/run_r5_step5_stage_a_executor.py')
e = importlib.util.module_from_spec(loader)
loader.loader.exec_module(e)


def control(job, action='probe'):
    return json.loads(subprocess.check_output(
        [sys.executable, '-c', e.REMOTE_CONTROL, json.dumps(job), action], text=True))


def test_detached_launch_lost_ack_and_atomic_result(tmp_path):
    pending = tmp_path / 'pending'
    count = tmp_path / 'count'
    job = dict(job_path=str(tmp_path / 'job'), result_path=str(tmp_path / 'result'),
               pending_path=str(pending), log_path=str(tmp_path / 'log'),
               argv=[sys.executable, '-c',
                     'import pathlib,time; '
                     f'pathlib.Path({str(count)!r}).write_text("once"); '
                     f'pathlib.Path({str(pending)!r}).write_text("partial"); '
                     'time.sleep(0.3); '
                     f'pathlib.Path({str(pending)!r}).write_text("complete")'])
    assert control(job)['status'] == 'ABSENT'
    first = control(job, 'launch')
    assert first['status'] == 'RUNNING'
    # Simulate a lost acknowledgement: repeat launch after controller exited.
    second = control(job, 'launch')
    assert second['status'] in ('RUNNING', 'COMPLETE')
    if second['status'] == 'RUNNING':
        assert second['pid'] == first['pid']
        assert not Path(job['result_path']).exists()
    for _ in range(100):
        status = control(job)
        if status['status'] == 'COMPLETE':
            break
        time.sleep(.02)
    assert status['status'] == 'COMPLETE'
    assert Path(job['result_path']).read_text() == 'complete'
    assert count.read_text() == 'once'
    assert control(job, 'launch')['status'] == 'COMPLETE'


def test_ambiguous_claim_never_relaunches(tmp_path):
    job = dict(job_path=str(tmp_path / 'job'), result_path=str(tmp_path / 'result'))
    Path(job['job_path']).mkdir()
    (Path(job['job_path']) / 'identity.json').write_text(json.dumps(job))
    assert control(job, 'launch')['status'] == 'AMBIGUOUS'
    different = dict(job, identity='changed')
    with pytest.raises(subprocess.CalledProcessError):
        control(different, 'launch')


def test_legacy_result_harvest_does_not_launch(tmp_path):
    spec = dict(trial_number=1, params={'x': 1})
    with patch.object(e, 'remote_job_status', return_value={'status': 'COMPLETE'}), \
         patch.object(e, 'run_checked', side_effect=AssertionError('must not upload or launch')):
        info = e.launch_remote(spec, tmp_path / 'params', tmp_path / 'results/one.json',
                               tmp_path / 'log', tmp_path / 'key', 'host',
                               '/home/test/.venvs/worker/bin/python', 'sealed')
    saved = json.loads(info['record'].read_text())
    assert saved['status'] == 'COMPLETE'
    assert saved['job']['result_path'] == '/tmp/r5_step5_trial_001_result.json'
    assert saved['job']['argv'][3] == '/home/test/projects/zerodha-phase1'


def test_invalid_download_not_installed(tmp_path):
    target = tmp_path / 'result.json'
    def download(args):
        Path(args[-1]).write_text('{"mode":"WRONG"}')
    with patch.object(e, 'run_checked', side_effect=download):
        with pytest.raises(SystemExit, match='RESULT_MODE'):
            e.copy_remote_result(tmp_path / 'key', 'host', '/result', target, {}, 'p', 'w')
    assert not target.exists()
    assert not list(tmp_path.glob('*.download'))


def test_sealed_batch_resume_and_deterministic_next_plan(tmp_path):
    protocol = json.loads((ROOT / 'revision5/step5_sealed_calibration_protocol.json').read_text())
    state = e.fresh_state('p', 'w', protocol)
    batch = e.next_batch_plan(protocol, state)
    state['batches'].append(batch)
    results = {}
    for spec in batch['trials']:
        n = spec['trial_number']
        results[n] = dict(mode='CANDIDATE_EVALUATION', stage='A', protocol_sha256='p',
                          worker_sha256='w', params=spec['params'], candidate_sha256=str(n),
                          aggregate=dict(safety_violations=0, rankable=True, score=-float(n+1)))
        e.atomic_json(e.result_path(tmp_path, n), results[n])
    with patch.object(e, 'launch_local', side_effect=AssertionError('replay')), \
         patch.object(e, 'launch_remote', side_effect=AssertionError('remote replay')):
        recovered = e.execute_batch(state_dir=tmp_path, batch=batch, protocol=protocol,
                                    protocol_path=Path('unused'), protocol_sha='p', worker_path=Path('unused'),
                                    worker_sha='w', ssh_key=Path('unused'), laptop='unused', remote_python='unused')
    e.freeze_completed_batch(state, batch, recovered)
    before = copy.deepcopy(state)
    assert e.next_batch_plan(protocol, state) == e.next_batch_plan(protocol, state)
    assert state == before
    assert [t['trial_number'] for t in e.next_batch_plan(protocol, state)['trials']] == [2, 3]


def test_failed_detached_worker_is_not_relaunched(tmp_path):
    job = dict(job_path=str(tmp_path / 'job'), result_path=str(tmp_path / 'result'),
               pending_path=str(tmp_path / 'pending'), log_path=str(tmp_path / 'log'),
               argv=[sys.executable, '-c', 'raise SystemExit(7)'])
    control(job, 'launch')
    for _ in range(100):
        status = control(job)
        if status['status'] == 'FAILED':
            break
        time.sleep(.02)
    assert status == {'status': 'FAILED', 'returncode': 7}
    assert control(job, 'launch') == status
    assert not Path(job['result_path']).exists()


def test_disconnect_preserves_job_for_resume(tmp_path):
    record = tmp_path / 'job.json'
    saved = {'job': {'result_path': '/remote/result'}, 'status': 'RUNNING', 'pid': 123}
    e.atomic_json(record, saved)
    info = dict(job=saved['job'], record=record, status={'status': 'RUNNING'}, remote_python='python')
    with patch.object(e.time, 'sleep'), patch.object(e, 'remote_job_status', side_effect=subprocess.CalledProcessError(255, 'ssh')):
        with pytest.raises(subprocess.CalledProcessError):
            e.wait_remote(info, Path('key'), 'host')
    assert json.loads(record.read_text()) == saved
    with patch.object(e.time, 'sleep'), patch.object(e, 'remote_job_status', return_value={'status': 'COMPLETE'}):
        e.wait_remote(info, Path('key'), 'host')
    assert json.loads(record.read_text()) == {**saved, 'status': 'COMPLETE'}
