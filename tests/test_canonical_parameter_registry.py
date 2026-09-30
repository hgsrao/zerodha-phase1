from canonical_parameter_registry import CanonicalParameterRegistry
from calibration_config import Revision2ParameterManifest


def test_revision_2_manifest_surface_contract_is_exact():
    expected_names = Revision2ParameterManifest.all_68()

    assert len(expected_names) == 179
    assert len(set(expected_names)) == 179

    registry = CanonicalParameterRegistry()

    assert set(expected_names) == set(registry.params)
    assert registry.total_target_surface() == 179
    # BB04 adds 16 eligible engineering-initial controls: 47 -> 63.
    # Earlier changes, 47 rather than 45: rebalance_frequency_minutes (FIXED, non-calibratable --
    # confirmed dead in both engines, read only for coverage tracking, the
    # real refit cadence was a hardcoded constant) was replaced by
    # trailing_stop_atr_mult (genuinely calibratable -- the continuous
    # exit controller's own ATR trail multiplier, independent of the
    # one-shot entry stop's stop_loss_atr_mult). See
    # FROZEN_IDENTITY_SHA256's comment for the full rationale.
    # Engine-scoped: 126 across engines = 46 shared + 80 external-only (see surface_counts());
    # governor authority added 14 external-only eligible controls (108 -> 122), V3 control 4 (126).
    assert len(registry.calibratable_names()) == 126
    assert len(registry.calibratable_names("IN_HOUSE")) == 46
    assert len(registry.calibratable_names("EXTERNAL")) == 126
    # Historical method name hardcoded_20() is retained for API
    # continuity, but BB07 adds two explicit fixed safety-envelope values.
    assert len(registry.hardcoded_names()) == 22
    assert set(registry.hardcoded_names()) == set(Revision2ParameterManifest.hardcoded_20())


def test_registry_identity_is_frozen_and_matches_contract():
    registry = CanonicalParameterRegistry()

    assert registry.CONTRACT_ID == "ECS_REVISION_2_PARAMETER_SURFACE_V3"
    assert registry.FROZEN_IDENTITY_SHA256 == (
        "8176017982ea9abc427976f4b015a12f85f44d96515f39e0ffbf3c6a4c701eea"
    )
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
        "PlantControl",
    }
    # 48 fixed (11 plant-control + 22 base + 5 external cost/regularization/shadow controls + 1 diagnostic
    # + 5 BB08 PositionManager controls + 4 governor-authority + 5 V3 fixed values); 126 eligible across engines.
    assert len(registry.fixed_target_names()) == 53
    assert len(set(registry.calibratable_names())) == 126
