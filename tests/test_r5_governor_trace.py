"""The governor trace tool records the loops without altering the replay."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from revision2.contracts import IDDecision
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.governor import BAY_GOVERNOR_SPECS
from revision5.topology import GTG2_TECH_TELECOM
from tests_external.test_audit_remediation import signal

_spec = importlib.util.spec_from_file_location(
    "r5_governor_trace", Path(__file__).resolve().parents[1] / "scripts/r5_governor_trace.py")
trace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace)


def _replay(monkeypatch, instrument):
    grid_times = pd.date_range("2024-02-28 00:00", periods=5 * 96, freq="15min", tz="Asia/Kolkata")
    nifty = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(len(grid_times))]
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": grid_times, "close": nifty}),
                                         pd.DataFrame({"timestamp": grid_times, "close": 15.0}))
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    orch = Engine(["INFY"], grid_context_provider=provider, real_plant_dcs=plant,
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  governor_authority="full")
    monkeypatch.setattr(orch.pa, "evaluate", lambda snapshot, cfg: (signal(), []))
    monkeypatch.setattr(orch.id_box, "evaluate", lambda *a, **kw: (IDDecision(True, "fixture", .8, 2, .6), []))
    monkeypatch.setattr(orch.id_box, "_current_regime", lambda *a: "calm")
    tracer = trace.Tracer(orch, plant, [GTG2_TECH_TELECOM]) if instrument else None
    i = np.arange(90)
    close = 1000.0 + 0.05 * i + 0.8 * np.sin(i / 3.0)
    open_ = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01 10:00", periods=90, freq="min", tz="Asia/Kolkata"),
        "open": open_, "close": close, "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3, "volume": 1000.0})
    report = orch.run({"INFY": frame}, warmup=30)
    return tracer, report


def _fingerprint(report):
    return hashlib.sha256(json.dumps(report["trades"], sort_keys=True, default=str).encode()).hexdigest()


def test_tracer_is_pass_through_and_records_every_loop(monkeypatch):
    tracer, traced = _replay(monkeypatch, instrument=True)
    _, plain = _replay(monkeypatch, instrument=False)
    assert _fingerprint(traced) == _fingerprint(plain)
    assert traced["governor_authority"] == plain["governor_authority"]
    assert traced["fills"] > 0
    rows = tracer.rows
    assert rows["entry"] and rows["inner"] and rows["admission"]
    summary = trace._summarize(tracer, traced, [GTG2_TECH_TELECOM], {"completed_trades": 0})
    gains = summary["gain_schedule"]
    assert gains["begin_bar_calls"] > 0
    # Every bar the replay hands the plant a dynamic environment; the R5 controller's
    # snapshot is installed on the bay governor and its gains move with the market.
    assert gains["begin_bar_with_environment"] == traced["plant_dcs_wiring"]["call_counts"]["step_bar"] > 0
    assert gains["apply_runtime_profile_calls"][GTG2_TECH_TELECOM] == gains["begin_bar_with_environment"]
    assert gains["verdict"][GTG2_TECH_TELECOM] == "SCHEDULED"
    orch, plant = tracer.orch, tracer.plant
    profile = plant.dynamic_snapshot.governor_by_bay[GTG2_TECH_TELECOM]
    governor = plant.bays[GTG2_TECH_TELECOM].governor
    assert (governor.runtime_kp, governor.runtime_ki, governor.runtime_kd) == (profile.kp, profile.ki, profile.kd)
    # The same schedule reaches the execution PIDs as a (Kp, Ki, Kd) ratio to the bay's base spec.
    spec = BAY_GOVERNOR_SPECS[GTG2_TECH_TELECOM]
    scale = (profile.kp / spec.kp, profile.ki / spec.ki, profile.kd / spec.kd)
    assert orch._gain_scale_for("INFY") == pytest.approx(scale) != pytest.approx((1.0, 1.0, 1.0))
    assert orch.mpc.gain_scale == pytest.approx(scale)
    for row in rows["entry"]:
        # trend_overspeed: limit = dynamic_z - 2 * base_z, with base_z now scheduled per bar.
        base_z = row["dynamic_z"] - row["feedback_offset"] + row["droop_penalty"]
        assert spec.base_z - 0.5 - 1e-12 <= base_z <= spec.base_z + 0.1 + 1e-12
        assert abs(row["limit"] - (row["dynamic_z"] - 2.0 * base_z)) < 1e-9
