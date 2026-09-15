import run_production as r
import institutional_engine_v34 as e
from decimal import Decimal
from datetime import datetime

class Broker:
    def get_positions(self): return []
    def get_orders(self): return []
    def get_order_details(self, oid): return []
    def ltp(self, instruments): return {}

class Clock:
    def now(self): return datetime.now()

class Store:
    def load(self, d): return e.BotState(trading_day=str(d))
    def save(self, state): pass

class Alert:
    def send(self, *args, **kwargs): pass

class Lock:
    def acquire(self): return True

class Terminator:
    def halt(self, reason): print('HALT:', reason)

audit = r.ProductionAudit()
engine = e.TradingEngineV34(
    broker=Broker(),
    clock=Clock(),
    sleeper=lambda x: None,
    store=Store(),
    audit=audit,
    alert=Alert(),
    lock=Lock(),
    terminator=Terminator(),
    cfg=e.Config(
        alert_webhook_url='',
        max_daily_loss=Decimal('2000'),
        observation_retry_budget=3,
    ),
)

print('=== AUDIT OBJECT IDENTITY ===')
print('passed audit id :', id(audit))
print('engine audit id :', id(engine.audit))
print('same object      :', engine.audit is audit)
print('engine audit type:', type(engine.audit))
print('engine audit log :', engine.audit.log)

print('=== DIRECT ENGINE AUDIT TEST ===')
engine.audit.log(
    'DIRECT_ENGINE_TEST',
    active_positions=0,
    active_orders=0,
)
print('[PASS] engine.audit accepts keyword arguments')

print('=== RECONCILIATION TEST ===')
engine.reconcile_startup()
print('[PASS] reconcile_startup completed')
