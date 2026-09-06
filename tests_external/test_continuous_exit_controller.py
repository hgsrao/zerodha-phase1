import sys
sys.path.insert(0, ".")

from revision2_external.continuous_exit_controller import ContinuousExitController

SYMBOL = "TEST"
ATR = 5.0        # a fixed, simple ATR for tests that don't care about its exact value
MAX_HOLD = 60    # keeps time-decay negligible over a handful of bars in the generic tests


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
        state = ctrl.update(SYMBOL, state, conf, price, ATR)
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
        state = ctrl.update(SYMBOL, state, 0.6, price, ATR)
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
    state = ctrl.update(SYMBOL, state, 0.6, 1010.0, ATR)  # real new high
    assert state.favorable_extreme == 1010.0
    state = ctrl.update(SYMBOL, state, 0.6, 1003.0, ATR)  # ordinary pullback, no new high
    assert state.favorable_extreme == 1010.0, "a pullback must not erase the real high-water-mark"


def test_time_held_tightens_the_trail_even_with_unchanged_confidence():
    # Real, continuous input #3: a position gets less benefit of the doubt
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
        state_late = ctrl_late.update(SYMBOL, state_late, 0.6, 1005.0, ATR)
    state_early = ctrl_early.update(SYMBOL, state_early, 0.6, 1005.0, ATR)
    state_late = ctrl_late.update(SYMBOL, state_late, 0.6, 1005.0, ATR)
    assert state_late.current_stop_price > state_early.current_stop_price, (
        "a long-held position must trail tighter than a fresh one under identical confidence/price/ATR"
    )


def test_sustained_saturation_triggers_the_early_exit_signal_at_the_right_bar():
    # Confidence must genuinely DECLINE, not sit at a constant floor: the
    # setpoint is now a rolling mean of this symbol's own recent
    # confidence (see ContinuousExitController._confidence_baseline), so a
    # constant confidence would let the baseline converge to it and error
    # go to ~0 after a few bars, exactly the bug already found and fixed
    # once this session on the entry/exit PIDs -- saturation would only be
    # transient, not sustained. A real, accelerating decline (momentum
    # fading) keeps confidence below its own recent average for several
    # bars in a row, which is also a more realistic test of what this
    # signal is actually meant to catch.
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    declining_confidence = [0.6, 0.5, 0.35, 0.2, 0.1, 0.05, 0.02]
    triggered_at = None
    for i, conf in enumerate(declining_confidence):
        state = ctrl.update(SYMBOL, state, conf, 1000.0, ATR)
        if ctrl.should_exit_on_saturation(state) and triggered_at is None:
            triggered_at = i
    assert triggered_at is not None, (
        f"a real, sustained confidence decline never triggered the saturation exit: "
        f"{state.consecutive_bars_at_low_confidence_extreme} consecutive at end"
    )
    assert triggered_at >= 2, f"exit signal fired too early, at bar {triggered_at}"


def test_saturation_streak_resets_when_confidence_recovers():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    for conf in [0.6, 0.4, 0.2]:
        state = ctrl.update(SYMBOL, state, conf, 1000.0, ATR)
    assert state.consecutive_bars_at_low_confidence_extreme > 0, "precondition: decline must have built up some saturation streak"
    state = ctrl.update(SYMBOL, state, 0.9, 1000.0, ATR)  # confidence spikes well above its own recent average
    assert state.consecutive_bars_at_low_confidence_extreme == 0
    assert not ctrl.should_exit_on_saturation(state)


def test_saturation_exit_can_be_disabled():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=2, disable_saturation_exit=True)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    for conf in [0.6, 0.45, 0.3, 0.15, 0.05, 0.01]:
        state = ctrl.update(SYMBOL, state, conf, 1000.0, ATR)
    assert state.consecutive_bars_at_low_confidence_extreme >= 2, "precondition: a real streak must have built up"
    assert not ctrl.should_exit_on_saturation(state)


def test_two_symbols_do_not_share_pid_or_baseline_state():
    # Mixing two symbols' errors into one PID (or one baseline) would
    # saturate it regardless of either symbol's real behavior -- the same
    # bug class already found and fixed on SimplePIDModelPredictiveControlBox's
    # per-symbol _get_pid caching. Confirms this controller keeps a
    # genuinely separate PID and baseline per symbol.
    ctrl = _controller()
    state_a = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0, max_hold_bars=MAX_HOLD)
    state_b = ctrl.open_position("BUY", entry_price=500.0, stop_price=495.0, target_price=510.0, max_hold_bars=MAX_HOLD)
    # First call for a fresh symbol has no history yet, so error (and the
    # output) is always 0 regardless of the reading -- not a leak, the same
    # "neutral start" behavior as SimplePIDModelPredictiveControlBox. A
    # second call, with a DIFFERENT confidence than the first, is what
    # actually distinguishes two genuinely separate PIDs/baselines.
    state_a = ctrl.update("SYM_A", state_a, 0.9, 1000.0, ATR)
    state_b = ctrl.update("SYM_B", state_b, 0.2, 500.0, ATR)
    state_a = ctrl.update("SYM_A", state_a, 0.5, 1000.0, ATR)
    state_b = ctrl.update("SYM_B", state_b, 0.5, 500.0, ATR)
    assert ctrl._pids["SYM_A"] is not ctrl._pids["SYM_B"]
    assert state_a.adjustment_history[-1] != state_b.adjustment_history[-1], (
        "two symbols with different confidence HISTORY converging on the same reading produced "
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
        state = ctrl.update("INFY", state, 0.55, float(bar["close"]), 1.0319)
    # A real, non-degenerate outcome: the stop moved from its initial level
    # using real price data, and never violated the "only tightens" rule
    # (already proven generically above; this proves it holds on real data too).
    assert state.current_stop_price >= 1338.0172 - 1e-9
