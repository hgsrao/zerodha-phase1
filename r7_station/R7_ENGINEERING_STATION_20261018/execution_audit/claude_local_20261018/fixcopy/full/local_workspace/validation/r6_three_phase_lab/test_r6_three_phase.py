"""Tests for the offline three-phase electrical measurement adapter and AVR bench."""
import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from r6_avr_core import AVRConfig, CausalID, VoltageAVR
from r6_three_phase import (
    BenchGeneratorConfig, GeneratorRating, ThreePhaseRMS, PhaseAnglesRMS,
    SOURCE_BENCH, SOURCE_EXTERNAL, ThreePhaseGeneratorBench,
    measure_three_phase, to_voltage_sample, reactive_droop_calibration,
    build_bench_controller,
)

BAY = "GTG2_TECH_TELECOM"
T0 = datetime(2026, 8, 6, 13, 25, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def ident(n=1, bay=BAY):
    return CausalID(n, (T0 + timedelta(seconds=n)).isoformat(), bay, "GENERATOR_BENCH")


class ThreePhaseMathTests(unittest.TestCase):
    def setUp(self):
        self.rating = GeneratorRating(BAY, 11.0, 100.0)

    def measure(self, p_mw, q_mvar, *, index=1, source=SOURCE_EXTERNAL):
        s = math.hypot(p_mw, q_mvar)
        current = s / (math.sqrt(3) * 11.0)
        phi = math.atan2(q_mvar, p_mw) if s else 0.0
        return measure_three_phase(
            ThreePhaseRMS(ident(index), 11.0, current, phi, source),
            self.rating,
        )

    def test_rating_current(self):
        self.assertAlmostEqual(
            self.rating.rated_current_ka, 100 / (math.sqrt(3) * 11)
        )

    def test_unity_pf(self):
        r = self.measure(80, 0)
        self.assertAlmostEqual(r.active_mw, 80)
        self.assertAlmostEqual(r.reactive_mvar, 0)
        self.assertAlmostEqual(r.apparent_mva, 80)
        self.assertAlmostEqual(r.power_factor_magnitude, 1)
        self.assertEqual(r.power_factor_status, "UNITY")

    def test_08_lagging(self):
        r = self.measure(80, 60)
        self.assertAlmostEqual(r.apparent_mva, 100)
        self.assertAlmostEqual(r.active_mw, 80)
        self.assertAlmostEqual(r.reactive_mvar, 60)
        self.assertAlmostEqual(r.cos_phi, .8)
        self.assertAlmostEqual(r.q_pu, .6)
        self.assertAlmostEqual(r.phi_rad, math.acos(.8))
        self.assertEqual(r.power_factor_status, "LAGGING")

    def test_leading_negative_q(self):
        r = self.measure(80, -60)
        self.assertAlmostEqual(r.reactive_mvar, -60)
        self.assertAlmostEqual(r.q_pu, -.6)
        self.assertAlmostEqual(r.cos_phi, .8)
        self.assertEqual(r.power_factor_status, "LEADING")

    def test_motoring_signed_power(self):
        r = self.measure(-20, 5)
        self.assertAlmostEqual(r.active_mw, -20)
        self.assertLess(r.cos_phi, 0)
        self.assertAlmostEqual(r.reactive_mvar, 5)

    def test_zero_current_has_undefined_phi_and_pf(self):
        r = self.measure(0, 0)
        self.assertIsNone(r.phi_rad)
        self.assertIsNone(r.cos_phi)
        self.assertIsNone(r.power_factor_magnitude)
        self.assertEqual(r.power_factor_status, "UNDEFINED")
        self.assertEqual(r.apparent_mva, 0)

    def test_nonzero_angle_at_zero_current_rejected(self):
        with self.assertRaises(ValueError):
            ThreePhaseRMS(ident(), 11, 0, .25, SOURCE_EXTERNAL)

    def test_voltage_and_current_angles_are_differenced(self):
        a = PhaseAnglesRMS(ident(), 11, 4, .5, .2, SOURCE_EXTERNAL).rms()
        self.assertAlmostEqual(a.phi_rad, .3)
        self.assertAlmostEqual(
            measure_three_phase(a, self.rating).reactive_mvar,
            math.sqrt(3) * 11 * 4 * math.sin(.3),
        )

    def test_phasor_angle_wrap(self):
        a = PhaseAnglesRMS(ident(), 11, 4, -3.0, 3.0, SOURCE_EXTERNAL).rms()
        self.assertAlmostEqual(a.phi_rad, 2 * math.pi - 6)

    def test_voltage_per_unit(self):
        r = measure_three_phase(
            ThreePhaseRMS(ident(), 10.45, 1, 0, SOURCE_EXTERNAL), self.rating
        )
        self.assertAlmostEqual(r.voltage_pu, .95)
        v = to_voltage_sample(r, field_feedback_pu=1.05)
        self.assertAlmostEqual(v.terminal_voltage_pu, .95)
        self.assertAlmostEqual(v.field_feedback_pu, 1.05)
        self.assertEqual(v.source, SOURCE_EXTERNAL)

    def test_independent_meter_disagreement_rejected(self):
        m = ThreePhaseRMS(
            ident(), 11, 2, math.pi / 6, SOURCE_EXTERNAL,
            meter_p_mw=9999, meter_q_mvar=1,
        )
        with self.assertRaisesRegex(ValueError, "inconsistent independent meter MW"):
            measure_three_phase(m, self.rating)

    def test_matching_independent_meters(self):
        p = 80
        q = 60
        current = 100 / (math.sqrt(3) * 11)
        m = ThreePhaseRMS(
            ident(), 11, current, math.atan2(q, p), SOURCE_EXTERNAL,
            meter_p_mw=p, meter_q_mvar=q,
        )
        r = measure_three_phase(m, self.rating)
        self.assertAlmostEqual(r.active_mw, p)
        self.assertAlmostEqual(r.reactive_mvar, q)

    def test_bay_mismatch_rejected(self):
        wrong = GeneratorRating("CSTG1_BFSI", 11, 100)
        m = ThreePhaseRMS(ident(), 11, 1, 0, SOURCE_EXTERNAL)
        with self.assertRaises(ValueError):
            measure_three_phase(m, wrong)

    def test_unknown_provenance_rejected(self):
        with self.assertRaises(ValueError):
            ThreePhaseRMS(ident(), 11, 1, 0, "MARKET_CHURN")

    def test_nonfinite_and_invalid_measurements(self):
        for voltage, current, angle in (
            (-1, 1, 0), (11, -1, 0), (11, 1, float('nan')),
            (11, 1, math.pi + .01),
        ):
            with self.subTest((voltage, current, angle)):
                with self.assertRaises(ValueError):
                    ThreePhaseRMS(ident(), voltage, current, angle, SOURCE_EXTERNAL)

    def test_mva_rating_overload_is_diagnostic_not_an_order(self):
        r = self.measure(101, 0)
        self.assertTrue(r.overload_relative_to_rating)
        self.assertAlmostEqual(r.p_pu, 1.01)

    def test_reactive_droop_has_declared_q_base(self):
        self.assertAlmostEqual(reactive_droop_calibration(reactive_droop=.075, q_pu=.6), -.045)
        self.assertAlmostEqual(reactive_droop_calibration(reactive_droop=.10, q_pu=-.6), .06)
        with self.assertRaises(ValueError):
            reactive_droop_calibration(reactive_droop=-.1, q_pu=.5)


class GeneratorBenchTests(unittest.TestCase):
    def setUp(self):
        self.rating = GeneratorRating(BAY, 11.0, 100.0)
        self.config = BenchGeneratorConfig(self.rating)

    def advance(self, bench, bar, cmd, **kwargs):
        return bench.advance(
            bar_index=bar, timestamp=ident(bar).timestamp,
            previous_excitation_command_pu=cmd,
            bus_voltage_pu=.985,
            mechanical_active_power_mw=30.0,
            **kwargs,
        )

    def test_command_changes_independent_field_then_voltage_and_reactive_power(self):
        low = ThreePhaseGeneratorBench(self.config)
        high = ThreePhaseGeneratorBench(self.config)
        lo = hi = None
        for bar in range(1, 31):
            lo = self.advance(low, bar, 1.0)
            hi = self.advance(high, bar, 1.10)
        self.assertGreater(hi.field_actual_pu, lo.field_actual_pu)
        self.assertGreater(hi.electrical.voltage_pu, lo.electrical.voltage_pu)
        self.assertGreater(hi.electrical.q_pu, lo.electrical.q_pu)
        self.assertAlmostEqual(hi.electrical.active_mw, 30)
        self.assertGreater(hi.electrical.reactive_mvar, lo.electrical.reactive_mvar)

    def test_closed_loop_excitation_response(self):
        bench = ThreePhaseGeneratorBench(self.config)
        avr = build_bench_controller(self.rating, .075)
        cmd = 1.0
        values = []
        for bar in range(1, 101):
            row = self.advance(bench, bar, cmd)
            state = avr.step(row.avr_sample, dt=1)
            self.assertEqual(row.electrical.source, SOURCE_BENCH)
            self.assertFalse(state.trip)
            values.append((row.electrical.voltage_pu, row.field_actual_pu, cmd))
            cmd = state.excitation_command_pu
        self.assertGreater(values[-1][1], 1.0)
        self.assertLess(abs(values[-1][0]-1.0), abs(values[0][0]-1.0))

    def test_checkpoint_restart_exact_replay(self):
        bench1 = ThreePhaseGeneratorBench(self.config)
        for bar in range(1, 11):
            self.advance(bench1, bar, 1.02)
        checkpoint = bench1.snapshot()
        bench2 = ThreePhaseGeneratorBench(self.config)
        bench2.restore(checkpoint)
        a = self.advance(bench1, 11, 1.03)
        b = self.advance(bench2, 11, 1.03)
        self.assertEqual(a.electrical, b.electrical)
        self.assertEqual(a.avr_sample, b.avr_sample)

    def test_config_mismatch_cannot_restore(self):
        bench = ThreePhaseGeneratorBench(self.config)
        snap = bench.snapshot()
        changed = ThreePhaseGeneratorBench(replace(self.config, field_tau_s=5.0))
        with self.assertRaises(ValueError):
            changed.restore(snap)

    def test_replayed_bar_rejected(self):
        bench = ThreePhaseGeneratorBench(self.config)
        self.advance(bench, 1, 1.0)
        with self.assertRaises(ValueError):
            self.advance(bench, 1, 1.0)

    def test_negative_and_nonfinite_plant_inputs_rejected(self):
        for kwargs in (
            {"previous_excitation_command_pu": -1},
            {"bus_voltage_pu": float('nan')},
            {"mechanical_active_power_mw": float('inf')},
        ):
            b = ThreePhaseGeneratorBench(self.config)
            args = {
                "bar_index": 1, "timestamp": ident(1).timestamp,
                "previous_excitation_command_pu": 1.0,
                "bus_voltage_pu": 1.0,
                "mechanical_active_power_mw": 30.0,
            }
            args.update(kwargs)
            with self.assertRaises(ValueError):
                b.advance(**args)

    def test_full_q_provenance_is_not_market_data(self):
        bench = ThreePhaseGeneratorBench(self.config)
        row = self.advance(bench, 1, 1.0)
        self.assertEqual(row.avr_sample.source, SOURCE_BENCH)
        self.assertAlmostEqual(
            row.electrical.q_pu, row.electrical.reactive_mvar / self.rating.rated_mva
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
