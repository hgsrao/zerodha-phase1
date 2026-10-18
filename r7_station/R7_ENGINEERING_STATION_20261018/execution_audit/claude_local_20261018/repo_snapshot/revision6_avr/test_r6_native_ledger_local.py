"""Local, offline behavioral tests for the R6 ledger probe."""

import unittest
from types import SimpleNamespace

from r6_broker_exposure_probe import (
    snapshot,
    LedgerProbeError,
)

BAY = "GTG2_TECH_TELECOM"
MAPPING = {
    "INFY": BAY,
    "TCS": BAY,
}
MARKS = {
    "INFY": 120.0,
    "TCS": 100.0,
}


class FakePort:
    def __init__(self, positions):
        self.positions = positions

    def net_position(self, symbol):
        return self.positions.get(symbol, 0)


def make_engine(variant):
    positions = {"INFY": 0, "TCS": 0}
    expected = dict(positions)

    engine = SimpleNamespace(
        port=FakePort(positions),
        real_plant_dcs=SimpleNamespace(
            bays={
                BAY: SimpleNamespace(
                    effective_allocation=250000.0
                )
            }
        ),
        order_audit=[],
        reconciliation_breaks=[],
        reconciler=SimpleNamespace(breaks=[]),
        _inflight={},
        _working={},
        _symbol_inflight={},
        _symbol_working={},
        _working_exits={},
        _execution_halted=False,
    )

    engine._expected_position = lambda s: expected.get(s, 0)
    engine.expected_position = lambda s: expected.get(s, 0)

    return engine, positions, expected


def add_order(
    engine,
    variant,
    *,
    order_id="ORDER1",
    symbol="INFY",
    kind="entry",
    requested=10,
    filled=0,
    reference=110.0,
    accepted=True,
    booked=False,
):
    record = {
        "kind": kind,
        "symbol": symbol,
        "requested": requested,
        "filled": filled,
        "booked": booked,
        "intent": {
            "plan": SimpleNamespace(
                entry_price=reference
            )
        },
    }

    if variant == "B":
        engine._inflight[order_id] = record
        engine._symbol_inflight[symbol] = order_id
        key, identity = "client_order_id", order_id
    else:
        identity = len(engine._working) + 1
        engine._working[identity] = record
        engine._symbol_working[symbol] = identity
        key = "bt_ref"

    engine.order_audit.append({
        key: identity,
        "event": "ACCEPTED" if accepted else "SUBMIT",
    })

    return record


def observe(engine, variant, marks=None):
    return snapshot(
        engine=engine,
        variant=variant,
        bay_id=BAY,
        symbol_to_bay=MAPPING,
        completed_bar_marks=MARKS if marks is None else marks,
        bar_timestamp="2026-08-06T13:25:00+05:30",
    )


class LedgerTests(unittest.TestCase):

    def test_b_empty_ledger(self):
        e, _, _ = make_engine("B")
        x = observe(e, "B")
        self.assertEqual(x.filled_notional, 0)
        self.assertEqual(
            x.acknowledged_pending_entry_notional, 0
        )

    def test_c_empty_ledger(self):
        e, _, _ = make_engine("C")
        x = observe(e, "C")
        self.assertEqual(x.bay_capital, 250000)

    def test_b_partial_fill_no_double_count(self):
        e, positions, expected = make_engine("B")
        positions["INFY"] = 4
        expected["INFY"] = 4
        add_order(
            e, "B",
            requested=10,
            filled=4,
            reference=110,
        )
        x = observe(e, "B")
        self.assertEqual(x.filled_notional, 480)
        self.assertEqual(
            x.acknowledged_pending_entry_notional, 720
        )

    def test_c_short_partial_fill(self):
        e, positions, expected = make_engine("C")
        positions["INFY"] = -2
        expected["INFY"] = -2
        add_order(
            e, "C",
            requested=8,
            filled=2,
            reference=130,
        )
        x = observe(e, "C")
        self.assertEqual(x.filled_notional, 240)
        self.assertEqual(
            x.acknowledged_pending_entry_notional, 780
        )

    def test_b_unacknowledged_entry_fails_closed(self):
        e, _, _ = make_engine("B")
        add_order(e, "B", accepted=False)
        with self.assertRaises(LedgerProbeError):
            observe(e, "B")

    def test_c_unacknowledged_entry_fails_closed(self):
        e, _, _ = make_engine("C")
        add_order(e, "C", accepted=False)
        with self.assertRaises(LedgerProbeError):
            observe(e, "C")

    def test_b_broker_reconciliation_mismatch(self):
        e, positions, expected = make_engine("B")
        positions["INFY"] = 3
        expected["INFY"] = 2
        with self.assertRaises(LedgerProbeError):
            observe(e, "B")

    def test_c_duplicate_entry_fails_closed(self):
        e, _, _ = make_engine("C")
        add_order(e, "C", order_id="ONE")
        add_order(e, "C", order_id="TWO")
        with self.assertRaises(LedgerProbeError):
            observe(e, "C")

    def test_b_parked_exit_blocks_new_entry(self):
        e, _, _ = make_engine("B")
        e._working_exits["INFY"] = {"kind": "exit"}
        x = observe(e, "B")
        self.assertIn("INFY", x.outstanding_symbols)
        self.assertEqual(
            x.acknowledged_pending_entry_notional, 0
        )

    def test_c_exit_not_entry_commitment(self):
        e, _, _ = make_engine("C")
        add_order(
            e, "C", kind="exit", accepted=False
        )
        x = observe(e, "C")
        self.assertIn("INFY", x.outstanding_symbols)
        self.assertEqual(
            x.acknowledged_pending_entry_notional, 0
        )

    def test_missing_market_mark_fails_closed(self):
        e, positions, expected = make_engine("B")
        positions["INFY"] = 4
        expected["INFY"] = 4
        with self.assertRaises(LedgerProbeError):
            observe(e, "B", marks={"TCS": 100})

    def test_execution_halt_fails_closed(self):
        e, _, _ = make_engine("B")
        e._execution_halted = True
        with self.assertRaises(LedgerProbeError):
            observe(e, "B")


if __name__ == "__main__":
    unittest.main()
