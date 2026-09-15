"""
STEP 5F - MPC -> P01D HANDOFF V1
================================

Purpose
-------
Create the one-way boundary:

STEP 2 -> PA -> ID -> MPC -> P01D

This module accepts ONLY:
1. A completed Step-5E MPC Core result.
2. The exact frozen serial input envelope that produced it.

Before handing anything to P01D it verifies:
- MPC policy was actually connected/promoted.
- MPC recommendation exists.
- MPC recommendation belongs to the exact MPC policy SHA.
- PA provenance matches the exact ID-qualified packet.
- ID provenance matches the exact ID-qualified packet.
- Symbol continuity is intact.
- Constraint state is still MPC-eligible.
- No intelligence layer carries execution authority.

The output is a P01D REQUEST ONLY.

execution_authorized = FALSE

P01D remains sovereign and independently decides authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import argparse
import ast
import hashlib

from black_box_principles_v1 import (
    BlackBoxConstitutionV1,
    Layer,
    P01DRequest,
)

from mpc_core_v2_serial import (
    MPCCoreResult,
)

from mpc_serial_input_interface_v1 import (
    MPCSerialInputEnvelope,
)


ROOT = Path(__file__).resolve().parent

MPC_CORE_FILE = (
    ROOT / "mpc_core_v2_serial.py"
)

MPC_CORE_FREEZE = (
    ROOT
    / "STEP5E_MPC_CORE_V2_SERIAL_FREEZE_20260825.json"
)

SERIAL_INTERFACE_FILE = (
    ROOT / "mpc_serial_input_interface_v1.py"
)


EXPECTED_MPC_CORE_SHA256 = (
    "53d40bee8a0aee8a5825ff7744c7ea50"
    "d6b401ae3e94aa231e4796f7f8c664e1"
)

EXPECTED_MPC_CORE_FREEZE_SHA256 = (
    "ac8c6bde1446bb663e3ae0023cc1cbff"
    "51c2b327aaa8a5908276465011b30e4f"
)

EXPECTED_SERIAL_INTERFACE_SHA256 = (
    "3a72aec76ab4eadef3573ed074688df0"
    "ef97844ebe550bcef1b706947fa56b90"
)


class P01DHandoffError(RuntimeError):
    pass


@dataclass(frozen=True)
class P01DHandoffEnvelope:

    symbol: str

    handed_off_at_utc: datetime

    pa_sha256: str
    id_sha256: str
    mpc_sha256: str

    constraint_source_sha256: str

    request: P01DRequest

    intelligence_chain_valid: bool

    execution_authorized: bool = False


def sha256_file(
    path: Path,
) -> str:

    h = hashlib.sha256()

    with path.open("rb") as f:

        for block in iter(
            lambda: f.read(
                1024 * 1024
            ),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def verify_frozen_step5() -> None:

    expected = {
        MPC_CORE_FILE:
            EXPECTED_MPC_CORE_SHA256,

        MPC_CORE_FREEZE:
            EXPECTED_MPC_CORE_FREEZE_SHA256,

        SERIAL_INTERFACE_FILE:
            EXPECTED_SERIAL_INTERFACE_SHA256,
    }

    for path, expected_sha in expected.items():

        if not path.exists():

            raise P01DHandoffError(
                f"FROZEN STEP-5 DEPENDENCY MISSING: "
                f"{path.name}"
            )

        actual = sha256_file(
            path
        )

        if actual != expected_sha:

            raise P01DHandoffError(
                f"FROZEN STEP-5 DEPENDENCY HASH "
                f"MISMATCH: {path.name}"
            )


class MPCToP01DHandoffV1:

    @classmethod
    def build(
        cls,
        serial_input: MPCSerialInputEnvelope,
        mpc_result: MPCCoreResult,
    ) -> P01DHandoffEnvelope:

        verify_frozen_step5()

        # ----------------------------------------------------
        # Serial input itself must remain valid.
        # ----------------------------------------------------

        if not serial_input.mpc_input_ready:

            raise P01DHandoffError(
                "MPC SERIAL INPUT NOT READY"
            )

        if serial_input.execution_authorized:

            raise P01DHandoffError(
                "SERIAL INPUT MAY NOT AUTHORIZE EXECUTION"
            )

        intelligence = (
            serial_input.intelligence
        )

        constraints = (
            serial_input.constraints
        )

        if intelligence.execution_authorized:

            raise P01DHandoffError(
                "INTELLIGENCE MAY NOT AUTHORIZE EXECUTION"
            )

        if constraints.execution_authorized:

            raise P01DHandoffError(
                "CONSTRAINTS MAY NOT AUTHORIZE EXECUTION"
            )

        if not constraints.eligible_for_mpc:

            raise P01DHandoffError(
                "CONSTRAINT STATE NO LONGER ELIGIBLE"
            )

        # ----------------------------------------------------
        # No-policy / fail-closed result must NOT generate
        # a P01D execution request.
        # ----------------------------------------------------

        if not mpc_result.policy_connected:

            raise P01DHandoffError(
                "NO PROMOTED MPC POLICY CONNECTED"
            )

        if mpc_result.recommendation is None:

            raise P01DHandoffError(
                "MPC RECOMMENDATION MISSING"
            )

        if mpc_result.execution_authorized:

            raise P01DHandoffError(
                "MPC CORE MAY NOT AUTHORIZE EXECUTION"
            )

        recommendation = (
            mpc_result.recommendation
        )

        if recommendation.execution_authorized:

            raise P01DHandoffError(
                "MPC RECOMMENDATION MAY NOT "
                "AUTHORIZE EXECUTION"
            )

        # ----------------------------------------------------
        # MPC identity must itself satisfy Constitution.
        # ----------------------------------------------------

        BlackBoxConstitutionV1.validate_model(
            recommendation.model,
            Layer.MPC,
        )

        # ----------------------------------------------------
        # MPC Core result continuity.
        # ----------------------------------------------------

        if (
            mpc_result.symbol
            != recommendation.symbol
        ):

            raise P01DHandoffError(
                "MPC RESULT / RECOMMENDATION "
                "SYMBOL MISMATCH"
            )

        if (
            mpc_result.action
            != recommendation.action
        ):

            raise P01DHandoffError(
                "MPC RESULT / RECOMMENDATION "
                "ACTION MISMATCH"
            )

        if (
            mpc_result.policy_sha256
            != recommendation.model.sha256
        ):

            raise P01DHandoffError(
                "MPC POLICY SHA CONTINUITY FAILURE"
            )

        # ----------------------------------------------------
        # Exact PA / ID provenance must survive MPC.
        # ----------------------------------------------------

        if (
            recommendation.pa_sha256
            != intelligence.pa_model_sha256
        ):

            raise P01DHandoffError(
                "PA PROVENANCE LOST BEFORE P01D"
            )

        if (
            recommendation.id_sha256
            != intelligence.id_model_sha256
        ):

            raise P01DHandoffError(
                "ID PROVENANCE LOST BEFORE P01D"
            )

        # ----------------------------------------------------
        # Symbol must remain identical from Step 4 through
        # constraints, MPC and P01D handoff.
        # ----------------------------------------------------

        symbols = {
            intelligence.symbol,
            constraints.snapshot.symbol,
            mpc_result.symbol,
            recommendation.symbol,
        }

        if len(symbols) != 1:

            raise P01DHandoffError(
                "STEP-5 SYMBOL CONTINUITY FAILURE"
            )

        # ----------------------------------------------------
        # Final MPC-output contract checks.
        # ----------------------------------------------------

        BlackBoxConstitutionV1.probability(
            "conviction",
            recommendation.conviction,
        )

        BlackBoxConstitutionV1.probability(
            "recommended_risk_fraction",
            recommendation.recommended_risk_fraction,
        )

        # ----------------------------------------------------
        # Construct P01D request.
        #
        # CRITICAL:
        # This does NOT authorize execution.
        # ----------------------------------------------------

        request = P01DRequest(
            symbol=recommendation.symbol,

            recommendation=recommendation,

            intelligence_provenance_valid=True,

            execution_authorized=False,
        )

        if request.execution_authorized:

            raise P01DHandoffError(
                "P01D REQUEST MUST ENTER "
                "UNAUTHORIZED"
            )

        return P01DHandoffEnvelope(
            symbol=recommendation.symbol,

            handed_off_at_utc=datetime.now(
                timezone.utc
            ),

            pa_sha256=(
                intelligence.pa_model_sha256
            ),

            id_sha256=(
                intelligence.id_model_sha256
            ),

            mpc_sha256=(
                recommendation.model.sha256
            ),

            constraint_source_sha256=(
                constraints
                .snapshot
                .source_sha256
            ),

            request=request,

            intelligence_chain_valid=True,

            execution_authorized=False,
        )


# ============================================================
# SECURITY AUDIT
# ============================================================

FORBIDDEN_IMPORTS = {
    "kiteconnect",
}

FORBIDDEN_CALLS = {
    "place_order",
    "modify_order",
    "cancel_order",
}


def security_audit():

    tree = ast.parse(
        Path(__file__)
        .read_text(
            encoding="utf-8"
        )
    )

    for node in ast.walk(tree):

        if isinstance(
            node,
            ast.Import,
        ):

            for item in node.names:

                if (
                    item.name
                    in FORBIDDEN_IMPORTS
                ):

                    raise RuntimeError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):

            if (
                node.module
                in FORBIDDEN_IMPORTS
            ):

                raise RuntimeError(
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

                raise RuntimeError(
                    "BROKER WRITE PROHIBITED"
                )


# ============================================================
# OFFLINE SELF TEST
# ============================================================

def self_test():

    from datetime import timezone

    from black_box_principles_v1 import (
        Action,
        ModelIdentity,
        MPCOutput,
        PromotionStatus,
    )

    from id_to_mpc_packet_v1 import (
        IDToMPCPacket,
    )

    from mpc_constraint_state_snapshot_v1 import (
        MPCConstraintSnapshot,
        PositionState,
        ReleasedMPCConstraintState,
    )

    security_audit()

    verify_frozen_step5()

    now = datetime.now(
        timezone.utc
    )

    pa_sha = "a" * 64
    id_sha = "b" * 64
    mpc_sha = "c" * 64

    # --------------------------------------------------------
    # Step-4 ID-qualified intelligence packet.
    # --------------------------------------------------------

    intelligence = IDToMPCPacket(
        symbol="RELIANCE",

        decision_time_utc=now,

        pa_model_name="TEST_PA",
        pa_model_version="1",
        pa_model_sha256=pa_sha,

        id_model_name="TEST_ID",
        id_model_version="1",
        id_model_sha256=id_sha,

        horizon_minutes=5,

        p_up=0.60,
        p_down=0.20,
        p_flat=0.20,

        expected_return_bps=10.0,
        expected_adverse_bps=5.0,

        pa_confidence=0.70,

        reliability=0.70,
        regime_match=0.80,
        data_quality=0.95,
        conflict_score=0.10,

        execution_authorized=False,
    )

    snapshot = MPCConstraintSnapshot(
        symbol="RELIANCE",

        asof_utc=now,

        source_sha256="d" * 64,

        position_state=(
            PositionState.FLAT
        ),

        position_quantity=0,

        gross_exposure_fraction=0.0,

        risk_budget_available_fraction=0.005,

        turnover_capacity_remaining_fraction=1.0,

        estimated_round_trip_cost_bps=5.0,

        session_open=True,
        entry_gate_open=True,
        cooldown_clear=True,
        same_symbol_clear=True,
        position_slot_available=True,
        hard_halt_active=False,

        execution_authorized=False,
    )

    constraints = (
        ReleasedMPCConstraintState(
            snapshot=snapshot,

            eligible_for_mpc=True,

            reason_codes=(),

            execution_authorized=False,
        )
    )

    serial = MPCSerialInputEnvelope(
        intelligence=intelligence,

        constraints=constraints,

        mpc_input_ready=True,

        execution_authorized=False,
    )

    mpc_identity = ModelIdentity(
        layer=Layer.MPC,

        name="TEST_MPC",

        version="1",

        sha256=mpc_sha,

        promotion_status=(
            PromotionStatus.PROMOTED
        ),

        validated_out_of_sample=True,
    )

    # Structural HOLD only.
    # No real trading policy is being tested.
    recommendation = MPCOutput(
        model=mpc_identity,

        symbol="RELIANCE",

        pa_sha256=pa_sha,

        id_sha256=id_sha,

        action=Action.HOLD,

        conviction=0.0,

        recommended_risk_fraction=0.0,

        execution_authorized=False,
    )

    result = MPCCoreResult(
        symbol="RELIANCE",

        action=Action.HOLD,

        policy_connected=True,

        policy_sha256=mpc_sha,

        recommendation=recommendation,

        reason_codes=(
            "TEST_ONLY_STRUCTURAL_HOLD",
        ),

        execution_authorized=False,
    )

    # --------------------------------------------------------
    # Valid handoff.
    # --------------------------------------------------------

    handoff = (
        MPCToP01DHandoffV1.build(
            serial,
            result,
        )
    )

    assert handoff.intelligence_chain_valid
    assert not handoff.execution_authorized

    assert (
        handoff
        .request
        .execution_authorized
        is False
    )

    assert handoff.pa_sha256 == pa_sha
    assert handoff.id_sha256 == id_sha
    assert handoff.mpc_sha256 == mpc_sha

    # --------------------------------------------------------
    # No-policy result must NOT enter P01D.
    # --------------------------------------------------------

    no_policy = MPCCoreResult(
        symbol="RELIANCE",

        action=Action.NO_TRADE,

        policy_connected=False,

        policy_sha256=None,

        recommendation=None,

        reason_codes=(
            "NO_PROMOTED_MPC_POLICY_CONNECTED",
            "FAIL_CLOSED",
        ),

        execution_authorized=False,
    )

    rejected = False

    try:

        MPCToP01DHandoffV1.build(
            serial,
            no_policy,
        )

    except RuntimeError:

        rejected = True

    assert rejected

    # --------------------------------------------------------
    # Wrong PA provenance must fail.
    # --------------------------------------------------------

    wrong_pa_recommendation = MPCOutput(
        model=mpc_identity,

        symbol="RELIANCE",

        pa_sha256="9" * 64,

        id_sha256=id_sha,

        action=Action.HOLD,

        conviction=0.0,

        recommended_risk_fraction=0.0,

        execution_authorized=False,
    )

    wrong_pa_result = MPCCoreResult(
        symbol="RELIANCE",

        action=Action.HOLD,

        policy_connected=True,

        policy_sha256=mpc_sha,

        recommendation=(
            wrong_pa_recommendation
        ),

        reason_codes=(
            "TEST",
        ),

        execution_authorized=False,
    )

    rejected = False

    try:

        MPCToP01DHandoffV1.build(
            serial,
            wrong_pa_result,
        )

    except RuntimeError:

        rejected = True

    assert rejected

    # --------------------------------------------------------
    # Wrong ID provenance must fail.
    # --------------------------------------------------------

    wrong_id_recommendation = MPCOutput(
        model=mpc_identity,

        symbol="RELIANCE",

        pa_sha256=pa_sha,

        id_sha256="8" * 64,

        action=Action.HOLD,

        conviction=0.0,

        recommended_risk_fraction=0.0,

        execution_authorized=False,
    )

    wrong_id_result = MPCCoreResult(
        symbol="RELIANCE",

        action=Action.HOLD,

        policy_connected=True,

        policy_sha256=mpc_sha,

        recommendation=(
            wrong_id_recommendation
        ),

        reason_codes=(
            "TEST",
        ),

        execution_authorized=False,
    )

    rejected = False

    try:

        MPCToP01DHandoffV1.build(
            serial,
            wrong_id_result,
        )

    except RuntimeError:

        rejected = True

    assert rejected

    # --------------------------------------------------------
    # Closed constraints must fail.
    # --------------------------------------------------------

    closed_constraints = (
        ReleasedMPCConstraintState(
            snapshot=snapshot,

            eligible_for_mpc=False,

            reason_codes=(
                "HARD_HALT_ACTIVE",
            ),

            execution_authorized=False,
        )
    )

    closed_serial = (
        MPCSerialInputEnvelope(
            intelligence=intelligence,

            constraints=closed_constraints,

            mpc_input_ready=True,

            execution_authorized=False,
        )
    )

    rejected = False

    try:

        MPCToP01DHandoffV1.build(
            closed_serial,
            result,
        )

    except RuntimeError:

        rejected = True

    assert rejected

    print("=" * 100)
    print("STEP 5F - MPC -> P01D HANDOFF V1 SELF TEST")
    print("=" * 100)

    print()
    print(
        "Frozen MPC Core integrity           : PASS"
    )

    print(
        "Step 5E freeze integrity            : PASS"
    )

    print(
        "Frozen serial interface integrity   : PASS"
    )

    print()
    print(
        "MPC policy-connected requirement    : PASS"
    )

    print(
        "MPC recommendation requirement      : PASS"
    )

    print(
        "PA provenance continuity            : PASS"
    )

    print(
        "ID provenance continuity            : PASS"
    )

    print(
        "MPC provenance continuity           : PASS"
    )

    print(
        "Constraint eligibility continuity   : PASS"
    )

    print(
        "Symbol continuity                   : PASS"
    )

    print(
        "No-policy P01D rejection            : PASS"
    )

    print(
        "Wrong-PA rejection                  : PASS"
    )

    print(
        "Wrong-ID rejection                  : PASS"
    )

    print(
        "Closed-constraint rejection         : PASS"
    )

    print()
    print(
        "P01D request created                : PASS / TEST ONLY"
    )

    print(
        "P01D request execution authority    : FALSE"
    )

    print(
        "MPC execution authority             : FALSE"
    )

    print(
        "Broker authority                    : NONE"
    )

    print()
    print(
        "REAL PA MODEL                       : NONE"
    )

    print(
        "REAL ID MODEL                       : NONE"
    )

    print(
        "REAL MPC POLICY                     : NONE"
    )

    print(
        "REAL TRADE REQUEST                  : NONE"
    )

    print(
        "PRODUCTION                          : FALSE"
    )

    print(
        "P01D SOVEREIGNTY                    : PRESERVED"
    )

    print("=" * 100)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.self_test:

        raise SystemExit(
            "MPC -> P01D Handoff library only. "
            "Use --self-test."
        )

    self_test()


if __name__ == "__main__":

    main()
