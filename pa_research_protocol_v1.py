"""
PA RESEARCH PROTOCOL V1
=======================

Purpose
-------
Freeze the scientific research procedure for discovering a real PA model.

This file DOES NOT:
- select prediction horizons
- select a label family
- choose triple-barrier values
- choose a model
- choose hyperparameters
- train anything
- consume a final holdout
- generate trade recommendations
- call a broker

Authoritative architecture:

STEP2 -> PA -> ID -> MPC -> P01D

Required model ladder:

NULL
 -> LOGISTIC / LINEAR
 -> GRADIENT BOOSTED TREE
 -> SIMPLE MLP
 -> FIVE-LEVEL DEEPLOB
 -> DEEPLOB REFERENCE
 -> TLOB

Complexity must earn promotion.
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

PA_FREEZE = (
    ROOT
    / "PA_PREDICTIVE_MATHEMATICAL_ARCHITECTURE_V1_FREEZE_20260825.json"
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

EXPECTED_PA_FREEZE_SHA256 = (
    "bb7ec8b778a374b54f6afcc08260fc9e"
    "51939f4e7694a26349bc57ca4136e526"
)

EXPECTED_ID_FREEZE_SHA256 = (
    "2103a7ef45bffbc947c95b486ebb0b33"
    "f5c6168bfcfd5c2a241944703dbaf5a7"
)

EXPECTED_STEP6_FREEZE_SHA256 = (
    "6a1ef5c69be84f7e9e6ca831c1639bb0"
    "f95a64f60f4844cf6e11b409ae5140ab"
)


class PAResearchProtocolError(RuntimeError):
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
    expected: str,
) -> None:

    if not path.exists():

        raise PAResearchProtocolError(
            f"REQUIRED FREEZE MISSING: {path.name}"
        )

    actual = sha256_file(path)

    if actual != expected:

        raise PAResearchProtocolError(
            f"DEPENDENCY HASH MISMATCH: {path.name}"
        )


PROTOCOL = {

    "schema":
        "PA_RESEARCH_PROTOCOL_V1",

    "status":
        "CANDIDATE_NOT_FROZEN",

    "architecture":
        "STEP2 -> PA -> ID -> MPC -> P01D",


    # ========================================================
    # RESEARCH QUESTION
    # ========================================================

    "research_question": {

        "primary":
            "CAN_CERTIFIED_STEP2_INFORMATION_PREDICT_FUTURE_"
            "DIRECTIONAL_MARKET_OUTCOMES_OUT_OF_SAMPLE",

        "secondary":
            "DO_MORE_COMPLEX_MODELS_ADD_REPRODUCIBLE_INFORMATION_"
            "BEYOND_SIMPLER_BASELINES",

        "profit_maximization_is_not_initial_selection_question":
            True,
    },


    # ========================================================
    # DATA GOVERNANCE
    # ========================================================

    "data_governance": {

        "primary_microstructure_source":
            "PROSPECTIVE_CERTIFIED_TOP5_L2_DATA",

        "historical_one_minute_ohlcv_may_be_treated_as_l2":
            False,

        "synthetic_l2_reconstruction_from_ohlcv":
            False,

        "certification_required_before_model_fit":
            True,

        "dataset_certification_must_cover": [

            "SYMBOL_UNIVERSE",

            "SESSION_BOUNDARIES",

            "TIMESTAMP_ORDER",

            "DUPLICATES",

            "GAPS",

            "MISSING_DEPTH",

            "INVALID_PRICE_OR_QUANTITY",

            "SOURCE_PROVENANCE",

            "FEATURE_AVAILABILITY",

            "DATA_START_AND_END",

            "NUMBER_OF_SESSIONS",
        ],

        "historical_ohlcv_allowed_roles": [

            "MPC_CONTROLLER_ENGINEERING",

            "RISK_ESTIMATION",

            "EXOGENOUS_CONTEXT_WHEN_CERTIFIED",

            "REALIZED_OUTCOME_CALCULATION_WHEN_TIMESTAMP_SAFE",
        ],

        "future_information_in_features":
            False,
    },


    # ========================================================
    # TARGET PREREGISTRATION
    # ========================================================

    "target_preregistration": {

        "required_before_first_model_fit":
            True,

        "selected_target_family":
            None,

        "allowed_candidate_families": [

            "FIXED_HORIZON_THREE_CLASS",

            "VOLATILITY_SCALED_THREE_CLASS",

            "TRIPLE_BARRIER_DIRECTIONAL",
        ],

        "prediction_horizons":
            None,

        "flat_threshold":
            None,

        "profit_taking_barrier":
            None,

        "stop_loss_barrier":
            None,

        "vertical_barrier":
            None,

        "volatility_scaling_rule":
            None,

        "same_target_definition_for_all_models":
            True,

        "per_model_target_redefinition":
            False,

        "target_parameters_may_change_after_validation_seen":
            False,

        "next_required_artifact":
            "PA_TARGET_AND_SPLIT_PREREG_V1",
    },


    # ========================================================
    # TEMPORAL SPLIT CONTRACT
    # ========================================================

    "split_contract": {

        "split_dates":
            None,

        "required_partitions": [

            "TRAIN",

            "CALIBRATION",

            "VALIDATION",

            "FINAL_HOLDOUT",
        ],

        "chronological_order_required":
            True,

        "random_train_test_split":
            False,

        "calibration_disjoint_from_model_fit":
            True,

        "final_holdout_used_for_model_selection":
            False,

        "final_holdout_access":
            "ONE_TIME_ONLY_AFTER_MODEL_AND_POLICY_FREEZE",

        "purge_required_for_overlapping_labels":
            True,

        "purge_width":
            "DERIVED_FROM_FROZEN_MAX_LABEL_HORIZON",

        "embargo_required_when_overlap_or_dependency_requires_it":
            True,

        "embargo_width":
            None,

        "cpcv": {

            "allowed":
                True,

            "role":
                "DEVELOPMENT_ROBUSTNESS_ANALYSIS",

            "may_replace_final_untouched_holdout":
                False,

            "parameters":
                None,
        },
    },


    # ========================================================
    # FEATURE GOVERNANCE
    # ========================================================

    "feature_governance": {

        "step2_frozen_dictionary_required":
            True,

        "feature_set_selected":
            None,

        "feature_selection_must_be_train_only":
            True,

        "holdout_driven_feature_selection":
            False,

        "published_feature_signs_inherited":
            False,

        "published_feature_thresholds_inherited":
            False,

        "distance_weights_invented":
            False,

        "model_input_families": {

            "TABULAR":
                "CERTIFIED_STEP2_FEATURE_VECTOR",

            "SEQUENCE":
                "CERTIFIED_TOP5_TEMPORAL_TENSOR",
        },

        "deep_model_may_fabricate_missing_book_levels":
            False,
    },


    # ========================================================
    # NORMALIZATION / PREPROCESSING
    # ========================================================

    "preprocessing_contract": {

        "normalization_method":
            None,

        "normalization_fit_on_train_only":
            True,

        "validation_statistics_used_to_fit_normalizer":
            False,

        "holdout_statistics_used_to_fit_normalizer":
            False,

        "sequence_length":
            None,

        "sampling_cadence":
            None,

        "missing_depth_policy":
            None,

        "warmup_policy":
            None,

        "padding_policy":
            None,

        "all_material_preprocessing_choices_frozen_before_fit":
            True,
    },


    # ========================================================
    # MODEL LADDER
    # ========================================================

    "model_ladder": [

        {
            "stage":
                "PA0",

            "family":
                "NULL_BASELINE",

            "required":
                True,
        },

        {
            "stage":
                "PA1",

            "family":
                "LOGISTIC_OR_LINEAR",

            "required":
                True,
        },

        {
            "stage":
                "PA2",

            "family":
                "GRADIENT_BOOSTED_TREE",

            "reference_candidate":
                "XGBOOST",

            "required":
                True,
        },

        {
            "stage":
                "PA3",

            "family":
                "SIMPLE_MLP",

            "required":
                True,
        },

        {
            "stage":
                "PA4",

            "family":
                "FIVE_LEVEL_DEEPLOB",

            "required_before_tlob":
                True,
        },

        {
            "stage":
                "PA5",

            "family":
                "DEEPLOB_REFERENCE",

            "top5_adaptation_required":
                True,

            "fabricated_levels_allowed":
                False,
        },

        {
            "stage":
                "PA6",

            "family":
                "TLOB",

            "advanced_challenger":
                True,
        },
    ],


    # ========================================================
    # FAIR COMPARISON CONTRACT
    # ========================================================

    "comparison_contract": {

        "same_target_for_all_models":
            True,

        "same_train_validation_holdout_boundaries":
            True,

        "same_eligible_observation_population":
            True,

        "model_specific_future_information":
            False,

        "model_family_hyperparameter_budget_must_be_declared_before_search":
            True,

        "hyperparameter_search_budget":
            None,

        "search_after_final_holdout":
            False,

        "deep_model_gets_unlimited_search_because_more_complex":
            False,

        "published_checkpoint_may_skip_training":
            False,
    },


    # ========================================================
    # STATISTICAL GATE
    # ========================================================

    "statistical_gate": {

        "primary_metric":
            "MULTICLASS_LOG_LOSS",

        "reason":
            "PA_OUTPUT_IS_PROBABILISTIC_AND_LOG_LOSS_IS_A_"
            "PROPER_SCORING_RULE",

        "secondary_metrics": [

            "MACRO_F1",

            "BALANCED_ACCURACY",

            "BRIER_SCORE",

            "PER_CLASS_PRECISION",

            "PER_CLASS_RECALL",

            "CONFUSION_MATRIX",

            "CALIBRATION_CURVE",
        ],

        "accuracy_primary":
            False,

        "promotion_requires_primary_metric_improvement":
            True,

        "improvement_direction":
            "LOWER_LOG_LOSS_IS_BETTER",

        "uncertainty_interval_method":
            None,

        "significance_or_resampling_method":
            None,

        "must_be_preregistered_before_validation":
            True,
    },


    # ========================================================
    # COMPLEXITY PROMOTION GATE
    # ========================================================

    "complexity_gate": {

        "challenger_compares_against":
            "BEST_PREVIOUS_SIMPLER_ELIGIBLE_MODEL",

        "complexity_without_oos_improvement":
            "REJECT",

        "tie":
            "PREFER_SIMPLER_MODEL",

        "worse_primary_metric":
            "REJECT",

        "may_promote_because_architecture_is_newer":
            False,

        "may_promote_because_external_paper_reports_better_results":
            False,
    },


    # ========================================================
    # CALIBRATION BOUNDARY
    # ========================================================

    "calibration_boundary": {

        "pa_raw_probability_calibration_status_must_be_recorded":
            True,

        "calibration_partition":
            "CALIBRATION",

        "calibration_method":
            None,

        "id_owns_final_meta_probability_calibration":
            True,

        "pa_calibration_may_replace_id":
            False,
    },


    # ========================================================
    # ECONOMIC GATE
    # ========================================================

    "economic_gate": {

        "required_before_real_pa_promotion":
            True,

        "stage":
            "AFTER_STATISTICAL_ELIGIBILITY",

        "expected_return_bridge_required":
            True,

        "expected_return_bridge":
            None,

        "nse_cost_model_required":
            True,

        "nse_cost_model":
            None,

        "must_evaluate": [

            "EDGE_RELATIVE_TO_SPREAD",

            "TURNOVER",

            "TRANSACTION_COST_SURVIVAL",

            "LIQUIDITY_FEASIBILITY",

            "STABILITY_ACROSS_SYMBOLS",

            "STABILITY_ACROSS_REGIMES",

            "MPC_USEFULNESS",
        ],

        "raw_classification_accuracy_can_bypass_economic_gate":
            False,

        "oracle_future_return_results_count_as_real_pa_performance":
            False,
    },


    # ========================================================
    # OUTPUT / ARTIFACT CONTRACT
    # ========================================================

    "required_run_artifacts": [

        "DATASET_CERTIFICATION",

        "TARGET_PREREGISTRATION",

        "SPLIT_MANIFEST",

        "FEATURE_SCHEMA",

        "PREPROCESSING_CONFIG",

        "MODEL_CONFIG",

        "ENVIRONMENT_LOCK",

        "RANDOM_SEEDS",

        "TRAIN_METRICS",

        "CALIBRATION_METRICS",

        "VALIDATION_PREDICTIONS",

        "VALIDATION_METRICS",

        "MODEL_ARTIFACT_SHA256",

        "PROMOTION_DECISION",
    ],

    "final_candidate_additional_artifacts": [

        "FINAL_HOLDOUT_PREDICTIONS",

        "FINAL_HOLDOUT_METRICS",

        "ECONOMIC_VALIDATION",

        "PA_PROMOTION_FREEZE",
    ],


    # ========================================================
    # FAILURE / NO-RESCUE CONTRACT
    # ========================================================

    "no_rescue_contract": {

        "retune_after_validation_failure":
            False,

        "change_horizon_after_validation_failure":
            False,

        "change_label_threshold_after_validation_failure":
            False,

        "remove_bad_symbols_after_validation_failure":
            False,

        "remove_bad_dates_after_validation_failure":
            False,

        "flip_direction_after_validation_failure":
            False,

        "change_metric_after_validation_failure":
            False,

        "reinterpret_failed_model_as_pass":
            False,

        "new_hypothesis_requires_new_preregistered_branch":
            True,
    },


    # ========================================================
    # AUTHORITY
    # ========================================================

    "authority": {

        "research_only":
            True,

        "pa_execution_authority":
            False,

        "broker_authority":
            "NONE",

        "production":
            False,

        "p01d":
            "SOVEREIGN",
    },


    # ========================================================
    # CURRENT STATE
    # ========================================================

    "current_state": {

        "certified_pa_l2_training_dataset":
            "NONE",

        "selected_target_family":
            "NONE",

        "selected_horizons":
            "NONE",

        "frozen_split_dates":
            "NONE",

        "selected_feature_set":
            "NONE",

        "selected_normalization":
            "NONE",

        "selected_pa_model":
            "NONE",

        "promoted_pa_model":
            "NONE",

        "final_holdout_consumed":
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

                    raise PAResearchProtocolError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):

            if node.module in FORBIDDEN_IMPORTS:

                raise PAResearchProtocolError(
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

                raise PAResearchProtocolError(
                    "BROKER WRITE PROHIBITED"
                )


def verify_dependencies() -> None:

    verify_dependency(
        STEP2_FREEZE,
        EXPECTED_STEP2_FREEZE_SHA256,
    )

    verify_dependency(
        PA_FREEZE,
        EXPECTED_PA_FREEZE_SHA256,
    )

    verify_dependency(
        ID_FREEZE,
        EXPECTED_ID_FREEZE_SHA256,
    )

    verify_dependency(
        STEP6_FREEZE,
        EXPECTED_STEP6_FREEZE_SHA256,
    )


def validate_protocol() -> None:

    if PROTOCOL[
        "architecture"
    ] != "STEP2 -> PA -> ID -> MPC -> P01D":

        raise PAResearchProtocolError(
            "ARCHITECTURE VIOLATION"
        )

    data = PROTOCOL[
        "data_governance"
    ]

    if data[
        "historical_one_minute_ohlcv_may_be_treated_as_l2"
    ]:

        raise PAResearchProtocolError(
            "OHLCV MAY NOT BE REBRANDED AS L2"
        )

    if data[
        "synthetic_l2_reconstruction_from_ohlcv"
    ]:

        raise PAResearchProtocolError(
            "SYNTHETIC HISTORICAL L2 PROHIBITED"
        )

    target = PROTOCOL[
        "target_preregistration"
    ]

    for key in [
        "selected_target_family",
        "prediction_horizons",
        "flat_threshold",
        "profit_taking_barrier",
        "stop_loss_barrier",
        "vertical_barrier",
        "volatility_scaling_rule",
    ]:

        if target[key] is not None:

            raise PAResearchProtocolError(
                f"TARGET PARAMETER INVENTED: {key}"
            )

    split = PROTOCOL[
        "split_contract"
    ]

    if split[
        "split_dates"
    ] is not None:

        raise PAResearchProtocolError(
            "SPLIT DATES PREMATURELY INVENTED"
        )

    if split[
        "random_train_test_split"
    ]:

        raise PAResearchProtocolError(
            "RANDOM SPLIT PROHIBITED"
        )

    if split[
        "final_holdout_used_for_model_selection"
    ]:

        raise PAResearchProtocolError(
            "FINAL HOLDOUT MAY NOT SELECT MODELS"
        )

    preprocessing = PROTOCOL[
        "preprocessing_contract"
    ]

    for key in [
        "normalization_method",
        "sequence_length",
        "sampling_cadence",
        "missing_depth_policy",
        "warmup_policy",
        "padding_policy",
    ]:

        if preprocessing[key] is not None:

            raise PAResearchProtocolError(
                f"PREPROCESSING PARAMETER INVENTED: {key}"
            )

    ladder = [
        x["family"]
        for x in PROTOCOL[
            "model_ladder"
        ]
    ]

    expected_ladder = [
        "NULL_BASELINE",
        "LOGISTIC_OR_LINEAR",
        "GRADIENT_BOOSTED_TREE",
        "SIMPLE_MLP",
        "FIVE_LEVEL_DEEPLOB",
        "DEEPLOB_REFERENCE",
        "TLOB",
    ]

    if ladder != expected_ladder:

        raise PAResearchProtocolError(
            "MODEL LADDER CHANGED"
        )

    statistical = PROTOCOL[
        "statistical_gate"
    ]

    if statistical[
        "primary_metric"
    ] != "MULTICLASS_LOG_LOSS":

        raise PAResearchProtocolError(
            "PRIMARY METRIC CHANGED"
        )

    if statistical[
        "accuracy_primary"
    ]:

        raise PAResearchProtocolError(
            "ACCURACY MAY NOT BE PRIMARY"
        )

    economic = PROTOCOL[
        "economic_gate"
    ]

    if economic[
        "expected_return_bridge"
    ] is not None:

        raise PAResearchProtocolError(
            "EXPECTED RETURN BRIDGE INVENTED"
        )

    if economic[
        "nse_cost_model"
    ] is not None:

        raise PAResearchProtocolError(
            "NSE COST MODEL INVENTED"
        )

    authority = PROTOCOL[
        "authority"
    ]

    if authority[
        "pa_execution_authority"
    ]:

        raise PAResearchProtocolError(
            "PA EXECUTION AUTHORITY PROHIBITED"
        )

    if authority[
        "production"
    ]:

        raise PAResearchProtocolError(
            "PRODUCTION MUST REMAIN FALSE"
        )


def self_test() -> None:

    security_audit()
    verify_dependencies()
    validate_protocol()

    print("=" * 100)
    print("PA RESEARCH PROTOCOL V1 SELF TEST")
    print("=" * 100)

    print()
    print(
        "Step 2 freeze integrity               : PASS"
    )

    print(
        "PA architecture freeze integrity      : PASS"
    )

    print(
        "ID architecture freeze integrity      : PASS"
    )

    print(
        "Step 6 MPC freeze integrity           : PASS"
    )

    print()
    print(
        "Primary PA dataset                    : PROSPECTIVE CERTIFIED TOP-5 L2"
    )

    print(
        "Historical 1-minute OHLCV as L2       : PROHIBITED"
    )

    print(
        "Synthetic historical L2               : PROHIBITED"
    )

    print(
        "Dataset certification before fit      : REQUIRED"
    )

    print()
    print(
        "Target family                         : UNFROZEN"
    )

    print(
        "Prediction horizons                   : UNFROZEN"
    )

    print(
        "Triple-barrier parameters             : UNFROZEN"
    )

    print(
        "Target must freeze before fit          : YES"
    )

    print()
    print(
        "Chronological TRAIN                    : REQUIRED"
    )

    print(
        "Disjoint CALIBRATION                   : REQUIRED"
    )

    print(
        "VALIDATION                             : REQUIRED"
    )

    print(
        "FINAL HOLDOUT                          : ONE-TIME"
    )

    print(
        "Random split                           : PROHIBITED"
    )

    print(
        "Purging                                : REQUIRED WHEN LABELS OVERLAP"
    )

    print(
        "Embargo                                : REQUIRED WHEN APPLICABLE"
    )

    print(
        "CPCV                                   : DEVELOPMENT ROBUSTNESS ONLY"
    )

    print()
    print(
        "Primary statistical metric            : MULTICLASS LOG LOSS"
    )

    print(
        "Accuracy primary                       : NO"
    )

    print(
        "Tie                                    : PREFER SIMPLER MODEL"
    )

    print(
        "Complexity without OOS gain            : REJECT"
    )

    print()
    print(
        "PA0 NULL                               : REQUIRED"
    )

    print(
        "PA1 LOGISTIC / LINEAR                  : REQUIRED"
    )

    print(
        "PA2 GRADIENT BOOSTED TREE              : REQUIRED"
    )

    print(
        "PA3 SIMPLE MLP                         : REQUIRED"
    )

    print(
        "PA4 FIVE-LEVEL DEEPLOB                 : CHALLENGER"
    )

    print(
        "PA5 DEEPLOB REFERENCE                  : CHALLENGER"
    )

    print(
        "PA6 TLOB                               : ADVANCED CHALLENGER"
    )

    print()
    print(
        "Economic validation                    : REQUIRED BEFORE REAL PROMOTION"
    )

    print(
        "Expected-return bridge                 : UNFROZEN"
    )

    print(
        "NSE cost model                         : UNFROZEN"
    )

    print(
        "Oracle results count as PA evidence    : NO"
    )

    print()
    print(
        "Validation rescue tuning               : PROHIBITED"
    )

    print(
        "Holdout-driven feature exclusion       : PROHIBITED"
    )

    print(
        "Holdout-driven horizon changes         : PROHIBITED"
    )

    print(
        "Failed model sign flip                 : PROHIBITED"
    )

    print()
    print(
        "REAL PA TRAINING DATASET               : NONE"
    )

    print(
        "REAL TARGET                            : NONE"
    )

    print(
        "REAL MODEL                             : NONE"
    )

    print(
        "FINAL HOLDOUT CONSUMED                 : FALSE"
    )

    print(
        "Broker authority                       : NONE"
    )

    print(
        "Execution authority                    : FALSE"
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
        / "PA_RESEARCH_PROTOCOL_V1_CANDIDATE_20260825.json"
    )

    md_path = (
        ROOT
        / "PA_RESEARCH_PROTOCOL_V1_CANDIDATE_20260825.md"
    )

    if json_path.exists() or md_path.exists():

        raise PAResearchProtocolError(
            "PA RESEARCH PROTOCOL CANDIDATE ALREADY EXISTS"
        )

    payload = {

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "dependencies": {

            STEP2_FREEZE.name:
                EXPECTED_STEP2_FREEZE_SHA256,

            PA_FREEZE.name:
                EXPECTED_PA_FREEZE_SHA256,

            ID_FREEZE.name:
                EXPECTED_ID_FREEZE_SHA256,

            STEP6_FREEZE.name:
                EXPECTED_STEP6_FREEZE_SHA256,
        },

        "protocol":
            PROTOCOL,
    }

    json_path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    md = """# PA Research Protocol V1

## Status

**CANDIDATE / NOT FROZEN**

## Purpose

Establish the research rules before fitting any real PA model.

## Data

The primary microstructure research dataset must be prospectively
collected and certified top-5 L2 data.

Historical one-minute OHLCV may not be rebranded as L2 and may not be
used to synthesize a historical order book.

## Preregistration

Before the first model is fitted, a separate target-and-split
preregistration must freeze:

- target family
- prediction horizons
- label/barrier parameters
- chronological split dates
- purge/embargo rules
- preprocessing
- feature schema
- model-search budgets

## Required partitions

1. TRAIN
2. CALIBRATION
3. VALIDATION
4. FINAL HOLDOUT

The final holdout is one-time and may not be used for model selection.

## Model ladder

PA0 — NULL

PA1 — logistic / linear

PA2 — gradient-boosted tree

PA3 — simple MLP

PA4 — five-level DeepLOB

PA5 — DeepLOB reference

PA6 — TLOB

Complexity must beat the best eligible simpler model out of sample.

Ties go to the simpler model.

## Primary statistical metric

**Multiclass log loss**

Secondary metrics include macro F1, balanced accuracy, Brier score,
per-class precision/recall, confusion matrix and calibration curves.

## Economic gate

Statistical eligibility is not sufficient for real promotion.

A surviving PA must later demonstrate:

- edge relative to spread
- transaction-cost survival
- turnover feasibility
- liquidity feasibility
- stability across symbols
- stability across regimes
- usefulness to MPC

The expected-return bridge and real NSE cost model remain unfrozen.

## No rescue

After validation failure, the same hypothesis may not be rescued by:

- changing horizons
- changing labels
- changing thresholds
- removing bad symbols
- removing bad dates
- changing the primary metric
- sign flipping
- retuning on the consumed validation set

A materially new idea requires a new preregistered research branch.

## Current state

Certified PA L2 training dataset: **NONE**

Target: **NONE**

Prediction horizons: **NONE**

Selected model: **NONE**

Promoted PA model: **NONE**

Final holdout consumed: **FALSE**

Execution authority: **FALSE**

Broker authority: **NONE**

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
