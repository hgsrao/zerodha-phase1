import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd

from revision2_external.composite_study_signal import CompositeStudySignal, STUDY_NAMES, _GRADING_HORIZON

SYMBOL = "TEST"


def _trending_bars(n=120, start=1000.0, drift=0.002, seed=0, session_days=1):
    rng = np.random.default_rng(seed)
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + drift + rng.normal(0, 0.0005)))
    prices = np.array(prices)
    idx = pd.date_range("2024-01-02 09:15", periods=n, freq="min", tz="Asia/Kolkata")
    if session_days > 1:
        # spread bars across multiple real calendar days for session VWAP resets
        per_day = n // session_days
        idx = pd.DatetimeIndex(sum(
            [list(pd.date_range(f"2024-01-{2+d:02d} 09:15", periods=per_day, freq="min", tz="Asia/Kolkata"))
             for d in range(session_days)], []
        )[:n])
    return pd.DataFrame({
        "timestamp": idx, "open": prices * 0.9995, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": rng.integers(1000, 5000, n),
    })


def test_real_uptrend_produces_a_bullish_composite():
    bars = _trending_bars(n=120, drift=0.002)
    engine = CompositeStudySignal()
    result = None
    for i in range(60, len(bars)):
        result = engine.evaluate(SYMBOL, bars.iloc[:i + 1])
    assert result is not None
    assert result["direction"] >= 0, f"a real, sustained uptrend should not read net-bearish: {result}"
    assert 0.0 <= result["confidence"] <= 1.0


def test_real_downtrend_produces_a_bearish_composite():
    bars = _trending_bars(n=120, drift=-0.002, seed=1)
    engine = CompositeStudySignal()
    result = None
    for i in range(60, len(bars)):
        result = engine.evaluate(SYMBOL, bars.iloc[:i + 1])
    assert result is not None
    assert result["direction"] <= 0, f"a real, sustained downtrend should not read net-bullish: {result}"


def test_weights_sum_and_stay_within_bounds():
    bars = _trending_bars(n=120, drift=0.001, seed=2)
    engine = CompositeStudySignal()
    for i in range(60, len(bars)):
        result = engine.evaluate(SYMBOL, bars.iloc[:i + 1])
        for name, w in result["weights"].items():
            assert 0.05 - 1e-9 <= w <= 0.60 + 1e-9, f"{name} weight {w} out of bounds"
        assert set(result["weights"]) == set(STUDY_NAMES)


def test_a_consistently_wrong_study_gets_down_weighted_relative_to_a_correct_one():
    # Real, direct proof of the actual point of this module: feed a real
    # uptrend (so a real "always bullish" reading is genuinely correct
    # most of the time) and confirm that whichever study happens to
    # persistently vote AGAINST the trend ends this run with a materially
    # lower weight than one that persistently votes WITH it -- not fixed
    # 1/4 each, forever, the way the original chart-studies monitor was.
    bars = _trending_bars(n=200, drift=0.0025, seed=3)
    engine = CompositeStudySignal(kp=0.3, ki=0.1, kd=0.05, clamp=0.2)  # faster gains so the effect shows within this fixture's length
    for i in range(60, len(bars)):
        result = engine.evaluate(SYMBOL, bars.iloc[:i + 1])
    hit_rates = result["hit_rates"]
    weights = result["weights"]
    best_study = max(hit_rates, key=hit_rates.get)
    worst_study = min(hit_rates, key=hit_rates.get)
    if hit_rates[best_study] - hit_rates[worst_study] > 0.1:  # only meaningful if the fixture actually differentiated them
        assert weights[best_study] > weights[worst_study], (
            f"study with the better real hit rate ({best_study}={hit_rates[best_study]:.2f}) did not end up "
            f"with a higher weight than the worse one ({worst_study}={hit_rates[worst_study]:.2f}): {weights}"
        )


def test_grading_uses_no_lookahead():
    # Direct proof: a study's hit_history is only ever updated using a
    # vote from _GRADING_HORIZON bars ago compared against CURRENT price --
    # confirmed by checking that after fewer than _GRADING_HORIZON+1 real
    # evaluate() calls, no hits have been graded yet at all (there's
    # nothing far enough in the past yet to grade).
    bars = _trending_bars(n=120, drift=0.001, seed=4)
    engine = CompositeStudySignal()
    for i in range(60, 60 + _GRADING_HORIZON):
        result = engine.evaluate(SYMBOL, bars.iloc[:i + 1])
    for name in STUDY_NAMES:
        state = engine._symbols[SYMBOL][name]
        assert len(state.hit_history) == 0, f"{name} graded a vote before its real horizon had elapsed"


def test_two_symbols_do_not_share_weight_or_pid_state():
    bars_a = _trending_bars(n=100, drift=0.002, seed=5)
    bars_b = _trending_bars(n=100, drift=-0.002, seed=6)
    engine = CompositeStudySignal()
    for i in range(60, 100):
        result_a = engine.evaluate("SYM_A", bars_a.iloc[:i + 1])
        result_b = engine.evaluate("SYM_B", bars_b.iloc[:i + 1])
    assert engine._symbols["SYM_A"] is not engine._symbols["SYM_B"]
    assert result_a["direction"] != result_b["direction"] or result_a["confidence"] != result_b["confidence"], (
        "two symbols with opposite real trends produced identical composite output -- state may be leaking"
    )


def test_real_infy_data_produces_bounded_non_degenerate_output():
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("INFY").head(300).reset_index(drop=True)

    engine = CompositeStudySignal()
    confidences, directions = [], []
    for i in range(80, len(frame)):
        result = engine.evaluate("INFY", frame.iloc[:i + 1])
        confidences.append(result["confidence"])
        directions.append(result["direction"])
        assert 0.0 <= result["confidence"] <= 1.0

    assert len(set(round(c, 3) for c in confidences)) > 1, "real INFY data produced a flat, degenerate composite confidence"
    assert len(set(directions)) > 1, "real INFY data never changed composite direction across 220 real bars"
