from dataclasses import replace
import pytest
from .r7_coordinated_controller import PulseFSM, SymbolController
from .conftest import make_bar, context


def test_release_threshold_and_hold_independent_of_cooldown(config):
    # hold_bars=3: the pulse is visible for exactly 3 completed bars before release.
    # cooldown_bars=2: the release bar plus one more HOLD bar precede a new pulse.
    f = PulseFSM(replace(config, hold_bars=3, cooldown_bars=2))
    assert f.step(0.2)[0] == 'BUY'
    assert f.step(0.01)[0] == 'BUY'
    assert f.step(0.01)[0] == 'BUY'
    assert f.step(0.01)[0] == 'HOLD'
    assert f.step(-0.2)[0] == 'HOLD'
    assert f.step(-0.2)[0] == 'SELL'


@pytest.mark.parametrize('hold_bars', [1, 2, 4])
def test_pulse_visible_for_exactly_hold_bars(config, hold_bars):
    f = PulseFSM(replace(config, hold_bars=hold_bars, max_pulse_age=max(hold_bars, 30)))
    seen = [f.step(0.2)[0]] + [f.step(0.0)[0] for _ in range(hold_bars + 1)]
    assert seen.count('BUY') == hold_bars
    assert seen[hold_bars] == 'HOLD'


@pytest.mark.parametrize('cooldown', [1, 2, 3])
def test_cooldown_counts_release_bar(config, cooldown):
    f = PulseFSM(replace(config, hold_bars=1, cooldown_bars=cooldown))
    assert f.step(0.2)[0] == 'BUY'
    assert f.step(0.0)[0] == 'HOLD'                     # release bar = HOLD bar 1
    for _ in range(cooldown - 1):
        assert f.step(0.5)[0] == 'HOLD'
    assert f.step(0.5)[0] == 'BUY'


def test_max_pulse_age_release_when_unsupported(config):
    f = PulseFSM(replace(config, hold_bars=1, max_pulse_age=3))
    assert f.step(0.2)[0] == 'BUY'
    assert [f.step(0.1)[0] for _ in range(3)] == ['BUY', 'BUY', 'HOLD']


@pytest.mark.parametrize('sign', [1, -1])
def test_emergency_reversal_symmetric(config, sign):
    f = PulseFSM(config)
    assert f.step(sign * 0.2)[0] == ('BUY' if sign == 1 else 'SELL')
    assert f.step(-sign * 0.3)[0] == ('SELL' if sign == 1 else 'BUY')


def test_hysteresis_and_invalid_input(config):
    f = PulseFSM(config)
    f.step(0.2)
    for _ in range(5):
        assert f.step(0.1)[0] == 'BUY'
    assert f.step(0.01)[0] == 'HOLD'
    f.step(0.3)
    assert f.step(0.3, eligible=False)[0] == 'HOLD'


def test_same_bar_immutable_context_and_no_double_integration(config):
    c = SymbolController(config)
    b, ctx = make_bar(0), context()
    c.step(b, ctx)
    before = c.snapshot()
    c.step(b, ctx)
    assert c.snapshot() == before
    with pytest.raises(ValueError):
        c.step(b, context(study=0.2))
    assert c.snapshot() == before
