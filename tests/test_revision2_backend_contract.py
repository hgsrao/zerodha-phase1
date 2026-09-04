import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.backend_contract import BackendEvent, canonical_parameter_snapshot, first_divergence


def event(backend="in_house", **changes):
    values = dict(
        backend=backend, event_type="fill", symbol="INFY",
        decision_timestamp="2026-01-02 10:00:00",
        event_timestamp="2026-01-02 10:01:00", sequence=1,
        side="BUY", quantity=2, price=1500.0, config_hash="abc",
    )
    values.update(changes)
    return BackendEvent(**values)


def test_snapshot_contains_all_target_and_safety_parameters_and_is_stable():
    registry = CanonicalParameterRegistry()
    first = canonical_parameter_snapshot(registry)
    second = canonical_parameter_snapshot(registry)
    assert first == second
    assert len(first["target"]) == 68
    assert len(first["safety"]) == 20


def test_snapshot_applies_only_valid_calibration_overrides():
    registry = CanonicalParameterRegistry()
    snapshot = canonical_parameter_snapshot(registry, {"momentum_weight": 0.3})
    assert snapshot["target"]["momentum_weight"] == 0.3
    with pytest.raises(ValueError):
        canonical_parameter_snapshot(registry, {"no_such_parameter": 1})


def test_backend_name_does_not_create_false_divergence():
    assert first_divergence([event()], [event(backend="backtrader")]) is None


def test_first_divergence_reports_exact_event_and_missing_tail():
    changed = event(backend="backtrader", price=1501.0)
    diff = first_divergence([event()], [changed])
    assert diff["index"] == 0
    assert diff["in_house"]["price"] == 1500.0
    assert diff["backtrader"]["price"] == 1501.0
    assert first_divergence([event()], [event(backend="backtrader"), changed])["index"] == 1
