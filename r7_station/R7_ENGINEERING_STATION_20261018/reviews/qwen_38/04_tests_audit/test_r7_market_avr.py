from dataclasses import replace
import math
from .r7_features import SymbolFeatureExtractor
from .r7_market_avr import MarketAVR
from .conftest import make_bar


def test_actual_r6_pi_keyword_api_and_tuple(config):
    avr = MarketAVR()
    value, telemetry = avr.pi.step(error=0.1, dt=60.0, bias=0.0, low=-1.0, high=1.0)
    assert math.isfinite(value) and isinstance(telemetry, dict)


def test_avr_bounded_convergent_and_bad_data_decay(config):
    avr = MarketAVR()
    f = SymbolFeatureExtractor(config)
    for i in range(100):
        features = f.extract(make_bar(i))
        vote = avr.step(features, 60)
        assert -1 <= vote.score <= 1 and 0 <= vote.quality <= 1
    old = abs(vote.score)
    for _ in range(10):
        vote = avr.step(replace(features, degraded=True), 60)
    assert abs(vote.score) < old and vote.quality == 0
    assert abs(avr.pi.i_term) <= avr.pi.cfg.integral_limit
