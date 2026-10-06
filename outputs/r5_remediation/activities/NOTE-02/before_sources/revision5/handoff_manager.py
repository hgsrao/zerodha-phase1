"""Correlated, durable ownership transfer; sends no orders and realizes no P&L."""
from dataclasses import dataclass, asdict
from math import isfinite
from uuid import uuid4
import json
import pandas as pd
from revision5.combined_cycle_store import _default, _hook
from revision5.position_lifecycle import A_OPEN, TRANSFER_REQUESTED, CLOSED, request_transfer, acknowledge_transfer, reject_transfer


@dataclass(frozen=True)
class HandoffConfig:
    enabled: bool = False
    minimum_r: float = .75
    start: str = '15:10'
    deadline: str = '15:14'

    def __post_init__(self):
        from datetime import datetime
        for clock in (self.start, self.deadline):
            if datetime.strptime(clock, '%H:%M').strftime('%H:%M') != clock:
                raise ValueError('clock must be HH:MM')
        if self.start > self.deadline or not isfinite(self.minimum_r) or self.minimum_r < 0:
            raise ValueError('invalid handoff configuration')


@dataclass(frozen=True)
class ConversionRequest:
    request_id: str
    position_id: str
    symbol: str
    quantity: int
    requested_at: pd.Timestamp
    status: str = 'PENDING'


class HandoffManager:
    def __init__(self, store, config=None):
        self.store = store
        self.config = config or HandoffConfig()

    @staticmethod
    def _local(timestamp):
        stamp = pd.Timestamp(timestamp)
        return stamp.tz_localize('Asia/Kolkata') if stamp.tzinfo is None else stamp.tz_convert('Asia/Kolkata')

    def qualifies(self, record, timestamp, current_r, mfe_r, trend_aligned, completed_bar=True):
        clock = self._local(timestamp).strftime('%H:%M')
        return bool(self.config.enabled and completed_bar and record.lifecycle_state == A_OPEN
                    and record.direction == 'BUY' and self.config.start <= clock <= self.config.deadline
                    and isfinite(current_r) and isfinite(mfe_r)
                    and current_r >= self.config.minimum_r and mfe_r >= self.config.minimum_r
                    and trend_aligned is True)

    def request(self, position_id, quantity, timestamp, *, current_r, mfe_r, trend_aligned, completed_bar=True):
        snapshot = self.store.load(position_id)
        if (isinstance(quantity, bool) or not isinstance(quantity, int)
                or quantity != self.store.trade_quantity(snapshot.trade)):
            raise ValueError('conversion must match authoritative trade quantity')
        existing = self.store.connection.execute('SELECT payload FROM conversions WHERE position_id=?',(position_id,)).fetchone()
        if existing:
            return ConversionRequest(**{k:v for k,v in json.loads(existing[0],object_hook=_hook).items() if k in ConversionRequest.__dataclass_fields__})
        if not self.qualifies(snapshot.record,timestamp,current_r,mfe_r,trend_aligned,completed_bar):
            return None
        if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity <= 0:
            raise ValueError('positive integer quantity required')
        request = ConversionRequest(uuid4().hex, position_id,snapshot.record.symbol,quantity,self._local(timestamp))
        db = self.store.connection
        db.execute('BEGIN IMMEDIATE')
        try:
            self.store._save(request_transfer(snapshot.record),snapshot.trade,snapshot.protection,snapshot.revision)
            db.execute('INSERT INTO conversions VALUES (?,?,?)',(request.request_id,position_id,json.dumps(asdict(request),default=_default)))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
        return request

    def resolve(self, request_id, status, product=None, quantity=None, timestamp=None):
        db = self.store.connection
        db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute('SELECT payload FROM conversions WHERE request_id=?',(request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            receipt = json.loads(row[0],object_hook=_hook)
            snapshot = self.store.load(receipt['position_id'])
            outcome = status.upper()
            if outcome not in ('UNKNOWN','PENDING','ACKNOWLEDGED','REJECTED','TIMEOUT'):
                raise ValueError('unknown conversion outcome')
            resolution = dict(status=outcome, product=product, quantity=quantity,
                              timestamp=self._local(timestamp) if timestamp is not None else None)
            if receipt['status'] != 'PENDING':
                if outcome not in ('UNKNOWN', 'PENDING'):
                    previous = receipt.get('resolution')
                    if previous is None and receipt['status'] != 'CLOSED_RACE':
                        # Older receipts stored ACK time and requested quantity,
                        # but did not store a separate broker resolution payload.
                        previous = dict(status=receipt['status'],
                                        product='CNC' if receipt['status']=='ACKNOWLEDGED' else None,
                                        quantity=receipt['quantity'] if receipt['status']=='ACKNOWLEDGED' else None,
                                        timestamp=self._local(receipt['resolved_at']) if receipt.get('resolved_at') is not None else None)
                    if resolution != previous:
                        raise ValueError('conflicting terminal conversion receipt: reconcile broker before proceeding')
                db.execute('COMMIT')
                return snapshot
            record = snapshot.record
            if outcome in ('UNKNOWN','PENDING'):
                db.execute('COMMIT')
                return snapshot
            if outcome not in ('ACKNOWLEDGED','REJECTED','TIMEOUT'):
                raise ValueError('unknown conversion outcome')
            if record.lifecycle_state == CLOSED:
                receipt['status'] = 'CLOSED_RACE'
            elif record.lifecycle_state != TRANSFER_REQUESTED:
                raise ValueError('conversion has no pending lifecycle')
            elif outcome == 'ACKNOWLEDGED':
                if timestamp is None or product != 'CNC' or type(quantity) is not int or quantity != receipt['quantity']:
                    raise ValueError('acknowledgement requires exact product, quantity and timestamp')
                stamp = self._local(timestamp)
                if stamp.date() != receipt['requested_at'].date() or stamp < receipt['requested_at'] or stamp.strftime('%H:%M') > self.config.deadline:
                    raise ValueError('late/invalid acknowledgement: reconcile adapter before closing')
                record = acknowledge_transfer(record)
                receipt['status'] = outcome
            else:
                record = reject_transfer(record)
                receipt['status'] = outcome
            receipt['resolved_at'] = timestamp
            receipt['resolution'] = resolution
            self.store._save(record,snapshot.trade,snapshot.protection,snapshot.revision)
            db.execute('UPDATE conversions SET payload=? WHERE request_id=?',(json.dumps(receipt,default=_default),request_id))
            db.execute('COMMIT')
            return self.store.load(record.position_id)
        except BaseException:
            db.execute('ROLLBACK')
            raise
