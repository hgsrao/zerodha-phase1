from revision2_external.continuous_exit_controller import ContinuousExitController
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def _controller():
    return ContinuousExitController(
        kp=0.12, ki=0.04, kd=0.06, clamp=0.1, atr_droop_mult=1.0, baseline_window=5,
    )


def test_shadow_r_path_only_tightens_its_own_stop_when_trade_lags():
    controller = _controller()
    state = controller.open_position("BUY", 100.0, 90.0, 115.0, max_hold_bars=10)
    # Establish elapsed time using the live controller, then make R fall
    # behind the predeclared shadow reference path.
    for _ in range(4):
        state = controller.update("TEST", state, 0.6, 0.6, 100.0, 2.0)
    live_stop = state.current_stop_price
    shadow = controller.update_shadow_r_trajectory(state, current_close=90.0)

    assert shadow["shadow_lagging"] is True
    assert shadow["shadow_stop_after"] > shadow["shadow_stop_before"]
    assert state.current_stop_price == live_stop, "shadow path must not alter the live stop"


def test_shadow_stop_is_next_bar_only_and_records_a_gap_fill():
    controller = _controller()
    state = controller.open_position("BUY", 100.0, 90.0, 115.0, max_hold_bars=10)
    state.shadow_stop_price = 96.0  # armed from a prior bar

    event = controller.check_shadow_stop(
        state, {"open": 95.0, "low": 94.0, "high": 97.0, "close": 95.0}, "2024-01-01T09:20:00+05:30"
    )

    assert event["shadow_exit_reason"] == "shadow_r_stop_gap"
    assert event["shadow_exit_price"] == 95.0
    assert controller.check_shadow_stop(state, {"open": 90.0, "low": 89.0, "high": 91.0}, "later") is None


def test_shadow_path_never_widens_a_short_stop():
    controller = _controller()
    state = controller.open_position("SELL", 100.0, 110.0, 85.0, max_hold_bars=10)
    for _ in range(4):
        state = controller.update("TEST", state, 0.6, 0.6, 100.0, 2.0)
    before = state.shadow_stop_price
    controller.update_shadow_r_trajectory(state, current_close=110.0)

    assert state.shadow_stop_price <= before


def test_shadow_counterfactual_uses_the_same_adverse_paper_fill_model_as_live_exits():
    assert Revision2ExternalEngineOrchestrator._paper_fill_price(100.0, "SELL", 0.0005) == 99.95
    assert Revision2ExternalEngineOrchestrator._paper_fill_price(100.0, "BUY", 0.0005) == 100.05


def test_excursion_ledger_tracks_only_passive_high_low_extremes():
    state = _controller().open_position("BUY", 100.0, 90.0, 115.0, max_hold_bars=10)
    ContinuousExitController.observe_completed_bar_excursion(
        state, {"open": 100.0, "high": 106.0, "low": 97.0, "close": 101.0}
    )
    assert (state.mfe_price, state.mae_price) == (106.0, 97.0)
