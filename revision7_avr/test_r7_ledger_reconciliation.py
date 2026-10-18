"""Ledger reconciliation against a deterministic fake broker.

The fake broker is the 'broker of record' with its own independent arithmetic and idempotent execution ids.
The R5 side (FillLedger + expected position) receives scripted deliveries. Engine C's Reconciler must
report no breaks when both views agree and must record a break for every divergent delivery pattern."""
from types import SimpleNamespace
import pytest

from engine_c.reconciliation import FillLedger, Reconciler
from revision2.transaction_costs import leg_cost
from .test_r7_demand_allocation import engine

CASH = 1_000_000.0


class FakeBroker:
    """Deterministic broker of record: average-cost positions, idempotent on exec_id, explicit order states."""
    def __init__(self, cash=CASH):
        self._cash, self.pos, self.avg = cash, {}, {}
        self.realized, self.commission, self.seen, self.orders, self.log = 0.0, 0.0, set(), {}, []

    def submit(self, ref, symbol, side, qty):
        self.orders[ref] = {"symbol": symbol, "side": side, "qty": qty, "filled": 0, "state": "WORKING"}

    def reject(self, ref):
        self.orders[ref]["state"] = "REJECTED"

    def fill(self, exec_id, ref, qty, price):
        if exec_id in self.seen:
            self.log.append(("DUPLICATE_IGNORED", exec_id)); return False
        o = self.orders[ref]
        if o["state"] != "WORKING" or o["filled"] + qty > o["qty"]:
            self.log.append(("INVALID_FILL_IGNORED", exec_id)); return False
        self.seen.add(exec_id); o["filled"] += qty
        if o["filled"] == o["qty"]:
            o["state"] = "FILLED"
        s, side = o["symbol"], o["side"]
        signed = qty if side == "BUY" else -qty
        q, a = self.pos.get(s, 0), self.avg.get(s, 0.0)
        if q == 0 or (q > 0) == (signed > 0):
            self.avg[s] = (abs(q) * a + qty * price) / (abs(q) + qty)
        else:
            closed = min(qty, abs(q))
            self.realized += closed * (price - a) * (1 if q > 0 else -1)
            if q + signed == 0:
                self.avg[s] = 0.0
            elif (q + signed > 0) != (q > 0):
                self.avg[s] = price
        self.pos[s] = q + signed
        charge = leg_cost(price, qty, side)
        self.commission += charge
        self._cash += -signed * price - charge
        return True

    # ---- broker-of-record view used by the Reconciler ----
    def now_label(self): return "T"
    def net_position(self, s): return self.pos.get(s, 0)
    def position_price(self, s): return self.avg.get(s, 0.0)
    def cash(self): return self._cash
    def realized_gross_pnl(self): return self.realized
    def commissions_total(self): return self.commission
    def working_refs(self): return [r for r, o in self.orders.items() if o["state"] == "WORKING"]


class FakeR5:
    """R5-side view: receives fill deliveries (possibly wrong) and keeps its own ledger."""
    def __init__(self, broker, symbols):
        self.port, self.symbols = broker, symbols
        self.ledger, self.open_trades, self.ended_refs, self.completed_trades = FillLedger(CASH), {}, set(), []
        self.expected, self.breaks_flagged, self._working = {}, [], {}

    def deliver(self, symbol, side, qty, price):
        self.ledger.apply(symbol, side, qty, price)
        self.expected[symbol] = self.expected.get(symbol, 0) + (qty if side == "BUY" else -qty)

    def expected_position(self, s): return self.expected.get(s, 0)
    def _break(self, kind): self.breaks_flagged.append(kind)
    def track_working(self, ref): self._working[ref] = {"symbol": "X", "kind": "entry", "fills": []}
    def untrack_working(self, ref): self._working.pop(ref, None)


def setup(symbols=("INFY",)):
    b = FakeBroker(); r5 = FakeR5(b, list(symbols)); return b, r5, Reconciler(r5)


def kinds(rec): return sorted({x["kind"] for x in rec.breaks})


def test_full_fill_in_order_reconciles_cleanly():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100); r5.track_working("o1")
    assert b.fill("e1", "o1", 100, 1500.0); r5.deliver("INFY", "BUY", 100, 1500.0); r5.untrack_working("o1")
    b.submit("o2", "INFY", "SELL", 100); r5.track_working("o2")
    assert b.fill("e2", "o2", 100, 1510.0); r5.deliver("INFY", "SELL", 100, 1510.0); r5.untrack_working("o2")
    rec.check("fill")
    assert rec.breaks == [] and b.net_position("INFY") == 0 and b.realized == pytest.approx(1000.0)


def test_partial_fills_accumulate_and_reconcile():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100); r5.track_working("o1")
    for i, (q, p) in enumerate(((40, 1500.0), (35, 1501.0))):
        b.fill(f"e{i}", "o1", q, p); r5.deliver("INFY", "BUY", q, p)
        rec.check("partial")                                    # still working: order must stay outstanding on both sides
    assert rec.breaks == [] and b.orders["o1"]["state"] == "WORKING"
    b.fill("e2", "o1", 25, 1502.0); r5.deliver("INFY", "BUY", 25, 1502.0); r5.untrack_working("o1")
    rec.check("complete")
    assert rec.breaks == [] and b.net_position("INFY") == 100


def test_duplicate_delivery_to_r5_is_detected_while_broker_ignores_it():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100)
    b.fill("e1", "o1", 100, 1500.0); r5.deliver("INFY", "BUY", 100, 1500.0)
    assert b.fill("e1", "o1", 100, 1500.0) is False            # broker is idempotent on exec id
    r5.deliver("INFY", "BUY", 100, 1500.0)                      # R5 receives the duplicate callback
    rec.check("duplicate")
    assert "position_mismatch" in kinds(rec) and b.net_position("INFY") == 100 and r5.ledger.qty("INFY") == 200


def test_duplicate_to_broker_does_not_change_exposure_seen_by_r7():
    b = FakeBroker(); b.submit("o1", "INFY", "BUY", 100)
    b.fill("e1", "o1", 100, 100.0); b.fill("e1", "o1", 100, 100.0)
    e = engine(["INFY"]); e.positions = b.pos
    assert e._r7_filled_gross_notional() == pytest.approx(100 * 100.0)


def test_rejected_order_creates_no_position_and_no_break():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100); r5.track_working("o1")
    b.reject("o1"); r5.untrack_working("o1")
    assert b.fill("e1", "o1", 100, 1500.0) is False             # a late fill on a rejected order is ignored by the broker
    rec.check("rejected")
    assert rec.breaks == [] and b.net_position("INFY") == 0 and r5.ledger.qty("INFY") == 0


def test_late_fill_on_rejected_order_delivered_to_r5_is_detected():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100); b.reject("o1")
    b.fill("e1", "o1", 100, 1500.0)                             # ignored
    r5.deliver("INFY", "BUY", 100, 1500.0)                      # but R5 believes it
    rec.check("late")
    assert "position_mismatch" in kinds(rec)


def test_missing_fill_never_delivered_to_r5_is_detected():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100)
    b.fill("e1", "o1", 100, 1500.0)                             # broker filled; callback lost
    rec.check("missing")
    assert "position_mismatch" in kinds(rec) and "cash_mismatch" in kinds(rec)


def test_overfill_is_refused_by_the_broker_and_is_a_break_if_delivered():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100)
    b.fill("e1", "o1", 80, 1500.0); r5.deliver("INFY", "BUY", 80, 1500.0)
    assert b.fill("e2", "o1", 40, 1500.0) is False
    r5.deliver("INFY", "BUY", 40, 1500.0)
    rec.check("overfill")
    assert "position_mismatch" in kinds(rec)


def test_out_of_order_partials_with_same_totals_still_reconcile():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100)
    fills = [("e1", 40, 1500.0), ("e2", 35, 1501.0), ("e3", 25, 1502.0)]
    for x in fills: b.fill(x[0], "o1", x[1], x[2])
    for x in (fills[2], fills[0], fills[1]):                    # delivered 3,1,2
        r5.deliver("INFY", "BUY", x[1], x[2])
    rec.check("ooo")
    assert rec.breaks == [] and r5.ledger.avg("INFY") == pytest.approx(b.position_price("INFY"))


def test_out_of_order_entry_and_exit_is_detected_when_cost_basis_differs():
    b, r5, rec = setup()
    for ref, side in (("o1", "BUY"), ("o2", "BUY"), ("o3", "SELL")): b.submit(ref, "INFY", side, 100)
    b.fill("e1", "o1", 100, 1500.0); b.fill("e2", "o2", 100, 1520.0); b.fill("e3", "o3", 100, 1530.0)
    # R5 receives the exit callback first, then the two entries
    r5.deliver("INFY", "SELL", 100, 1530.0); r5.deliver("INFY", "BUY", 100, 1500.0); r5.deliver("INFY", "BUY", 100, 1520.0)
    rec.check("ooo-exit")
    assert b.net_position("INFY") == 100 == r5.ledger.qty("INFY")           # same net quantity ...
    assert {"average_price_mismatch", "realized_gross_mismatch"} <= set(kinds(rec))   # ... different cost basis and P&L


def test_outstanding_order_mismatch_is_detected():
    b, r5, rec = setup()
    b.submit("o1", "INFY", "BUY", 100)                          # working at broker, unknown to R5
    rec.check("outstanding")
    assert "outstanding_orders_mismatch" in kinds(rec)
