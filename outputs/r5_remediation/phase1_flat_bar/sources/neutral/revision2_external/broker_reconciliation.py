"""Read-only broker truth comparison. A mismatch never authorizes new risk."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence

@dataclass(frozen=True)
class ReconciliationReceipt:
    passed: bool
    new_risk_allowed: bool
    discrepancies: tuple[str, ...]
    positions: tuple[dict, ...]
    orders: tuple[dict, ...]

    def to_dict(self):
        return asdict(self)

class BrokerReconciliationService:
    def reconcile(self, runtime_records: Sequence[Mapping[str, Any]], snapshot: Mapping[str, Any]):
        errors = []
        if not snapshot.get('passed', False):
            errors.append('broker snapshot unavailable')
        raw_positions = snapshot.get('positions', [])
        if isinstance(raw_positions, dict):
            raw_positions = raw_positions.get('net', [])
        positions = tuple(dict(p) for p in raw_positions if int(p.get('quantity', 0)))
        orders = tuple(dict(o) for o in snapshot.get('orders', []))
        def key(p):
            return (p.get('exchange', 'NSE'), p.get('tradingsymbol', p.get('symbol')), p.get('product'))
        actual = {}
        for p in positions:
            k = key(p)
            if k in actual:
                errors.append(f'duplicate broker position: {k}')
            actual[k] = actual.get(k, 0) + int(p['quantity'])
        # Equity carry moves from net positions to holdings after settlement.
        # Mixed holdings/day positions require explicit account-specific allocation;
        # never guess or double-count them at restart.
        for h in snapshot.get('holdings', []):
            k = key(h)
            qty = int(h.get('quantity', 0)) + int(h.get('t1_quantity', 0)) - int(h.get('used_quantity', 0))
            if qty:
                if k in actual:
                    errors.append(f'mixed holdings and net position requires allocation: {k}')
                else:
                    actual[k] = qty
        expected = {}
        protection_ids = set()
        for p in runtime_records:
            k = key(p)
            expected[k] = expected.get(k, 0) + int(p.get('quantity', 0))
            if p.get('protection_required', True) and int(p.get('quantity', 0)):
                oid = p.get('protective_order_id')
                if oid in protection_ids:
                    errors.append(f'shared protective order requires explicit allocation: {oid}')
                protection_ids.add(oid)
                matches = [o for o in orders if o.get('order_id') == oid] if oid else []
                if len(matches) != 1:
                    errors.append(f'missing or duplicate protection: {k}')
                else:
                    o = matches[0]
                    pending = int(o.get('pending_quantity', int(o.get('quantity', 0)) - int(o.get('filled_quantity', 0))))
                    side = 'SELL' if int(p['quantity']) > 0 else 'BUY'
                    if (o.get('status') not in ('OPEN', 'TRIGGER PENDING') or pending != abs(int(p['quantity']))
                        or key(o) != k or o.get('transaction_type') != side or o.get('order_type') not in ('SL', 'SL-M')):
                        errors.append(f'inactive or insufficient protection: {k}')
                    stop = p.get('current_stop_price', p.get('stop_price'))
                    if stop is not None:
                        trigger = float(o.get('trigger_price', 0))
                        if trigger <= 0 or (side == 'SELL' and trigger < float(stop)) or (side == 'BUY' and trigger > float(stop)):
                            errors.append(f'broker trigger loosens durable stop: {k}')
                        if o.get('order_type') == 'SL':
                            price = float(o.get('price', 0))
                            if price <= 0 or (side == 'SELL' and price > trigger) or (side == 'BUY' and price < trigger):
                                errors.append(f'invalid stop-limit geometry: {k}')
        for k in set(actual) | set(expected):
            if actual.get(k, 0) != expected.get(k, 0):
                errors.append(f'position mismatch: {k}: runtime={expected.get(k, 0)} broker={actual.get(k, 0)}')
        # Unknown open entry orders could fill after restart and create unowned risk.
        known = {p.get('protective_order_id') for p in runtime_records} | {p.get('entry_order_id') for p in runtime_records}
        for o in orders:
            if o.get('status') in ('OPEN', 'TRIGGER PENDING', 'PUT ORDER REQ RECEIVED', 'VALIDATION PENDING', 'OPEN PENDING', 'MODIFY PENDING', 'MODIFY VALIDATION PENDING', 'CANCEL PENDING', 'AMO REQ RECEIVED') and o.get('order_id') not in known:
                errors.append(f'unknown pending order: {o.get("order_id")}')
        return ReconciliationReceipt(not errors, not errors, tuple(errors), positions, orders)
