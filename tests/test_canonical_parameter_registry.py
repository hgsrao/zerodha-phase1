from canonical_parameter_registry import CanonicalParameterRegistry
from calibration_config import Revision2ParameterManifest


def test_revision_2_manifest_surface_contract_is_exact():
    expected_names = Revision2ParameterManifest.all_68()

    assert len(expected_names) == 141
    assert len(set(expected_names)) == 141

    registry = CanonicalParameterRegistry()

    assert set(expected_names) == set(registry.params)
    assert registry.total_target_surface() == 141
    # BB04 adds 16 eligible engineering-initial controls: 47 -> 63.
    # Earlier changes, 47 rather than 45: rebalance_frequency_minutes (FIXED, non-calibratable --
    # confirmed dead in both engines, read only for coverage tracking, the
    # real refit cadence was a hardcoded constant) was replaced by
    # trailing_stop_atr_mult (genuinely calibratable -- the continuous
    # exit controller's own ATR trail multiplier, independent of the
    # one-shot entry stop's stop_loss_atr_mult). See
    # FROZEN_IDENTITY_SHA256's comment for the full rationale.
    # Engine-scoped: 108 across engines = 46 shared + 62 external-only (see surface_counts()).
    assert len(registry.calibratable_names()) == 108
    assert len(registry.calibratable_names("IN_HOUSE")) == 46
    assert len(registry.calibratable_names("EXTERNAL")) == 108
    # Historical method name hardcoded_20() is retained for API
    # continuity, but BB07 adds two explicit fixed safety-envelope values.
    assert len(registry.hardcoded_names()) == 22
    assert set(registry.hardcoded_names()) == set(Revision2ParameterManifest.hardcoded_20())


def test_registry_identity_is_frozen_and_matches_contract():
    registry = CanonicalParameterRegistry()

    assert registry.CONTRACT_ID == "ECS_REVISION_2_PARAMETER_SURFACE_V3"
    assert registry.FROZEN_IDENTITY_SHA256 == (
        "965184f27855c1f8eb7e5fc7f29cd5391e92e07aa074308778f6403f751cc6f9"
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
    }
    # 33 fixed (22 base + 5 external cost/regularization/shadow controls + 1 diagnostic
    # + 5 BB08 PositionManager controls); 108 eligible across engines.
    assert len(registry.fixed_target_names()) == 33
    assert len(set(registry.calibratable_names())) == 108
