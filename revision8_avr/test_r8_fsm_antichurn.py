"""FSM anti-churn: no single noisy sample may reverse a pulse; the emergency path is bounded and symmetric."""
import copy
import json
import random
import pytest

from .r8_coordinated_controller import PulseFSM, SymbolController
from .r8_state import Context
from .conftest import make_bar


def flips(seq):
    return [(a, b) for a, b in zip(seq, seq[1:]) if a in ('BUY', 'SELL') and b in ('BUY', 'SELL') and a != b]


@pytest.mark.parametrize('first', [0.35, -0.35])
def test_alternating_emergency_inputs_never_flip(config, first):
    f = PulseFSM(config)
    seq = [f.step(first if i % 2 == 0 else -first)[0] for i in range(200)]
    assert flips(seq) == []


def test_random_inputs_reverse_only_after_two_consecutive_emergency_samples(config):
    rnd = random.Random(20261018)
    total = 0
    for _ in range(200):
        f, hist = PulseFSM(config), []
        for _ in range(200):
            s = rnd.choice([-0.4, -0.3, -0.1, 0.0, 0.1, 0.3, 0.4]) if rnd.random() < 0.6 else rnd.uniform(-1, 1)
            prev = f.pulse
            now = f.step(s)[0]
            hist.append(s)
            if prev in ('BUY', 'SELL') and now in ('BUY', 'SELL') and now != prev:
                total += 1
                opposing = (lambda x: x <= -config.emergency) if prev == 'BUY' else (lambda x: x >= config.emergency)
                assert len(hist) >= 2 and opposing(hist[-1]) and opposing(hist[-2])
    assert total > 100                                            # the property was actually exercised


def test_pulse_older_than_hold_bars_goes_through_hold_not_direct_reversal(config):
    f = PulseFSM(config)
    seq = [f.step(s)[0] for s in (0.3, 0.3, 0.3, -0.3, -0.3, -0.3)]
    assert seq[:3] == ['BUY'] * 3 and 'HOLD' in seq[3:5] and flips(seq) == []


def test_fresh_pulse_reverses_only_on_the_second_opposing_emergency_sample(config):
    f = PulseFSM(config)
    assert [f.step(s)[0] for s in (0.3, -0.3, -0.3)] == ['BUY', 'BUY', 'SELL']


def test_non_emergency_sample_resets_the_streak(config):
    f = PulseFSM(config)
    f.step(0.3); f.step(-0.3)
    assert f.emergency_age == 1
    f.step(0.3)                                                   # supportive sample
    assert f.emergency_age == 0


def test_ineligible_input_resets_the_streak_and_forces_hold(config):
    f = PulseFSM(config)
    f.step(0.3); f.step(-0.3)
    assert f.step(0.0, eligible=False)[0] == 'HOLD' and f.emergency_age == 0
    assert f.step(-0.3)[0] == 'HOLD'                              # cooldown, no carried-over streak


def test_cooldown_blocks_immediate_reentry_after_release(config):
    f = PulseFSM(config)
    seq = [f.step(s)[0] for s in (0.3, 0.3, 0.0, 0.3)]            # release, then an enter-level sample on the release bar
    assert seq[2] == 'HOLD'


def test_symbol_controller_alternating_signals_do_not_flip_pulse(config):
    c = SymbolController(config)
    pulses = []
    for i in range(120):
        study = 1.0 if i % 2 == 0 else -1.0
        pulses.append(c.step(make_bar(i, close=100.0 + (i % 2)), Context(0.0, study, study)).pulse)
    assert flips(pulses) == []


def test_checkpoint_roundtrip_preserves_the_pending_streak(config):
    a = SymbolController(config)
    for i in range(6): a.step(make_bar(i), Context(0.0, 0.9, 0.9))
    snap = json.loads(json.dumps(a.snapshot())); b = SymbolController(config); b.restore(snap)
    assert b.fsm.emergency_age == a.fsm.emergency_age and b.fsm.pulse == a.fsm.pulse


@pytest.mark.parametrize('bad', [-1, 2, 5, 10**9, 1.0, True, '1', None])
def test_restore_rejects_an_impossible_emergency_streak(config, bad):
    a = SymbolController(config)
    for i in range(6): a.step(make_bar(i), Context(0.0, 0.9, 0.9))
    snap = json.loads(json.dumps(a.snapshot())); snap['pulse']['emergency_age'] = bad
    b = SymbolController(config)
    before = json.dumps(b.snapshot(), sort_keys=True)
    with pytest.raises((ValueError, TypeError)):
        b.restore(snap)
    assert json.dumps(b.snapshot(), sort_keys=True) == before


def test_restore_still_accepts_a_pre_emergency_age_checkpoint(config):
    a = SymbolController(config)
    for i in range(6): a.step(make_bar(i), Context(0.0, 0.9, 0.9))
    snap = json.loads(json.dumps(a.snapshot())); snap['pulse'].pop('emergency_age')
    b = SymbolController(config); b.restore(snap)
    assert b.fsm.emergency_age == 0
