from canonical_parameter_registry import CanonicalParameterRegistry
from calibration_config import Revision2ParameterManifest


def test_revision_2_manifest_surface_contract_is_exact():
    expected_names = Revision2ParameterManifest.all_68()

    assert len(expected_names) == 68
    assert len(set(expected_names)) == 68

    registry = CanonicalParameterRegistry()

    assert set(expected_names) == set(registry.params)
    assert registry.total_target_surface() == 68
    assert len(registry.calibratable_names()) == 45
    assert len(registry.hardcoded_names()) == 20
    assert set(registry.hardcoded_names()) == set(Revision2ParameterManifest.hardcoded_20())


def test_registry_identity_is_frozen_and_matches_contract():
    registry = CanonicalParameterRegistry()

    assert registry.identity_sha256() == registry.FROZEN_IDENTITY_SHA256
    registry.verify_frozen_identity()


def test_registry_black_box_mapping_and_fixed_surface_are_consistent():
    registry = CanonicalParameterRegistry()
    black_boxes = registry.black_box_mapping()

    assert set(black_boxes) == {
        "PA",
        "ID",
        "MPC",
        "SafetyGates",
        "PositionManager",
        "UnifiedExecution",
        "P01D",
        "DataIngestion",
        "L2DataCertifier",
        "StartupCapabilityLock",
    }
    assert len(registry.fixed_target_names()) == 23
    assert len(set(registry.calibratable_names())) == 45
