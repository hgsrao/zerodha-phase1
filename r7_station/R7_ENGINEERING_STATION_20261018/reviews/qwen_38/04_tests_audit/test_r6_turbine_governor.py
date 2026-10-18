import unittest
from dataclasses import replace
from math import pi

from revision6_avr.r6_turbine_governor import (
    BAYS, TurbineGovernorConfig, TurbineGovernorUnit, inertia_constant_h,
    repository_defaults, speed_droop_command,
)


class GovernorTests(unittest.TestCase):
    def test_five_bay_settings_and_immutable_droop(self):
        cfg = repository_defaults()
        self.assertEqual([cfg[b].droop_r for b in BAYS], [.04] * 5)
        self.assertEqual([cfg[b].inertia_m for b in BAYS], [8., 8., 5., 5., 5.])
        with self.assertRaises(Exception): cfg[BAYS[0]].droop_r = .05
        self.assertEqual(replace(cfg[BAYS[0]], droop_r=.05).droop_r, .05)

    def test_droop_sign_and_limits(self):
        kw = dict(p_ref_pu=.5, droop_r=.04, p_min_pu=0., p_max_pu=1.)
        self.assertAlmostEqual(speed_droop_command(speed_pu=.99, **kw), .75)
        self.assertAlmostEqual(speed_droop_command(speed_pu=1.01, **kw), .25)
        self.assertEqual(speed_droop_command(speed_pu=.95, **kw), 1.)

    def test_m_j_formula_and_consistency(self):
        s = 100e6; j = 4 * 2 * s / (100 * pi) ** 2
        self.assertAlmostEqual(inertia_constant_h(j_kg_m2=j, s_base_va=s, f_nominal_hz=50, pole_pairs=1), 4.)
        c = TurbineGovernorConfig.from_inertia(j_kg_m2=j, s_base_va=s, pole_pairs=1, bay_id=BAYS[0], machine_kind="GAS_TURBINE", droop_r=.04, damping_d=1., turbine_response_tau=.3)
        self.assertAlmostEqual(c.inertia_m, 8.)
        with self.assertRaises(ValueError): replace(c, inertia_m=7.)

    def test_swing_sign_and_separate_turbine_lag(self):
        u = TurbineGovernorUnit(repository_defaults()[BAYS[0]], p_mech_pu=.5)
        r = u.step(dt_s=.001, p_ref_pu=.5, p_elec_pu=.6, dt_max_s=.001)
        self.assertLess(r["speed_pu"], 1.)
        self.assertNotEqual(r["mechanical_power_pu"], r["p_command_pu"])

    def test_checkpoint_replay(self):
        c = repository_defaults()[BAYS[2]]; a = TurbineGovernorUnit(c, p_mech_pu=.5)
        a.step(dt_s=.1, p_ref_pu=.5, p_elec_pu=.6); snap = a.snapshot(); b = TurbineGovernorUnit(c); b.restore(snap)
        self.assertEqual(a.step(dt_s=.1, p_ref_pu=.5, p_elec_pu=.6), b.step(dt_s=.1, p_ref_pu=.5, p_elec_pu=.6))


if __name__ == "__main__": unittest.main()
