import sqlite3
import pytest
from revision5.state_recovery import StateRecoveryJournal
from unittest.mock import Mock
from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter


def test_receipt_and_post_state_commit_together(tmp_path):
    con = sqlite3.connect(tmp_path/'state.sqlite', isolation_level=None)
    journal = StateRecoveryJournal(con)
    con.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fault'):
        journal.commit_feedback('close-1', {'integral': -1})
    assert con.execute('SELECT count(*) FROM fleet_state').fetchone() == (0,)
    con.execute('DROP TRIGGER fail_receipt')
    journal.commit_feedback('close-1', {'integral': -1})
    journal.commit_feedback('close-1', {'integral': -1})
    assert con.execute('SELECT count(*) FROM receipts').fetchone() == (1,)
    with pytest.raises(RuntimeError, match='mismatch'):
        journal.commit_feedback('close-1', {'integral': 0})
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        con.execute('DELETE FROM fleet_state')
    con.close()


def test_conversion_restart_does_not_repeat_broker_write(tmp_path):
    path = tmp_path/'conversion.sqlite'
    con = sqlite3.connect(path, isolation_level=None)
    journal = StateRecoveryJournal(con)
    client = Mock()
    old = dict(exchange='NSE', tradingsymbol='TITAN', product='MIS', quantity=10)
    client.positions.side_effect = [{'net': [old]}, {'net': [dict(old, product='CNC')]}]
    client.convert_position.return_value = True
    adapter = KiteConnectBrokerAdapter(client=client, account_id='test-account', recovery_journal=journal)
    receipt = adapter.convert_position('TITAN', 10, correlation_id='one')
    assert receipt['product_confirmed']
    con.close()
    con = sqlite3.connect(path, isolation_level=None)
    adapter = KiteConnectBrokerAdapter(client=client, account_id='test-account', recovery_journal=StateRecoveryJournal(con))
    assert adapter.convert_position('TITAN', 10, correlation_id='one') == receipt
    assert client.convert_position.call_count == 1
    with pytest.raises(ValueError, match='collision'):
        adapter.convert_position('TITAN', 11, correlation_id='one')
    con.close()


def test_unknown_durable_intent_blocks_resubmission(tmp_path):
    con = sqlite3.connect(tmp_path/'unknown.sqlite', isolation_level=None)
    journal = StateRecoveryJournal(con)
    arguments = dict(symbol='TITAN', quantity=10, old_product='MIS', new_product='CNC',
                     exchange='NSE', transaction_type='BUY', account_id='test-account')
    assert journal.reserve_conversion('one', arguments) is None
    client = Mock()
    adapter = KiteConnectBrokerAdapter(client=client, account_id='test-account', recovery_journal=journal)
    result = adapter.convert_position('TITAN', 10, correlation_id='one')
    assert result['ambiguous'] and not result['retry_allowed']
    client.convert_position.assert_not_called()
    client.positions.assert_not_called()
    con.close()


def test_reopen_checks_durable_state(tmp_path):
    path = tmp_path/'state.sqlite'
    con = sqlite3.connect(path, isolation_level=None)
    StateRecoveryJournal(con).commit_feedback('one', {'history': [1, -1]})
    con.close()
    con = sqlite3.connect(path, isolation_level=None)
    journal = StateRecoveryJournal(con)
    journal.commit_feedback('one', {'history': [1, -1]})
    with pytest.raises(RuntimeError, match='mismatch'):
        journal.commit_feedback('one', {'history': []})
    con.close()
