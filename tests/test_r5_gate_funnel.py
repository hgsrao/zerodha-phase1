"""R5 entry-gate funnel: the observer is passive, stages nest, and the side table is computed."""
import importlib.util
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from revision2.contracts import IDDecision
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from tests_external.test_audit_remediation import signal

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("r5_gate_funnel", ROOT / "scripts/diagnostics/r5_gate_funnel.py")
funnel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(funnel)


def _replay(observer=None, n=240, seed=3):
    grid = pd.date_range("2024-02-28 00:00", periods=5 * 96, freq="15min", tz="Asia/Kolkata")
    nifty = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(len(grid))]
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": grid, "close": nifty}),
                                         pd.DataFrame({"timestamp": grid, "close": 15.0}))
    orch = Engine(["INFY"], grid_context_provider=provider,
                  real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  governor_authority="full", governor_position_control="legacy")
    orch.pa.evaluate = lambda snapshot, cfg: (signal(), [])
    orch.id_box.evaluate = lambda *a, **kw: (IDDecision(True, "fixture", .8, 2, .6), [])
    orch.id_box._current_regime = lambda *a: "calm"
    orch.gate_observer = observer
    i = np.arange(n)
    close = 1000.0 + 0.05 * i + 0.8 * np.sin(i / 3.0) + np.cumsum(np.random.default_rng(seed).normal(0, 0.6, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01 09:30", periods=n, freq="min", tz="Asia/Kolkata"),
        "open": open_, "close": close, "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3, "volume": 1000.0})
    return orch.run({"INFY": frame}, warmup=30), frame


@pytest.mark.parametrize("seed", [3, 11])
def test_gate_observer_is_passive_and_stages_nest(seed):
    recorder = funnel.GateRecorder()
    observed, _ = _replay(recorder, seed=seed)
    plain, _ = _replay(None, seed=seed)
    assert observed["trades"] == plain["trades"] and observed["trades"]       # nothing changed
    assert funnel.ledger_sha256(observed["trades"]) == funnel.ledger_sha256(plain["trades"])
    counts = {stage: sum(1 for e in recorder.events if e[0] == stage) for stage in funnel.STAGES}
    assert counts["filled"] == len(observed["trades"])
    chain = [s for s in funnel.STAGES if s != "pa_birth"]
    for upstream, downstream in zip(chain, chain[1:]):
        assert counts[upstream] >= counts[downstream], (upstream, downstream, counts)
    assert 0 < counts["pa_birth"] <= counts["pa_directional"]


def test_pa_birth_marks_the_first_bar_of_each_same_side_run():
    r = funnel.GateRecorder()
    for bar, side in [(10, "BUY"), (11, "BUY"), (12, "SELL"), (14, "SELL"), (15, "SELL")]:
        r.on_gate("pa_directional", "INFY", f"t{bar}", bar, side)
    births = [(e[3], e[4]) for e in r.events if e[0] == "pa_birth"]
    assert births == [(10, "BUY"), (12, "SELL"), (14, "SELL")]              # a gap starts a new run
    r.on_gate("id_approved", "INFY", "t15", 15, "SELL")                      # tagged with the live run
    r.on_gate("id_approved", "INFY", "t15", 15, "BUY")                       # no live BUY run
    assert r.events[-2][5] == "INFY|14|SELL" and r.events[-1][5] is None


def test_analyse_counts_sides_and_measures_edge_against_other_sessions():
    audit = funnel._load("scripts/diagnostics/r5_entry_edge_audit.py", "audit_for_funnel_test")
    day = [100.0 + 0.1 * k for k in range(120)]
    frames = pd.concat([pd.DataFrame({
        "timestamp": pd.date_range(f"{d} 09:15", periods=120, freq="min", tz="Asia/Kolkata"),
        "open": day, "high": [x + 0.05 for x in day], "low": [x - 0.05 for x in day], "close": day, "volume": 1.0})
        for d in ("2024-03-01", "2024-03-04")], ignore_index=True)
    bars = {"INFY": audit.SymbolBars(frames)}
    ts = str(bars["INFY"].ts.iloc[20])
    events = [("pa_birth", "INFY", ts, 20, "SELL", "r1"), ("pa_birth", "INFY", ts, 20, "BUY", "r2"),
              ("pa_birth", "INFY", ts, 20, "SELL", "r3"),
              ("filled", "INFY", ts, 20, "SELL", "r1"), ("filled", "INFY", ts, 20, "SELL", "r1"),
              ("filled", "INFY", ts, 20, "BUY", "r2")]
    table = funnel.analyse([events], [bars], controls=5, stop_atr_mult=1.2)
    f = table["filled"]
    assert f["SELL"]["bars"] == 2 and f["SELL"]["runs"] == 1 and f["BUY"]["runs"] == 1
    assert f["sell_share"] == pytest.approx(2 / 3)
    assert f["SELL"]["cumulative_from_birth_runs"] == pytest.approx(0.5)       # 1 of 2 SELL runs filled
    assert f["BUY"]["cumulative_from_birth_runs"] == pytest.approx(1.0)
    assert table["pa_birth"]["sell_share"] == pytest.approx(2 / 3)
    # The same drift on both days: a random time-matched entry does as well, so edge is ~0.
    assert f["BUY"]["horizons"]["15"]["edge_r"]["mean"] == pytest.approx(0.0, abs=1e-6)
    assert set(f["BUY"]["horizons"]["15"]["ladder"]) == {"0.5R", "1R"}
    assert table["pa_directional"]["BUY"]["bars"] == 0
    funnel.print_table(table)                                               # renders without error


def test_ledger_parity_reports_field_level_differences():
    trade = {"trade_id": "a", "symbol": "INFY", "side": "SELL", "entry_timestamp": "t", "entry_price": 1.0,
             "quantity": 5, "exit_timestamp": "u", "reason": "stop", "exit_price": 0.9, "costs": 0.1, "net_pnl": 0.4}
    assert funnel.ledger_parity([trade], [dict(trade)]) == []
    assert funnel.ledger_parity([dict(trade, quantity=6)], [trade]) == ["trade 0 quantity: 6 != 5"]
    assert funnel.ledger_parity([], [trade])[0] == "trade count 0 != 1"


def test_stratified_sample_keeps_every_stratum():
    rng = np.random.default_rng(0)
    items = [(0, "A", f"2024-03-0{d} 10:00", ) for d in (1, 4) for _ in range(500)] + [(0, "B", "2024-03-05 10:00")] * 5
    picked = funnel.stratified(items, 60, rng)
    assert len(picked) <= 60 and {(it[1], it[2][:10]) for it in picked} == {("A", "2024-03-01"), ("A", "2024-03-04"),
                                                                            ("B", "2024-03-05")}


@pytest.mark.parametrize("seed", [3, 11])
def test_choke_point_decisions_reconcile_with_gate_events(seed):
    recorder = funnel.GateRecorder()
    observed, _ = _replay(recorder, seed=seed)
    plain, _ = _replay(None, seed=seed)
    assert funnel.ledger_sha256(observed["trades"]) == funnel.ledger_sha256(plain["trades"])
    gate = Counter(e[0] for e in recorder.events)
    passed = Counter(d[0] for d in recorder.decisions if d[5])
    seen = Counter(d[0] for d in recorder.decisions)
    assert seen["id"] == gate["eligible"] and passed["id"] == gate["id_approved"]
    assert seen["governor"] == gate["pre_sizing_ok"] and passed["governor"] == gate["governor_admitted"]
    assert passed["risk"] == gate["risk_checked"] and passed["pre_submit"] == gate["gates_passed"]
    assert passed["pre_submit"] <= seen["pre_submit"] <= gate["risk_checked"]   # order construction sits between
    for point, *_rest, feats in recorder.decisions:
        assert {"book_buy", "book_sell"} <= set(feats)
        if point == "governor":
            assert {"signed_z", "signed_z_limit", "limiter_FSRN", "controlling_limiter"} <= set(feats)


def test_plain_gate_observer_without_on_decision_still_works():
    class GatesOnly:
        def __init__(self):
            self.events = []

        def on_gate(self, *event):
            self.events.append(event)
    observer = GatesOnly()
    observed, _ = _replay(observer)
    assert observer.events and observed["trades"] == _replay(None)[0]["trades"]


def test_anatomy_counts_reasons_by_side_and_splits_feature_distributions():
    decisions = [
        ("id", "INFY", "t1", 1, "BUY", False, "confidence 0.41 below entry threshold 0.55", "r1",
         {"pa_confidence": 0.41, "pa_quality_band": "amber"}),
        ("id", "INFY", "t2", 2, "BUY", False, "confidence 0.30 below entry threshold 0.55", "r1",
         {"pa_confidence": 0.30, "pa_quality_band": "amber"}),
        ("id", "INFY", "t3", 3, "BUY", True, "approved", "r2", {"pa_confidence": 0.70, "pa_quality_band": "green"}),
        ("id", "INFY", "t4", 4, "SELL", True, "approved", "r3", {"pa_confidence": 0.80, "pa_quality_band": "green"}),
        ("pre_submit", "INFY", "t5", 5, "BUY", False, "Gate05ConcurrentPositions", "r2", {"open_positions_count": 3}),
        ("pre_submit", "INFY", "t6", 6, "SELL", True, "EntryDecisionEngine", "r3", {"open_positions_count": 1}),
        ("governor", "INFY", "t7", 7, "FLAT", False, "ignored", None, {}),
    ]
    anat = funnel.anatomy([decisions], [{}], controls=5, stop_atr_mult=1.2, measure=False)
    buy = anat["id"]["BUY"]
    assert buy["total"] == 3
    assert buy["reasons"]["confidence # below entry threshold #"] == {"bars": 2, "share": pytest.approx(2 / 3), "runs": 1}
    assert buy["reasons"]["PASS"]["bars"] == 1 and anat["id"]["SELL"]["reasons"]["PASS"]["bars"] == 1
    assert buy["features"]["rejected"]["numeric"]["pa_confidence"]["p50"] == pytest.approx(0.355)
    assert buy["features"]["admitted"]["categorical"]["pa_quality_band"] == {"green": 1}
    assert anat["pre_submit"]["BUY"]["reasons"]["Gate05ConcurrentPositions"]["bars"] == 1
    assert "governor" not in anat                                         # only FLAT: nothing by side
    funnel.print_anatomy(anat)
