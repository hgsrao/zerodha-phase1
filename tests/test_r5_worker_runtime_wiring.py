"""Exercise the actual worker factory on the recorded real TITAN fixture.

Only data loading is substituted: this is not a 48-symbol Block 1 certification.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import run_r5_step5_candidate as worker
from revision5.combined_cycle_store import CombinedCycleStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/r5_d01'


def test_worker_explicit_runtime_executes_real_fill_and_preserves_ledger(tmp_path, monkeypatch):
    protocol = json.loads((ROOT / 'revision5/step5_sealed_calibration_protocol_v2.json').read_text())
    params = json.loads((FIXTURE / 'trial_007_params.json').read_text())
    stock = pd.read_csv(FIXTURE / 'titan_1min_block1_session1.csv')
    stock['timestamp'] = pd.to_datetime(stock['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
    feeds = {}
    for name, filename in [('NIFTY_50_15MIN', 'nifty_50_15min_prefix.csv'),
                           ('INDIA_VIX_15MIN', 'india_vix_15min_prefix.csv')]:
        frame = pd.read_csv(FIXTURE / filename)
        frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True)
        feeds[name] = frame
    audit = {'slice_sha256': hashlib.sha256((FIXTURE / 'titan_1min_block1_session1.csv').read_bytes()).hexdigest()}
    monkeypatch.setattr(worker, 'prepare_block', lambda *args: ({'TITAN': stock}, feeds, audit))
    block = protocol['sampling_plan']['stage_b'][0]
    baseline = worker.execute_block(ROOT, protocol, block, params)
    path = tmp_path / 'worker.sqlite3'
    wired = worker.execute_block(ROOT, protocol, block, params, combined_cycle_state_path=path)
    assert baseline['trades'] == wired['trades']
    assert len(wired['trades']) == 1
    assert wired['trades'][0]['side'] == 'SELL'
    assert 'combined_cycle_runtime' not in baseline
    assert wired['combined_cycle_runtime']['live_admissions'] is False
    assert wired['combined_cycle_runtime']['handoff']['enabled'] is True
    assert wired['block_fingerprint'] != baseline['block_fingerprint']
    store = CombinedCycleStore(path)
    try:
        rows = store.connection.execute('SELECT id FROM positions').fetchall()
        assert len(rows) == 1
        assert store.load(rows[0][0]).record.lifecycle_state == 'CLOSED'
        assert not store.list_open()
    finally:
        store.close()
    with pytest.raises(FileExistsError):
        worker.build_paper_combined_cycle_runtime(path)


def test_sealed_identity_guard_still_rejects_engine_drift(monkeypatch):
    monkeypatch.setattr(worker.subprocess, 'check_call', lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, 'git_output', lambda *args: 'revision2_external/orchestrator.py')
    with pytest.raises(SystemExit, match='ENGINE_PARENT_DRIFT'):
        worker.verify_engine_identity(ROOT, {'distributed_execution': {'engine_parent_commit': 'sealed'}})
