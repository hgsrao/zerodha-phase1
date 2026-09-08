from types import SimpleNamespace

from revision4.range_atr_shadow import RangeATRShadowMonitor


def test_zero_atr_is_recorded_as_unavailable_not_divided():
    monitor = RangeATRShadowMonitor(period=2)
    bar = SimpleNamespace(high=0.0, low=0.0, close=0.0)

    assert monitor.observe_bars({"FLAT": bar})["FLAT"] is None
    assert monitor.observe_bars({"FLAT": bar})["FLAT"] is None
