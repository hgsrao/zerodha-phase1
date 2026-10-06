"""Opt-in correlated execution boundary; never treats API acceptance as a fill.

This component does not replace the paper-only orchestrator. Cursor certification
must be supplied by the composed execution application, not by a CLI boolean.
"""
from __future__ import annotations
import json
import math
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from revision2_external.broker_reconciliation import BrokerReconciliationService
from revision2_external.protection_policy import DeliveryAuthorisationTrip, DELIVERY_UNAUTHORIZED


class AdmissionBlocked(RuntimeError):
    pass


class LiveExecutionBoundary:
    def __init__(self, adapter, database, *, account_id, mode='offline', cursor_reader=None, ownership_reader=None, clock=None):
        if mode not in ('offline', 'enabled') or not account_id:
            raise ValueError('Explicit mode/account required')
        self.adapter, self.account_id, self.mode = adapter, account_id, mode
        self.cursor_reader = cursor_reader
        self.ownership_reader = ownership_reader
        self.clock = clock or (lambda: datetime.now(ZoneInfo('Asia/Kolkata')))
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS execution_intents (account TEXT, tag TEXT, request TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(account,tag))')

    @staticmethod
    def validate(request, tag):
        if not isinstance(tag, str) or not tag.isalnum() or len(tag) > 20:
            raise ValueError('Invalid correlation identity')
        if not isinstance(request.get('symbol'), str) or not request['symbol']:
            raise ValueError('Symbol required')
        if type(request.get('quantity')) is not int or request['quantity'] <= 0:
            raise ValueError('Positive integer quantity required')
        if request.get('side') not in ('BUY', 'SELL') or request.get('product') not in ('MIS', 'CNC') or request.get('exchange', 'NSE') != 'NSE':
            raise ValueError('Unsupported side/product/exchange')
        if request.get('order_type') not in ('MARKET', 'LIMIT'):
            raise ValueError('Unsupported order type')
        price = request.get('limit_price')
        if request['order_type'] == 'LIMIT' and (isinstance(price, bool) or not isinstance(price, (int,float)) or not math.isfinite(price) or price <= 0):
            raise ValueError('Invalid limit geometry')
        allowed = {'symbol','side','quantity','order_type','limit_price','exchange','product'}
        if set(request) - allowed:
            raise ValueError('Unexpected execution arguments')

    def _current_cursor(self, now, expected=None):
        try:
            cursor = self.cursor_reader() if self.cursor_reader else None
            if (not isinstance(cursor,dict) or cursor.get('account_id') != self.account_id
                    or not isinstance(cursor.get('checkpoint_id'),str) or not cursor['checkpoint_id']):
                raise ValueError('missing account-bound durable checkpoint')
            stamp = datetime.fromisoformat(cursor['timestamp'])
            if stamp.tzinfo is None or now.tzinfo is None or not 0 <= (now-stamp).total_seconds() <= 5:
                raise ValueError('stale or naive cursor')
            if expected is not None and cursor != expected:
                raise ValueError('cursor changed after reconciliation')
            return cursor
        except Exception as exc:
            raise AdmissionBlocked('Invalid execution cursor: '+str(exc)) from exc

    def _gate(self, request, runtime_records):
        if self.mode != 'enabled':
            raise AdmissionBlocked('Offline contract validation cannot submit')
        if self.adapter.environment != 'live' or not self.adapter.verify_account_identity(self.account_id):
            raise AdmissionBlocked('Broker/account identity unverified')
        now = self.clock()
        cursor = self._current_cursor(now)
        stamp = datetime.fromisoformat(cursor['timestamp'])
        ownership = self.ownership_reader() if self.ownership_reader else None
        if not ownership or ownership.get('account_id') != self.account_id or ownership.get('checkpoint_id') != cursor['checkpoint_id'] or not isinstance(ownership.get('records'), list):
            raise AdmissionBlocked('Missing or mismatched durable ownership inventory')
        if runtime_records is not None and list(runtime_records) != ownership['records']:
            raise AdmissionBlocked('Caller inventory differs from durable ownership')
        runtime_records = ownership['records']
        if any(r.get('quantity',0) and r.get('protection_required',True) is not True for r in runtime_records):
            raise AdmissionBlocked('Owned position lacks protection requirement')
        snapshot = self.adapter.snapshot()
        if (snapshot.get('passed') is not True or not isinstance(snapshot.get('positions'),dict)
                or not isinstance(snapshot['positions'].get('net'),list)
                or not isinstance(snapshot.get('orders'),list) or not isinstance(snapshot.get('holdings'),list)):
            raise AdmissionBlocked('Incomplete broker truth snapshot')
        reconciled = BrokerReconciliationService().reconcile(runtime_records, snapshot)
        if not reconciled.new_risk_allowed:
            raise AdmissionBlocked('; '.join(reconciled.discrepancies))
        if not self.adapter.verify_account_identity(self.account_id) or not 0 <= (self.clock()-stamp).total_seconds() <= 5:
            raise AdmissionBlocked('Account/cursor changed during broker reconciliation')
        if request['product'] == 'CNC' and request['side'] == 'SELL':
            rows = [r for r in snapshot.get('holdings', []) if r.get('tradingsymbol') == request['symbol'] and r.get('exchange') == 'NSE']
            # Broker-reported current-day e-DIS authorization only. A user DDPI
            # flag is never accepted as evidence; unsupported DDPI truth blocks.
            if len(rows) != 1 or rows[0].get('authorised_date') != now.date().isoformat() or int(rows[0].get('authorised_quantity',0)) < request['quantity']:
                raise AdmissionBlocked('Current delivery authorization not verified')
        return cursor

    def submit(self, tag, request, *, runtime_records=None):
        self.validate(request, tag)
        encoded = json.dumps(request, sort_keys=True, allow_nan=False)
        prior = self.db.execute('SELECT request,receipt FROM execution_intents WHERE account=? AND tag=?', (self.account_id,tag)).fetchone()
        if prior:
            if prior[0] != encoded:
                raise ValueError('Correlation identity collision')
            receipt = json.loads(prior[1])
            if receipt.get('trip_code') == DELIVERY_UNAUTHORIZED:
                raise DeliveryAuthorisationTrip(DELIVERY_UNAUTHORIZED)
            return receipt  # Never repeat a write, even UNKNOWN.
        unresolved = [json.loads(r[0]) for r in self.db.execute('SELECT receipt FROM execution_intents WHERE account=?', (self.account_id,))]
        if any(r.get('trip_code') or r.get('status') not in ('COMPLETE', 'CANCELLED', 'REJECTED') for r in unresolved):
            raise AdmissionBlocked('Unresolved durable order intent')
        cursor = self._gate(request, runtime_records)
        intent = {'status':'UNKNOWN','filled_quantity':0,'retry_allowed':False}
        try:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.db.execute('SELECT tag,receipt FROM execution_intents WHERE account=?', (self.account_id,)).fetchall()
            if any(t != tag and (json.loads(r).get('trip_code') or json.loads(r).get('status') not in ('COMPLETE', 'CANCELLED', 'REJECTED')) for t,r in existing):
                raise AdmissionBlocked('Concurrent unresolved durable order intent')
            self.db.execute('INSERT INTO execution_intents VALUES (?,?,?,?)', (self.account_id,tag,encoded,json.dumps(intent)))
            self.db.execute('COMMIT')
        except sqlite3.IntegrityError:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            return self.submit(tag,request,runtime_records=runtime_records)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
        try:
            if not self.adapter.verify_account_identity(self.account_id):
                raise AdmissionBlocked('Account identity changed before submission')
            self._current_cursor(self.clock(),expected=cursor)
        except Exception as exc:
            self._save(tag,dict(status='NOT_SUBMITTED',filled_quantity=0,retry_allowed=False,reason=str(exc)))
            raise
        try:
            receipt = self.adapter.place_order(**request, correlation_id=tag)
        except Exception:
            # Keep durable unknown intent. Actuation may have succeeded.
            raise
        self._save(tag, receipt)
        if receipt.get('trip_code') == DELIVERY_UNAUTHORIZED:
            raise DeliveryAuthorisationTrip(DELIVERY_UNAUTHORIZED)
        return receipt

    def _save(self, tag, receipt):
        self.db.execute('UPDATE execution_intents SET receipt=? WHERE account=? AND tag=?', (json.dumps(receipt,sort_keys=True,allow_nan=False), self.account_id,tag))

    def reconcile(self, tag):
        row = self.db.execute('SELECT request,receipt FROM execution_intents WHERE account=? AND tag=?',(self.account_id,tag)).fetchone()
        if not row:
            raise KeyError(tag)
        request, old = map(json.loads,row)
        if old.get('status') == 'NOT_SUBMITTED':
            return old
        if not self.adapter.verify_account_identity(self.account_id):
            raise AdmissionBlocked('Account identity changed')
        orders = [r for r in self.adapter.orders() if r.get('tag') == tag]
        if len(orders) != 1:
            receipt = dict(old, status='UNKNOWN',known_status=old.get('known_status',old.get('status')),retry_allowed=False)
            self._save(tag,receipt)
            return receipt
        order = orders[0]
        fields = {'tradingsymbol':'symbol','transaction_type':'side','quantity':'quantity','order_type':'order_type','product':'product'}
        if any(order.get(k) != request[v] for k,v in fields.items()) or order.get('exchange') != request.get('exchange','NSE'):
            raise AdmissionBlocked('Correlated order geometry mismatch')
        if request['order_type'] == 'LIMIT' and order.get('price') != request['limit_price']:
            raise AdmissionBlocked('Correlated limit price mismatch')
        if old.get('order_id') and old['order_id'] != order['order_id']:
            raise AdmissionBlocked('Order identity changed')
        history = self.adapter.client.order_history(order['order_id'])
        if not history:
            receipt = dict(old,status='UNKNOWN',known_status=old.get('known_status',old.get('status')),retry_allowed=False)
            self._save(tag,receipt)
            return receipt
        latest = history[-1]
        if latest.get('order_id') != order['order_id']:
            raise AdmissionBlocked('History identity mismatch')
        qty = latest.get('filled_quantity',0)
        if type(qty) is not int or not old.get('filled_quantity',0) <= qty <= request['quantity']:
            raise AdmissionBlocked('Nonmonotonic/invalid fill quantity')
        status = latest.get('status','UNKNOWN')
        prior_status = old.get('known_status',old.get('status'))
        if prior_status in ('COMPLETE', 'CANCELLED', 'REJECTED') and status != prior_status:
            raise AdmissionBlocked('Terminal order status changed')
        if status == 'COMPLETE' and qty != request['quantity']:
            raise AdmissionBlocked('Complete order has incomplete fill')
        average = latest.get('average_price',0)
        if qty and (not isinstance(average,(int,float)) or not math.isfinite(average) or average <= 0):
            raise AdmissionBlocked('Missing executed price')
        # Physical inventory is read alongside history. Allocation/reconciliation
        # of netted multiple orders belongs to the execution application.
        positions = self.adapter.positions()
        receipt = dict(order_id=order['order_id'],status=status,filled_quantity=qty,
                       fill_delta=qty-old.get('filled_quantity',0),average_price=average,
                       filled=status=='COMPLETE',retry_allowed=False,positions=positions)
        if latest.get('status_message') and request['product']=='CNC' and request['side']=='SELL':
            from revision2_external.protection_policy import delivery_authorisation_failure
            if delivery_authorisation_failure(latest['status_message']):
                receipt['trip_code']=DELIVERY_UNAUTHORIZED
        self._save(tag,receipt)
        if receipt.get('trip_code') == DELIVERY_UNAUTHORIZED:
            raise DeliveryAuthorisationTrip(DELIVERY_UNAUTHORIZED)
        return receipt

    def close(self):
        self.db.close()
