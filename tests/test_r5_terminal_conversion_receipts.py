"""Terminal conversion conflicts must stop reconciliation, never change ownership."""
import pandas as pd
import pytest

from test_combined_cycle_handoff import setup, request

ACK_TIME = pd.Timestamp('2023-12-05 15:11')


@pytest.mark.parametrize('terminal', ['REJECTED'])
def test_late_ack_after_negative_terminal_outcome_is_not_ignored(tmp_path, terminal):
    store, manager = setup(tmp_path / 'cycle.db')
    conversion = request(manager)
    snap = manager.resolve(conversion.request_id, terminal)
    with pytest.raises(ValueError, match='conflicting terminal conversion receipt'):
        manager.resolve(conversion.request_id, 'ACKNOWLEDGED', 'CNC', 10, ACK_TIME)
    assert store.load('p1').revision == snap.revision
    assert store.load('p1').record.lifecycle_state == 'A_OPEN'


@pytest.mark.parametrize('status,product,quantity,timestamp', [
    ('REJECTED', None, None, None),
    ('ACKNOWLEDGED', 'MIS', 10, ACK_TIME),
    ('ACKNOWLEDGED', 'CNC', 9, ACK_TIME),
    ('ACKNOWLEDGED', 'CNC', 10.0, ACK_TIME),
    ('ACKNOWLEDGED', 'CNC', 10, pd.Timestamp('2023-12-05 15:12')),
])
def test_conflicting_ack_duplicate_rolls_back(tmp_path, status, product, quantity, timestamp):
    store, manager = setup(tmp_path / 'cycle.db')
    conversion = request(manager)
    snap = manager.resolve(conversion.request_id, 'ACKNOWLEDGED', 'CNC', 10, ACK_TIME)
    with pytest.raises(ValueError, match='conflicting terminal conversion receipt'):
        manager.resolve(conversion.request_id, status, product, quantity, timestamp)
    assert store.load('p1').revision == snap.revision
    assert store.load('p1').record.lifecycle_state == 'B_OPEN'


def test_identical_terminal_receipt_remains_idempotent_after_restart(tmp_path):
    from revision5.combined_cycle_store import CombinedCycleStore
    from revision5.handoff_manager import HandoffManager, HandoffConfig
    path = tmp_path / 'cycle.db'
    store, manager = setup(path)
    conversion = request(manager)
    snap = manager.resolve(conversion.request_id, 'ACKNOWLEDGED', 'CNC', 10, ACK_TIME)
    store.close()
    store = CombinedCycleStore(path)
    manager = HandoffManager(store, HandoffConfig(enabled=True))
    same = manager.resolve(conversion.request_id, 'acknowledged', 'CNC', 10, ACK_TIME)
    assert same.revision == snap.revision
    assert manager.resolve(conversion.request_id, 'UNKNOWN').revision == snap.revision
