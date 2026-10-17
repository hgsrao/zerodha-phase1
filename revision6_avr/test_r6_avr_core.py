"""Run with python -m unittest -v test_r6_avr_core.py (stdlib only)."""
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from r6_avr_core import (
    AVRConfig, Action, BenchExciterPlant, CausalID, ExposureAVR, ExposureConfig,
    ExposureSample, PIConfig, PositionalPI, SafetyPermit, UnitMaster,
    VoltageAVR, VoltageSample, Vote,
)

BAY_GAS = "GTG2_TECH_TELECOM"
BAY_STEAM = "CSTG1_BFSI"


def identity(bar=1, bay=BAY_GAS, symbol="INFY", position_id="FLAT"):
    when = datetime(2026, 8, 6, 13, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    return CausalID(bar, (when + timedelta(minutes=bar)).isoformat(), bay,
                    symbol, position_id)


def vote(src, act, qty, ident=None, reason="TEST"):
    return Vote(ident or identity(), src, act, qty, reason)


def safe(allowed=True, cap=1000, trip=""):
    return SafetyPermit(allowed, cap, trip)


def decide(g, a, *, safety=None, held=0, requested=30, price=1000,
           minimum=15000, pending=False):
    return UnitMaster.decide(governor=g, avr=a, safety=safety or safe(),
                             held_quantity=held, requested_entry_quantity=requested,
                             market_price=price, minimum_order_notional=minimum,
                             outstanding_order=pending)


class PITest(unittest.TestCase):
    def test_positional_not_double_integral(self):
        p = PositionalPI(PIConfig(1.0, 0.0, 0.0, 1.0))
        outs = [p.step(error=-0.02, dt=1, bias=1,
                       low=0.1, high=1.2)[0] for _ in range(50)]
        self.assertAlmostEqual(outs[0], 0.98)
        self.assertEqual(outs, [outs[0]] * 50)

    def test_backcalculation_stops_runaway_integral(self):
        p = PositionalPI(PIConfig(10, 2, 3, 0.2))
        for _ in range(50):
            output, trace = p.step(error=10, dt=0.1, bias=0.5, low=0, high=1)
            self.assertEqual(output, 1)
            self.assertLessEqual(abs(p.i_term), 0.2)
            self.assertTrue(trace["saturated"])

    def test_rate_limit_in_antiwindup(self):
        p = PositionalPI(PIConfig(1, .2, 2, .3))
        output, trace = p.step(error=1, dt=1, bias=.5, low=0, high=1,
                               previous_applied=.5, rate_per_second=.05)
        self.assertAlmostEqual(output, .55)
        self.assertTrue(trace["saturated"])


class VoltageAVRTest(unittest.TestCase):
    def setUp(self):
        self.avr = VoltageAVR(AVRConfig.experimental(BAY_GAS))

    def sample(self, bar=1, v=1.0, field=1.0, q=0.0, source="BENCH_SIMULATED"):
        return VoltageSample(bar, identity(bar).timestamp, BAY_GAS, v, field, q, source)

    def test_two_distinct_loops_at_neutral(self):
        s = self.avr.step(self.sample(), 1)
        self.assertAlmostEqual(s.voltage_error, 0)
        self.assertAlmostEqual(s.field_demand_pu, 1)
        self.assertAlmostEqual(s.excitation_command_pu, 1)
        self.assertEqual(s.limiter, "NORMAL")
        self.assertEqual(s.outer["error"], 0)
        self.assertEqual(s.inner["error"], 0)
        self.assertEqual(self.avr.vote(identity(), held_quantity=0,
                                      max_total_quantity=20).action, Action.ENTRY)

    def test_gtg_and_steam_droop_are_explicit_study_values(self):
        self.assertEqual(AVRConfig.experimental(BAY_GAS).reactive_droop, .075)
        self.assertEqual(AVRConfig.experimental(BAY_STEAM).reactive_droop, .10)
        s = self.avr.step(self.sample(q=.4), 1)
        self.assertAlmostEqual(s.vref_effective_pu, .97)

    def test_saturated_reactive_operation_blocks_new_entry(self):
        s = self.avr.step(self.sample(q=.85), 1)
        self.assertFalse(s.trip)
        self.assertIn("OEL", s.limiter)
        self.assertEqual(self.avr.vote(identity(), held_quantity=0,
                                      max_total_quantity=100).action, Action.BLOCK)

    def test_voltage_trip_latches_and_survives_session(self):
        s = self.avr.step(self.sample(v=.89), 1)
        self.assertEqual(s.trip, "ANSI_27_UNDERVOLTAGE")
        s2 = self.avr.step(self.sample(bar=2, v=1), 1)
        self.assertEqual(s2.trip, "ANSI_27_UNDERVOLTAGE")
        self.assertEqual(self.avr.vote(identity(2), held_quantity=5,
                                      max_total_quantity=50).action, Action.TRIP)
        with self.assertRaises(PermissionError):
            self.avr.reset_latch(operator_authorized=False, healthy=self.sample(bar=2))
        self.avr.reset_latch(operator_authorized=True, healthy=self.sample(bar=2))
        s3 = self.avr.step(self.sample(bar=3), 1)
        self.assertFalse(s3.trip)

    def test_no_faked_market_mvar(self):
        s = self.avr.step(self.sample(source="MARKET_CHURN_PROXY"), 1)
        self.assertEqual(s.trip, "INVALID_MEASUREMENT_PROVENANCE")

    def test_missing_measurement_fails_closed(self):
        s = self.avr.step(self.sample(q=None), 1)
        self.assertEqual(s.trip, "AVR_MEASUREMENT_UNAVAILABLE")
        self.assertEqual(self.avr.vote(identity(), held_quantity=0,
                                      max_total_quantity=50).action, Action.BLOCK)

    def test_anti_replay(self):
        self.avr.step(self.sample(), 1)
        with self.assertRaises(ValueError):
            self.avr.step(self.sample(), 1)

    def test_full_field_and_voltage_feedback_on_bench(self):
        plant = BenchExciterPlant(BAY_GAS)
        first_v = None
        command = 1.0
        for bar in range(1, 81):
            s = plant.advance(bar_index=bar, timestamp=identity(bar).timestamp,
                              command=command, bus_voltage_pu=.985,
                              load_pu=0, dt=1)
            if first_v is None:
                first_v = s.terminal_voltage_pu
            result = self.avr.step(s, 1)
            self.assertFalse(result.trip, f"unexpected bench trip: {result.trip}")
            command = result.excitation_command_pu
        self.assertGreater(plant.field_pu, 1.0)
        self.assertLess(abs(1 - plant.voltage_pu), abs(1 - first_v))


    def test_reactive_limit_constrains_final_inner_actuator(self):
        # With positive Q above the OEL operating limit, even the INNER
        # actuator may not raise field excitation beyond current measurement.
        s = self.avr.step(self.sample(q=.85, field=.7), 1)
        self.assertFalse(s.trip)
        self.assertLessEqual(s.excitation_command_pu, .7 + 1e-12)

    def test_reverse_timestamp_is_rejected(self):
        self.avr.step(self.sample(bar=2), 1)
        earlier = replace(self.sample(bar=3), timestamp=identity(1).timestamp)
        with self.assertRaises(ValueError):
            self.avr.step(earlier, 1)

    def test_checkpoint_restores_causal_pi_state(self):
        self.avr.step(self.sample(), 1)
        state = self.avr.snapshot()
        restored = VoltageAVR(AVRConfig.experimental(BAY_GAS))
        restored.restore(state)
        self.assertEqual(restored.snapshot(), state)
        a = self.avr.step(self.sample(bar=2, v=.99), 1)
        b = restored.step(self.sample(bar=2, v=.99), 1)
        self.assertEqual(a, b)

    def test_checkpoint_config_mismatch_fails(self):
        self.avr.step(self.sample(), 1)
        other = VoltageAVR(replace(AVRConfig.experimental(BAY_GAS), reactive_droop=.04))
        with self.assertRaises(ValueError):
            other.restore(self.avr.snapshot())

    def test_stale_snapshot_vote_is_fail_closed(self):
        self.avr.step(self.sample(), 1)
        self.assertEqual(self.avr.vote(identity(2), held_quantity=10,
                                      max_total_quantity=15).action, Action.TRIP)


class ExposureAVRTest(unittest.TestCase):
    def setUp(self):
        self.ctrl = ExposureAVR(ExposureConfig(BAY_GAS, .20, .12))

    def sample(self, bar=1, filled=0, pending=0, held=0, price=1000, capital=1_000_000,
               source="FILL_LEDGER"):
        return ExposureSample(identity(bar, position_id="POS1" if held else "FLAT"),
                              capital, filled, pending, held, price,
                              filled_source=source)

    def test_entry_capacity_uses_acknowledged_committed_feedback(self):
        v0 = self.ctrl.step(self.sample())
        self.assertEqual(v0.action, Action.ENTRY)
        self.assertGreater(v0.max_total_quantity, 0)
        v1 = self.ctrl.step(self.sample(bar=2, pending=190000))
        self.assertEqual(v1.telemetry["committed_util"], .19)
        self.assertNotEqual(v0.telemetry["inner"]["error"],
                            v1.telemetry["inner"]["error"])

    def test_filled_exposure_closes_outer_feedback(self):
        zero = self.ctrl.step(self.sample())
        filled = self.ctrl.step(self.sample(bar=2, filled=80000, held=80))
        self.assertEqual(zero.telemetry["filled_util"], 0)
        self.assertAlmostEqual(filled.telemetry["filled_util"], .08)
        self.assertNotEqual(zero.telemetry["outer"]["error"],
                            filled.telemetry["outer"]["error"])

    def test_invalid_measurement_fails_closed(self):
        v = self.ctrl.step(self.sample(source="SYNTHETIC_MVAR"))
        self.assertEqual(v.action, Action.BLOCK)

    def test_checkpoint_replay(self):
        self.ctrl.step(self.sample())
        saved = self.ctrl.snapshot()
        restored = ExposureAVR(ExposureConfig(BAY_GAS, .20, .12))
        restored.restore(saved)
        self.assertEqual(self.ctrl.step(self.sample(bar=2, pending=10000)),
                         restored.step(self.sample(bar=2, pending=10000)))

    def test_zero_target_requests_exit(self):
        ctrl = ExposureAVR(ExposureConfig(BAY_GAS, .20, 0))
        self.assertEqual(ctrl.step(self.sample(held=10, filled=10000)).action, Action.EXIT)


class UnitMasterTest(unittest.TestCase):
    def test_enter_requires_all_three_permissions(self):
        i = decide(vote("GOVERNOR", Action.ENTRY, 100),
                   vote("AVR", Action.ENTRY, 80), requested=60)
        self.assertEqual((i.action, i.order_quantity), (Action.ENTRY, 60))
        self.assertEqual(i.event_id, decide(vote("GOVERNOR", Action.ENTRY, 100),
                                             vote("AVR", Action.ENTRY, 80),
                                             requested=60).event_id)

    def test_hold_does_not_authorize_entry(self):
        i = decide(vote("GOVERNOR", Action.ENTRY, 100),
                   vote("AVR", Action.HOLD, 100))
        self.assertEqual(i.action, Action.BLOCK)

    def test_either_controller_may_exit_unilaterally(self):
        i = identity(position_id="POS1")
        for g, a in [(Action.EXIT, Action.HOLD), (Action.HOLD, Action.EXIT)]:
            result = decide(vote("GOVERNOR", g, 100, i),
                            vote("AVR", a, 100, i), held=20)
            self.assertEqual((result.action, result.order_quantity), (Action.EXIT, 20))

    def test_protection_outweighs_exit_and_hold(self):
        i = identity(position_id="POS1")
        result = decide(vote("GOVERNOR", Action.EXIT, 0, i),
                        vote("AVR", Action.HOLD, 100, i), held=20,
                        safety=safe(trip="SEL300G_ANSI_87G"))
        self.assertEqual(result.action, Action.TRIP)
        self.assertEqual(result.order_quantity, 20)

    def test_reduce_quantity_means_shares_to_close(self):
        i = identity(position_id="POS1")
        result = decide(vote("GOVERNOR", Action.HOLD, 80, i),
                        vote("AVR", Action.REDUCE, 55, i), held=80)
        self.assertEqual(result.action, Action.REDUCE)
        self.assertEqual(result.order_quantity, 25)

    def test_commercial_floor_applied_once_and_does_not_upsize(self):
        result = decide(vote("GOVERNOR", Action.ENTRY, 100),
                        vote("AVR", Action.ENTRY, 5), requested=30,
                        price=1000, minimum=15000)
        self.assertEqual(result.action, Action.BLOCK)
        self.assertEqual(result.reason, "BELOW_COMMERCIAL_MINIMUM")

    def test_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            decide(vote("GOVERNOR", Action.ENTRY, 100),
                   vote("AVR", Action.ENTRY, 100, identity(bar=2)))

    def test_pending_orders_cannot_duplicate(self):
        result = decide(vote("GOVERNOR", Action.ENTRY, 100),
                        vote("AVR", Action.ENTRY, 100), pending=True)
        self.assertEqual(result.action, Action.BLOCK)
        self.assertEqual(result.reason, "OUTSTANDING_ORDER")

    def test_zero_cap_blocks_entry(self):
        result = decide(vote("GOVERNOR", Action.ENTRY, 100),
                        vote("AVR", Action.ENTRY, 0))
        self.assertEqual(result.action, Action.BLOCK)

    def test_safety_entry_denial_blocks(self):
        result = decide(vote("GOVERNOR", Action.ENTRY, 100),
                        vote("AVR", Action.ENTRY, 100), safety=safe(False))
        self.assertEqual(result.action, Action.BLOCK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
