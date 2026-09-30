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


def test_worktree_layout_runs_worker_from_named_root(tmp_path):
    layout = e.remote_worktree_layout('/home/test/projects/zerodha-protocol-v3',
                                      Path('/x/step5_sealed_calibration_protocol_v3.json'), 'abcdef0123456789')
    spec = dict(trial_number=1, params={'x': 1})
    with patch.object(e, 'remote_job_status', return_value={'status': 'COMPLETE'}), \
         patch.object(e, 'run_checked', side_effect=AssertionError('must not upload or launch')):
        info = e.launch_remote(spec, tmp_path / 'params', tmp_path / 'results/one.json',
                               tmp_path / 'log', tmp_path / 'key', 'host',
                               '/home/test/.venvs/worker/bin/python', 'sealed', layout)
    argv = info['job']['argv']
    assert argv[1] == '/home/test/projects/zerodha-protocol-v3/scripts/run_r5_step5_candidate.py'
    assert argv[argv.index('--root') + 1] == '/home/test/projects/zerodha-protocol-v3'
    assert argv[argv.index('--protocol') + 1] == \
        '/home/test/projects/zerodha-protocol-v3/revision5/step5_sealed_calibration_protocol_v3.json'
    # Namespaced by protocol: a V2 job or result left in /tmp is never harvested.
    assert info['job']['result_path'] == '/tmp/r5_step5_abcdef012345_trial_001_result.json'
    with pytest.raises(SystemExit, match='REMOTE_ROOT_NOT_ABSOLUTE'):
        e.remote_worktree_layout('~/projects/zerodha-protocol-v3', Path('p.json'), 'abc')


def test_remote_root_defaults_to_protocol_then_override():
    protocol = {'distributed_execution': {'remote_worker_root': '~/projects/zerodha-protocol-v3'}}
    assert e.configured_remote_root(protocol, None) == '~/projects/zerodha-protocol-v3'
    assert e.configured_remote_root(protocol, '/srv/v3') == '/srv/v3'
    assert e.configured_remote_root({'distributed_execution': {}}, None) is None
    calls = []
    def fake(key, host, command):
        calls.append(command)
        return '/home/test/projects/zerodha-protocol-v3'
    with patch.object(e, 'remote_text', side_effect=fake):
        layout = e.resolve_remote_layout(Path('k'), 'h', protocol, Path('v3.json'), 'f' * 64)
    assert calls == ['cd "$HOME/projects/zerodha-protocol-v3" && pwd -P']
    assert layout['mode'] == 'worktree' and layout['root'] == '/home/test/projects/zerodha-protocol-v3'


def _remote(answers):
    def fake(key, host, command):
        for needle, answer in answers.items():
            if needle in command:
                assert command.startswith("cd /home/test/projects/zerodha-protocol-v3 && ")
                return answer
        raise AssertionError(command)
    return fake


def test_remote_engine_check_targets_worktree_and_allows_protocol_commit_only():
    layout = e.remote_worktree_layout('/home/test/projects/zerodha-protocol-v3', Path('v3.json'), 'abc')
    clean = {'rev-parse': 'dbe01d8', 'merge-base': 'yes',
             'diff --name-only': 'revision5/step5_sealed_calibration_protocol_v3.json\ndocs/R5_PROTOCOL_V3.md',
             'status': ''}
    with patch.object(e, 'remote_text', side_effect=_remote(clean)):
        assert e.verify_remote_engine(Path('k'), 'h', layout, 'a695913') == 'dbe01d8'
    for change, error in [({'merge-base': 'no'}, 'REMOTE_ENGINE_PARENT'),
                          ({'diff --name-only': 'revision5/governor.py'}, 'REMOTE_ENGINE_DRIFT'),
                          ({'diff --name-only': 'scripts/run_r5_step5_candidate.py'}, 'REMOTE_ENGINE_DRIFT'),
                          ({'status': ' M scripts/run_r5_step5_candidate.py'}, 'REMOTE_TREE_DIRTY')]:
        with patch.object(e, 'remote_text', side_effect=_remote({**clean, **change})):
            with pytest.raises(SystemExit, match=error):
                e.verify_remote_engine(Path('k'), 'h', layout, 'a695913')


def test_legacy_remote_engine_check_unchanged():
    with patch.object(e, 'remote_text', return_value='c0b4672fa45'):
        assert e.verify_remote_engine(Path('k'), 'h', e.legacy_remote_layout(), 'c0b4672') == 'c0b4672fa45'
    with patch.object(e, 'remote_text', return_value='ab70c7c'):
        with pytest.raises(SystemExit, match='REMOTE_ENGINE_PARENT'):
            e.verify_remote_engine(Path('k'), 'h', e.legacy_remote_layout(), 'c0b4672')


def test_local_worktree_descent_rule(tmp_path):
    head = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    answers = {'merge-base': 0, 'diff': 'docs/x.md', 'status': ''}
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, answers['merge-base'])
    def fake_output(args):
        return answers['diff'] if 'diff' in args else answers['status']
    with patch.object(e.subprocess, 'run', side_effect=fake_run), \
         patch.object(e, 'command_output', side_effect=fake_output):
        e.verify_local_worktree(head)
        answers['diff'] = 'revision5/exit_shadow.py'
        with pytest.raises(SystemExit, match='LOCAL_ENGINE_DRIFT'):
            e.verify_local_worktree(head)
        answers.update(diff='', status=' M scripts/run_r5_step5_candidate.py')
        with pytest.raises(SystemExit, match='LOCAL_TREE_DIRTY'):
            e.verify_local_worktree(head)
        answers['merge-base'] = 1
        with pytest.raises(SystemExit, match='LOCAL_ENGINE_PARENT'):
            e.verify_local_worktree(head)
