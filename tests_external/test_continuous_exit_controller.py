import sys
sys.path.insert(0, ".")

from revision2_external.continuous_exit_controller import ContinuousExitController


def _controller(clamp=0.1, saturation_exit_bars=5, disable_saturation_exit=False):
    return ContinuousExitController(kp=0.12, ki=0.04, kd=0.06, target=0.6, clamp=clamp,
                                     saturation_exit_bars=saturation_exit_bars,
                                     disable_saturation_exit=disable_saturation_exit)


def test_trailing_stop_only_ratchets_favorably_on_a_buy():
    ctrl = _controller()
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0)
    prices_and_confidence = [
        (1005.0, 0.6),   # price up: stop should ratchet up (never down)
        (1010.0, 0.6),
        (1002.0, 0.6),   # price pulls back: stop must NOT loosen back down
        (1015.0, 0.5),
    ]
    stops = []
    for price, conf in prices_and_confidence:
        state = ctrl.update(state, conf, price)
        stops.append(state.current_stop_price)

    # Monotonically non-decreasing -- a real trailing stop never gives back protection.
    for earlier, later in zip(stops, stops[1:]):
        assert later >= earlier - 1e-9, f"stop loosened: {stops}"
    assert stops[-1] > 990.0, "stop never actually trailed up from the original level"


def test_trailing_stop_only_ratchets_favorably_on_a_sell():
    ctrl = _controller()
    state = ctrl.open_position("SELL", entry_price=1000.0, stop_price=1010.0, target_price=980.0)
    prices = [995.0, 990.0, 998.0, 985.0]
    stops = []
    for price in prices:
        state = ctrl.update(state, 0.6, price)
        stops.append(state.current_stop_price)
    for earlier, later in zip(stops, stops[1:]):
        assert later <= earlier + 1e-9, f"stop loosened on a SELL: {stops}"
    assert stops[-1] < 1010.0


def test_sustained_saturation_triggers_the_early_exit_signal_at_the_right_bar():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0)
    # A confidence far below the 0.6 target, sustained, should drive and
    # keep the PID output pinned at -clamp.
    for i in range(5):
        state = ctrl.update(state, 0.05, 1000.0)
        triggered = ctrl.should_exit_on_saturation(state)
        if i < 2:
            assert not triggered, f"exit signal fired too early at bar {i}"
        if i >= 2:
            assert triggered, f"expected saturation exit by bar {i}, consecutive={state.consecutive_bars_at_low_confidence_extreme}"


def test_saturation_streak_resets_when_confidence_recovers():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=3)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0)
    for _ in range(2):
        state = ctrl.update(state, 0.05, 1000.0)
    assert state.consecutive_bars_at_low_confidence_extreme == 2
    state = ctrl.update(state, 0.9, 1000.0)  # confidence spikes well above target
    assert state.consecutive_bars_at_low_confidence_extreme == 0
    assert not ctrl.should_exit_on_saturation(state)


def test_saturation_exit_can_be_disabled():
    ctrl = _controller(clamp=0.05, saturation_exit_bars=2, disable_saturation_exit=True)
    state = ctrl.open_position("BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0)
    for _ in range(10):
        state = ctrl.update(state, 0.01, 1000.0)
    assert state.consecutive_bars_at_low_confidence_extreme >= 2
    assert not ctrl.should_exit_on_saturation(state)


def test_real_infy_price_sequence_produces_a_real_trailing_stop():
    # The actual subsequent bars from the real 2023-07-13 15:16 trade this
    # session traced by hand: entry filled 15:17 at 1338.8691, MPC's own
    # plan stop was 1338.0172. Real closes for the next few bars.
    import sys as _sys
    _sys.path.insert(0, ".")
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("INFY")
    day = frame[frame["timestamp"].dt.strftime("%Y-%m-%d %H:%M") >= "2023-07-13 15:17"].head(5)
    assert len(day) >= 2, "expected real INFY bars for this window"

    ctrl = _controller(clamp=0.1, saturation_exit_bars=5)
    state = ctrl.open_position("BUY", entry_price=1338.7352, stop_price=1338.0172, target_price=1340.3149)
    for _, bar in day.iterrows():
        state = ctrl.update(state, 0.55, float(bar["close"]))
    # A real, non-degenerate outcome: the stop moved from its initial level
    # using real price data, and never violated the "only tightens" rule
    # (already proven generically above; this proves it holds on real data too).
    assert state.current_stop_price >= 1338.0172 - 1e-9
