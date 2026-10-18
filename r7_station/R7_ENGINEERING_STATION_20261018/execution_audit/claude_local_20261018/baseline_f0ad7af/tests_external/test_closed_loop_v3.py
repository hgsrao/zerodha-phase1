from revision2_external.closed_loop_v2 import CompletedOutcome, EntryQualityLoop
from revision2_external.closed_loop_v3 import ClosedLoopV3Tester, PortfolioRiskLoop, TradePathLoop


def test_trade_path_feedback_has_frozen_setpoint_and_one_way_stop_ratchet():
    loop = TradePathLoop(progress_gamma=1.0, max_tighten_r_per_bar=.3)
    first = loop.update(trade_id="t1", bars_held=10, max_hold_bars=20, target_r=1.5, measurement_r=-.2, prior_stop_r=-1.0)
    second = loop.update(trade_id="t1", bars_held=11, max_hold_bars=20, target_r=1.5, measurement_r=.8, prior_stop_r=first.stop_r)
    assert first.setpoint_r == .75
    assert first.error_r == -.95
    assert first.stop_r > -1.0
    assert second.stop_r >= first.stop_r


def test_portfolio_loop_is_a_brake_never_a_capital_deployment_target():
    loop = PortfolioRiskLoop(target_heat=.25, drawdown_limit=.03)
    underexposed = loop.decide(exposure_fraction=.01, drawdown_fraction=0)
    stressed = loop.decide(exposure_fraction=.50, drawdown_fraction=.02)
    halted = loop.decide(exposure_fraction=.10, drawdown_fraction=.03)
    assert underexposed.new_risk_derate == 1.0
    assert 0.0 < stressed.new_risk_derate < 1.0
    assert halted.halted and halted.new_risk_derate == 0.0


def test_v3_emits_all_forward_and_feedback_events_and_enforces_bounds():
    tester = ClosedLoopV3Tester(entry_quality=EntryQualityLoop(minimum_samples=1, prior_strength=2))
    tester.record_outcome(CompletedOutcome("2023-09-01T10:00:00", "INFY", "BUY", 1.5, .1, True, 1.4))
    entry = tester.entry(symbol="INFY", side="BUY", decision_timestamp="2023-09-01T10:01:00", target_r=1.5, round_trip_cost_r=.1)
    path = tester.held_bar(trade_id="INFY-1", bars_held=5, max_hold_bars=30, target_r=1.5, measurement_r=-.1, prior_stop_r=-1.0)
    risk = tester.portfolio(exposure_fraction=.20, drawdown_fraction=.005)
    tester.validate()
    assert entry.completed_samples == 1
    assert path.stop_r >= -1.0
    assert risk.new_risk_derate <= 1.0
    assert {row["event"] for row in tester.events} == {"COMPLETED_OUTCOME_FEEDBACK", "ENTRY_QUALITY_CONTROL", "TRADE_PATH_CONTROL", "PORTFOLIO_RISK_CONTROL"}
