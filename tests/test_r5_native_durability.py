"""Native coupling and actual subprocess crashes; synthetic prices are explicit fixtures."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest
from revision5.paper_state_journal import PaperStateJournal, checkpoint_state
from revision5.topology import bay_for_symbol
from tests.test_r5_paper_apply import engine


def test_native_clock_feedback_and_cooldown():
    o, t = engine()
    bay = o.real_plant_dcs.bays[bay_for_symbol('INFY')]
    assert o._bay_governors[bay.bay_id] is bay.governor
    assert o.plant_control.dispatch_controller.merit_source is o.real_plant_dcs.dispatcher
    for _ in range(48):
        o._plant_control_shadow_step(t, 1)
    assert o.real_plant_dcs.current_bar_index == 0
    trade = {'trade_id': 'one'}
    o._register_realized_r_close_feedback(symbol='INFY', trade=trade, bay_id=bay.bay_id, realized_r=-1, reason='STOP')
    o._register_realized_r_close_feedback(symbol='INFY', trade=trade, bay_id=bay.bay_id, realized_r=-1, reason='STOP')
    assert bay.consecutive_stops == 1 and len(bay.governor.history_r) == 1
    assert o._paper_plant_entry_limit('TCS', 1, 100, t) == 0
    for n in range(1, bay.loss_cooldown_bars + 2):
        o._plant_control_shadow_step(t + pd.Timedelta(minutes=n), 1)
    assert bay.cooldown_remaining(o.real_plant_dcs.current_bar_index) == 0
    assert o._paper_plant_entry_limit('TCS', 1, 100, t + pd.Timedelta(minutes=n)) == 1
    with pytest.raises(ValueError, match='backwards'):
        o._plant_control_shadow_step(t, 1)


def test_lockout_survives_session_clock_rollover():
    o, t = engine()
    key = bay_for_symbol('INFY')
    o.real_plant_dcs.trip_unit_breaker(bay_id=key, reason='test', source='test', lockout=True)
    o._plant_control_shadow_step(t + pd.Timedelta(days=1), 1)
    assert o.real_plant_dcs.electrical_network.unit_breaker(key).lockout_86
    assert o._paper_plant_entry_limit('INFY', 1, 100, t + pd.Timedelta(days=1)) == 0


def fixture_run(path, crash='none'):
    from tests_external.test_audit_remediation import signal, long_bars
    from revision2.contracts import IDDecision
    o, t = engine()
    o.pa.evaluate = lambda snapshot, cfg: (signal(), [])
    o.id_box.evaluate = lambda *a, **kw: (IDDecision(True, 'fixture', .8, 2, .6), [])
    o.id_box._current_regime = lambda *a: 'calm'
    data = long_bars()
    data['timestamp'] = pd.date_range(t-pd.Timedelta(minutes=30), periods=len(data), freq='min')
    def after_commit(journal, eng):
        if crash == 'checkpoint' and eng.open_trades and journal.cursor >= 2:
            os._exit(73)
    journal = PaperStateJournal(path, commands=[{
        'tick': 1, 'action': 'trip', 'bay_id': bay_for_symbol('INFY'), 'reason': 'CRASH_FIXTURE_86',
    }], after_commit=after_commit)
    o.paper_journal = journal
    if crash == 'fill':
        original = o.broker.place_order
        def crash_after_fill(*args, **kwargs):
            result = original(*args, **kwargs)
            if result['passed'] and journal.cursor > 0:
                os._exit(74)
            return result
        o.broker.place_order = crash_after_fill
    report = o.run({'INFY': data}, warmup=30)
    assert report['fills'] > 0
    result = {'state': checkpoint_state(o), 'reconciled': journal.reconciled_checkpoints}
    Path(str(path)+'.result.json').write_text(json.dumps(result, default=str, sort_keys=True))
    journal.close()


@pytest.mark.parametrize('crash,code', [('checkpoint', 73), ('fill', 74)])
def test_process_restart_matches_uninterrupted(tmp_path, crash, code):
    def run(path, mode):
        return subprocess.run([sys.executable, '-m', 'tests.test_r5_native_durability', str(path), mode],
                              capture_output=True, text=True, timeout=60)
    baseline, recovered = tmp_path/'base.sqlite', tmp_path/'recover.sqlite'
    base = run(baseline, 'none')
    assert base.returncode == 0, base.stderr
    stopped = run(recovered, crash)
    assert stopped.returncode == code, stopped.stderr
    with sqlite3.connect(recovered) as con:
        rows = con.execute('SELECT payload FROM checkpoints ORDER BY seq').fetchall()
    assert rows
    state = json.loads(rows[-1][0])
    assert any(b['lockout_86'] for b in state['plant']['protection']['bays'])
    if crash == 'checkpoint':
        assert state['active_fills']
    resumed = run(recovered, 'none')
    assert resumed.returncode == 0, resumed.stderr
    original = json.loads(Path(str(baseline)+'.result.json').read_text())
    actual = json.loads(Path(str(recovered)+'.result.json').read_text())
    assert actual['state'] == original['state']
    assert actual['reconciled'] == len(rows)
    assert all(v == 'DONE' for v in actual['state']['close_receipts'].values())


def test_tampered_checkpoint_fails_closed(tmp_path):
    path = tmp_path/'state.db'
    fixture_run(path)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE checkpoints SET payload='{}' WHERE seq=1")
    with pytest.raises(RuntimeError, match='reconciliation mismatch'):
        fixture_run(path)


def test_changed_inputs_fail_before_any_orders(tmp_path):
    from tests_external.test_audit_remediation import long_bars
    path = tmp_path/'identity.db'
    o, t = engine()
    frame = long_bars()
    journal = PaperStateJournal(path)
    journal.bind(o, {'INFY': frame}, 30)
    journal.close()
    o, t = engine()
    frame.loc[0, 'close'] += 1
    journal = PaperStateJournal(path)
    with pytest.raises(RuntimeError, match='identity mismatch'):
        journal.bind(o, {'INFY': frame}, 30)
    assert not o.broker.orders
    journal.close()


def test_journal_excludes_second_writer(tmp_path):
    journal = PaperStateJournal(tmp_path/'state.db')
    with pytest.raises(RuntimeError, match='active writer'):
        PaperStateJournal(tmp_path/'state.db')
    journal.close()


if __name__ == '__main__':
    fixture_run(Path(sys.argv[1]), sys.argv[2])
