"""Reservation / acknowledgment lifecycle of R6ActivePaperMixin (the code used in the real B/C runs).

The base class here stands in ONLY for the native orchestrator's plumbing (B-variant ledger names,
as asserted against engine_b/orchestrator.py); every R6 decision runs through the real mixin,
ActiveBayGate, ExposureAVR and UnitMaster.
    PYTHONPATH=<worktree> python -m pytest -q revision6_avr/test_r6_native_active_lifecycle.py
"""
from collections import Counter
from types import SimpleNamespace

import pandas as pd
import pytest

from revision6_avr.r6_native_active import R6ActivePaperMixin

BAY = "GTG2_TECH_TELECOM"
T0 = pd.Timestamp("2026-08-06 13:25", tz="Asia/Kolkata")


class Port:
    def __init__(self):
        self.pos = Counter()

    def net_position(self, s):
        return self.pos[s]


class FakeNative:
    """Native seams the mixin delegates to."""
    def __init__(self, *a, **k):
        self.governor_authority = "full"
        self.symbols = ["INFY", "TCS", "WIPRO", "HCLTECH"]
        self.order_audit, self._inflight, self._symbol_inflight, self._working_exits = [], {}, {}, {}
        self.reconciliation_breaks, self._execution_halted = [], False
        self.port = Port()
        self.real_plant_dcs = SimpleNamespace(bays={BAY: SimpleNamespace(effective_allocation=200_000.0)})
        self._last_close = {s: 1000.0 for s in self.symbols}
        self._native_bar_index, self._micom_trip, self.events = 30, None, []
        self.native_qty, self.create_order, self._seq = 100, True, 0

    def _expected_position(self, s):
        return self.port.pos[s]

    def _record_controller_event(self, *a):
        self.events.append(a)

    def _plant_entry_gate(self, symbol, timestamp, side, quantity, price, bar, candidate_id, governor_entry):
        return self.native_qty

    def _register(self, order, record):
        coid = str(order.client_order_id)
        self._inflight[coid] = record
        self._symbol_inflight[record["symbol"]] = coid
        self.order_audit.append({"client_order_id": coid, "event": "SUBMIT"})

    def _admit_entry_candidate(self, pending, timestamp):
        q = self._plant_entry_gate(pending["symbol"], timestamp, "BUY", 100, 1000.0, {}, pending["candidate_id"],
                                   {"action": "ENTRY", "size_multiplier": 1.0, "reason": "GOVERNOR_ENTRY"})
        if q > 0 and self.create_order:
            self._seq += 1
            plan = SimpleNamespace(entry_price=1000.0)
            self._register(SimpleNamespace(client_order_id=f"O{self._seq}"),
                           {"kind": "entry", "symbol": pending["symbol"], "requested": q, "filled": 0,
                            "intent": {"plan": plan}})
        self.last_q = q


class Engine(R6ActivePaperMixin, FakeNative):
    r6_variant = "B"


def engine(max_u=0.15, target_u=0.15):
    return Engine(r6_max_utilization=max_u, r6_target_utilization=target_u)


def admit(e, sym, ts=T0, cid=None):
    e._admit_entry_candidate({"symbol": sym, "candidate_id": cid or f"{sym}-{ts}"}, ts)
    return e.r6_decisions[-1]


def test_same_bar_candidates_share_one_bay_budget():
    e = engine()                                    # 0.15 * 200k = 30k bay allowance
    a = admit(e, "INFY")
    b = admit(e, "TCS")
    assert a["action"] == "ENTRY" and a["r6_quantity"] == 30          # min(native 100, 30k/1000)
    assert b["action"] == "BLOCK" and b["bay_available_notional_before"] == 0  # reserved by INFY's order
    assert sum(e._r6_gates[BAY].reserved.values()) == 30_000


def test_reservation_released_when_no_native_order_is_created():
    e = engine()
    e.create_order = False                          # a later native gate rejected the candidate
    admit(e, "INFY")
    assert e._r6_gates[BAY].reserved == {}
    assert admit(e, "TCS")["r6_quantity"] == 30     # full budget available again, same bar


def test_unacknowledged_order_blocks_the_bay_next_bar_and_acceptance_settles():
    e = engine()
    admit(e, "INFY")
    e._native_bar_index += 1
    nxt = T0 + pd.Timedelta("1min")
    blocked = admit(e, "TCS", nxt)                  # INFY order only SUBMIT: fail closed
    assert blocked["action"] == "BLOCK" and "NOT_YET_ACKNOWLEDGED" in blocked["reason"]
    e.order_audit.append({"client_order_id": "O1", "event": "ACCEPTED"})
    e._native_bar_index += 1
    later = admit(e, "TCS", nxt + pd.Timedelta("1min"))
    assert later["bay_ack_pending_notional"] == 30_000        # now counted from the real ledger
    assert e._r6_bindings[[k for k, v in e._r6_bindings.items() if v["symbol"] == "INFY"][0]]["settled_by"] == ["ACCEPTED"]
    assert e.r6_counts["reservations_settled"] == 1


def test_fill_is_not_double_counted_with_its_working_order():
    e = engine(max_u=0.30, target_u=0.30)           # 60k
    admit(e, "INFY")
    e.order_audit.append({"client_order_id": "O1", "event": "ACCEPTED"})
    e._inflight["O1"]["filled"] = 10                # partial fill booked at the broker
    e.port.pos["INFY"] = 10
    e._native_bar_index += 1
    d = admit(e, "TCS", T0 + pd.Timedelta("1min"))
    assert d["bay_filled_notional"] == 10_000 and d["bay_ack_pending_notional"] == 50_000   # 60 req - 10 filled


def test_replayed_bar_fails_closed():
    e = engine()
    admit(e, "INFY")
    e.order_audit.append({"client_order_id": "O1", "event": "ACCEPTED"})
    e._native_bar_index -= 1                        # bar index goes backwards
    d = admit(e, "TCS", T0 - pd.Timedelta("1min"))
    assert d["action"] == "BLOCK" and "NON_MONOTONIC" in d["reason"]


def test_missing_mark_for_held_symbol_fails_closed():
    e = engine()
    e.port.pos["WIPRO"] = 5
    del e._last_close["WIPRO"]
    d = admit(e, "INFY")
    assert d["action"] == "BLOCK" and "missing causal market mark" in d["reason"]


def test_r6_never_raises_native_quantity_and_respects_native_zero():
    e = engine(max_u=1.0, target_u=1.0)
    e.native_qty = 7
    assert admit(e, "INFY")["r6_quantity"] == 7
    e2 = engine(max_u=1.0, target_u=1.0)
    e2.native_qty = 0
    d = admit(e2, "INFY")
    assert d["action"] == "BLOCK" and d["r6_quantity"] == 0


def test_global_adaptation_or_partial_governor_authority_is_refused():
    class Adapted(FakeNative):
        def __init__(self, *a, **k):
            super().__init__()
            self.full_adaptation = True

    class Partial(FakeNative):
        def __init__(self, *a, **k):
            super().__init__()
            self.governor_authority = "advisory"

    for base in (Adapted, Partial):
        cls = type("E", (R6ActivePaperMixin, base), {"r6_variant": "B"})
        with pytest.raises(ValueError):
            cls(r6_max_utilization=1.0, r6_target_utilization=1.0)


def _scenario(e, bars):
    out = []
    for k in bars:
        e._native_bar_index = 30 + k
        t = T0 + pd.Timedelta(minutes=k)
        for coid in list(e._inflight):                 # venue acknowledges everything from the previous bar
            if not any(a["client_order_id"] == coid and a["event"] == "ACCEPTED" for a in e.order_audit):
                e.order_audit.append({"client_order_id": coid, "event": "ACCEPTED"})
        for sym in ("INFY", "TCS"):
            if sym not in e._symbol_inflight:
                out.append(admit(e, sym, t, cid=f"{sym}-{k}"))
    return out


def test_checkpoint_restart_is_equivalent_and_rejects_incompatible_state():
    import copy
    ref = engine(max_u=0.2, target_u=0.1)
    full = _scenario(ref, range(6))
    a = engine(max_u=0.2, target_u=0.1)
    first = _scenario(a, range(3))
    snap = copy.deepcopy(a.r6_snapshot())
    native = copy.deepcopy({k: getattr(a, k) for k in ("order_audit", "_inflight", "_symbol_inflight", "_seq")})
    b = engine(max_u=0.2, target_u=0.1)
    for k, v in native.items():
        setattr(b, k, v)
    b.r6_restore(snap)
    rest = _scenario(b, range(3, 6))
    strip = lambda ds: [{k: v for k, v in d.items() if k != "event_id"} | {"eid": d.get("event_id")} for d in ds]
    assert strip(first + rest) == strip(full)
    assert b.r6_snapshot() == ref.r6_snapshot()
    with pytest.raises(Exception):
        engine(max_u=0.3, target_u=0.1).r6_restore(snap)          # different approved settings
