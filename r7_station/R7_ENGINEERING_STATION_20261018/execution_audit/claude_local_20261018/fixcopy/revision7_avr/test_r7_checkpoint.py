from copy import deepcopy
import json
import pytest
from .r7_coordinated_controller import SymbolController
from .conftest import make_bar, context


def test_json_checkpoint_continuation_and_same_bar_cache(config):
    a, b = SymbolController(config), SymbolController(config)
    for i in range(30):
        a.step(make_bar(i), context())
    state = json.loads(json.dumps(a.snapshot()))
    b.restore(state)
    assert b.step(make_bar(29), context()) == a.last_result
    assert b.snapshot() == a.snapshot()
    for i in range(30, 80):
        assert b.step(make_bar(i), context()) == a.step(make_bar(i), context())


@pytest.mark.parametrize('case', ['config', 'nan', 'identity', 'clock', 'integral'])
def test_invalid_restore_atomic(config, case):
    c = SymbolController(config)
    c.step(make_bar(0), context())
    original = c.snapshot()
    bad = deepcopy(original)
    if case == 'config':
        bad['config']['budget_fraction'] = 0.9
    elif case == 'nan':
        bad['governor']['output'] = float('nan')
    elif case == 'identity':
        bad['last_result']['symbol'] = 'TCS'
    elif case == 'clock':
        bad['last_bar']['index'] = -1
    else:
        bad['governor']['integral'] = 100
    with pytest.raises(ValueError):
        c.restore(bad)
    assert c.snapshot() == original
