from canonical_parameter_registry import CanonicalParameterRegistry
from calibration_config import Revision2ParameterManifest


def test_revision_2_manifest_surface_contract_is_exact():
    expected_names = Revision2ParameterManifest.all_68()

    assert len(expected_names) == 170
    assert len(set(expected_names)) == 170

    registry = CanonicalParameterRegistry()

    controller = set(registry.CONTROLLER_AUTHORITY)
    assert set(expected_names) | controller == set(registry.params) and not set(expected_names) & controller
    assert registry.total_target_surface() == 170 + 291  # + R5 controller authority (291 rows: 178 tunable, 80 fixed safety, 12 advisory, 21 inactive)
    # BB04 adds 16 eligible engineering-initial controls: 47 -> 63.
    # Earlier changes, 47 rather than 45: rebalance_frequency_minutes (FIXED, non-calibratable --
    # confirmed dead in both engines, read only for coverage tracking, the
    # real refit cadence was a hardcoded constant) was replaced by
    # trailing_stop_atr_mult (genuinely calibratable -- the continuous
    # exit controller's own ATR trail multiplier, independent of the
    # one-shot entry stop's stop_loss_atr_mult). See
    # FROZEN_IDENTITY_SHA256's comment for the full rationale.
    # Engine-scoped: 122 across engines = 46 shared + 76 external-only (see surface_counts());
    # governor authority added 14 external-only eligible controls (108 -> 122).
    assert len(registry.calibratable_names()) == 122 + 178  # + R5 controller authority (291 rows: 178 tunable, 80 fixed safety, 12 advisory, 21 inactive)
    assert len(registry.calibratable_names("IN_HOUSE")) == 46
    assert len(registry.calibratable_names("EXTERNAL")) == 122 + 178
    # Historical method name hardcoded_20() is retained for API
    # continuity, but BB07 adds two explicit fixed safety-envelope values.
    assert len(registry.hardcoded_names()) == 22
    assert set(registry.hardcoded_names()) == set(Revision2ParameterManifest.hardcoded_20())


def test_registry_identity_is_frozen_and_matches_contract():
    registry = CanonicalParameterRegistry()

    assert registry.CONTRACT_ID == "ECS_REVISION_2_PARAMETER_SURFACE_V3"
    assert registry.FROZEN_IDENTITY_SHA256 == (
        # 2026-10-15 R5 controller parameter authority; supersedes 12b700d6caa88b76...
        "fc04a43394e4d744a44991f28058a244790524e54320ca30546f91d0559ab88e"
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
    # + 5 BB08 PositionManager controls + 4 governor-authority fixed values); 122 eligible across engines.
    assert len(registry.fixed_target_names()) == 48 + 113  # + 80 fixed safety, 12 advisory, 21 inactive controller rows
    assert len(set(registry.calibratable_names())) == 122 + 178
