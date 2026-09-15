"""
PA PREDICTIVE MATHEMATICAL ARCHITECTURE V1
==========================================

Purpose
-------
Define the scientific and interface contract for the PA predictive layer.

This file DOES NOT:
- train a model
- select a model
- choose prediction horizons
- choose label thresholds
- choose sequence length
- choose normalization
- choose hyperparameters
- generate trading authority
- call a broker

Authoritative architecture:

STEP 2 -> PA -> ID -> MPC -> P01D

PA owns directional prediction.

PA does NOT decide whether to trade.
PA does NOT size positions.
PA does NOT authorize execution.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json

from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


STEP2_FREEZE = (
    ROOT
    / "STEP2_MICROSTRUCTURE_FEATURE_DICTIONARY_V1_FREEZE_20260825.json"
)

ID_FREEZE = (
    ROOT
    / "ID_META_LABELING_MATHEMATICAL_ARCHITECTURE_V1_FREEZE_20260825.json"
)

STEP6_FREEZE = (
    ROOT
    / "STEP6_MPC_MATHEMATICAL_ARCHITECTURE_V1_FREEZE_20260825.json"
)


EXPECTED_STEP2_FREEZE_SHA256 = (
    "881f157fbbc0efba59d24cb6d570ec87"
    "2ccebc159c1e5c7d43050e782ff12ad2"
)

EXPECTED_ID_FREEZE_SHA256 = (
    "2103a7ef45bffbc947c95b486ebb0b33"
    "f5c6168bfcfd5c2a241944703dbaf5a7"
)

EXPECTED_STEP6_FREEZE_SHA256 = (
    "6a1ef5c69be84f7e9e6ca831c1639bb0"
    "f95a64f60f4844cf6e11b409ae5140ab"
)


class PAArchitectureError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:

    h = hashlib.sha256()

    with path.open("rb") as f:

        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def verify_dependency(
    path: Path,
    expected_sha256: str,
) -> None:

    if not path.exists():

        raise PAArchitectureError(
            f"REQUIRED FREEZE MISSING: {path.name}"
        )

    actual = sha256_file(path)

    if actual != expected_sha256:

        raise PAArchitectureError(
            f"DEPENDENCY HASH MISMATCH: {path.name}"
        )


ARCHITECTURE = {

    "schema":
        "PA_PREDICTIVE_MATHEMATICAL_ARCHITECTURE_V1",

    "status":
        "CANDIDATE_NOT_FROZEN",

    "serial_path":
        "STEP2 -> PA -> ID -> MPC -> P01D",

    "role":
        "PRIMARY_DIRECTIONAL_PREDICTION_ENGINE",

    "authority": {

        "originates_direction":
            True,

        "decides_take_pass":
            False,

        "sizes_position":
            False,

        "authorizes_execution":
            False,

        "broker_authority":
            "NONE",
    },


    # ========================================================
    # INPUT CONTRACT
    # ========================================================

    "input_contract": {

        "source":
            "CERTIFIED_STEP2_INFORMATION_ONLY",

        "step2_freeze_required":
            True,

        "symbol_identity_required":
            True,

        "observation_timestamp_required":
            True,

        "source_provenance_required":
            True,

        "future_information_allowed":
            False,

        "direct_uncertified_raw_l2_to_model_allowed":
            False,

        "approved_input_representations": [

            "TABULAR_STEP2_FEATURE_VECTOR",

            "CERTIFIED_TEMPORAL_LOB_TENSOR",
        ],

        "top5_depth_boundary":
            True,

        "deeper_than_feed_depth_may_not_be_fabricated":
            True,
    },


    # ========================================================
    # TEMPORAL / TENSOR CONTRACT
    # ========================================================

    "tensor_contract": {

        "purpose":
            "SEQUENCE_MODEL_INPUT",

        "conceptual_shape":
            "[BATCH, SEQUENCE, FEATURES]",

        "sequence_length":
            None,

        "sampling_cadence":
            None,

        "channel_order":
            None,

        "normalization_method":
            None,

        "normalization_fit_scope":
            None,

        "missing_depth_policy":
            None,

        "padding_policy":
            None,

        "warmup_policy":
            None,

        "all_values_require_preregistration":
            True,

        "published_100_snapshot_window_inherited":
            False,

        "published_10_or_20_level_geometry_inherited":
            False,

        "kite_top5_geometry_must_be_respected":
            True,
    },


    # ========================================================
    # LABEL / TARGET CONTRACT
    # ========================================================

    "target_contract": {

        "target_family_selected":
            None,

        "candidate_target_families": [

            "FIXED_HORIZON_3_CLASS",

            "VOLATILITY_SCALED_3_CLASS",

            "TRIPLE_BARRIER_DIRECTIONAL",
        ],

        "class_set": [
            "DOWN",
            "FLAT",
            "UP",
        ],

        "prediction_horizons":
            None,

        "number_of_horizons":
            None,

        "profit_taking_barrier":
            None,

        "stop_loss_barrier":
            None,

        "vertical_barrier":
            None,

        "flat_class_threshold":
            None,

        "volatility_scaling_rule":
            None,

        "published_thresholds_inherited":
            False,

        "all_label_parameters_require_preregistration":
            True,
    },


    # ========================================================
    # OUTPUT CONTRACT
    # ========================================================

    "output_contract": {

        "multi_horizon_required_for_real_mpc":
            True,

        "per_horizon_output": [

            "P_DOWN",

            "P_FLAT",

            "P_UP",
        ],

        "probabilities_must_sum_to_one":
            True,

        "expected_return_output":
            "UNRESOLVED",

        "probability_to_expected_return_mapping":
            None,

        "p_up_minus_p_down_as_expected_return_authorized":
            False,

        "separate_regression_head":
            "RESEARCH_CANDIDATE",

        "conditional_class_return_mapping":
            "RESEARCH_CANDIDATE",

        "distributional_return_model":
            "RESEARCH_CANDIDATE",

        "calibration_status_must_be_recorded":
            True,

        "model_identity_required":
            True,

        "model_sha256_required":
            True,

        "feature_schema_identity_required":
            True,
    },


    # ========================================================
    # MODEL PROMOTION LADDER
    # ========================================================

    "model_ladder": {

        "principle":
            "COMPLEXITY_MUST_EARN_PROMOTION",

        "mandatory_baselines": [

            "NULL_OR_FLAT_BASELINE",

            "LOGISTIC_OR_LINEAR_BASELINE",

            "GRADIENT_BOOSTED_TREE_BASELINE",

            "SIMPLE_MLP_BASELINE",
        ],

        "deep_challengers": [

            "FIVE_LEVEL_DEEPLOB_ADAPTATION",

            "DEEPLOB_REFERENCE",

            "TLOB",
        ],

        "complex_model_may_skip_baseline_comparison":
            False,

        "deep_model_automatically_preferred":
            False,

        "published_checkpoint_may_be_promoted_directly":
            False,

        "external_trained_weights_may_become_our_model_without_validation":
            False,
    },


    # ========================================================
    # EXTERNAL REFERENCE IMPLEMENTATIONS
    # ========================================================

    "reference_implementations": [

        {
            "name":
                "DEEPLOB",

            "role":
                "ESTABLISHED_NEURAL_LOB_BENCHMARK",

            "architecture":
                "CNN_SPATIAL_PLUS_TEMPORAL_SEQUENCE_MODEL",

            "inherit_published_hyperparameters":
                False,
        },

        {
            "name":
                "GENESIS2025",

            "role":
                "IMPLEMENTATION_REFERENCE_ONLY",

            "source_market":
                "CRYPTO_BINANCE",

            "source_depth":
                20,

            "published_sequence_length":
                100,

            "inherit_source_depth":
                False,

            "inherit_sequence_length":
                False,

            "inherit_trained_checkpoint":
                False,

            "inherit_reported_accuracy":
                False,
        },

        {
            "name":
                "TLOB",

            "role":
                "ADVANCED_TRANSFORMER_CHALLENGER",

            "generic_model_contract":
                "[BATCH, SEQUENCE, FEATURES] -> [BATCH, 3]",

            "includes_reference_models": [
                "MLPLOB",
                "DEEPLOB",
                "TLOB",
            ],

            "inherit_hyperparameters":
                False,
        },
    ],


    # ========================================================
    # VALIDATION CONTRACT
    # ========================================================

    "validation_contract": {

        "random_train_test_split_authorized":
            False,

        "temporal_split_required":
            True,

        "purging_required_when_labels_overlap":
            True,

        "embargo_required_when_labels_overlap":
            True,

        "consumed_holdout_retuning_allowed":
            False,

        "future_leakage_allowed":
            False,

        "normalization_fit_on_future_data_allowed":
            False,

        "model_selection_on_final_holdout_allowed":
            False,

        "symbol_generalization_should_be_measured":
            True,

        "regime_stability_should_be_measured":
            True,

        "temporal_degradation_should_be_measured":
            True,
    },


    # ========================================================
    # STATISTICAL EVALUATION
    # ========================================================

    "statistical_evaluation": {

        "candidate_metrics": [

            "LOG_LOSS",

            "MACRO_F1",

            "BALANCED_ACCURACY",

            "CONFUSION_MATRIX",

            "PER_CLASS_PRECISION",

            "PER_CLASS_RECALL",

            "BRIER_SCORE",

            "CALIBRATION_CURVE",
        ],

        "accuracy_alone_sufficient_for_promotion":
            False,
    },


    # ========================================================
    # ECONOMIC EVALUATION
    # ========================================================

    "economic_evaluation": {

        "required_before_real_promotion":
            True,

        "must_measure": [

            "PREDICTED_EDGE_VS_SPREAD",

            "TURNOVER",

            "TRANSACTION_COST_SURVIVAL",

            "LIQUIDITY_FEASIBILITY",

            "MPC_USEFULNESS",
        ],

        "classification_score_alone_can_promote_model":
            False,

        "oracle_future_returns_may_be_used_as_real_pa":
            False,
    },


    # ========================================================
    # ID BOUNDARY
    # ========================================================

    "id_boundary": {

        "pa_owns_direction":
            True,

        "id_may_reverse_pa_direction":
            False,

        "id_role":
            "TAKE_PASS_META_DISCRIMINATION_AND_CALIBRATION",

        "pa_output_must_preserve_model_provenance":
            True,

        "pa_output_must_preserve_feature_provenance":
            True,
    },


    # ========================================================
    # MPC BOUNDARY
    # ========================================================

    "mpc_boundary": {

        "direct_pa_to_mpc":
            False,

        "required_path":
            "PA -> ID -> MPC",

        "multi_horizon_forecast_required":
            True,

        "expected_return_bridge":
            "UNFROZEN",

        "position_sizing_authority":
            "MPC",

        "execution_authority":
            False,
    },


    # ========================================================
    # EXPLICITLY UNFROZEN
    # ========================================================

    "unfrozen_research_parameters": {

        "label_family":
            None,

        "prediction_horizons":
            None,

        "sequence_length":
            None,

        "sampling_cadence":
            None,

        "tensor_channel_order":
            None,

        "normalization_method":
            None,

        "normalization_window":
            None,

        "model_family":
            None,

        "architecture_depth":
            None,

        "hidden_dimensions":
            None,

        "cnn_kernel_sizes":
            None,

        "recurrent_type":
            None,

        "attention_configuration":
            None,

        "learning_rate":
            None,

        "batch_size":
            None,

        "optimizer":
            None,

        "loss_function":
            None,

        "class_weights":
            None,

        "early_stopping_rule":
            None,

        "expected_return_mapping":
            None,
    },


    "current_real_state": {

        "selected_pa_model":
            "NONE",

        "trained_pa_model":
            "NONE",

        "promoted_pa_model":
            "NONE",

        "selected_horizons":
            "NONE",

        "selected_label_family":
            "NONE",

        "expected_return_mapping":
            "NONE",

        "production":
            False,

        "broker_authority":
            "NONE",

        "execution_authority":
            False,
    },
}


FORBIDDEN_IMPORTS = {
    "kiteconnect",
}

FORBIDDEN_CALLS = {
    "place_order",
    "modify_order",
    "cancel_order",
}


def security_audit() -> None:

    tree = ast.parse(
        Path(__file__).read_text(
            encoding="utf-8"
        )
    )

    for node in ast.walk(tree):

        if isinstance(
            node,
            ast.Import,
        ):

            for item in node.names:

                if item.name in FORBIDDEN_IMPORTS:

                    raise PAArchitectureError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):

            if node.module in FORBIDDEN_IMPORTS:

                raise PAArchitectureError(
                    "BROKER IMPORT PROHIBITED"
                )

        if isinstance(
            node,
            ast.Call,
        ):

            name = ""

            if isinstance(
                node.func,
                ast.Attribute,
            ):

                name = node.func.attr

            elif isinstance(
                node.func,
                ast.Name,
            ):

                name = node.func.id

            if name in FORBIDDEN_CALLS:

                raise PAArchitectureError(
                    "BROKER WRITE PROHIBITED"
                )


def verify_dependencies() -> None:

    verify_dependency(
        STEP2_FREEZE,
        EXPECTED_STEP2_FREEZE_SHA256,
    )

    verify_dependency(
        ID_FREEZE,
        EXPECTED_ID_FREEZE_SHA256,
    )

    verify_dependency(
        STEP6_FREEZE,
        EXPECTED_STEP6_FREEZE_SHA256,
    )


def validate_architecture() -> None:

    if ARCHITECTURE[
        "serial_path"
    ] != "STEP2 -> PA -> ID -> MPC -> P01D":

        raise PAArchitectureError(
            "SERIAL ARCHITECTURE VIOLATION"
        )

    authority = ARCHITECTURE[
        "authority"
    ]

    if not authority[
        "originates_direction"
    ]:

        raise PAArchitectureError(
            "PA MUST OWN DIRECTION"
        )

    if authority[
        "decides_take_pass"
    ]:

        raise PAArchitectureError(
            "PA MAY NOT OWN TAKE/PASS"
        )

    if authority[
        "sizes_position"
    ]:

        raise PAArchitectureError(
            "PA MAY NOT SIZE POSITIONS"
        )

    if authority[
        "authorizes_execution"
    ]:

        raise PAArchitectureError(
            "PA MAY NOT AUTHORIZE EXECUTION"
        )

    tensor = ARCHITECTURE[
        "tensor_contract"
    ]

    for key in [
        "sequence_length",
        "sampling_cadence",
        "channel_order",
        "normalization_method",
        "normalization_fit_scope",
        "missing_depth_policy",
        "padding_policy",
        "warmup_policy",
    ]:

        if tensor[key] is not None:

            raise PAArchitectureError(
                f"TENSOR PARAMETER INVENTED: {key}"
            )

    target = ARCHITECTURE[
        "target_contract"
    ]

    for key in [
        "target_family_selected",
        "prediction_horizons",
        "number_of_horizons",
        "profit_taking_barrier",
        "stop_loss_barrier",
        "vertical_barrier",
        "flat_class_threshold",
        "volatility_scaling_rule",
    ]:

        if target[key] is not None:

            raise PAArchitectureError(
                f"TARGET PARAMETER INVENTED: {key}"
            )

    output = ARCHITECTURE[
        "output_contract"
    ]

    if (
        output[
            "probability_to_expected_return_mapping"
        ]
        is not None
    ):

        raise PAArchitectureError(
            "EXPECTED RETURN MAPPING INVENTED"
        )

    if output[
        "p_up_minus_p_down_as_expected_return_authorized"
    ]:

        raise PAArchitectureError(
            "NAIVE PROBABILITY RETURN MAPPING PROHIBITED"
        )

    ladder = ARCHITECTURE[
        "model_ladder"
    ]

    if ladder[
        "complex_model_may_skip_baseline_comparison"
    ]:

        raise PAArchitectureError(
            "COMPLEX MODEL MAY NOT SKIP BASELINES"
        )

    if ladder[
        "deep_model_automatically_preferred"
    ]:

        raise PAArchitectureError(
            "DEEP MODEL MAY NOT BE AUTOMATICALLY PREFERRED"
        )

    for key, value in ARCHITECTURE[
        "unfrozen_research_parameters"
    ].items():

        if value is not None:

            raise PAArchitectureError(
                f"RESEARCH PARAMETER PREMATURELY FROZEN: {key}"
            )

    if ARCHITECTURE[
        "mpc_boundary"
    ][
        "direct_pa_to_mpc"
    ]:

        raise PAArchitectureError(
            "DIRECT PA -> MPC PROHIBITED"
        )

    if ARCHITECTURE[
        "current_real_state"
    ][
        "production"
    ]:

        raise PAArchitectureError(
            "PRODUCTION MUST REMAIN FALSE"
        )


def probability_vector_valid(
    p_down: float,
    p_flat: float,
    p_up: float,
) -> bool:

    values = [
        float(p_down),
        float(p_flat),
        float(p_up),
    ]

    if any(
        x < 0.0 or x > 1.0
        for x in values
    ):

        return False

    return abs(
        sum(values) - 1.0
    ) <= 1e-9


def self_test() -> None:

    security_audit()

    verify_dependencies()

    validate_architecture()

    assert probability_vector_valid(
        0.2,
        0.3,
        0.5,
    )

    assert not probability_vector_valid(
        0.5,
        0.5,
        0.5,
    )

    assert not probability_vector_valid(
        -0.1,
        0.5,
        0.6,
    )

    print("=" * 100)
    print("PA PREDICTIVE MATHEMATICAL ARCHITECTURE V1 SELF TEST")
    print("=" * 100)

    print()
    print(
        "Step 2 feature freeze integrity        : PASS"
    )

    print(
        "ID architecture freeze integrity       : PASS"
    )

    print(
        "Step 6 MPC freeze integrity            : PASS"
    )

    print()
    print(
        "Serial path                            : STEP2 -> PA -> ID -> MPC -> P01D"
    )

    print(
        "PA owns direction                      : YES"
    )

    print(
        "PA owns TAKE/PASS                      : NO"
    )

    print(
        "PA sizes positions                     : NO"
    )

    print(
        "PA execution authority                 : FALSE"
    )

    print()
    print(
        "Top-5 feed geometry respected          : YES"
    )

    print(
        "Uncertified raw L2 bypass              : PROHIBITED"
    )

    print(
        "Sequence length                        : UNFROZEN"
    )

    print(
        "Sampling cadence                       : UNFROZEN"
    )

    print(
        "Tensor channel order                   : UNFROZEN"
    )

    print(
        "Normalization                          : UNFROZEN"
    )

    print()
    print(
        "PA output                              : P(DOWN), P(FLAT), P(UP)"
    )

    print(
        "Multi-horizon real output required     : YES"
    )

    print(
        "Expected-return bridge                 : UNFROZEN"
    )

    print(
        "P(up)-P(down) shortcut                 : NOT AUTHORIZED"
    )

    print()
    print(
        "NULL baseline                          : REQUIRED"
    )

    print(
        "Linear/logistic baseline               : REQUIRED"
    )

    print(
        "Boosted-tree baseline                  : REQUIRED"
    )

    print(
        "Simple MLP baseline                    : REQUIRED"
    )

    print(
        "5-level DeepLOB                        : ADMITTED CHALLENGER"
    )

    print(
        "DeepLOB                                : REFERENCE BENCHMARK"
    )

    print(
        "TLOB                                   : ADMITTED CHALLENGER"
    )

    print()
    print(
        "Genesis2025 trained checkpoint         : NOT INHERITED"
    )

    print(
        "Genesis2025 20-level geometry          : NOT INHERITED"
    )

    print(
        "Genesis2025 100-snapshot window        : NOT INHERITED"
    )

    print(
        "Published model accuracy               : NOT TRANSFERRED"
    )

    print()
    print(
        "Random train/test split                : NOT AUTHORIZED"
    )

    print(
        "Temporal validation                    : REQUIRED"
    )

    print(
        "Purging/embargo                        : REQUIRED WHEN APPLICABLE"
    )

    print(
        "Accuracy alone can promote PA          : NO"
    )

    print(
        "Economic validation                    : REQUIRED"
    )

    print()
    print(
        "REAL PA MODEL                          : NONE"
    )

    print(
        "REAL PA HORIZONS                       : NONE"
    )

    print(
        "REAL LABEL FAMILY                      : NONE"
    )

    print(
        "REAL EXPECTED RETURN MAPPING           : NONE"
    )

    print(
        "Broker authority                       : NONE"
    )

    print(
        "Production                             : FALSE"
    )

    print(
        "P01D                                   : SOVEREIGN"
    )

    print("=" * 100)


def emit_candidate() -> None:

    json_path = (
        ROOT
        / "PA_PREDICTIVE_MATHEMATICAL_ARCHITECTURE_V1_CANDIDATE_20260825.json"
    )

    md_path = (
        ROOT
        / "PA_PREDICTIVE_MATHEMATICAL_ARCHITECTURE_V1_CANDIDATE_20260825.md"
    )

    if (
        json_path.exists()
        or md_path.exists()
    ):

        raise PAArchitectureError(
            "PA CANDIDATE ALREADY EXISTS; REFUSING OVERWRITE"
        )

    payload = {

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "dependencies": {

            STEP2_FREEZE.name:
                EXPECTED_STEP2_FREEZE_SHA256,

            ID_FREEZE.name:
                EXPECTED_ID_FREEZE_SHA256,

            STEP6_FREEZE.name:
                EXPECTED_STEP6_FREEZE_SHA256,
        },

        "architecture":
            ARCHITECTURE,
    }

    json_path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    md = """# PA Predictive Mathematical Architecture V1

## Status

**CANDIDATE / NOT FROZEN**

## Authoritative path

STEP 2 -> PA -> ID -> MPC -> P01D

PA owns directional prediction.

PA does not decide TAKE/PASS.

PA does not size positions.

PA has no execution authority.

## Input boundary

PA may consume only certified Step-2 information.

Two representation families are admitted:

1. tabular Step-2 feature vectors
2. certified temporal LOB tensors

Sequence length, cadence, channel ordering, normalization, padding and
missing-depth handling remain unfrozen.

Published 10-level, 20-level or 100-snapshot configurations are not
automatically inherited.

## Target

Candidate target families include:

- fixed-horizon three-class
- volatility-scaled three-class
- triple-barrier directional

No target family is selected.

No prediction horizon is selected.

No barrier or class threshold is selected.

## Output

For every approved horizon, PA must produce:

- P(DOWN)
- P(FLAT)
- P(UP)

A real MPC requires multi-horizon information.

The conversion from class probabilities to expected returns remains
unfrozen.

P(UP) - P(DOWN) is not an authorized default expected-return formula.

## Promotion ladder

Mandatory baselines:

- NULL / FLAT
- logistic / linear
- gradient-boosted tree
- simple MLP

Deep challengers:

- five-level DeepLOB adaptation
- DeepLOB benchmark
- TLOB

Complexity must earn promotion against simpler baselines.

## External implementations

DeepLOB is an established benchmark.

Genesis2025 is admitted only as an implementation reference. Its
crypto-specific geometry, sequence length, checkpoint, thresholds and
reported accuracy are not inherited.

TLOB is admitted as an advanced challenger and reference framework.

## Validation

Random train/test splitting is not authorized.

Temporal validation is mandatory.

Purging and embargo are required when labels overlap.

Normalization may not be fitted using future information.

Consumed holdouts may not be used for rescue tuning.

## Promotion

Statistical classification quality alone is insufficient.

Economic relevance must also be demonstrated, including spread,
transaction costs, turnover, liquidity feasibility and MPC usefulness.

## Current real state

Selected PA model: **NONE**

Promoted PA model: **NONE**

Prediction horizons: **NONE**

Selected label family: **NONE**

Expected-return mapping: **NONE**

Broker authority: **NONE**

Execution authority: **FALSE**

Production: **FALSE**

P01D remains sovereign.
"""

    md_path.write_text(
        md,
        encoding="utf-8",
    )

    print()
    print(
        "Candidate JSON :",
        json_path.name,
    )

    print(
        "Candidate MD   :",
        md_path.name,
    )

    print(
        "Candidate JSON SHA256 :",
        sha256_file(json_path),
    )

    print(
        "Candidate MD SHA256   :",
        sha256_file(md_path),
    )


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--emit-candidate",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        self_test()

    if args.emit_candidate:

        emit_candidate()

    if not (
        args.self_test
        or args.emit_candidate
    ):

        raise SystemExit(
            "Use --self-test and/or --emit-candidate"
        )


if __name__ == "__main__":

    main()
