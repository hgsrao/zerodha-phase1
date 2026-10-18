"""Each fix F1-F9 in r6_avr_core_fixed.py: the same scenario FAILS on the supplied core and PASSES fixed.
    PYTHONPATH=<worktree> python -m pytest -q revision6_avr/test_r6_core_fixes.py
"""
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ORIG = importlib.import_module("r6_avr_core")
FIXED = importlib.import_module("r6_avr_core_fixed")
BAY = "GTG2_TECH_TELECOM"
T0 = datetime(2026, 8, 6, 13, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def ts(bar):
    return (T0 + timedelta(minutes=bar)).isoformat()


def sample(m, bar, v=1.0, field=1.0, q=0.0, src="BENCH_SIMULATED"):
    return m.VoltageSample(bar, ts(bar), BAY, v, field, q, src)


def ident(m, bar=1, pos="FLAT"):
    return m.CausalID(bar, ts(bar), BAY, "INFY", pos)


# F1 -------------------------------------------------------------------------------------------
def outputs_constant_error(m, dt):
    pi = m.PositionalPI(m.PIConfig(1.0, 0.06, 0.8, 0.30))
    return [pi.step(error=0.1, dt=dt, bias=1.0, low=0.8, high=1.2)[0] for _ in range(8)]


def test_f1_antiwindup_monotone_at_large_dt():
    orig = outputs_constant_error(ORIG, 60.0)
    fixed = outputs_constant_error(FIXED, 60.0)
    assert any(b < a - 1e-12 for a, b in zip(orig, orig[1:]))          # supplied: chatters
    assert all(b >= a - 1e-12 for a, b in zip(fixed, fixed[1:]))       # fixed: monotone under +error


@pytest.mark.xfail(strict=True, reason="OPEN: closed-loop limit cycle at dt=3 s remains after F1 (loop gain x dt with placeholder PI gains); needs approved gains/dt bound, not a redesign")
def test_f1_bench_steady_bus_no_limit_cycle_at_dt3():
    def run(m):
        avr, plant, cmd, cmds = m.VoltageAVR(m.AVRConfig.experimental(BAY)), m.BenchExciterPlant(BAY), 1.0, []
        for bar in range(1, 121):
            s = plant.advance(bar_index=bar, timestamp=ts(bar), command=cmd, bus_voltage_pu=0.985, load_pu=0, dt=3)
            r = avr.step(s, 3)
            cmd = r.excitation_command_pu
            cmds.append(cmd)
        return max(cmds[-20:]) - min(cmds[-20:])
    assert run(ORIG) > 0.05 and run(FIXED) < 0.01


# F2 -------------------------------------------------------------------------------------------
def test_f2_missing_measurement_blocks_entry_holds_position_and_does_not_latch():
    for m, latched in ((ORIG, True), (FIXED, False)):
        avr = m.VoltageAVR(m.AVRConfig.experimental(BAY))
        avr.step(sample(m, 1, q=None), 1)
        assert (avr.trip_latch is not None) is latched
        held = avr.vote(ident(m, 1, "POS1"), held_quantity=10, max_total_quantity=10)
        assert held.action == (m.Action.TRIP if latched else m.Action.HOLD)
        assert avr.vote(ident(m, 1), held_quantity=0, max_total_quantity=10).action == m.Action.BLOCK
        nxt = avr.step(sample(m, 2), 1)
        assert bool(nxt.trip) is latched                         # fixed: recovers on the next good sample


def test_f2_stale_vote_does_not_liquidate():
    for m, act in ((ORIG, "TRIP"), (FIXED, "HOLD")):
        avr = m.VoltageAVR(m.AVRConfig.experimental(BAY))
        avr.step(sample(m, 1), 1)
        assert avr.vote(ident(m, 2, "POS1"), held_quantity=10, max_total_quantity=15).action.value == act


def test_f2_real_protection_still_latches():
    avr = FIXED.VoltageAVR(FIXED.AVRConfig.experimental(BAY))
    assert avr.step(sample(FIXED, 1, v=0.89), 1).trip == "ANSI_27_UNDERVOLTAGE"
    assert avr.step(sample(FIXED, 2), 1).trip == "ANSI_27_UNDERVOLTAGE"
    assert avr.vote(ident(FIXED, 2, "POS1"), held_quantity=5, max_total_quantity=5).action == FIXED.Action.TRIP


# F3 -------------------------------------------------------------------------------------------
def test_f3_reset_needs_newer_evidence_and_is_bumpless():
    for m in (ORIG, FIXED):
        avr = m.VoltageAVR(m.AVRConfig.experimental(BAY))
        avr.step(sample(m, 1), 1)
        avr.step(sample(m, 2, v=0.89), 1)
        stale = sample(m, 1)
        if m is ORIG:
            avr.reset_latch(operator_authorized=True, healthy=stale)   # supplied accepts stale evidence
            assert avr.command == avr.config.field_min_pu               # and jumps to field_min
        else:
            with pytest.raises(ValueError):
                avr.reset_latch(operator_authorized=True, healthy=stale)
            avr.reset_latch(operator_authorized=True, healthy=sample(m, 3, field=0.97))
            assert avr.command == pytest.approx(0.97)                   # bumpless
            assert not avr.step(sample(m, 4, field=0.97), 1).trip
    fresh = FIXED.VoltageAVR(FIXED.AVRConfig.experimental(BAY))
    with pytest.raises(ValueError):
        fresh.reset_latch(operator_authorized=True, healthy=sample(FIXED, 1))   # nothing to reset


# F4 -------------------------------------------------------------------------------------------
def test_f4_restore_cannot_clear_live_latch_or_rewind():
    for m in (ORIG, FIXED):
        avr = m.VoltageAVR(m.AVRConfig.experimental(BAY))
        avr.step(sample(m, 1), 1)
        pre_trip = avr.snapshot()
        avr.step(sample(m, 2, v=0.89), 1)
        if m is ORIG:
            avr.restore(pre_trip)
            assert avr.trip_latch is None                                  # supplied: latch silently gone
        else:
            with pytest.raises(ValueError):
                avr.restore(pre_trip)
            assert avr.trip_latch == "ANSI_27_UNDERVOLTAGE"


# F5 -------------------------------------------------------------------------------------------
def test_f5_repeated_exit_keeps_one_event_id():
    ids = {}
    for m in (ORIG, FIXED):
        out = []
        for bar in (1, 2, 3):
            i = ident(m, bar, "POS1")
            out.append(m.UnitMaster.decide(governor=m.Vote(i, "GOVERNOR", m.Action.EXIT, 0, "GOV_EXIT"),
                                           avr=m.Vote(i, "AVR", m.Action.HOLD, 20, "H"),
                                           safety=m.SafetyPermit(True, 100), held_quantity=20,
                                           requested_entry_quantity=0, market_price=1000,
                                           minimum_order_notional=0, outstanding_order=True).event_id)
        ids[m] = len(set(out))
    assert ids[ORIG] == 3 and ids[FIXED] == 1


# F6 / F7 / F8 ---------------------------------------------------------------------------------
def test_f6_flat_identity_with_shares_rejected():
    for m in (ORIG, FIXED):
        i = ident(m)
        call = lambda: m.UnitMaster.decide(governor=m.Vote(i, "GOVERNOR", m.Action.HOLD, 50, "x"),
                                           avr=m.Vote(i, "AVR", m.Action.HOLD, 50, "x"), safety=m.SafetyPermit(True, 50),
                                           held_quantity=50, requested_entry_quantity=0, market_price=1,
                                           minimum_order_notional=0)
        if m is ORIG:
            assert call().action == m.Action.HOLD
        else:
            with pytest.raises(ValueError):
                call()


def test_f7_safety_permit_for_other_snapshot_rejected():
    i, other = ident(FIXED, 1), ident(FIXED, 2)
    with pytest.raises(ValueError):
        FIXED.UnitMaster.decide(governor=FIXED.Vote(i, "GOVERNOR", FIXED.Action.ENTRY, 10, "g"),
                                avr=FIXED.Vote(i, "AVR", FIXED.Action.ENTRY, 10, "a"),
                                safety=FIXED.SafetyPermit(True, 10, identity=other), held_quantity=0,
                                requested_entry_quantity=10, market_price=1, minimum_order_notional=0)


def test_f8_simultaneous_trip_reasons_kept():
    i = ident(FIXED, 1, "POS1")
    r = FIXED.UnitMaster.decide(governor=FIXED.Vote(i, "GOVERNOR", FIXED.Action.TRIP, 0, "GOV_TRIP"),
                                avr=FIXED.Vote(i, "AVR", FIXED.Action.TRIP, 0, "ANSI_59"),
                                safety=FIXED.SafetyPermit(False, 0, "MICOM_81U"), held_quantity=5,
                                requested_entry_quantity=0, market_price=1, minimum_order_notional=0)
    assert r.reason == "MICOM_81U;GOV_TRIP;ANSI_59"


# F9 -------------------------------------------------------------------------------------------
def test_f9_checkpoint_carries_limiter_and_integrity_and_restart_is_equivalent():
    a = FIXED.VoltageAVR(FIXED.AVRConfig.experimental(BAY))
    a.step(sample(FIXED, 1, q=0.85), 1)
    snap = a.snapshot()
    assert snap["limiter"] == "OEL_REACTIVE_LIMITING"
    b = FIXED.VoltageAVR(FIXED.AVRConfig.experimental(BAY))
    b.restore(snap)
    assert b.snapshot() == snap
    assert a.step(sample(FIXED, 2, q=0.5), 1) == b.step(sample(FIXED, 2, q=0.5), 1)
