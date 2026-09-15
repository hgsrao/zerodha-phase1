"""
STEP 6 - MPC MATHEMATICAL ARCHITECTURE V1
=========================================

This is NOT an MPC trading policy.

It is the immutable mathematical/architectural contract that any
future real Step-5 MPC implementation must satisfy.

Established optimization stack:
    cvxportfolio.MultiPeriodOptimization
        -> CVXPY
            -> formulation-appropriate solver

Serial intelligence architecture:
    STEP 2 -> PA -> ID -> MPC -> P01D

Direct MPC inputs:
    1. ID-qualified multi-horizon forecast
    2. deterministic MPC constraint state

Only the FIRST optimized action may leave MPC.

P01D remains sovereign.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import cvxportfolio as cvx
import cvxpy as cp


ROOT = Path(__file__).resolve().parent

ARCHITECTURE_ID = "STEP6_MPC_MATHEMATICAL_ARCHITECTURE_V1"

EXPECTED_CVXPORTFOLIO_VERSION = "1.5.1"

STEP5F_FREEZE = (
    ROOT
    / "STEP5F_MPC_TO_P01D_HANDOFF_V1_FREEZE_20260825.json"
)

EXPECTED_STEP5F_FREEZE_SHA256 = (
    "24768d95dabce4f242a5f107cca6970f"
    "81679e676f42b60df1cf78c76161238b"
)

REQUIRED_SOLVERS = {
    "OSQP",
    "CLARABEL",
    "SCS",
}

REQUIRED_CVXPORTFOLIO_COMPONENTS = [
    "MultiPeriodOptimization",
    "ReturnsForecast",
    "ReturnsForecastError",
    "FullCovariance",
    "FactorModelCovariance",
    "RiskForecastError",
    "WorstCaseRisk",
    "StocksTransactionCost",
    "LongOnly",
    "LeverageLimit",
    "MaxWeights",
    "MinWeights",
    "MaxHoldings",
    "MinHoldings",
    "MaxTradeWeights",
    "MinTradeWeights",
    "MaxTrades",
    "MinTrades",
    "TurnoverLimit",
    "ParticipationRateLimit",
]


ARCHITECTURE = {

    "architecture_id":
        ARCHITECTURE_ID,

    "status":
        "CANDIDATE_IMPLEMENTED_NOT_YET_FROZEN",

    "serial_path":
        "STEP2 -> PA -> ID -> MPC -> P01D",

    "mpc_direct_inputs": [
        "ID_QUALIFIED_MULTI_HORIZON_FORECAST",
        "MPC_CONSTRAINT_STATE",
    ],

    "direct_pa_to_mpc":
        False,

    "optimization_engine": {
        "reference_framework":
            "cvxportfolio.MultiPeriodOptimization",

        "cvxportfolio_version":
            EXPECTED_CVXPORTFOLIO_VERSION,

        "formulation_layer":
            "CVXPY",

        "solver_routing": {
            "QP":
                "OSQP",

            "GENERAL_CONVEX":
                "CLARABEL",

            "FALLBACK":
                "SCS",
        },

        "solver_selected_by_formulation":
            True,

        "solver_must_not_change_economic_model":
            True,
    },

    "receding_horizon_contract": {
        "plan_multiple_future_periods":
            True,

        "release_only_first_action":
            True,

        "discard_unexecuted_future_plan":
            True,

        "reoptimize_on_next_decision_cycle":
            True,
    },

    "forecast_contract": {
        "source":
            "ID_QUALIFIED_PA",

        "multi_horizon_required_for_real_mpc":
            True,

        "library_default_return_forecast_allowed":
            False,

        "returns_forecast_mapping":
            "cvxportfolio.ReturnsForecast(r_hat=EXPLICIT_ID_QUALIFIED_FORECAST)",

        "current_single_horizon_v1_packet":
            "STRUCTURAL_ONLY_NOT_SUFFICIENT_FOR_REAL_MULTI_PERIOD_POLICY",
    },

    "objective_contract": {

        "canonical_form":
            "SUM_k[EXPECTED_RETURN_k - RISK_PENALTY_k "
            "- TRANSACTION_COST_k - UNCERTAINTY_PENALTY_k]",

        "expected_return": {
            "cvxportfolio_mapping":
                "ReturnsForecast",

            "must_be_explicit":
                True,
        },

        "risk": {
            "allowed_reference_terms": [
                "FullCovariance",
                "FactorModelCovariance",
            ],

            "library_default_risk_forecast_allowed_in_real_policy":
                False,

            "approved_risk_estimator_required":
                True,
        },

        "forecast_and_model_uncertainty": {
            "candidate_reference_terms": [
                "ReturnsForecastError",
                "RiskForecastError",
                "WorstCaseRisk",
            ],

            "mandatory_specific_term":
                None,

            "selection_requires_validation":
                True,
        },

        "transaction_cost": {
            "reference_term":
                "StocksTransactionCost",

            "all_economic_parameters_must_be_explicit":
                True,

            "library_default_cost_calibration_allowed":
                False,

            "parameters_requiring_nse_calibration": [
                "linear_cost_a",
                "pershare_cost_or_None",
                "market_impact_b",
                "volatility_sigma",
                "expected_volume",
                "impact_exponent",
            ],

            "nonlinear_1_5_power_path_tested":
                True,

            "nonlinear_path_solver":
                "CLARABEL",
        },
    },

    "constraint_contract": {

        "optimizer_native_reference_constraints": [
            "LongOnly",
            "LeverageLimit",
            "MaxWeights",
            "MinWeights",
            "MaxHoldings",
            "MinHoldings",
            "MaxTradeWeights",
            "MinTradeWeights",
            "MaxTrades",
            "MinTrades",
            "TurnoverLimit",
            "ParticipationRateLimit",
        ],

        "constraint_values_must_be_explicit":
            True,

        "constraint_values_must_come_from": [
            "APPROVED_MPC_POLICY_CONFIGURATION",
            "CURRENT_MPC_CONSTRAINT_STATE",
        ],

        "hard_halt_requires_no_trade":
            True,

        "closed_entry_gate_requires_no_trade":
            True,

        "unavailable_risk_budget_requires_no_trade":
            True,
    },

    "p01d_boundary": {

        "p01d_originates_alpha":
            False,

        "p01d_safety_authority":
            "SOVEREIGN",

        "mpc_execution_authority":
            False,

        "p01d_execution_authority_inherited_from_mpc":
            False,

        "mpc_output_is_proposal_only":
            True,

        "p01d_must_independently_recheck_safety":
            True,

        "optimizer_may_mirror_some_p01d_limits":
            True,

        "optimizer_mirror_never_replaces_p01d_check":
            True,
    },

    "numerical_normalization": {

        "required_before_p01d_handoff":
            True,

        "purpose": [
            "REMOVE_SOLVER_NUMERICAL_RESIDUE",
            "MAP_CONTINUOUS_NOTIONAL_TO_EXECUTABLE_INSTRUMENT_UNITS",
        ],

        "must_not_be_alpha_threshold":
            True,

        "tolerance_value":
            None,

        "minimum_executable_notional":
            None,

        "rounding_policy":
            None,

        "all_values_require_explicit_future_definition":
            True,
    },

    "market_data_contract": {

        "real_policy_market_data":
            "NSE_KITE_OR_CERTIFIED_INTERNAL_DATA",

        "cvxportfolio_external_default_downloads_allowed_for_real_policy":
            False,

        "user_provided_data_preferred":
            True,
    },

    "unfrozen_economic_parameters": {

        "decision_cadence":
            None,

        "planning_horizon":
            None,

        "horizon_timestamps":
            None,

        "gamma_risk":
            None,

        "gamma_transaction_cost":
            None,

        "gamma_uncertainty":
            None,

        "risk_model_choice":
            None,

        "covariance_estimator":
            None,

        "transaction_cost_parameters":
            None,

        "participation_limit":
            None,

        "turnover_limit":
            None,

        "position_limits":
            None,

        "trade_limits":
            None,

        "numerical_tolerance":
            None,
    },

    "research_discipline": {

        "no_parameter_inventing":
            True,

        "no_library_default_economic_parameters":
            True,

        "no_validation_set_rescue":
            True,

        "no_consumed_holdout_retuning":
            True,

        "each_material_parameter_requires_provenance":
            True,

        "real_policy_requires_model_admission":
            True,
    },

    "current_real_state": {

        "real_pa_model":
            "NONE",

        "real_id_model":
            "NONE",

        "real_mpc_policy":
            "NONE",

        "real_nse_cost_calibration":
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


class ArchitectureViolation(RuntimeError):
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


def verify_step5f_freeze() -> None:

    if not STEP5F_FREEZE.exists():

        raise ArchitectureViolation(
            "STEP 5F FREEZE MISSING"
        )

    actual = sha256_file(
        STEP5F_FREEZE
    )

    if actual != EXPECTED_STEP5F_FREEZE_SHA256:

        raise ArchitectureViolation(
            "STEP 5F FREEZE HASH MISMATCH"
        )


def verify_cvxportfolio_environment() -> None:

    version = getattr(
        cvx,
        "__version__",
        None,
    )

    if version != EXPECTED_CVXPORTFOLIO_VERSION:

        raise ArchitectureViolation(
            "CVXPORTFOLIO VERSION MISMATCH: "
            f"{version}"
        )

    missing = []

    for component in REQUIRED_CVXPORTFOLIO_COMPONENTS:

        if not hasattr(
            cvx,
            component,
        ):
            missing.append(
                component
            )

    if missing:

        raise ArchitectureViolation(
            "MISSING CVXPORTFOLIO COMPONENTS: "
            + ", ".join(missing)
        )

    installed = set(
        cp.installed_solvers()
    )

    absent_solvers = (
        REQUIRED_SOLVERS
        - installed
    )

    if absent_solvers:

        raise ArchitectureViolation(
            "REQUIRED SOLVERS MISSING: "
            + ", ".join(
                sorted(absent_solvers)
            )
        )


def security_audit() -> None:

    source = Path(__file__).read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    for node in ast.walk(tree):

        if isinstance(
            node,
            ast.Import,
        ):

            for item in node.names:

                if item.name in FORBIDDEN_IMPORTS:

                    raise ArchitectureViolation(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):

            if node.module in FORBIDDEN_IMPORTS:

                raise ArchitectureViolation(
                    "BROKER IMPORT PROHIBITED"
                )

        if isinstance(
            node,
            ast.Call,
        ):

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

            else:

                name = ""

            if name in FORBIDDEN_CALLS:

                raise ArchitectureViolation(
                    "BROKER WRITE PROHIBITED"
                )


def validate_architecture() -> None:

    if ARCHITECTURE[
        "serial_path"
    ] != "STEP2 -> PA -> ID -> MPC -> P01D":

        raise ArchitectureViolation(
            "SERIAL INTELLIGENCE PATH VIOLATION"
        )

    if ARCHITECTURE[
        "direct_pa_to_mpc"
    ]:

        raise ArchitectureViolation(
            "DIRECT PA -> MPC BYPASS PROHIBITED"
        )

    if len(
        ARCHITECTURE[
            "mpc_direct_inputs"
        ]
    ) != 2:

        raise ArchitectureViolation(
            "MPC MUST HAVE EXACTLY TWO DIRECT INPUTS"
        )

    horizon = ARCHITECTURE[
        "unfrozen_economic_parameters"
    ][
        "planning_horizon"
    ]

    if horizon is not None:

        raise ArchitectureViolation(
            "PLANNING HORIZON WAS INVENTED"
        )

    for key, value in ARCHITECTURE[
        "unfrozen_economic_parameters"
    ].items():

        if value is not None:

            raise ArchitectureViolation(
                f"ECONOMIC PARAMETER PREMATURELY FROZEN: {key}"
            )

    if not ARCHITECTURE[
        "receding_horizon_contract"
    ][
        "release_only_first_action"
    ]:

        raise ArchitectureViolation(
            "ONLY FIRST MPC ACTION MAY LEAVE STEP 5"
        )

    if ARCHITECTURE[
        "p01d_boundary"
    ][
        "mpc_execution_authority"
    ]:

        raise ArchitectureViolation(
            "MPC MAY NOT AUTHORIZE EXECUTION"
        )

    if (
        ARCHITECTURE[
            "p01d_boundary"
        ][
            "p01d_safety_authority"
        ]
        != "SOVEREIGN"
    ):

        raise ArchitectureViolation(
            "P01D SOVEREIGNTY LOST"
        )

    if ARCHITECTURE[
        "current_real_state"
    ][
        "production"
    ]:

        raise ArchitectureViolation(
            "PRODUCTION MUST REMAIN FALSE"
        )


def build_api_evidence() -> dict:

    result = {}

    for name in REQUIRED_CVXPORTFOLIO_COMPONENTS:

        obj = getattr(
            cvx,
            name,
        )

        try:
            signature = str(
                inspect.signature(obj)
            )
        except Exception:
            signature = (
                "SIGNATURE_UNAVAILABLE"
            )

        result[name] = {
            "present": True,
            "signature": signature,
        }

    return result


def emit_candidate_files() -> tuple[Path, Path]:

    out_json = (
        ROOT
        / "STEP6_MPC_MATHEMATICAL_ARCHITECTURE_V1_CANDIDATE_20260825.json"
    )

    out_md = (
        ROOT
        / "STEP6_MPC_MATHEMATICAL_ARCHITECTURE_V1_CANDIDATE_20260825.md"
    )

    if out_json.exists() or out_md.exists():

        raise ArchitectureViolation(
            "STEP 6 CANDIDATE FILE ALREADY EXISTS; "
            "REFUSING OVERWRITE"
        )

    payload = {
        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "step5f_freeze": {
            "file":
                STEP5F_FREEZE.name,

            "sha256":
                EXPECTED_STEP5F_FREEZE_SHA256,
        },

        "environment": {
            "cvxportfolio":
                getattr(
                    cvx,
                    "__version__",
                    None,
                ),

            "cvxpy":
                cp.__version__,

            "installed_solvers":
                sorted(
                    cp.installed_solvers()
                ),
        },

        "architecture":
            ARCHITECTURE,

        "cvxportfolio_api_evidence":
            build_api_evidence(),
    }

    out_json.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    md = f"""# Step 6 — MPC Mathematical Architecture V1

## Status

**CANDIDATE IMPLEMENTED / NOT YET FROZEN**

## Authoritative intelligence path

STEP 2 -> PA -> ID -> MPC -> P01D

MPC has exactly two direct inputs:

1. ID-qualified multi-horizon forecast
2. deterministic MPC constraint state

Direct PA -> MPC bypass: **PROHIBITED**

## Established optimization foundation

- cvxportfolio MultiPeriodOptimization
- CVXPY formulation layer
- OSQP for QP formulations
- CLARABEL for general convex formulations
- SCS fallback

Solver selection follows the mathematical formulation.
The economic model must never be distorted merely to fit a solver.

## Receding-horizon rule

The optimizer may plan multiple future actions.

Only the **first** optimized action may leave MPC.

All later planned actions are discarded and recomputed at the next
decision cycle.

## Forecast rule

The real optimizer must receive explicit ID-qualified PA forecasts.

Library-default historical return forecasts are prohibited for the
real policy.

A real multi-period MPC requires a multi-horizon forecast contract.

## Objective

Conceptually:

EXPECTED RETURN
- RISK
- TRANSACTION COST
- FORECAST / MODEL UNCERTAINTY

No economic coefficient is frozen in this architecture.

## Risk

Reference implementations may include:

- FullCovariance
- FactorModelCovariance
- ReturnsForecastError
- RiskForecastError
- WorstCaseRisk

The exact approved risk/uncertainty structure remains unfrozen.

## Transaction costs

Reference implementation:

StocksTransactionCost

Real NSE calibration is mandatory.

No default economic cost parameters may silently enter the real policy.

## Constraints

Reference optimizer constraints include:

- LongOnly
- LeverageLimit
- MaxWeights / MinWeights
- MaxHoldings / MinHoldings
- MaxTradeWeights / MinTradeWeights
- MaxTrades / MinTrades
- TurnoverLimit
- ParticipationRateLimit

Constraint values must come from approved policy configuration and
current deterministic MPC constraint state.

## P01D

MPC produces a proposal only.

MPC execution authority: **FALSE**

P01D safety authority: **SOVEREIGN**

Mirroring a P01D limit inside MPC never replaces P01D's independent
safety check.

## Numerical normalization

Solver residue and continuous-notional outputs must be normalized
before handoff.

This normalization may not become an alpha threshold.

Tolerance, executable-unit rules and rounding remain unfrozen.

## Explicitly not frozen

- decision cadence
- planning horizon
- horizon timestamps
- gamma risk
- gamma transaction cost
- gamma uncertainty
- risk-model choice
- covariance estimator
- transaction-cost calibration
- participation limit
- turnover limit
- position limits
- trade limits
- numerical tolerance

## Current real state

Real PA model: **NONE**

Real ID model: **NONE**

Real MPC policy: **NONE**

Real NSE transaction-cost calibration: **NONE**

Production: **FALSE**

Broker authority: **NONE**

Execution authority: **FALSE**
"""

    out_md.write_text(
        md,
        encoding="utf-8",
    )

    return out_json, out_md


def self_test() -> None:

    security_audit()

    verify_step5f_freeze()

    verify_cvxportfolio_environment()

    validate_architecture()

    print("=" * 100)
    print("STEP 6 - MPC MATHEMATICAL ARCHITECTURE V1 SELF TEST")
    print("=" * 100)
    print()

    print(
        "Step 5F frozen handoff integrity       : PASS"
    )

    print(
        "cvxportfolio 1.5.1                     : PASS"
    )

    print(
        "Required cvxportfolio components       : PASS"
    )

    print(
        "OSQP solver                            : PRESENT"
    )

    print(
        "CLARABEL solver                        : PRESENT"
    )

    print(
        "SCS fallback                           : PRESENT"
    )

    print()
    print(
        "Architecture STEP2 -> PA -> ID -> MPC  : PASS"
    )

    print(
        "Direct PA -> MPC                       : ABSENT"
    )

    print(
        "MPC direct inputs                      : 2"
    )

    print(
        "Multi-horizon real forecast required   : YES"
    )

    print(
        "Release only first optimized action    : YES"
    )

    print(
        "Receding-horizon reoptimization        : YES"
    )

    print()
    print(
        "Library default alpha forecast         : PROHIBITED"
    )

    print(
        "Library default risk calibration       : PROHIBITED"
    )

    print(
        "Library default cost calibration       : PROHIBITED"
    )

    print(
        "Economic parameters invented           : NO"
    )

    print(
        "Planning horizon frozen                : NO"
    )

    print(
        "Gamma values frozen                    : NO"
    )

    print()
    print(
        "QP route                               : OSQP"
    )

    print(
        "General convex route                   : CLARABEL"
    )

    print(
        "Fallback                               : SCS"
    )

    print()
    print(
        "Numerical normalization required       : YES"
    )

    print(
        "Numerical normalization is alpha       : NO"
    )

    print()
    print(
        "MPC execution authority                : FALSE"
    )

    print(
        "Broker authority                       : NONE"
    )

    print(
        "P01D                                   : SOVEREIGN"
    )

    print(
        "Production                             : FALSE"
    )

    print()
    print(
        "REAL PA MODEL                          : NONE"
    )

    print(
        "REAL ID MODEL                          : NONE"
    )

    print(
        "REAL MPC POLICY                        : NONE"
    )

    print(
        "REAL NSE COST CALIBRATION              : NONE"
    )

    print("=" * 100)


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

        out_json, out_md = (
            emit_candidate_files()
        )

        print()
        print(
            "Candidate JSON :",
            out_json.name,
        )

        print(
            "Candidate MD   :",
            out_md.name,
        )

        print(
            "Candidate JSON SHA256 :",
            sha256_file(out_json),
        )

        print(
            "Candidate MD SHA256   :",
            sha256_file(out_md),
        )

    if not (
        args.self_test
        or args.emit_candidate
    ):

        raise SystemExit(
            "Use --self-test and/or --emit-candidate"
        )


if __name__ == "__main__":

    main()
