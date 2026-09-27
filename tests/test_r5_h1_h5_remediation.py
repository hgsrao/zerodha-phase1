"""Regression tests for the R5 audit remediation items H2-H5.

Every test drives the real orchestrator, the real native R5 plant and the offline
CostedPaperBrokerAdapter.  Only the Nifty/VIX grid is a synthetic, explicit fixture.
"""
import pandas as pd
import pytest

from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision2_external.grid_context import SealedGridContextProvider
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.topology import BAY_IDS, symbols_for_bay


def _paper_engine(symbols, vix=15.0):
    times = pd.date_range("2024-03-01 09:15", periods=100, freq="15min", tz="Asia/Kolkata")
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": 21000.0}),
                                         pd.DataFrame({"timestamp": times, "close": vix}))
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    orch = Engine(symbols, grid_context_provider=provider, real_plant_dcs=plant,
                  plant_control_mode="PAPER_APPLY")
    ts = times[-1] + pd.Timedelta(minutes=1)
    limit = float(orch.safety_contract.values["max_gross_exposure_fraction"])
    for _ in range(10):                      # ECS ramps up from HOLD in bounded steps
        orch._plant_control_shadow_step(ts, limit)
    return orch, ts, limit


# --------------------------------------------------------------------- H2 exposure

def test_h2_plant_cap_reaches_the_configured_gross_limit_across_evaluations():
    """Gross exposure is enforced once.  Re-evaluating plant control after every admission
    (as a new portfolio tick does) must not shrink the reachable exposure to ~50% of the limit."""
    symbols = [symbols_for_bay(bay)[0] for bay in BAY_IDS]
    orch, ts, limit = _paper_engine(symbols)
    budget = orch._equity() * limit
    for _ in range(4):
        for symbol in symbols:
            orch._plant_control_shadow_step(ts, limit)
            quantity = orch._paper_plant_entry_limit(symbol, 10**9, 100.0, ts)
            if quantity > 0:
                trade = orch.open_trades.setdefault(symbol, {"quantity": 0, "entry_price": 100.0})
                trade["quantity"] += quantity
                orch._last_close[symbol] = 100.0
    gross = orch._gross_exposure_notional()
    assert gross >= 0.85 * budget
    assert gross <= budget + 100.0           # never above the hard limit (one lot of rounding)


def test_h2_plant_cap_still_enforces_the_per_bay_dispatch_ceiling():
    """Removing the double count must not remove the bay concentration limit."""
    symbols = list(symbols_for_bay(BAY_IDS[0])[:3])
    orch, ts, limit = _paper_engine(symbols)
    budget = orch._equity() * limit
    for _ in range(4):
        for symbol in symbols:
            orch._plant_control_shadow_step(ts, limit)
            quantity = orch._paper_plant_entry_limit(symbol, 10**9, 100.0, ts)
            if quantity > 0:
                trade = orch.open_trades.setdefault(symbol, {"quantity": 0, "entry_price": 100.0})
                trade["quantity"] += quantity
                orch._last_close[symbol] = 100.0
    ceiling = orch.plant_control.dispatch_controller.merit_source.max_ceiling
    assert orch._gross_exposure_notional() <= ceiling * budget + 100.0


def test_h2_ecs_demand_is_a_loading_reference_not_a_second_headroom():
    """Exposure no longer lowers the ECS demand reference; it is reported only."""
    symbols = [symbols_for_bay(BAY_IDS[0])[0]]
    orch, ts, limit = _paper_engine(symbols)
    orch.open_trades[symbols[0]] = {"quantity": 1000, "entry_price": 100.0}
    orch._last_close[symbols[0]] = 100.0
    orch._plant_control_shadow_step(ts, limit)
    ecs = orch._paper_plant_snapshot.ecs
    assert ecs.plant_demand_reference_pu == pytest.approx(1.0)


# ------------------------------------------------------------- H3 / H4 time handling

def _shadow_engine():
    return Engine(["INFY"])


def test_h3_replay_event_time_is_the_bar_time_never_the_wall_clock():
    event = Engine._replay_event_time(pd.Timestamp("2024-03-01 10:15", tz="Asia/Kolkata"))
    assert event.isoformat() == "2024-03-01T10:15:00+05:30"
    for bad in ("not-a-timestamp", None, pd.NaT):
        with pytest.raises(ValueError):
            Engine._replay_event_time(bad)


def test_h3_orchestrator_has_no_wall_clock_fallback():
    import ast
    import inspect
    import revision2_external.orchestrator as module
    tree = ast.parse(inspect.getsource(module))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr in {"now", "utcnow", "today"}]
    assert not calls


def test_h4_trading_window_fails_closed_and_records_the_fault():
    orch = _shadow_engine()
    assert orch._in_trading_window("2024-03-01 10:15") is True
    assert orch._in_trading_window("2024-03-01 20:15") is False
    assert orch._in_trading_window("garbage") is False
    assert orch._controller_event_counts["TRADING_WINDOW_FAULT"] == 1


def test_h4_trading_window_uses_the_exchange_clock_for_aware_timestamps():
    orch = _shadow_engine()
    utc_morning = pd.Timestamp("2024-03-01 04:45", tz="UTC")          # 10:15 IST
    assert orch._in_trading_window(str(utc_morning)) is True
    utc_evening = pd.Timestamp("2024-03-01 14:45", tz="UTC")          # 20:15 IST
    assert orch._in_trading_window(str(utc_evening)) is False


# ---------------------------------------------------------- H5 exit bookkeeping order

def _open(orch, with_controller_state=True, candidate=True):
    from tests.test_r5_paper_apply_blocker_closure import open_pos
    trade = open_pos(orch)
    if candidate:
        trade["candidate_id"] = "cand-1"
        orch.entry_expectancy_ledger.observe_fill(dict(
            candidate_id="cand-1", symbol="INFY", side="BUY", timestamp="2024-01-01 10:00",
            pa_confidence=.5, id_confidence=.5, studies_confidence=.5, atr_fraction=.01, target_r=2))
    if not with_controller_state:
        orch._exit_controller_states.pop("INFY")
    return trade


def test_h5_exit_without_controller_state_closes_the_ledger_and_keeps_the_candidate():
    orch = _shadow_engine()
    trade = _open(orch, with_controller_state=False)
    orch._execute_exit("INFY", "2024-01-01 10:05", trade, 110.0, "target")
    assert "INFY" not in orch.open_trades
    assert orch.broker.get_position("INFY")["quantity"] == 0
    assert len(orch.completed_trades) == 1 and orch.completed_trades[0]["bars_held"] is None
    assert orch._controller_event_counts["ENTRY_EXPECTANCY_OUTCOME_UNAVAILABLE"] == 1
    assert orch.entry_expectancy_ledger.summary() == {"pending_candidates": 1, "resolved_candidates": 0}


def test_h5_telemetry_fault_after_fill_still_leaves_ledger_and_feedback_consistent():
    orch = _shadow_engine()
    trade = _open(orch)
    dispatcher = orch.plant_control.dispatch_controller.merit_source

    def boom(*args, **kwargs):
        raise RuntimeError("simulated research-telemetry defect")

    orch.closed_loop.record_outcome = boom
    with pytest.raises(RuntimeError, match="research-telemetry"):
        orch._execute_exit("INFY", "2024-01-01 10:05", trade, 110.0, "target")
    # The defect propagates, but the authoritative books already match the flat broker.
    assert "INFY" not in orch.open_trades and "INFY" not in orch._exit_controller_states
    assert orch.broker.get_position("INFY")["quantity"] == 0
    assert len(orch.completed_trades) == 1
    assert sum(len(h) for h in dispatcher.trade_history_r.values()) == 1
    assert abs(orch.broker.realized_pnl - sum(t["pnl"] for t in orch.completed_trades)) < 1e-9


@pytest.mark.parametrize("outcome", [
    dict(),                                                                   # all fields missing
    dict(net_pnl=float("nan"), pnl=1, costs=1, exit_reason="x", bars_held=1),
    dict(net_pnl=1, pnl=1, costs=1, exit_reason="x", bars_held=None),
    dict(net_pnl=1, pnl=1, costs=1, exit_reason="x", bars_held=2.5),
    dict(net_pnl=1, pnl=1, costs=1, exit_reason="", bars_held=1),
    dict(net_pnl=None, pnl=1, costs=1, exit_reason="x", bars_held=1),
])
def test_h5_invalid_outcome_never_consumes_the_pending_candidate(outcome):
    from revision2_external.entry_expectancy_evidence import CausalEntryExpectancyLedger
    ledger = CausalEntryExpectancyLedger()
    ledger.observe_fill(dict(candidate_id="1", symbol="S", side="BUY", timestamp="2025-01-01",
                             pa_confidence=.5, id_confidence=.5, studies_confidence=.5,
                             atr_fraction=.01, target_r=2))
    with pytest.raises(ValueError):
        ledger.record_outcome(dict(candidate_id="1", **outcome))
    assert ledger.summary()["pending_candidates"] == 1
    resolved = ledger.record_outcome(dict(candidate_id="1", net_pnl=1.0, pnl=2.0, costs=1.0,
                                          exit_reason="target", bars_held=3))
    assert resolved["bars_held"] == 3 and ledger.summary() == {"pending_candidates": 0, "resolved_candidates": 1}
