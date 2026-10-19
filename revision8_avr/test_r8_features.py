from dataclasses import replace
import pytest
from .conftest import make_bar
from .r8_features import SymbolFeatureExtractor
from .r8_state import Bar


def test_volume_history_baseline_and_spike(config):
    f = SymbolFeatureExtractor(config)
    for i in range(5):
        assert not f.extract(make_bar(i)).ready
    assert f.extract(make_bar(5, 1000)).liquidity == 1
    assert f.extract(make_bar(6, 10)).liquidity < 0.1
    assert len(f.volumes) == 7
    assert list(f.volumes)[-2:] == [1000, 10]


def test_zero_volume_degraded_and_bounded_history(config):
    f = SymbolFeatureExtractor(config)
    for i in range(50):
        r = f.extract(make_bar(i, 0))
    assert r.degraded and r.liquidity == 0
    assert len(f.volumes) == config.feature_window


def test_same_bar_conflict_and_gap(config):
    f = SymbolFeatureExtractor(config)
    b = make_bar(0)
    r = f.extract(b)
    assert f.extract(b) is r
    with pytest.raises(ValueError):
        f.extract(replace(b, volume=999))
    assert f.extract(make_bar(2)).gap
    with pytest.raises(ValueError):
        f.extract(make_bar(1))


def test_features_causal_and_distinct(config):
    a, b = SymbolFeatureExtractor(config), SymbolFeatureExtractor(config)
    for i in range(30):
        assert a.extract(make_bar(i)) == b.extract(make_bar(i))
    assert a.last_result.pvt != a.last_result.pressure


def test_market_speed_proxy_price_volume_sign_and_no_r_dependency(config):
    f = SymbolFeatureExtractor(config)
    for i in range(5):
        f.extract(make_bar(i, close=100))
    up = f.extract(make_bar(5, close=101, volume=400))
    assert up.market_speed_pu < 1
    assert up.speed_reference_price == pytest.approx(100)
    assert up.relative_volume == 4
    down = f.extract(make_bar(6, close=99))
    assert down.market_speed_pu > 1


@pytest.mark.parametrize('field,value', [('volume', -1), ('close', float('nan')), ('high', 0)])
def test_bad_ohlcv_rejected(field, value):
    with pytest.raises(ValueError):
        replace(make_bar(0), **{field: value})
