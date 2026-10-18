from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.startup_validation import validate_runtime_parameters, validate_safety_contract


def test_real_registry_defaults_pass():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    safety = {name: spec.default for name, spec in registry.safety_params.items()}
    assert validate_runtime_parameters(registry, values) == []
    assert validate_safety_contract(registry, safety) == []


def test_missing_out_of_range_and_unknown_params_are_all_caught():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    values["momentum_weight"] = 999.0
    del values["vwap_weight"]
    values["nonexistent_param"] = 1
    errors = validate_runtime_parameters(registry, values)
    joined = " ".join(errors)
    assert "vwap_weight" in joined and "Field required" in joined
    assert "momentum_weight" in joined
    assert "nonexistent_param" in joined and "not permitted" in joined


def test_safety_sentinel_zero_zero_range_is_not_enforced_as_a_literal_bound():
    # minimum == maximum == 0 is the registry's "no real range" placeholder,
    # not a literal [0, 0] constraint -- every safety default would fail
    # otherwise, since none of them are actually 0.
    registry = CanonicalParameterRegistry()
    safety = {name: spec.default for name, spec in registry.safety_params.items()}
    assert safety["max_daily_loss_rupees"] != 0
    assert validate_safety_contract(registry, safety) == []


def test_wrong_type_is_caught():
    registry = CanonicalParameterRegistry()
    safety = {name: spec.default for name, spec in registry.safety_params.items()}
    safety["kill_switch_enabled"] = "yes"
    errors = validate_safety_contract(registry, safety)
    assert any("kill_switch_enabled" in e for e in errors)
