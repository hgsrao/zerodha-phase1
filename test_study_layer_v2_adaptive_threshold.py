"""Tests for study_layer_v2_adaptive_threshold.py. Synthetic data only."""
from __future__ import annotations

import pytest

from study_layer_v2_adaptive_threshold import AdaptiveThresholdController


def test_threshold_is_base_before_any_outcome_recorded():
    """No evidence yet -> no adjustment. Not 0.5, not a guess - the base."""
    c = AdaptiveThresholdController(base_threshold=75.0, kp=1.0, ki=1.0, kd=1.0)
    assert c.current_threshold == 75.0
    assert c.trailing_hit_rate is None


def test_losing_streak_raises_the_threshold_p_term_only():
    """All losses -> hit_rate=0 -> error = target - 0 = target (positive,
    underperforming) -> P term pushes threshold UP (stricter)."""
    c = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=10.0, ki=0.0, kd=0.0, window=5)
    for _ in range(3):
        c.record_outcome(False)
    assert c.current_threshold > 75.0


def test_winning_streak_lowers_the_threshold_p_term_only():
    """All wins -> hit_rate=1 -> error = 0.5-1 = -0.5 (outperforming) ->
    P term pushes threshold DOWN (looser, more entries allowed)."""
    c = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=10.0, ki=0.0, kd=0.0, window=5)
    for _ in range(3):
        c.record_outcome(True)
    assert c.current_threshold < 75.0


def test_integral_term_accumulates_across_calls():
    """A pure-I controller keeps moving in the same direction across
    consecutive identical-error updates, unlike a pure-P controller which
    would settle at one offset - proving the integral term is really
    accumulating, not just re-deriving the same instantaneous error."""
    c = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=0.0, ki=1.0, kd=0.0, window=100,
                                     integral_clamp=1000.0)
    c.record_outcome(False)
    t1 = c.current_threshold
    c.record_outcome(False)
    t2 = c.current_threshold
    c.record_outcome(False)
    t3 = c.current_threshold
    assert t1 < t2 < t3, "integral term should keep raising the threshold further with each additional loss"


def test_anti_windup_clamp_bounds_the_integral_contribution():
    """A long losing stretch must not ratchet the threshold to an
    absurd, never-recovering level - the documented real hazard. With a
    tight integral_clamp, the threshold must stop climbing well before
    an unclamped run of 200 losses would take it."""
    clamped = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=0.0, ki=1.0, kd=0.0,
                                           window=200, integral_clamp=2.0)
    unclamped = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=0.0, ki=1.0, kd=0.0,
                                             window=200, integral_clamp=10_000.0)
    for _ in range(200):
        clamped.record_outcome(False)
        unclamped.record_outcome(False)
    assert clamped.current_threshold < unclamped.current_threshold
    assert clamped.current_threshold <= 75.0 + 2.0 * 1.0 + 1e-9  # bounded by the clamp, not by outcome count


def test_threshold_min_max_clip_is_respected():
    c = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=1000.0, ki=0.0, kd=0.0,
                                     window=5, threshold_min=50.0, threshold_max=90.0)
    for _ in range(5):
        c.record_outcome(False)
    assert c.current_threshold == 90.0
    c2 = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=1000.0, ki=0.0, kd=0.0,
                                      window=5, threshold_min=50.0, threshold_max=90.0)
    for _ in range(5):
        c2.record_outcome(True)
    assert c2.current_threshold == 50.0


def test_trailing_window_only_considers_the_most_recent_n_outcomes():
    """window=3: 2 old wins should stop mattering once 3 newer losses
    have pushed them out of the trailing deque."""
    c = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=10.0, ki=0.0, kd=0.0, window=3)
    c.record_outcome(True)
    c.record_outcome(True)
    for _ in range(3):
        c.record_outcome(False)
    assert c.trailing_hit_rate == 0.0
    assert c.current_threshold > 75.0


def test_derivative_term_reacts_to_rate_of_change_not_just_level():
    """Two controllers reach the SAME hit-rate level, but one got there
    by a rapid recent decline and the other by a rapid recent recovery.
    A D-only controller must tell them apart even though a P-only
    controller (kp=0 here) would not."""
    rapid_decline = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=0.0, ki=0.0, kd=20.0,
                                                 window=4)
    for outcome in [True, True, False, False]:
        rapid_decline.record_outcome(outcome)

    rapid_recovery = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=0.0, ki=0.0, kd=20.0,
                                                   window=4)
    for outcome in [False, False, True, True]:
        rapid_recovery.record_outcome(outcome)

    assert rapid_decline.trailing_hit_rate == rapid_recovery.trailing_hit_rate == 0.5
    assert rapid_decline.current_threshold != rapid_recovery.current_threshold


def test_no_lookahead_current_threshold_never_reads_future_outcomes():
    """The strict no-lookahead contract stated in the module docstring:
    current_threshold after N record_outcome() calls must be identical
    regardless of what happens to the controller AFTERWARD - i.e. reading
    it now, then recording 5 more outcomes, must not have retroactively
    changed what it WAS at the earlier point. We prove this by snapshotting
    current_threshold at each step, then confirming those snapshots are
    exactly reproduced by fresh controllers replayed only up to that step."""
    full_history = [True, False, False, True, True, False, True, False, False, True]
    snapshots = []
    live = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=5.0, ki=1.0, kd=2.0, window=4,
                                        integral_clamp=1.0)
    for outcome in full_history:
        live.record_outcome(outcome)
        snapshots.append(live.current_threshold)

    for i in range(len(full_history)):
        replay = AdaptiveThresholdController(base_threshold=75.0, target_hit_rate=0.5, kp=5.0, ki=1.0, kd=2.0,
                                              window=4, integral_clamp=1.0)
        for outcome in full_history[: i + 1]:
            replay.record_outcome(outcome)
        assert replay.current_threshold == pytest.approx(snapshots[i]), \
            f"threshold at step {i} depended on outcomes recorded after it - a lookahead leak"
