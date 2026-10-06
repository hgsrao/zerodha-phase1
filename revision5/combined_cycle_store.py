"""Crash-safe opt-in cycle snapshots and correlated conversion receipts."""
from dataclasses import asdict, dataclass
import json
from datetime import datetime, date
import sqlite3
from math import isfinite
import pandas as pd
from revision5.position_lifecycle import PositionLifecycleRecord, CLOSED, LEGAL_TRANSITIONS


@dataclass(frozen=True)
class RuntimeSnapshot:
    record: PositionLifecycleRecord
    trade: dict
    protection: dict
    revision: int


def _default(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return {"__timestamp__": value.isoformat()}
    if isinstance(value, date):
        return {'__date__': value.isoformat()}
    if hasattr(value, 'item'):
        return value.item()
    raise TypeError(f"Unsupported durable value: {type(value).__name__}")


def _hook(value):
    if set(value) == {'__timestamp__'}:
        return pd.Timestamp(value['__timestamp__'])
    if set(value) == {'__date__'}:
        return date.fromisoformat(value['__date__'])
    return value


class CombinedCycleStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.executescript('''CREATE TABLE IF NOT EXISTS positions
            (id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conversions
            (request_id TEXT PRIMARY KEY, position_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);''')

    def close(self):
        self.connection.close()

    def load(self, position_id):
        row = self.connection.execute('SELECT payload,revision FROM positions WHERE id=?', (position_id,)).fetchone()
        if row is None:
            raise KeyError(position_id)
        data = json.loads(row[0], object_hook=_hook)
        return RuntimeSnapshot(PositionLifecycleRecord(**data['record']), data['trade'], data['protection'], row[1])

    def list_open(self):
        return [self.load(row[0]) for row in self.connection.execute('SELECT id FROM positions')
                if self.load(row[0]).record.lifecycle_state != CLOSED]

    def save(self, record, trade, protection, expected_revision=None):
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            revision = self._save(record, trade, protection, expected_revision)
            self.connection.execute('COMMIT')
            return revision
        except BaseException:
            self.connection.execute('ROLLBACK')
            raise

    def _save(self, record, trade, protection, expected_revision=None):
        row = self.connection.execute('SELECT revision FROM positions WHERE id=?', (record.position_id,)).fetchone()
        actual = row[0] if row else 0
        if expected_revision is not None and actual != expected_revision:
            raise ValueError('stale snapshot revision')
        if row:
            previous = self.load(record.position_id)
            old = previous.record
            for field in ('symbol','direction','anchor_price','initial_risk_r','created_bar_timestamp'):
                if getattr(old, field) != getattr(record, field):
                    raise ValueError('position identity/risk cannot change')
            if record.lifecycle_state != old.lifecycle_state and record.lifecycle_state not in LEGAL_TRANSITIONS[old.lifecycle_state]:
                raise ValueError('illegal durable lifecycle transition')
            if (record.current_stop_price < old.current_stop_price if record.direction == 'BUY'
                    else record.current_stop_price > old.current_stop_price):
                raise ValueError('durable stop cannot loosen')
        payload = json.dumps(dict(record=asdict(record), trade=trade, protection=protection), default=_default, allow_nan=False)
        quantity = self.trade_quantity(trade)
        if row and quantity != self.trade_quantity(previous.trade):
            raise ValueError('authoritative trade quantity cannot change')
        for key, expected in (('trade_id', record.position_id), ('symbol', record.symbol),
                              ('side', record.direction), ('entry_price', record.anchor_price)):
            if key in trade and trade[key] != expected:
                raise ValueError('trade identity differs from lifecycle')
        if protection.get('stop') != record.current_stop_price:
            raise ValueError('durable protective stop differs from lifecycle')
        if 'target_price' in trade and (not isfinite(trade['target_price']) or trade['target_price'] <= 0):
            raise ValueError('invalid durable target price')
        self.connection.execute('INSERT OR REPLACE INTO positions VALUES (?,?,?)', (record.position_id,payload,actual+1))
        return actual+1

    @staticmethod
    def trade_quantity(trade):
        # Older component snapshots used qty; live engine snapshots use quantity.
        quantity = trade.get('quantity', trade.get('qty'))
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError('positive integer authoritative trade quantity required')
        if 'quantity' in trade and 'qty' in trade and trade['qty'] != quantity:
            raise ValueError('conflicting authoritative trade quantity fields')
        return quantity

    def requests(self):
        return [json.loads(row[0], object_hook=_hook) for row in self.connection.execute('SELECT payload FROM conversions')]
