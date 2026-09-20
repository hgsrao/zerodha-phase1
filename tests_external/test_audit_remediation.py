"""Regression tests for the supplied Revision 4 audit; paper only."""
from dataclasses import replace
import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig, PASignal, IDDecision, TradePlan
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2_external.dynamic_parameter_controller import DynamicParameterController
from revision2_external.position_sizing_pyportfolioopt import PyPortfolioOptPositionManagerBox
from revision2_external.final_execution_controller import FinalExecutionController
from revision2_external.composite_study_signal import CompositeStudySignal
from revision2.boxes import SafetyGatesTargetBox
from gates_framework import Gate03DailyLossHalt, SafetyGateConfig

def config(**overrides):
    r = CanonicalParameterRegistry()
    values = {n: s.default for n, s in r.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=r.FROZEN_IDENTITY_SHA256)

def signal(symbol="INFY", direction=1, confidence=.8):
    return PASignal(symbol=symbol, timestamp="2024-01-01 10:00", direction=direction,
                    confidence=confidence, momentum=.5, volatility=.01,
                    vwap_deviation=.1, volume_confirmation=.2, exit_confidence=.8)

def open_position(orch, side="BUY", price=100., quantity=10):
    fill = orch.broker.place_order("INFY", side, quantity, "MARKET", price,
                                  orch.safety_contract.as_dict(), orch.registry)
    assert fill["passed"]
    entry = fill["filled_price"]
    stop, target = (entry-5, entry+10) if side=="BUY" else (entry+5, entry-10)
    trade = dict(side=side, entry_price=entry, quantity=quantity, stop_price=stop,
                 target_price=target, minimum_hold_bars=2, maximum_hold_bars=60,
                 entry_timestamp="2024-01-01 10:00", entry_atr=1.,
                 planned_entry_price=entry, planned_stop_price=stop,
                 planned_target_price=target)
    orch.open_trades["INFY"]=trade
    orch._exit_controller_states["INFY"]=orch.exit_controller.open_position(side, entry, stop, target,60)
    return trade

@pytest.mark.parametrize("reason", ["stop", "stop_gap", "max_hold", "regime_stressed_exit", "mis_session_close"])
def test_c2_c3_profitable_exit_breaks_loss_streak(reason):
    orch=Engine(["INFY"])
    trade=open_position(orch)
    orch.symbol_consecutive_losses["INFY"]=1
    orch._execute_exit("INFY","2024-01-01 10:02",trade,110.,reason)
    assert orch.completed_trades[-1]["net_pnl"]>0
    assert orch.symbol_consecutive_losses["INFY"]==0
    assert not orch.symbol_tripped.get("INFY",False)

def test_c3_two_net_losses_trip_even_when_reason_is_target():
    orch=Engine(["INFY"])
    for _ in range(2):
        trade=open_position(orch)
        orch._execute_exit("INFY","2024-01-01 10:02",trade,90.,"target")
    assert orch.symbol_consecutive_losses["INFY"]==2
    assert orch.symbol_tripped["INFY"]

def test_c5_costs_booked_at_each_fill_and_reconcile_to_net_equity():
    orch=Engine(["INFY"])
    trade=open_position(orch)
    entry_cost=orch._leg_cost(trade["entry_price"],10,"BUY")
    assert orch._equity()==pytest.approx(orch.starting_equity-entry_cost, rel=0, abs=1e-8)
    orch._execute_exit("INFY","2024-01-01 10:02",trade,95.,"stop")
    assert orch._equity()==pytest.approx(orch.starting_equity+orch.completed_trades[-1]["net_pnl"], rel=0, abs=1e-8)
    assert orch._mark_to_market_equity()==pytest.approx(orch._equity())

def test_c4_gate03_counts_realized_and_unrealized_once():
    orch=Engine(["INFY"])
    orch.broker.realized_pnl=-100.
    trade=open_position(orch)
    orch._last_close["INFY"]=trade["entry_price"]-5.
    state=orch._system_state()
    fees=orch._leg_cost(trade["entry_price"],10,"BUY")
    assert state.daily_realized_loss==pytest.approx(100+fees)
    assert state.daily_unrealized_loss==pytest.approx(50)
    gate=Gate03DailyLossHalt(SafetyGateConfig(daily_loss_halt_threshold=200))
    assert gate.evaluate(state).passed
    orch._last_close["INFY"]=trade["entry_price"]-15
    assert not gate.evaluate(orch._system_state()).passed

def test_c6_prior_session_loss_is_not_today_loss():
    box=SafetyGatesTargetBox()
    plan=TradePlan("BUY",100.,95.,110.,2,60)
    ok,reason,_=box.evaluate_post_sizing([1_000_000.,940_000.],plan,1,config(),
                                        daily_loss=0.)
    assert ok, reason
    ok,_,_=box.evaluate_post_sizing([1_000_000.,940_000.],plan,1,config(),
                                   daily_loss=50_001.)
    assert not ok

def test_c11_gain_schedule_updates_existing_pid():
    box=SimplePIDModelPredictiveControlBox()
    cfg=config()
    box.build_plan(signal(),IDDecision(True,"ok",.8,2.,.6),100.,1.,cfg)
    pid=box._entry_pids["INFY"]
    first=pid.tunings
    box.build_plan(signal(confidence=.55),IDDecision(True,"ok",.55,2.,.6),100.,1.,cfg)
    expected=DynamicParameterController.get_tier3_pid_schedule(
        cfg.require("pid_kp_entry"),cfg.require("pid_ki_entry"),cfg.require("pid_kd_entry"),-.25)
    assert box._entry_pids["INFY"] is pid
    assert pid.tunings==pytest.approx(expected)
    assert pid.tunings != first

@pytest.mark.parametrize("symbol,lot",[("INFY",5),("TCS",10)])
def test_c12_lots_stay_valid_after_safety_cap(symbol,lot):
    plan=TradePlan("BUY",100.,95.,110.,2,60)
    quantity,_=PyPortfolioOptPositionManagerBox().size(
        plan,100_000.,1.,config(lot_size_by_symbol={"INFY":5,"TCS":10}),
        symbol,{"INFY":.5,"TCS":.5},.137)
    assert quantity>0 and quantity%lot==0
    assert quantity*100<=90_000*.137

def test_c13_report_uses_authoritative_elapsed_bar_count():
    orch=Engine(["INFY"])
    trade=open_position(orch)
    orch.id_box._current_regime=lambda *args:"calm"
    bar=dict(open=100.,high=101.,low=99.,close=100.)
    orch._maybe_exit("INFY","2024-01-01 11:00",bar,signal(),60,False,.8)
    assert orch.completed_trades[-1]["reason"]=="max_hold"
    assert orch.completed_trades[-1]["bars_held"]==60

def test_c14_terminal_excursion_explicit_and_does_not_change_prior_measure():
    orch=Engine(["INFY"])
    trade=open_position(orch)
    target=trade["target_price"]
    bar=dict(open=trade["entry_price"],high=target+1,low=trade["entry_price"]-1,close=target)
    orch._maybe_exit("INFY","2024-01-01 10:01",bar,signal(),1,False,.8)
    result=orch.completed_trades[-1]
    assert result["reason"]=="target"
    assert result["mfe_pre_exit_bar_r"]==0
    assert result["mfe_terminal_inclusive_r"]>=2
    assert result["terminal_bar_excursion"]=="intrabar_order_unknown"

def test_c8_force_close_without_entry_candidate():
    orch=Engine(["INFY"])
    open_position(orch)
    bar=dict(open=100.,high=101.,low=99.,close=100.)
    orch._maybe_exit("INFY","2024-01-01 15:25",bar,signal(),1,False,.8)
    assert not orch.open_trades
    assert orch.completed_trades[-1]["reason"]=="force_close_time"

def test_c15_unsupported_limit_replay_fails_closed():
    with pytest.raises(ValueError,match="MARKET"):
        registry=CanonicalParameterRegistry()
        registry.params["order_type"]=replace(registry.params["order_type"],default="LIMIT")
        Engine(["INFY"],registry=registry)

@pytest.mark.parametrize("side,direction",[("BUY",1),("SELL",-1)])
@pytest.mark.parametrize("multiplier",[.8,1.,1.5])
def test_c16_plan_matches_actual_fill_at_every_slippage_multiplier(side,direction,multiplier):
    orch=Engine(["INFY"],calibration_overrides={"slippage_cost_multiplier":multiplier})
    plan,info,_=orch.mpc.build_plan(signal(direction=direction),IDDecision(True,"ok",.8,2.,.6),
                                  100.,1.,orch.config)
    fill=orch.broker.place_order("INFY",side,1,"MARKET",info["execution_market_price"],
                                orch.safety_contract.as_dict(),orch.registry)
    assert fill["filled_price"]==plan.entry_price

def test_f15_sell_stop_tightening_is_protection():
    result=FinalExecutionController().exit_decision(
        side="SELL",path=None,exit_pid={"stop_before":110.,"stop_after":105.},
        held_bars=1,minimum_hold_bars=2,maximum_hold_bars=60)
    assert result["action"]=="PROTECT"

def test_f18_exposure_uses_marked_notional():
    orch=Engine(["INFY"])
    open_position(orch)
    orch._last_close["INFY"]=150.
    assert orch._gross_exposure_notional()==1500.

def bars():
    return pd.DataFrame({"timestamp":pd.date_range("2024-01-01 10:00",periods=8,freq="min",tz="Asia/Kolkata"),
                         "open":100.,"high":101.,"low":99.,"close":100.,"volume":1000.})

def test_f21_raw_clock_cannot_misindex_certified_frames():
    frame=bars()
    frame=pd.concat([frame.iloc[[2]],frame,frame.iloc[[3]]],ignore_index=True)
    orch=Engine(["INFY"])
    clock=orch.build_clock({"INFY":frame},2)
    # A raw clock can be rejected, or rebuilt. It must never index the wrong bar.
    with pytest.raises(ValueError,match="clock"):
        orch.run({"INFY":frame},warmup=2,precomputed_clock=clock)


@pytest.mark.parametrize("vote",[-1,1])
def test_c9_equal_conviction_has_equal_confidence(monkeypatch,vote):
    import revision2_external.composite_study_signal as module
    for name in ("_ichimoku_vote","_bollinger_vote","_stochastic_vote","_session_vwap_vote"):
        monkeypatch.setattr(module,name,lambda *args:vote)
    result=CompositeStudySignal().evaluate("INFY",bars())
    assert result["direction"]==vote
    assert result["confidence"]==1.

def test_c1_session_rollover_preserves_local_fix(monkeypatch):
    orch=Engine(["INFY"])
    orch._active_trading_date=pd.Timestamp("2023-12-29").date()
    orch.symbol_tripped["INFY"]=True
    orch.symbol_consecutive_losses["INFY"]=2
    orch.symbol_cooldown_until_bar["INFY"]=1000
    monkeypatch.setattr(orch.id_box,"evaluate",lambda *a,**kw:(IDDecision(False,"test",0.,0.,0.),[]))
    orch.run({"INFY":bars()},warmup=2)
    assert not orch.symbol_tripped
    assert not orch.symbol_consecutive_losses
    assert not orch.symbol_cooldown_until_bar


def test_c7_post_fill_uses_actual_quantity_slippage_and_latency():
    from gates_framework import EntryDecisionEngine
    gate=EntryDecisionEngine(SafetyGateConfig(slippage_tolerance_percent=.001))
    ok=gate.evaluate_post_fill(target_price=100,fill_price=100.05,expected_qty=10,actual_qty=10,
                              elapsed_seconds=1,expected_position=10,actual_position=10)
    assert ok["passed"]
    for kwargs in ({"actual_qty":5},{"fill_price":101},{"elapsed_seconds":31},{"actual_position":0}):
        values=dict(target_price=100,fill_price=100.05,expected_qty=10,actual_qty=10,
                    elapsed_seconds=1,expected_position=10,actual_position=10)
        values.update(kwargs)
        assert not gate.evaluate_post_fill(**values)["passed"]

def test_c7_duplicate_intents_use_event_time():
    from revision2_external.paper_execution import ReplayIntentLedger
    ledger=ReplayIntentLedger(window_seconds=5)
    assert not ledger.seen_recent("INFY","BUY","2024-01-01 10:00:00")
    ledger.record("INFY","BUY","2024-01-01 10:00:00")
    assert ledger.seen_recent("INFY","BUY","2024-01-01 10:00:04")
    assert not ledger.seen_recent("INFY","BUY","2024-01-01 10:00:06")
    assert not ledger.seen_recent("TCS","BUY","2024-01-01 10:00:04")

def test_f1_gate_policy_comes_from_config():
    from gates_framework import Gate07StaleData,Gate10DrawdownDerating,SystemState
    cfg=SafetyGateConfig(max_market_data_age_seconds=10,drawdown_derate_threshold=.10,
                         drawdown_derate_multiplier=.5,lambda_derate_multiplier=.8)
    assert not Gate07StaleData(cfg).evaluate(SystemState(market_data_age_seconds=11)).passed
    _,qty=Gate10DrawdownDerating(cfg).evaluate(SystemState(current_dd_percent=.11),20)
    assert qty==10

def test_f7_lambda_derating_compares_actual_risk():
    box=SafetyGatesTargetBox()
    _,_,low,_=box.evaluate_pre_sizing([1_000_000.],config(),current_lambda=.1)
    _,_,high,_=box.evaluate_pre_sizing([1_000_000.],config(),current_lambda=.3)
    assert low==1
    assert high==.5

def test_f23_rejection_preserves_exact_gate_evidence():
    from revision2_external.entry_candidate_observations import EntryCandidateObservationLedger
    ledger=EntryCandidateObservationLedger()
    ledger.observe(dict(candidate_id="c",symbol="INFY",side="BUY",timestamp="t",pa_confidence=.8,id_confidence=.8))
    ledger.dispose("c","REJECTED","entry_decision_gate",details={"gate":"Gate03DailyLossHalt","reason":"loss","adjusted_quantity":10})
    row=ledger.report()["rows"][0]
    assert row["execution_details"]["gate"]=="Gate03DailyLossHalt"

def test_f24_f25_f26_funnel_reports_evaluations_and_exit_submissions(monkeypatch):
    orch=Engine(["INFY"])
    trade=open_position(orch)
    orch._execute_exit("INFY","2024-01-01 10:02",trade,110.,"target")
    monkeypatch.setattr(orch.id_box,"evaluate",lambda *a,**kw:(IDDecision(False,"test",0.,0.,0.),[]))
    report=orch.run({"INFY":bars()},warmup=2)
    assert report["pa_evaluations"]==report["bars_processed"]
    assert report["exit_orders_submitted"]==1
    assert report["bay_trip_rejections"]==0
    assert report["bay_cooldown_rejections"]==0

def test_c9_opposite_direction_is_not_high_confidence_for_held_trade(monkeypatch):
    orch=Engine(["INFY"])
    open_position(orch,side="SELL")
    captured={}
    original=orch.exit_controller.update
    def observe(symbol,state,pa,studies,close,atr,**kw):
        captured["studies"]=studies
        return original(symbol,state,pa,studies,close,atr,**kw)
    monkeypatch.setattr(orch.exit_controller,"update",observe)
    orch._maybe_exit("INFY","2024-01-01 10:01",dict(open=100.,high=101.,low=99.,close=100.),
                     signal(),1,False,1.,{"direction":1})
    assert captured["studies"]==0.


def controlled_engine(monkeypatch, *, id_rr=2.):
    orch=Engine(["INFY"])
    monkeypatch.setattr(orch.pa,"evaluate",lambda snapshot,cfg:(signal(),[]))
    monkeypatch.setattr(orch.id_box,"evaluate",lambda *a,**kw:(IDDecision(True,"test",.8,id_rr,.6),[]))
    monkeypatch.setattr(orch.id_box,"_current_regime",lambda *a:"calm")
    return orch

def long_bars():
    frame=bars()
    return pd.DataFrame({"timestamp":pd.date_range("2024-01-01 10:00",periods=90,freq="min",tz="Asia/Kolkata"),
                         "open":1000.,"high":1001.,"low":999.,"close":1000.,"volume":1000.})

def test_f2_execution_rr_uses_actual_bracket_not_confidence_proxy(monkeypatch):
    orch=controlled_engine(monkeypatch,id_rr=.01)
    report=orch.run({"INFY":long_bars()},warmup=30)
    assert report["fills"]>0
    assert all(row["passed"] for row in report["post_fill_checks"])

def test_c5_final_mtm_includes_all_booked_costs(monkeypatch):
    orch=controlled_engine(monkeypatch)
    report=orch.run({"INFY":long_bars()},warmup=30)
    assert report["fills"]>0
    assert report["mtm_equity_curve"][-1][1]==pytest.approx(report["ending_equity"],rel=0,abs=1e-8)
    assert orch._equity()==pytest.approx(report["ending_equity"],rel=0,abs=1e-8)

def test_c7_integrated_partial_fill_is_booked_and_halts_new_entries(monkeypatch):
    orch=controlled_engine(monkeypatch)
    original=orch.broker.place_order
    def partial(**kw):
        if not orch.open_trades:
            kw["quantity"]=max(1,kw["quantity"]//2)
        return original(**kw)
    monkeypatch.setattr(orch.broker,"place_order",partial)
    report=orch.run({"INFY":long_bars()},warmup=30)
    assert report["fills"]==1
    assert report["execution_halted"]
    assert report["safety_violations"]==1
    assert report["post_fill_checks"][0]["gate"]=="Gate15OrderReconciliation"
    assert report["trades"][0]["quantity"]>0
    assert orch.broker.get_position("INFY")["quantity"]==0

def test_c17_full_calibration_refuses_unsealed_production_run(monkeypatch):
    from scripts import run_external_engine_48symbol_FULL_3YEAR_calibration as runner
    monkeypatch.setattr("sys.argv",["calibration"])
    monkeypatch.setattr(runner,"load_symbols_full_dataset",
                        lambda:pytest.fail("Must reject production freeze before loading data"))
    with pytest.raises(SystemExit):
        runner.main()

def test_s3_f5_f6_external_search_excludes_known_inert_controls():
    from revision2.calibration_supervisor import CalibrationSupervisor
    supervisor=CalibrationSupervisor(CanonicalParameterRegistry(),["INFY"],{"INFY":bars()},orchestrator_class=Engine)
    assert not {"exit_confidence_threshold","pid_derivative_smoothing",
                "limit_order_offset_percent","max_retry_attempts","retry_delay_seconds"} & set(supervisor.space.names)


def test_c10_approved_band_defaults_and_relational_validation():
    from revision2_external.startup_validation import validate_runtime_parameters
    registry=CanonicalParameterRegistry()
    values={n:s.default for n,s in registry.params.items()}
    assert (values["red_threshold"],values["amber_threshold_lower"],values["green_threshold"])==(.25,.5,.75)
    bad={**values,"red_threshold":.3,"amber_threshold_lower":.5,"green_threshold":.25}
    assert validate_runtime_parameters(registry,bad)
    assert not validate_runtime_parameters(registry,values)

def test_c18_trial_profile_is_stricter_and_paper_only():
    orch=Engine(["INFY"],risk_profile="trial")
    assert orch.broker.environment=="paper"
    assert orch.safety_contract.values["max_concurrent_positions"]==1
    assert orch.safety_contract.values["max_daily_loss_rupees"]==5000
    assert orch.safety_contract.values["safety_drawdown_halt_threshold"]==.03
    assert orch.safety_contract.values["max_gross_exposure_fraction"]<=1.
    assert orch.config.require("capital_per_trade_fraction")==.005
    trade=open_position(orch)
    assert trade["quantity"]==10
    with pytest.raises(ValueError):
        Engine(["INFY"],risk_profile="trial",calibration_overrides={"capital_per_trade_fraction":.02})


def test_f21_supervisor_builds_clock_from_certified_frames():
    from revision2.calibration_supervisor import CalibrationSupervisor
    frame=bars()
    dirty=pd.concat([frame.iloc[[2]],frame,frame.iloc[[3]]],ignore_index=True)
    supervisor=CalibrationSupervisor(CanonicalParameterRegistry(),["INFY"],{"INFY":dirty},orchestrator_class=Engine)
    assert len(supervisor.symbol_bars["INFY"])==len(frame)
    assert supervisor.symbol_bars["INFY"]["timestamp"].is_monotonic_increasing

def test_f22_signal_histories_are_warmed_before_first_trading_bar(monkeypatch):
    orch=Engine(["INFY"])
    observed={}
    original=orch.pa.evaluate
    def observe(snapshot,cfg):
        if len(snapshot.bars)==31:
            observed["history"]=len(orch.pa._history.get("INFY",[]))
            observed["studies"]=len(orch.chart_studies._symbols["INFY"]["ichimoku"].vote_history) if "INFY" in orch.chart_studies._symbols else 0
        return original(snapshot,cfg)
    monkeypatch.setattr(orch.pa,"evaluate",observe)
    orch.run({"INFY":long_bars()},warmup=30)
    assert observed["history"]>=3
    assert observed["studies"]>=5

def test_f20_completeness_requires_explicit_session_schedule():
    from revision2_external.data_certification_pandera import certify_session_completeness
    frame=bars()
    schedule={"2024-01-01":("10:00","10:08")}
    assert certify_session_completeness(frame,schedule)["complete"]
    result=certify_session_completeness(frame.iloc[:-1],schedule)
    assert not result["complete"]
    assert result["missing_minutes"]==1
    assert not certify_session_completeness(frame,{})["complete"]



def test_s3_entry_threshold_changes_real_id_admission():
    """S3: a searched threshold must alter an actual admission decision."""
    from revision2.boxes import IntelligentDiscriminationBox

    registry = CanonicalParameterRegistry()
    threshold = registry.get("entry_confidence_threshold")

    common = {
        "min_risk_reward_ratio": registry.get("min_risk_reward_ratio").minimum,
        "slippage_guard_threshold": registry.get("slippage_guard_threshold").maximum,
    }

    low_cfg = config(
        **common,
        entry_confidence_threshold=threshold.minimum,
    )
    high_cfg = config(
        **common,
        entry_confidence_threshold=threshold.maximum,
    )

    # 0.34 is deliberately below the maximum threshold (0.35), while its
    # calculated RR is high enough not to be rejected for the unrelated
    # min-risk-reward gate.
    sig = PASignal(
        "INFY", "2024-01-01 10:00",
        1, .34, .3, .001, .1, .2,
    )

    low, _ = IntelligentDiscriminationBox().evaluate(sig, low_cfg)
    high, _ = IntelligentDiscriminationBox().evaluate(sig, high_cfg)

    assert low.approved
    assert not high.approved
    assert "entry threshold" in high.reason


def test_s3_saturation_exit_bars_changes_external_exit_decision():
    """S3: the searched saturation horizon must alter exit behavior."""
    from revision2_external.continuous_exit_controller import ContinuousExitController

    def controller(limit):
        return ContinuousExitController(
            kp=.12,
            ki=.04,
            kd=.06,
            clamp=.05,
            atr_droop_mult=1.,
            baseline_window=10,
            saturation_exit_bars=limit,
        )

    c3 = controller(3)
    c4 = controller(4)

    s3 = c3.open_position(
        "BUY",
        entry_price=1000.,
        stop_price=990.,
        target_price=1020.,
        max_hold_bars=60,
    )
    s4 = c4.open_position(
        "BUY",
        entry_price=1000.,
        stop_price=990.,
        target_price=1020.,
        max_hold_bars=60,
    )

    # Same physical state; only the calibrated threshold differs.
    s3.consecutive_bars_at_low_confidence_extreme = 3
    s4.consecutive_bars_at_low_confidence_extreme = 3

    assert c3.saturation_exit_reason(s3) == "saturation_exit_pa"
    assert c4.saturation_exit_reason(s4) is None



def test_s4_simultaneous_candidate_arbitration_is_permutation_invariant():
    """S4: batch ranking cannot depend on candidate arrival/symbol-map order."""
    from types import SimpleNamespace

    def candidate(symbol, confidence, rr):
        entry = 100.0
        stop = 95.0
        target = entry + 5.0 * rr
        return {
            "symbol": symbol,
            "decision": SimpleNamespace(confidence=confidence),
            "plan": SimpleNamespace(
                entry_price=entry,
                stop_price=stop,
                target_price=target,
            ),
        }

    # BBB has better ID confidence and therefore must rank first even though
    # AAA is lexically first.
    aaa = candidate("AAA", .70, 3.0)
    bbb = candidate("BBB", .80, 1.5)
    ccc = candidate("CCC", .80, 2.0)

    expected = ["CCC", "BBB", "AAA"]

    forward = Engine.rank_simultaneous_entry_candidates([aaa, bbb, ccc])
    reverse = Engine.rank_simultaneous_entry_candidates([ccc, bbb, aaa])
    mixed = Engine.rank_simultaneous_entry_candidates([bbb, aaa, ccc])

    assert [x["symbol"] for x in forward] == expected
    assert [x["symbol"] for x in reverse] == expected
    assert [x["symbol"] for x in mixed] == expected


def test_s4_exact_quality_tie_uses_symbol_only_as_final_tiebreak():
    """S4: exact-quality ties remain deterministic without arrival priority."""
    from types import SimpleNamespace

    def candidate(symbol):
        return {
            "symbol": symbol,
            "decision": SimpleNamespace(confidence=.75),
            "plan": SimpleNamespace(
                entry_price=100.0,
                stop_price=95.0,
                target_price=110.0,
            ),
        }

    a = candidate("AAA")
    z = candidate("ZZZ")

    one = Engine.rank_simultaneous_entry_candidates([z, a])
    two = Engine.rank_simultaneous_entry_candidates([a, z])

    assert [x["symbol"] for x in one] == ["AAA", "ZZZ"]
    assert [x["symbol"] for x in two] == ["AAA", "ZZZ"]



def test_s4_simultaneous_candidates_compete_as_batch_not_symbol_order(monkeypatch):
    """Two same-timestamp candidates compete before either consumes capacity.

    BBB deliberately has higher ID confidence than alphabetically-earlier AAA.
    With max_positions_live=1, BBB must receive the first entry under both
    input dictionary permutations. The old sequential implementation would
    have admitted AAA first solely because the canonical clock sorts symbols.
    """

    def run_case(symbol_order):
        orch = Engine(
            list(symbol_order),
            calibration_overrides={"max_positions_live": 1},
        )

        confidence = {
            "AAA": 0.70,
            "BBB": 0.80,
        }

        def pa_evaluate(snapshot, cfg):
            c = confidence[snapshot.symbol]
            return (
                PASignal(
                    symbol=snapshot.symbol,
                    timestamp=str(snapshot.timestamp),
                    direction=1,
                    confidence=c,
                    momentum=.5,
                    volatility=.01,
                    vwap_deviation=.1,
                    volume_confirmation=.2,
                    exit_confidence=.8,
                ),
                [],
            )

        def id_evaluate(sig, *args, **kwargs):
            return (
                IDDecision(
                    True,
                    "test",
                    float(sig.confidence),
                    2.0,
                    .6,
                ),
                [],
            )

        def studies_evaluate(symbol, bars):
            return {
                "direction": 1,
                "confidence": .8,
            }

        monkeypatch.setattr(orch.pa, "evaluate", pa_evaluate)
        monkeypatch.setattr(orch.id_box, "evaluate", id_evaluate)
        monkeypatch.setattr(orch.id_box, "_current_regime", lambda *a, **kw: "calm")
        monkeypatch.setattr(orch.chart_studies, "evaluate", studies_evaluate)

        broker_calls = []
        original_place_order = orch.broker.place_order

        def record_place_order(*args, **kwargs):
            symbol = kwargs.get("symbol")
            if symbol is None and args:
                symbol = args[0]
            broker_calls.append(symbol)
            return original_place_order(*args, **kwargs)

        monkeypatch.setattr(orch.broker, "place_order", record_place_order)

        frames = {
            symbol: long_bars().copy()
            for symbol in symbol_order
        }

        report = orch.run(frames, warmup=30)

        assert report["fills"] > 0
        assert broker_calls, "expected at least one broker entry submission"

        return broker_calls[0]

    first_forward = run_case(["AAA", "BBB"])
    first_reverse = run_case(["BBB", "AAA"])

    assert first_forward == "BBB"
    assert first_reverse == "BBB"
