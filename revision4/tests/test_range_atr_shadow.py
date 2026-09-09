from types import SimpleNamespace

from revision4.range_atr_shadow import RangeATRShadowMonitor


def test_zero_atr_is_recorded_as_unavailable_not_divided():
    monitor = RangeATRShadowMonitor(period=2)
    bar = SimpleNamespace(high=0.0, low=0.0, close=0.0)

    assert monitor.observe_bars({"FLAT": bar})["FLAT"] is None


def test_candidate_confidence_is_observed_without_affecting_execution():
    monitor = RangeATRShadowMonitor()
    candidate = SimpleNamespace(
        order=SimpleNamespace(order_id="o1", symbol="ABC"),
        pa_confidence=0.42, id_risk_reward=2.0,
    )
    monitor.observe_candidates([candidate], {"ABC": 1.5})
    summary = monitor.summary()
    assert summary["candidate_count"] == 1
    assert summary["pa_confidence"] == {"count": 1, "minimum": 0.42, "maximum": 0.42}
    assert monitor.observe_bars({"FLAT": bar})["FLAT"] is None
