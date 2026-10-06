"""R5-D02.2: constructing StateRecoveryJournal inside a caller transaction must never end that transaction."""
import sqlite3

from revision5.combined_cycle_store import CombinedCycleStore
from revision5.state_recovery import StateRecoveryJournal


def _count(con, table):
    return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_journal_construction_and_checkpoint_nested_roll_back_together(tmp_path):
    store = CombinedCycleStore(tmp_path / 'cc.sqlite')
    con = store.connection
    con.execute('BEGIN IMMEDIATE')
    con.execute("INSERT INTO positions VALUES ('P1','{}',1)")
    journal = StateRecoveryJournal(con)  # NEW journal: schema created for the first time inside the txn
    assert con.in_transaction, 'journal construction committed the caller transaction'
    journal.checkpoint_boot({'ecs_demand_pu': 0.5})
    assert con.in_transaction, 'nested checkpoint ended the caller transaction'
    assert _count(con, 'positions') == 1 and _count(con, 'boot_checkpoints') == 1
    con.execute('ROLLBACK')
    assert _count(con, 'positions') == 0
    # The schema was created inside the same transaction, so rollback removes
    # the table itself as well as the checkpoint row.
    assert con.execute("SELECT name FROM sqlite_master WHERE name='boot_checkpoints'").fetchone() is None
    store.close()


def test_journal_schema_is_complete_and_idempotent(tmp_path):
    con = sqlite3.connect(str(tmp_path / 'j.sqlite'), isolation_level=None)
    con.execute('CREATE TABLE positions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL)')
    con.execute('CREATE TABLE conversions (request_id TEXT PRIMARY KEY, position_id TEXT, payload TEXT)')
    StateRecoveryJournal(con)
    StateRecoveryJournal(con)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {'boot_checkpoints', 'conversion_intents', 'protection_intents', 'feedback_epochs', 'applied_feedback',
            'fleet_state', 'receipts', 'boot_no_update', 'boot_no_delete', 'epoch_no_update', 'epoch_no_delete',
            'applied_no_update', 'applied_no_delete', 'fleet_no_update', 'fleet_no_delete',
            'receipt_no_update', 'receipt_no_delete'} <= names
    for table in ('fleet_state', 'boot_checkpoints'):
        assert 'ecs_demand_pu' in {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    assert not con.in_transaction
