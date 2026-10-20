"""Plant physics wired into trading: synchronizer, machine scan, relays, 86 lockout, Mark V cross-check.

Synthetic bars only; no market data. Observe must leave trades identical to off; enforce must
gate dispatch on the startup sequence and act on trips.
"""
import unittest

import numpy as np
import pandas as pd

from revision2.contracts import IDDecision
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.plant_physics_runtime import BayInputs, PlantPhysicsRuntime, settings_report
from revision5.startup_synchronization import StartupState
from tests_external.test_audit_remediation import signal

SYMBOLS = ["INFY", "TCS"]


def _frame(seed):
    rng = np.random.default_rng(seed)
    per = 120
    ts = (list(pd.date_range("2024-03-04 09:16", periods=per, freq="min", tz="Asia/Kolkata"))
          + list(pd.date_range("2024-03-05 09:16", periods=per, freq="min", tz="Asia/Kolkata")))
    c = 1000 * np.exp(np.cumsum(rng.normal(0, .003, 2 * per)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.001,
                         "low": np.minimum(o, c) * .999, "close": c, "volume": 1000.})


def _run(physics):
    gt = pd.date_range("2024-03-01 09:15", "2024-03-05 15:30", freq="1min", tz="Asia/Kolkata")
    gt = gt[(gt.hour * 60 + gt.minute >= 555) & (gt.hour * 60 + gt.minute <= 930)]
    provider = SealedGridContextProvider(
        pd.DataFrame({"timestamp": gt, "close": 21000 * (1 + 0.0005 * np.sin(np.arange(len(gt)) / 30))}),
        pd.DataFrame({"timestamp": gt, "close": 15.0}))
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    orch = Engine(SYMBOLS, grid_context_provider=provider, real_plant_dcs=plant,
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  governor_authority="full", plant_engine_mode="ENGINE_B", plant_physics_mode=physics)
    orch.pa.evaluate = lambda s, cfg: (signal(symbol=s.symbol), [])
    orch.id_box.evaluate = lambda *a, **k: (IDDecision(True, "test", .8, 2., .6), [])
    report = orch.run({s: _frame(i + 3) for i, s in enumerate(SYMBOLS)}, warmup=30)
    return orch, report


def _trade_key(report):
    return [(t.get("symbol"), round(float(t.get("pnl", 0.0)), 6)) for t in report["trades"]]


class PlantPhysicsStandaloneTests(unittest.TestCase):
    def setUp(self):
        self.plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
        self.rt = PlantPhysicsRuntime(self.plant, mode="enforce", exhaust_spread_trip=1.0,
                                      fsrt_drawdown_span=0.05)
        self.ts = pd.Timestamp("2024-03-04 09:16", tz="Asia/Kolkata")

    def _bars(self, n, **inputs):
        base = dict(bar_present=True, vibration_ratio=0.2, velocity=1.0, exhaust_spread=0.0,
                    broker_notional=0.0)
        base.update(inputs)
        for i in range(n):
            self.rt.step_bar(self.ts + pd.Timedelta(minutes=i),
                             {b: BayInputs(**base) for b in self.plant.bays}, 0.0)

    def test_every_unit_synchronizes_and_reaches_dispatch_ready(self):
        self.rt.begin_session(self.ts)
        self._bars(6)
        for bay_id, bay in self.plant.bays.items():
            self.assertEqual(bay.startup_sequencer.state, StartupState.DISPATCH_READY, bay_id)
            self.assertFalse(self.plant.electrical_network.unit_breaker(bay_id).lockout_86, bay_id)
        self.assertGreater(self.rt.counts.get("52G_CLOSED_SYNCHRONIZED", 0), 0)
        self.assertGreater(self.rt.counts.get("MACHINE_SCAN_STEP", 0), 0)

    def test_vibration_trip_opens_the_breaker(self):
        self.rt.begin_session(self.ts)
        self._bars(6)
        self._bars(3, vibration_ratio=5.0)         # 2 + 4*5 = 22 mm/s, beyond the trip
        self.assertTrue(any(k.startswith("TRIP:") for k in self.rt.counts), self.rt.counts)
        self.assertTrue(any(self.plant.bays[b].startup_sequencer.state == StartupState.TRIPPED
                            for b in self.plant.bays))

    def test_settings_are_reported(self):
        report = settings_report()
        for key in ("SYNCHROCHECK_25", "AUTO_SYNCHRONIZER_25A", "IDMT_51", "DIFFERENTIAL_87G", "DISTANCE_21"):
            self.assertIn(key.lower(), report)
        self.assertIn("NOT_CALIBRATED", report["status"])

    def test_off_mode_cannot_build_a_runtime(self):
        with self.assertRaises(ValueError):
            PlantPhysicsRuntime(self.plant, mode="off", exhaust_spread_trip=1.0, fsrt_drawdown_span=0.05)


class PlantPhysicsReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.off = _run("off")
        cls.observe = _run("observe")
        cls.enforce = _run("enforce")

    def test_observe_leaves_trades_identical_to_off(self):
        self.assertEqual(_trade_key(self.off[1]), _trade_key(self.observe[1]))
        self.assertEqual(self.off[1]["fills"], self.observe[1]["fills"])

    def test_observe_runs_every_physics_component(self):
        orch = self.observe[0]
        events = {e["event_type"] for e in orch.controller_telemetry}
        self.assertIn("PLANT_PHYSICS_BAR", events)
        self.assertIn("MARK_V_CROSSCHECK", events)
        counts = orch.plant_physics.counts
        for key in ("52G_CLOSED_SYNCHRONIZED", "SYNC_STEP", "MACHINE_SCAN_STEP", "BAR"):
            self.assertGreater(counts.get(key, 0), 0, key)
        bars = [e for e in orch.controller_telemetry if e["event_type"] == "PLANT_PHYSICS_BAR"]
        relays = [r for e in bars for b in (e.get("bays") or {}).values() for r in (b.get("relays") or {})]
        self.assertTrue({"51", "87G", "21"} <= set(relays))
        self.assertGreater(sum(orch._markv_counts.values()), 0)

    def test_observe_physics_acts_on_a_shadow_plant_only(self):
        orch = self.observe[0]
        self.assertIsNot(orch.plant_physics.plant, orch.real_plant_dcs)

    def test_enforce_acts_on_the_live_plant_and_still_dispatches(self):
        orch, report = self.enforce
        self.assertIs(orch.plant_physics.plant, orch.real_plant_dcs)
        self.assertGreater(report["fills"], 0)

    def test_physics_requires_the_paper_apply_plant(self):
        with self.assertRaises(ValueError):
            Engine(SYMBOLS, plant_physics_mode="observe")


if __name__ == "__main__":
    unittest.main()
