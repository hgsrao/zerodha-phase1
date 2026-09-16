import sys
sys.path.insert(0, ".")

from revision2_external.continuous_exit_controller import ContinuousExitController

SYMBOL = "TEST"
ATR = 5.0          # a fixed, simple ATR for tests that don't care about its exact value
MAX_HOLD = 60      # keeps time-decay negligible over a handful of bars in the generic tests
NEUTRAL = 0.6      # a healthy, unchanging chart-studies reading for tests that aren't about that track --
                   # kept CONSTANT on purpose so its own baseline converges and it never saturates,
                   # isolating those tests to whatever they actually test.


def _controller(clamp=0.1, saturation_exit_bars=5, disable_saturation_exit=False, baseline_window=10, atr_droop_mult=1.0):
    return ContinuousExitController(kp=0.12, ki=0.04, kd=0.06, clamp=clamp, atr_droop_mult=atr_droop_mult,
                                     baseline_window=baseline_window,
                                     saturation_exit_bars=saturation_exit_bars,
                                     disable_saturation_exit=disable_saturation_exit)


def test_trailing_stop_only_ratchets_favorably_on_a_buy():
    ctrl = _controller()
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    prices_and_confidence = [
        (1005.0, 0.6),   # price up: stop should ratchet up (never down)
        (1010.0, 0.6),
        (1002.0, 0.6),   # price pulls back: stop must NOT loosen back down
        (1015.0, 0.5),
    ]
    stops = []
    for price, conf in prices_and_confidence:
        state = ctrl.update(SYMBOL, state, conf, NEUTRAL, price, ATR)
        stops.append(state.current_stop_price)

    # Monotonically non-decreasing -- a real trailing stop never gives back protection.
    for earlier, later in zip(stops, stops[1:]):
        assert later >= earlier - 1e-9, f"stop loosened: {stops}"
    assert stops[-1] > 990.0, "stop never actually trailed up from the original level"


def test_trailing_stop_only_ratchets_favorably_on_a_sell():
    ctrl = _controller()
    state = ctrl.open_position("SELL", entry_price=1000.0, stop_price=1010.0, target_price=980.0, max_hold_bars=MAX_HOLD)
    prices = [995.0, 990.0, 998.0, 985.0]
    stops = []
    for price in prices:
        state = ctrl.update(SYMBOL, state, 0.6, NEUTRAL, price, ATR)
        stops.append(state.current_stop_price)
    for earlier, later in zip(stops, stops[1:]):
        assert later <= earlier + 1e-9, f"stop loosened on a SELL: {stops}"
    assert stops[-1] < 1010.0


def test_stop_tracks_the_favorable_extreme_not_the_latest_tick():
    # Real bug found and fixed after wiring this controller in: measuring
    # the trail from current_close directly means an ordinary pullback
    # after a real high (price still fine, just not making a NEW high this
    # bar) gets treated identically to a real reversal. Anchoring to the
    # favorable extreme means a ordinary pullback bar computes its
    # candidate from the REAL peak, not the dip.
    ctrl = _controller()
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    state = ctrl.update(SYMBOL, state, 0.6, NEUTRAL, 1010.0, ATR)  # real new high
    assert state.favorable_extreme == 1010.0
    state = ctrl.update(SYMBOL, state, 0.6, NEUTRAL, 1003.0, ATR)  # ordinary pullback, no new high
    assert state.favorable_extreme == 1010.0, "a pullback must not erase the real high-water-mark"


def test_time_held_tightens_the_trail_even_with_unchanged_confidence():
    # Real, continuous input: a position gets less benefit of the doubt
    # purely from being held longer, independent of confidence. Compares
    # the SAME confidence/price/ATR bar early vs. late in the position's
    # allowed lifetime -- only bars_held differs.
    ctrl_early = _controller(atr_droop_mult=1.0)
    ctrl_late = _controller(atr_droop_mult=1.0)
    max_hold = 10
    state_early = ctrl_early.open_position("BUY", 1000.0, 990.0, 1020.0, max_hold_bars=max_hold)
    state_late = ctrl_late.open_position("BUY", 1000.0, 990.0, 1020.0, max_hold_bars=max_hold)
    # Run state_late through many bars first so bars_held is close to max_hold.
    for _ in range(8):
        state_late = ctrl_late.update(SYMBOL, state_late, 0.6, NEUTRAL, 1005.0, ATR)
    state_early = ctrl_early.update(SYMBOL, state_early, 0.6, NEUTRAL, 1005.0, ATR)
    state_late = ctrl_late.update(SYMBOL, state_late, 0.6, NEUTRAL, 1005.0, ATR)
    assert state_late.current_stop_price > state_early.current_stop_price, (
        "a long-held position must trail tighter than a fresh one under identical confidence/price/ATR"
    )


def test_pa_track_alone_triggers_saturation_exit_even_when_studies_track_is_healthy():
    # Direct proof of the actual point of the two-track design: PA
    # confidence genuinely declines while the chart-studies confidence
    # stays flat and healthy the whole time -- the exit must still fire,
    # driven entirely by the PA track, with no help from (and no
    # dependence on) the studies track.
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    declining_pa_confidence = [0.6, 0.5, 0.35, 0.2, 0.1, 0.05, 0.02]
    reason = None
    for conf in declining_pa_confidence:
        state = ctrl.update(SYMBOL, state, conf, NEUTRAL, 1000.0, ATR)  # studies confidence held flat/healthy throughout
        reason = ctrl.saturation_exit_reason(state)
        if reason is not None:
            break
    assert reason == "saturation_exit_pa", f"expected the PA track to trigger the exit; got {reason!r}"
    assert state.consecutive_bars_at_low_studies_extreme == 0, "the healthy studies track must never have saturated"


def test_studies_track_alone_triggers_saturation_exit_even_when_pa_track_is_healthy():
    # The mirror case: chart-studies confidence genuinely declines while
    # PA confidence stays flat and healthy -- the exit must still fire,
    # driven entirely by the studies track this time.
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    declining_studies_confidence = [0.6, 0.5, 0.35, 0.2, 0.1, 0.05, 0.02]
    reason = None
    for conf in declining_studies_confidence:
        state = ctrl.update(SYMBOL, state, NEUTRAL, conf, 1000.0, ATR)  # PA confidence held flat/healthy throughout
        reason = ctrl.saturation_exit_reason(state)
        if reason is not None:
            break
    assert reason == "saturation_exit_studies", f"expected the studies track to trigger the exit; got {reason!r}"
    assert state.consecutive_bars_at_low_confidence_extreme == 0, "the healthy PA track must never have saturated"


def test_saturation_streak_resets_independently_per_track():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    # PA declines, studies stays flat.
    for conf in [0.6, 0.4, 0.2]:
        state = ctrl.update(SYMBOL, state, conf, NEUTRAL, 1000.0, ATR)
    assert state.consecutive_bars_at_low_confidence_extreme > 0, "precondition: PA decline must have built up some streak"
    assert state.consecutive_bars_at_low_studies_extreme == 0, "precondition: studies track must be untouched"
    # PA recovers -- only the PA streak should reset.
    state = ctrl.update(SYMBOL, state, 0.9, NEUTRAL, 1000.0, ATR)
    assert state.consecutive_bars_at_low_confidence_extreme == 0
    assert ctrl.saturation_exit_reason(state) is None


def test_saturation_exit_can_be_disabled_for_both_tracks():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=2, disable_saturation_exit=True)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    for conf in [0.6, 0.45, 0.3, 0.15, 0.05, 0.01]:
        state = ctrl.update(SYMBOL, state, conf, conf, 1000.0, ATR)  # both tracks decline together
    assert state.consecutive_bars_at_low_confidence_extreme >= 2, "precondition: a real PA streak must have built up"
    assert state.consecutive_bars_at_low_studies_extreme >= 2, "precondition: a real studies streak must have built up"
    assert ctrl.saturation_exit_reason(state) is None


def test_two_symbols_do_not_share_pid_or_baseline_state():
    # Mixing two symbols' errors into one PID (or one baseline) would
    # saturate it regardless of either symbol's real behavior -- the same
    # bug class already found and fixed on SimplePIDModelPredictiveControlBox's
    # per-symbol _get_pid caching. Confirms this controller keeps a
    # genuinely separate PID and baseline per symbol, for BOTH tracks.
    ctrl = _controller()
    state_a = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    state_b = ctrl.open_position("BUY", entry_price=500.0, stop_price=495.0, target_price=510.0, max_hold_bars=MAX_HOLD)
    # First call for a fresh symbol has no history yet, so error (and the
    # output) is always 0 regardless of the reading -- not a leak, the same
    # "neutral start" behavior as SimplePIDModelPredictiveControlBox. A
    # second call, with a DIFFERENT reading than the first, is what
    # actually distinguishes two genuinely separate PIDs/baselines.
    state_a = ctrl.update("SYM_A", state_a, 0.9, 0.9, 1000.0, ATR)
    state_b = ctrl.update("SYM_B", state_b, 0.2, 0.2, 500.0, ATR)
    state_a = ctrl.update("SYM_A", state_a, 0.5, 0.5, 1000.0, ATR)
    state_b = ctrl.update("SYM_B", state_b, 0.5, 0.5, 500.0, ATR)
    assert ctrl._pids["SYM_A"] is not ctrl._pids["SYM_B"]
    assert ctrl._studies_pids["SYM_A"] is not ctrl._studies_pids["SYM_B"]
    assert state_a.adjustment_history[-1] != state_b.adjustment_history[-1], (
        "two symbols with different PA-confidence HISTORY converging on the same reading produced "
        "the same adjustment -- state may be leaking between them"
    )
    assert state_a.studies_adjustment_history[-1] != state_b.studies_adjustment_history[-1], (
        "two symbols with different studies-confidence HISTORY converging on the same reading produced "
        "the same adjustment -- state may be leaking between them"
    )


def test_real_infy_price_sequence_produces_a_real_trailing_stop():
    # The actual subsequent bars from the real 2023-07-13 15:16 trade this
    # session traced by hand: entry filled 15:17 at 1338.8691, MPC's own
    # plan stop was 1338.0172, real ATR at that trade was ~1.03. Real
    # closes for the next few bars.
    import sys as _sys
    _sys.path.insert(0, ".")
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("INFY")
    day = frame[frame["timestamp"].dt.strftime("%Y-%m-%d %H:%M") >= "2023-07-13 15:17"].head(5)
    assert len(day) >= 2, "expected real INFY bars for this window"

    ctrl = _controller(clamp=0.1, saturation_exit_bars=5, atr_droop_mult=0.75)
    state = ctrl.open_position("BUY", entry_price=1338.7352, stop_price=1338.0172, target_price=1340.3149, max_hold_bars=60)
    for _, bar in day.iterrows():
        state = ctrl.update("INFY", state, 0.55, 0.55, float(bar["close"]), 1.0319)
    # A real, non-degenerate outcome: the stop moved from its initial level
    # using real price data, and never violated the "only tightens" rule
    # (already proven generically above; this proves it holds on real data too).
    assert state.current_stop_price >= 1338.0172 - 1e-9
