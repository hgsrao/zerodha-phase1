"""Atomic feedback evidence for offline, deterministic paper reconstruction.

Receipts never suppress replay into fresh controller objects. A matching receipt
verifies the reconstructed post-state; a mismatch blocks recovery. This module
does not restore live broker positions or submit protective orders.
"""
import hashlib
import json
from revision5.combined_cycle_store import _default, _hook


class StateRecoveryJournal:
    def __init__(self, connection):
        self.connection = connection
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS boot_checkpoints (
                seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS boot_no_update BEFORE UPDATE ON boot_checkpoints
                BEGIN SELECT RAISE(ABORT,'append-only boot checkpoints'); END;
            CREATE TRIGGER IF NOT EXISTS boot_no_delete BEFORE DELETE ON boot_checkpoints
                BEGIN SELECT RAISE(ABORT,'append-only boot checkpoints'); END;
            CREATE TABLE IF NOT EXISTS conversion_intents (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                receipt TEXT);
            CREATE TABLE IF NOT EXISTS protection_intents (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                receipt TEXT);
            CREATE TABLE IF NOT EXISTS feedback_epochs (
                epoch TEXT PRIMARY KEY, identity_checksum TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS applied_feedback (
                epoch TEXT NOT NULL, event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, state_checksum TEXT NOT NULL,
                PRIMARY KEY(epoch,event_id));
            CREATE TRIGGER IF NOT EXISTS epoch_no_update BEFORE UPDATE ON feedback_epochs
                BEGIN SELECT RAISE(ABORT,'append-only feedback epochs'); END;
            CREATE TRIGGER IF NOT EXISTS epoch_no_delete BEFORE DELETE ON feedback_epochs
                BEGIN SELECT RAISE(ABORT,'append-only feedback epochs'); END;
            CREATE TRIGGER IF NOT EXISTS applied_no_update BEFORE UPDATE ON applied_feedback
                BEGIN SELECT RAISE(ABORT,'append-only feedback consumption'); END;
            CREATE TRIGGER IF NOT EXISTS applied_no_delete BEFORE DELETE ON applied_feedback
                BEGIN SELECT RAISE(ABORT,'append-only feedback consumption'); END;
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
        for table in ('fleet_state', 'boot_checkpoints'):
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            if 'ecs_demand_pu' not in columns:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN ecs_demand_pu REAL CHECK(ecs_demand_pu IS NULL OR (ecs_demand_pu >= 0 AND ecs_demand_pu <= 1))')

    def checkpoint_boot(self, state, *, store=None, position_updates=()):
        """Pin fleet state to lifecycle rows in this SAME database transaction."""
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            if position_updates:
                if store is None or store.connection is not con:
                    raise ValueError('Boot positions require the same transactional store')
                for record,trade,protection,revision in position_updates:
                    store._save(record,trade,protection,revision)
            positions = list(con.execute('SELECT id,payload,revision FROM positions ORDER BY id'))
            conversions = list(con.execute('SELECT request_id,payload FROM conversions ORDER BY request_id'))
            payload = json.dumps(dict(version=1, state=state, positions=positions,
                                      conversions=conversions), sort_keys=True,
                                 default=_default, allow_nan=False)
            checksum = hashlib.sha256(payload.encode()).hexdigest()
            con.execute('INSERT INTO boot_checkpoints(payload,checksum,ecs_demand_pu) VALUES (?,?,?)',
                        (payload, checksum, state.get('ecs_demand_pu')))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise

    def load_boot(self):
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute('SELECT payload,checksum,ecs_demand_pu FROM boot_checkpoints ORDER BY seq DESC LIMIT 1').fetchone()
            if row is None or hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                raise RuntimeError('Missing or corrupt boot checkpoint')
            # Compare raw canonical lifecycle rows before decoding timestamp objects.
            raw = json.loads(row[0])
            positions = [list(r) for r in con.execute('SELECT id,payload,revision FROM positions ORDER BY id')]
            conversions = [list(r) for r in con.execute('SELECT request_id,payload FROM conversions ORDER BY request_id')]
            if raw.get('version') != 1 or raw['positions'] != positions or raw['conversions'] != conversions:
                raise RuntimeError('Lifecycle store advanced beyond boot checkpoint; recovery reconciliation required')
            result = json.loads(row[0], object_hook=_hook)['state']
            if row[2] != result.get('ecs_demand_pu'):
                raise RuntimeError('ECS checkpoint column differs from hashed payload')
            con.execute('COMMIT')
            return result
        except BaseException:
            con.execute('ROLLBACK')
            raise

    def reserve_conversion(self, request_id, arguments):
        return self._reserve_intent('conversion_intents', request_id, arguments)

    def reserve_protection(self, request_id, arguments):
        return self._reserve_intent('protection_intents', request_id, arguments)

    def _reserve_intent(self, table, request_id, arguments):
        if not isinstance(request_id, str) or not request_id:
            raise ValueError('Durable conversion requires a correlation identity')
        fingerprint = json.dumps(arguments, sort_keys=True, allow_nan=False)
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute(f'SELECT fingerprint,receipt FROM {table} WHERE request_id=?',
                              (request_id,)).fetchone()
            if row is None:
                con.execute(f'INSERT INTO {table} VALUES (?,?,NULL)',
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
        return self._finish_intent('conversion_intents', request_id, receipt)

    def finish_protection(self, request_id, receipt):
        return self._finish_intent('protection_intents', request_id, receipt)

    def _finish_intent(self, table, request_id, receipt):
        payload = json.dumps(receipt, sort_keys=True, allow_nan=False)
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute(f'SELECT receipt FROM {table} WHERE request_id=?',
                              (request_id,)).fetchone()
            if row is None or (row[0] is not None and row[0] != payload):
                raise ValueError('Missing or conflicting conversion receipt')
            con.execute(f'UPDATE {table} SET receipt=? WHERE request_id=?',
                        (payload, request_id))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise

    def begin_feedback_epoch(self, epoch, identity_checksum):
        if not isinstance(epoch, str) or not epoch or not isinstance(identity_checksum, str) or len(identity_checksum) != 64:
            raise ValueError('Invalid feedback epoch identity')
        self.connection.execute('INSERT INTO feedback_epochs VALUES (?,?)', (epoch, identity_checksum))

    def feedback_applied(self, epoch, identity_checksum, event_id, fingerprint):
        """Validate durable consumption in this reconstruction epoch before mutation."""
        row=self.connection.execute('SELECT identity_checksum FROM feedback_epochs WHERE epoch=?', (epoch,)).fetchone()
        if row != (identity_checksum,):
            raise RuntimeError('Feedback epoch identity mismatch')
        applied=self.connection.execute('SELECT fingerprint,state_checksum FROM applied_feedback WHERE epoch=? AND event_id=?', (epoch,event_id)).fetchone()
        if applied is None:
            return False
        state=self.connection.execute('SELECT payload,checksum FROM fleet_state WHERE event_id=?', (event_id,)).fetchone()
        receipt=self.connection.execute('SELECT status FROM receipts WHERE event_id=?', (event_id,)).fetchone()
        if (applied[0] != fingerprint or state is None or receipt != ('DONE',)
            or hashlib.sha256(state[0].encode()).hexdigest() != state[1]
            or state[1] != applied[1]
            or json.loads(state[0]).get('close_receipts', {}).get(event_id) != 'DONE'):
            raise RuntimeError('Corrupt or conflicting durable feedback consumption')
        return True

    def commit_feedback(self, event_id, state, *, epoch=None, identity_checksum=None, fingerprint=None):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError('Feedback event identity is required')
        payload = json.dumps(state, sort_keys=True, separators=(',', ':'),
                             default=str, allow_nan=False)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        con = self.connection
        con.execute('BEGIN IMMEDIATE')
        try:
            if epoch is not None:
                epoch_row=con.execute('SELECT identity_checksum FROM feedback_epochs WHERE epoch=?', (epoch,)).fetchone()
                if epoch_row != (identity_checksum,) or not isinstance(fingerprint, str) or len(fingerprint) != 64:
                    raise RuntimeError('Feedback epoch identity mismatch')
                if con.execute('SELECT 1 FROM applied_feedback WHERE epoch=? AND event_id=?', (epoch,event_id)).fetchone():
                    raise RuntimeError('Feedback consumption already committed in this epoch')
            row = con.execute('SELECT payload,checksum FROM fleet_state WHERE event_id=?',
                              (event_id,)).fetchone()
            receipt = con.execute('SELECT status FROM receipts WHERE event_id=?',
                                  (event_id,)).fetchone()
            if row is not None or receipt is not None:
                if row != (payload, checksum) or receipt != ('DONE',):
                    raise RuntimeError('Feedback recovery checkpoint mismatch')
            else:
                con.execute('INSERT INTO fleet_state(event_id,payload,checksum,ecs_demand_pu) VALUES (?,?,?,?)',
                            (event_id, payload, checksum, state.get('ecs_demand_pu')))
                con.execute('INSERT INTO receipts VALUES (?,?)', (event_id, 'DONE'))
            if epoch is not None:
                con.execute('INSERT INTO applied_feedback VALUES (?,?,?,?)', (epoch,event_id,fingerprint,checksum))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise
