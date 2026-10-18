from dataclasses import replace
import pytest
from .r7_coordinated_controller import PulseFSM, SymbolController
from .conftest import make_bar, context


def test_release_threshold_and_hold_independent_of_cooldown(config):
    f = PulseFSM(replace(config, hold_bars=3, cooldown_bars=2))
    assert f.step(0.2)[0] == 'BUY'
    assert f.step(0.01)[0] == 'BUY'
    assert f.step(0.01)[0] == 'HOLD'
    assert f.step(-0.2)[0] == 'HOLD'
    assert f.step(-0.2)[0] == 'HOLD'
    assert f.step(-0.2)[0] == 'SELL'


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
