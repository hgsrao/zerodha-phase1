"""Regression tests for the approved Stage 1 ₹20 replay configuration."""

from pathlib import Path

from canonical_parameter_registry import CanonicalParameterRegistry
from stage1_sealed_replay_v20 import build_stage1_orchestrator, load_stage1_overrides


def test_stage1_override_file_contains_only_the_approved_economic_change():
    overrides = load_stage1_overrides(Path("config_override_stage1_v20.json"))

    assert overrides == {"minimum_absolute_profit_rupees": 20.0}


def test_stage1_override_is_consumed_by_the_real_orchestrator_config():
    registry = CanonicalParameterRegistry()
    orchestrator = build_stage1_orchestrator(registry)

    assert orchestrator.config.require("minimum_absolute_profit_rupees") == 20.0
    assert orchestrator.config.require("entry_confidence_threshold") == registry.params[
        "entry_confidence_threshold"
    ].default
    assert orchestrator.safety_contract.values["kill_switch_enabled"] is True
