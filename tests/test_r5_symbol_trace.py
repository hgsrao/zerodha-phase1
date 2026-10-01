"""R5 single-symbol trace: passive, and every trade carries its entry PID reading and hold decisions."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from revision2.contracts import IDDecision
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from tests_external.test_audit_remediation import signal

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("r5_symbol_trace", ROOT / "scripts/diagnostics/r5_symbol_trace.py")
trace_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trace_mod)


def _run(instrument=None, n=240, seed=3):
    grid = pd.date_range("2024-02-28 00:00", periods=5 * 96, freq="15min", tz="Asia/Kolkata")
    nifty = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(len(grid))]
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": grid, "close": nifty}),
                                         pd.DataFrame({"timestamp": grid, "close": 15.0}))
    orch = Engine(["INFY"], grid_context_provider=provider,
                  real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  governor_authority="full", governor_position_control="legacy", telemetry_mode="compact")
    orch.pa.evaluate = lambda snapshot, cfg: (signal(), [])
    orch.id_box.evaluate = lambda *a, **kw: (IDDecision(True, "fixture", .8, 2, .6), [])
    orch.id_box._current_regime = lambda *a: "calm"
    recorder = trace_mod.funnel.GateRecorder()
    orch.gate_observer = recorder
    if instrument is not None:
        instrument(orch)
    i = np.arange(n)
    close = 1000.0 + 0.05 * i + 0.8 * np.sin(i / 3.0) + np.cumsum(np.random.default_rng(seed).normal(0, 0.6, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01 09:30", periods=n, freq="min", tz="Asia/Kolkata"),
        "open": open_, "close": close, "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3, "volume": 1000.0})
    return orch.run({"INFY": frame}, warmup=30), recorder


def test_trace_is_passive_and_links_entry_and_hold_events_to_each_trade():
    tap = trace_mod.EventTap()
    traced, recorder = _run(tap)
    plain, _ = _run(None)
    assert traced["trades"] and trace_mod.funnel.ledger_sha256(traced["trades"]) == \
        trace_mod.funnel.ledger_sha256(plain["trades"])
    tr = trace_mod.build_trace("INFY", traced["trades"], tap.events, recorder.decisions)
    assert len(tr["trades"]) == len(traced["trades"])
    for x in tr["trades"]:
        assert "ENTRY_CONFIDENCE_THROTTLE" in x["entry"] and "GOVERNOR_ENTRY_DECISION" in x["entry"]
        assert any(h["event_type"] == "GOVERNOR_POSITION_DECISION" for h in x["hold"])
        assert x["realised_r"] is not None
    assert tr["entry_pid"] and all("entry_timing_multiplier" in p for p in tr["entry_pid"])
    assert any(k.startswith("governor|") for k in tr["choke_points"])
    trace_mod.print_trace(tr)
