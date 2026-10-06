"""Atomic feedback evidence for offline, deterministic paper reconstruction.

Receipts never suppress replay into fresh controller objects. A matching receipt
verifies the reconstructed post-state; a mismatch blocks recovery. This module
does not restore live broker positions or submit protective orders.
"""
import hashlib
import json


class StateRecoveryJournal:
    def __init__(self, connection):
        self.connection = connection
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS conversion_intents (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                receipt TEXT);
            CREATE TABLE IF NOT EXISTS fleet_state (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                event_id TEXT PRIMARY KEY REFERENCES fleet_state(event_id),
                status TEXT NOT NULL CHECK(status='DONE'));
            CREATE TRIGGER IF NOT EXISTS fleet_no_update BEFORE UPDATE ON fleet_state
                BEGIN SELECT RAISE(ABORT,'append-only fleet state'); END;
            CREATE TRIGGER IF NOT EXISTS fleet_no_delete BEFORE DELETE ON fleet_state
                BEGIN SELECT RAISE(ABORT,'append-only fleet state'); END;
            CREATE TRIGGER IF NOT EXISTS receipt_no_update BEFORE UPDATE ON receipts
                BEGIN SELECT RAISE(ABORT,'append-only receipts'); END;
            CREATE TRIGGER IF NOT EXISTS receipt_no_delete BEFORE DELETE ON receipts
                BEGIN SELECT RAISE(ABORT,'append-only receipts'); END;
        ''')

    def reserve_conversion(self, request_id, arguments):
        if not isinstance(request_id, str) or not request_id:
            raise ValueError('Durable conversion requires a correlation identity')
        fingerprint = json.dumps(arguments, sort_keys=True, allow_nan=False)
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute('SELECT fingerprint,receipt FROM conversion_intents WHERE request_id=?',
                              (request_id,)).fetchone()
            if row is None:
                con.execute('INSERT INTO conversion_intents VALUES (?,?,NULL)',
                            (request_id, fingerprint))
                result = None
            elif row[0] != fingerprint:
                raise ValueError('Conversion correlation identity collision')
            elif row[1] is None:
                result = dict(passed=False, ambiguous=True, retry_allowed=False,
                              product_confirmed=False, correlation_id=request_id,
                              reason='Durable intent has unknown broker outcome; reconciliation required')
            else:
                result = json.loads(row[1])
            con.execute('COMMIT')
            return result
        except BaseException:
            con.execute('ROLLBACK')
            raise

    def finish_conversion(self, request_id, receipt):
        payload = json.dumps(receipt, sort_keys=True, allow_nan=False)
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute('SELECT receipt FROM conversion_intents WHERE request_id=?',
                              (request_id,)).fetchone()
            if row is None or (row[0] is not None and row[0] != payload):
                raise ValueError('Missing or conflicting conversion receipt')
            con.execute('UPDATE conversion_intents SET receipt=? WHERE request_id=?',
                        (payload, request_id))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise

    def commit_feedback(self, event_id, state):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError('Feedback event identity is required')
        payload = json.dumps(state, sort_keys=True, separators=(',', ':'),
                             default=str, allow_nan=False)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute('SELECT payload,checksum FROM fleet_state WHERE event_id=?',
                              (event_id,)).fetchone()
            receipt = con.execute('SELECT status FROM receipts WHERE event_id=?',
                                  (event_id,)).fetchone()
            if row is not None or receipt is not None:
                if row != (payload, checksum) or receipt != ('DONE',):
                    raise RuntimeError('Feedback recovery checkpoint mismatch')
            else:
                con.execute('INSERT INTO fleet_state VALUES (?,?,?)',
                            (event_id, payload, checksum))
                con.execute('INSERT INTO receipts VALUES (?,?)', (event_id, 'DONE'))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise
