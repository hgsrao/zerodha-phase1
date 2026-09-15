"""
STEP 4 - ID META-LABELING MATHEMATICAL ARCHITECTURE V1
=======================================================

ID does NOT originate direction.

PA supplies the side / directional opportunity.

ID is a secondary statistical discrimination layer:
    TAKE / PASS
and eventually a calibrated estimate of:
    P(PRIMARY OPPORTUNITY SUCCEEDS | INFORMATION)

No confidence threshold is invented here.
No model family is chosen here.
No trading authority exists here.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent

STEP6_FREEZE = (
    ROOT
    / "STEP6_MPC_MATHEMATICAL_ARCHITECTURE_V1_FREEZE_20260825.json"
)

EXPECTED_STEP6_FREEZE_SHA256 = (
    "6a1ef5c69be84f7e9e6ca831c1639bb0"
    "f95a64f60f4844cf6e11b409ae5140ab"
)


class IDArchitectureError(RuntimeError):
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


def verify_step6() -> None:

    if not STEP6_FREEZE.exists():
        raise IDArchitectureError(
            "STEP 6 FREEZE MISSING"
        )

    if sha256_file(STEP6_FREEZE) != EXPECTED_STEP6_FREEZE_SHA256:
        raise IDArchitectureError(
            "STEP 6 FREEZE HASH MISMATCH"
        )


ARCHITECTURE = {

    "schema":
        "ID_META_LABELING_MATHEMATICAL_ARCHITECTURE_V1",

    "status":
        "CANDIDATE_NOT_FROZEN",

    "serial_path":
        "STEP2 -> PA -> ID -> MPC -> P01D",

    "role":
        "SECONDARY_DISCRIMINATION_META_MODEL",

    "primary_model":
        "PA",

    "direction_source":
        "PA_ONLY",

    "id_originates_direction":
        False,

    "core_question":
        "SHOULD_THE_PA_OPPORTUNITY_BE_TAKEN_OR_PASSED",

    "target_contract": {

        "meta_label_type":
            "BINARY",

        "positive":
            "PRIMARY_SIDE_WAS_SUCCESSFUL_UNDER_APPROVED_EVENT_LABELING",

        "negative":
            "PRIMARY_SIDE_WAS_NOT_SUCCESSFUL_UNDER_APPROVED_EVENT_LABELING",

        "allowed_values":
            [0, 1],

        "triple_barrier_reference":
            True,

        "profit_taking_barrier":
            None,

        "stop_loss_barrier":
            None,

        "vertical_barrier":
            None,

        "minimum_return":
            None,

        "all_barrier_parameters_require_preregistration":
            True,
    },

    "input_contract": {

        "pa_side_required":
            True,

        "pa_model_identity_required":
            True,

        "pa_prediction_timestamp_required":
            True,

        "context_features_allowed":
            True,

        "step2_features_allowed":
            True,

        "future_information_allowed":
            False,
    },

    "model_contract": {

        "classifier_family":
            None,

        "hyperparameters":
            None,

        "feature_subset":
            None,

        "class_weighting":
            None,

        "selection_requires_validation":
            True,

        "no_inherited_bollinger_primary_model":
            True,
    },

    "probability_contract": {

        "raw_meta_probability":
            "P(META_LABEL=1)",

        "calibration_required_before_probability_is_treated_as_confidence":
            True,

        "reference_framework":
            "SKLEARN_CALIBRATED_CLASSIFIER_CV",

        "permitted_candidate_methods": [
            "SIGMOID",
            "ISOTONIC",
            "TEMPERATURE",
        ],

        "selected_method":
            None,

        "calibration_data_must_be_disjoint_from_model_fit":
            True,

        "default_generic_random_cv_allowed":
            False,

        "financial_time_series_split_required":
            True,
    },

    "evaluation_contract": {

        "purged_temporal_validation_required":
            True,

        "embargo_when_label_overlap_requires_it":
            True,

        "train_calibration_test_separation_required":
            True,

        "probability_metrics_candidate_set": [
            "LOG_LOSS",
            "BRIER_SCORE",
            "CALIBRATION_CURVE",
        ],

        "classification_metrics_candidate_set": [
            "PRECISION",
            "RECALL",
            "ROC_AUC",
            "PR_AUC",
        ],

        "economic_evaluation_separate":
            True,
    },

    "decision_contract": {

        "confidence_floor":
            None,

        "inherited_example_0_90_threshold_allowed":
            False,

        "take_pass_threshold_requires_preregistration":
            True,

        "threshold_tuning_on_consumed_holdout":
            False,

        "abstention_allowed":
            True,
    },

    "mpc_output_contract": {

        "id_to_mpc_role":
            "QUALIFY_AND_CALIBRATE_PA_FORECAST",

        "should_carry_pa_identity":
            True,

        "should_carry_id_identity":
            True,

        "should_carry_calibrated_meta_probability":
            True,

        "should_carry_uncertainty_diagnostics":
            True,

        "direct_execution_authority":
            False,
    },

    "reference_sources": [

        {
            "name":
                "LOPEZ_DE_PRADO_META_LABELING",

            "role":
                "MATHEMATICAL_CONCEPT",
        },

        {
            "name":
                "MLFINPY_TRIPLE_BARRIER_META_LABELING",

            "role":
                "REFERENCE_IMPLEMENTATION",

            "runtime_dependency_status":
                "NOT_ADOPTED",
        },

        {
            "name":
                "DREYHSU_META_LABELING",

            "role":
                "EXPERIMENTAL_EXAMPLE_ONLY",

            "inherit_0_90_threshold":
                False,
        },

        {
            "name":
                "BLACKARBSCEO_AFML_EXERCISES",

            "role":
                "REFERENCE_EXERCISES",
        },

        {
            "name":
                "WONGYATCHUN_AFML_IMPLEMENTATION",

            "role":
                "REFERENCE_IMPLEMENTATION",
        },

        {
            "name":
                "SCIKIT_LEARN_CALIBRATION",

            "role":
                "PROBABILITY_CALIBRATION_REFERENCE",
        },
    ],

    "real_state": {

        "real_id_model":
            "NONE",

        "real_meta_labels":
            "NONE",

        "selected_classifier":
            "NONE",

        "selected_calibration_method":
            "NONE",

        "confidence_threshold":
            "NONE",

        "production":
            False,

        "broker_authority":
            "NONE",

        "execution_authority":
            False,
    },
}


def security_audit() -> None:

    tree = ast.parse(
        Path(__file__).read_text(
            encoding="utf-8"
        )
    )

    forbidden_imports = {
        "kiteconnect",
    }

    forbidden_calls = {
        "place_order",
        "modify_order",
        "cancel_order",
    }

    for node in ast.walk(tree):

        if isinstance(
            node,
            ast.Import,
        ):
            for item in node.names:
                if item.name in forbidden_imports:
                    raise IDArchitectureError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):
            if node.module in forbidden_imports:
                raise IDArchitectureError(
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

            if name in forbidden_calls:
                raise IDArchitectureError(
                    "BROKER WRITE PROHIBITED"
                )


def validate_architecture() -> None:

    if ARCHITECTURE[
        "direction_source"
    ] != "PA_ONLY":

        raise IDArchitectureError(
            "ID DIRECTION AUTHORITY VIOLATION"
        )

    if ARCHITECTURE[
        "id_originates_direction"
    ]:

        raise IDArchitectureError(
            "ID MAY NOT ORIGINATE DIRECTION"
        )

    target = ARCHITECTURE[
        "target_contract"
    ]

    for key in [
        "profit_taking_barrier",
        "stop_loss_barrier",
        "vertical_barrier",
        "minimum_return",
    ]:

        if target[key] is not None:
            raise IDArchitectureError(
                f"BARRIER PARAMETER INVENTED: {key}"
            )

    model = ARCHITECTURE[
        "model_contract"
    ]

    for key in [
        "classifier_family",
        "hyperparameters",
        "feature_subset",
        "class_weighting",
    ]:

        if model[key] is not None:
            raise IDArchitectureError(
                f"MODEL CHOICE INVENTED: {key}"
            )

    prob = ARCHITECTURE[
        "probability_contract"
    ]

    if prob[
        "selected_method"
    ] is not None:

        raise IDArchitectureError(
            "CALIBRATION METHOD PREMATURELY SELECTED"
        )

    decision = ARCHITECTURE[
        "decision_contract"
    ]

    if decision[
        "confidence_floor"
    ] is not None:

        raise IDArchitectureError(
            "CONFIDENCE FLOOR INVENTED"
        )

    if decision[
        "inherited_example_0_90_threshold_allowed"
    ]:

        raise IDArchitectureError(
            "0.90 EXAMPLE THRESHOLD MUST NOT BE INHERITED"
        )

    if ARCHITECTURE[
        "real_state"
    ][
        "execution_authority"
    ]:

        raise IDArchitectureError(
            "ID MAY NOT AUTHORIZE EXECUTION"
        )


def self_test() -> None:

    security_audit()
    verify_step6()
    validate_architecture()

    print("=" * 100)
    print("STEP 4 - ID META-LABELING MATHEMATICAL ARCHITECTURE V1 SELF TEST")
    print("=" * 100)
    print()
    print("Step 6 architecture integrity        : PASS")
    print("Primary side source                  : PA ONLY")
    print("ID originates direction              : NO")
    print("Meta-label target                    : BINARY TAKE/PASS")
    print("Triple-barrier concept               : ADMITTED")
    print("Barrier parameters                   : UNFROZEN")
    print("Classifier family                    : UNFROZEN")
    print("Feature subset                       : UNFROZEN")
    print("Probability calibration              : REQUIRED")
    print("Calibration method                   : UNFROZEN")
    print("Purged temporal validation           : REQUIRED")
    print("Embargo for overlap                  : REQUIRED WHEN APPLICABLE")
    print("Generic random CV                    : NOT AUTHORIZED")
    print("Example 0.90 threshold               : NOT INHERITED")
    print("Confidence floor                     : NONE")
    print("Real ID model                        : NONE")
    print("Broker authority                     : NONE")
    print("Execution authority                  : FALSE")
    print("Production                           : FALSE")
    print("=" * 100)


def emit_candidate():

    json_path = (
        ROOT
        / "ID_META_LABELING_MATHEMATICAL_ARCHITECTURE_V1_CANDIDATE_20260825.json"
    )

    md_path = (
        ROOT
        / "ID_META_LABELING_MATHEMATICAL_ARCHITECTURE_V1_CANDIDATE_20260825.md"
    )

    if json_path.exists() or md_path.exists():
        raise IDArchitectureError(
            "ID CANDIDATE ALREADY EXISTS"
        )

    payload = {
        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "step6_freeze_sha256":
            EXPECTED_STEP6_FREEZE_SHA256,

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

    md = """# ID Meta-Labeling Mathematical Architecture V1

## Status

**CANDIDATE / NOT FROZEN**

PA owns direction.

ID does not create a second directional alpha model.

ID learns a secondary binary discrimination problem:

- 1 = take / primary opportunity succeeded
- 0 = pass / primary opportunity did not succeed

The labeling implementation may use a preregistered triple-barrier
construction with the PA side supplied as the primary side.

### Probability calibration

Raw classifier probabilities may not automatically be treated as
confidence.

A disjoint calibration procedure is required.

Candidate calibration methods include sigmoid, isotonic and
temperature scaling.

No method is selected yet.

### Financial validation

Generic random cross-validation is not authorized.

Temporal splitting, purge rules and embargo where overlapping labels
require it must be defined before model fitting.

### Explicitly unfrozen

- triple-barrier parameters
- event horizon
- classifier family
- model hyperparameters
- ID feature set
- calibration method
- probability threshold
- take/pass threshold
- class weighting

The 0.90 threshold seen in an external experimental repository is not
an architecture default and is not inherited.

### Authority

ID does not authorize execution.

P01D remains sovereign downstream.
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


def main():

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
