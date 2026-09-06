from canonical_parameter_registry import CanonicalParameterRegistry
from calibration_config import Revision2ParameterManifest


def test_revision_2_manifest_surface_contract_is_exact():
    expected_names = Revision2ParameterManifest.all_68()

    assert len(expected_names) == 68
    assert len(set(expected_names)) == 68

    registry = CanonicalParameterRegistry()

    assert set(expected_names) == set(registry.params)
    assert registry.total_target_surface() == 68
    # 46, not 45: rebalance_frequency_minutes (FIXED, non-calibratable --
    # confirmed dead in both engines, read only for coverage tracking, the
    # real refit cadence was a hardcoded constant) was replaced by
    # trailing_stop_atr_mult (genuinely calibratable -- the continuous
    # exit controller's own ATR trail multiplier, independent of the
    # one-shot entry stop's stop_loss_atr_mult). See
    # FROZEN_IDENTITY_SHA256's comment for the full rationale.
    assert len(registry.calibratable_names()) == 46
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
    # 22/46, not 23/45 -- see test_revision_2_manifest_surface_contract_is_exact's comment.
    assert len(registry.fixed_target_names()) == 22
    assert len(set(registry.calibratable_names())) == 46
