"""
STEP 5E - MPC CORE V2 SERIAL
============================

Correct intelligence architecture:

STEP 2 FEATURES
      ->
STEP 3 PA
      ->
STEP 4 ID
      ->
ID-TO-MPC SERIAL PACKET
      ->
STEP 5 MPC CORE
      <-
MPC CONSTRAINT STATE

MPC therefore has exactly TWO direct runtime inputs:

1. ID-qualified intelligence packet
2. Deterministic MPC constraint state

PA is NOT a direct MPC input.

IMPORTANT
---------
This module contains NO real trading policy.

If no admitted/promoted MPC policy is connected:
    FAIL CLOSED -> NO_TRADE

A future promoted MPC policy may be plugged into this core
without changing the frozen serial input architecture.

No broker authority.
No execution authority.
P01D remains sovereign.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import argparse
import ast
import hashlib
import math

from black_box_principles_v1 import (
    Action,
    Layer,
    ModelIdentity,
    MPCOutput,
    PromotionStatus,
)

from model_admission_gate_v1 import (
    AdmittedModel,
)

from mpc_serial_input_interface_v1 import (
    MPCSerialInputEnvelope,
)


ROOT = Path(__file__).resolve().parent

SERIAL_PACKET_FILE = (
    ROOT / "id_to_mpc_packet_v1.py"
)

SERIAL_INTERFACE_FILE = (
    ROOT / "mpc_serial_input_interface_v1.py"
)

SERIAL_FREEZE_FILE = (
    ROOT
    / "STEP5D_4_SERIAL_MPC_INTERFACE_V1_FREEZE_20260825.json"
)


EXPECTED_PACKET_SHA256 = (
    "af073afacacb59551cfce411a0c5dab2"
    "9da0b75189b91436726a0c15335ca0e3"
)

EXPECTED_INTERFACE_SHA256 = (
    "3a72aec76ab4eadef3573ed074688df0"
    "ef97844ebe550bcef1b706947fa56b90"
)

EXPECTED_SERIAL_FREEZE_SHA256 = (
    "dd022e9c0a9662417835924ab48a720d"
    "8b170625f5fbd5a4e709b85c4a2a1713"
)


class MPCCoreError(RuntimeError):
    pass


# ============================================================
# FUTURE MPC POLICY CONTRACT
# ============================================================

@dataclass(frozen=True)
class MPCPolicyDecision:
    """
    Output contract of a future promoted MPC policy.

    This is NOT execution authorization.
    """

    action: Action

    conviction: float

    recommended_risk_fraction: float

    reason_codes: tuple[str, ...]

    execution_authorized: bool = False


class MPCPolicyExecutor(Protocol):
    """
    A future promoted MPC policy must implement this interface.

    The executor identity must match the admitted MPC artifact.
    """

    model_name: str
    model_version: str
    artifact_sha256: str

    def decide(
        self,
        envelope: MPCSerialInputEnvelope,
    ) -> MPCPolicyDecision:
        ...


# ============================================================
# CORE RESULT
# ============================================================

@dataclass(frozen=True)
class MPCCoreResult:

    symbol: str

    action: Action

    policy_connected: bool

    policy_sha256: str | None

    recommendation: MPCOutput | None

    reason_codes: tuple[str, ...]

    execution_authorized: bool = False


# ============================================================
# HASH / ARCHITECTURE INTEGRITY
# ============================================================

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


def verify_serial_architecture() -> None:

    required = {
        SERIAL_PACKET_FILE:
            EXPECTED_PACKET_SHA256,

        SERIAL_INTERFACE_FILE:
            EXPECTED_INTERFACE_SHA256,

        SERIAL_FREEZE_FILE:
            EXPECTED_SERIAL_FREEZE_SHA256,
    }

    for path, expected in required.items():

        if not path.exists():

            raise MPCCoreError(
                f"FROZEN SERIAL DEPENDENCY MISSING: "
                f"{path.name}"
            )

        actual = sha256_file(
            path
        )

        if actual != expected:

            raise MPCCoreError(
                f"FROZEN SERIAL DEPENDENCY HASH "
                f"MISMATCH: {path.name}"
            )


# ============================================================
# MPC CORE V2
# ============================================================

class MPCCoreV2Serial:

    @staticmethod
    def _validate_probability(
        name: str,
        value: float,
    ) -> None:

        if not math.isfinite(
            value
        ):

            raise MPCCoreError(
                f"{name}: NON-FINITE"
            )

        if not (
            0.0 <= value <= 1.0
        ):

            raise MPCCoreError(
                f"{name}: OUTSIDE [0,1]"
            )


    @classmethod
    def run(
        cls,
        envelope: MPCSerialInputEnvelope,

        mpc_admission: AdmittedModel | None = None,

        policy_executor: MPCPolicyExecutor | None = None,

    ) -> MPCCoreResult:

        verify_serial_architecture()

        # ----------------------------------------------------
        # Input interface must already have passed 5D-4.
        # ----------------------------------------------------

        if not envelope.mpc_input_ready:

            raise MPCCoreError(
                "MPC SERIAL INPUT NOT READY"
            )

        if envelope.execution_authorized:

            raise MPCCoreError(
                "SERIAL INPUT MAY NOT AUTHORIZE EXECUTION"
            )

        intelligence = (
            envelope.intelligence
        )

        constraints = (
            envelope.constraints
        )

        if intelligence.execution_authorized:

            raise MPCCoreError(
                "INTELLIGENCE INPUT MAY NOT EXECUTE"
            )

        if constraints.execution_authorized:

            raise MPCCoreError(
                "CONSTRAINT INPUT MAY NOT EXECUTE"
            )

        if not constraints.eligible_for_mpc:

            raise MPCCoreError(
                "CONSTRAINT STATE FAIL-CLOSED"
            )

        if (
            intelligence.symbol
            != constraints.snapshot.symbol
        ):

            raise MPCCoreError(
                "MPC INPUT SYMBOL MISMATCH"
            )

        # ----------------------------------------------------
        # CRITICAL FAIL-CLOSED RULE
        #
        # No real promoted MPC policy currently exists.
        #
        # Without BOTH:
        #   - admitted MPC artifact
        #   - matching policy executor
        #
        # the only legal action is NO_TRADE.
        # ----------------------------------------------------

        if (
            mpc_admission is None
            or policy_executor is None
        ):

            return MPCCoreResult(
                symbol=intelligence.symbol,

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

        # ----------------------------------------------------
        # Admission must be MPC.
        # ----------------------------------------------------

        if (
            mpc_admission.layer
            != Layer.MPC
        ):

            raise MPCCoreError(
                "ADMITTED POLICY IS NOT MPC"
            )

        if mpc_admission.execution_authorized:

            raise MPCCoreError(
                "ADMITTED MPC MAY NOT AUTHORIZE EXECUTION"
            )

        # ----------------------------------------------------
        # Executor must correspond EXACTLY to admitted MPC.
        # ----------------------------------------------------

        if (
            policy_executor.model_name
            != mpc_admission.model_name
        ):

            raise MPCCoreError(
                "MPC POLICY NAME MISMATCH"
            )

        if (
            policy_executor.model_version
            != mpc_admission.model_version
        ):

            raise MPCCoreError(
                "MPC POLICY VERSION MISMATCH"
            )

        if (
            policy_executor.artifact_sha256
            != mpc_admission.artifact_sha256
        ):

            raise MPCCoreError(
                "MPC POLICY SHA256 MISMATCH"
            )

        # ----------------------------------------------------
        # Policy execution.
        #
        # This core does NOT define that policy.
        # ----------------------------------------------------

        decision = (
            policy_executor.decide(
                envelope
            )
        )

        if decision.execution_authorized:

            raise MPCCoreError(
                "MPC POLICY MAY NOT AUTHORIZE EXECUTION"
            )

        cls._validate_probability(
            "conviction",
            decision.conviction,
        )

        cls._validate_probability(
            "recommended_risk_fraction",
            decision.recommended_risk_fraction,
        )

        # ----------------------------------------------------
        # Build Constitution-compatible MPC output.
        #
        # Still NOT execution-authorized.
        # ----------------------------------------------------

        identity = ModelIdentity(
            layer=Layer.MPC,

            name=(
                mpc_admission.model_name
            ),

            version=(
                mpc_admission.model_version
            ),

            sha256=(
                mpc_admission.artifact_sha256
            ),

            promotion_status=(
                PromotionStatus.PROMOTED
            ),

            validated_out_of_sample=True,
        )

        recommendation = MPCOutput(
            model=identity,

            symbol=intelligence.symbol,

            pa_sha256=(
                intelligence
                .pa_model_sha256
            ),

            id_sha256=(
                intelligence
                .id_model_sha256
            ),

            action=decision.action,

            conviction=(
                decision.conviction
            ),

            recommended_risk_fraction=(
                decision
                .recommended_risk_fraction
            ),

            execution_authorized=False,
        )

        return MPCCoreResult(
            symbol=intelligence.symbol,

            action=decision.action,

            policy_connected=True,

            policy_sha256=(
                mpc_admission
                .artifact_sha256
            ),

            recommendation=recommendation,

            reason_codes=(
                decision.reason_codes
            ),

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

    from datetime import (
        datetime,
        timezone,
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

    verify_serial_architecture()

    now = datetime.now(
        timezone.utc
    )

    # --------------------------------------------------------
    # TEST INPUT #1:
    # ID-qualified intelligence.
    # --------------------------------------------------------

    intelligence = IDToMPCPacket(
        symbol="RELIANCE",

        decision_time_utc=now,

        pa_model_name="TEST_PA",
        pa_model_version="1",
        pa_model_sha256="a" * 64,

        id_model_name="TEST_ID",
        id_model_version="1",
        id_model_sha256="b" * 64,

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

    # --------------------------------------------------------
    # TEST INPUT #2:
    # deterministic constraints.
    #
    # Numerical values are TEST VALUES ONLY.
    # --------------------------------------------------------

    snapshot = MPCConstraintSnapshot(
        symbol="RELIANCE",

        asof_utc=now,

        source_sha256="c" * 64,

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

    envelope = MPCSerialInputEnvelope(
        intelligence=intelligence,

        constraints=constraints,

        mpc_input_ready=True,

        execution_authorized=False,
    )

    # --------------------------------------------------------
    # TEST 1:
    # REAL current state:
    #
    # No MPC policy connected.
    #
    # MUST return NO_TRADE.
    # --------------------------------------------------------

    no_policy = (
        MPCCoreV2Serial.run(
            envelope
        )
    )

    assert (
        no_policy.action
        == Action.NO_TRADE
    )

    assert (
        no_policy.policy_connected
        is False
    )

    assert (
        no_policy.recommendation
        is None
    )

    assert (
        no_policy.execution_authorized
        is False
    )

    # --------------------------------------------------------
    # TEST 2:
    # TEST-ONLY promoted-policy socket.
    #
    # This is NOT a real MPC policy.
    # It always returns HOLD.
    # --------------------------------------------------------

    test_policy_sha = "d" * 64

    test_admission = AdmittedModel(
        layer=Layer.MPC,

        model_name="TEST_ONLY_MPC",

        model_version="1",

        artifact_path="TEST_ONLY",

        artifact_sha256=test_policy_sha,

        validation_artifact_sha256="f" * 64,

        constitution_sha256=(
            "8dd0ef7b1983a636df103dc84e2f52c8"
            "227624cb3cbc3cb4f7558eab3c9785c0"
        ),

        admitted_at_utc=(
            now.isoformat()
        ),

        execution_authorized=False,
    )


    class TestOnlyHoldPolicy:

        model_name = (
            "TEST_ONLY_MPC"
        )

        model_version = "1"

        artifact_sha256 = (
            test_policy_sha
        )

        def decide(
            self,
            envelope,
        ):

            return MPCPolicyDecision(
                action=Action.HOLD,

                conviction=0.0,

                recommended_risk_fraction=0.0,

                reason_codes=(
                    "TEST_ONLY_HOLD_POLICY",
                ),

                execution_authorized=False,
            )


    with_test_policy = (
        MPCCoreV2Serial.run(
            envelope,

            mpc_admission=(
                test_admission
            ),

            policy_executor=(
                TestOnlyHoldPolicy()
            ),
        )
    )

    assert (
        with_test_policy.action
        == Action.HOLD
    )

    assert (
        with_test_policy.policy_connected
        is True
    )

    assert (
        with_test_policy.recommendation
        is not None
    )

    assert (
        with_test_policy
        .recommendation
        .pa_sha256
        == intelligence.pa_model_sha256
    )

    assert (
        with_test_policy
        .recommendation
        .id_sha256
        == intelligence.id_model_sha256
    )

    assert (
        with_test_policy.execution_authorized
        is False
    )

    # --------------------------------------------------------
    # TEST 3:
    # Wrong MPC policy hash must fail.
    # --------------------------------------------------------

    class WrongHashPolicy:

        model_name = (
            "TEST_ONLY_MPC"
        )

        model_version = "1"

        artifact_sha256 = (
            "9" * 64
        )

        def decide(
            self,
            envelope,
        ):

            return MPCPolicyDecision(
                action=Action.HOLD,

                conviction=0.0,

                recommended_risk_fraction=0.0,

                reason_codes=(
                    "SHOULD_NOT_RUN",
                ),
            )


    rejected = False

    try:

        MPCCoreV2Serial.run(
            envelope,

            mpc_admission=(
                test_admission
            ),

            policy_executor=(
                WrongHashPolicy()
            ),
        )

    except RuntimeError:

        rejected = True

    assert rejected

    print("=" * 100)
    print("STEP 5E - MPC CORE V2 SERIAL SELF TEST")
    print("=" * 100)

    print()
    print(
        "Correct intelligence path          : STEP 2 -> PA -> ID -> MPC"
    )

    print(
        "Direct PA -> MPC                   : ABSENT"
    )

    print()
    print(
        "MPC direct input #1                : ID-QUALIFIED INTELLIGENCE"
    )

    print(
        "MPC direct input #2                : CONSTRAINT STATE"
    )

    print(
        "MPC direct input count             : 2"
    )

    print()
    print(
        "Frozen serial architecture         : PASS"
    )

    print(
        "No-policy fail-closed              : PASS"
    )

    print(
        "No-policy action                   : NO_TRADE"
    )

    print(
        "Promoted-policy socket             : PASS / TEST ONLY"
    )

    print(
        "Wrong-policy SHA rejection         : PASS"
    )

    print(
        "PA provenance carried to output    : PASS"
    )

    print(
        "ID provenance carried to output    : PASS"
    )

    print()
    print(
        "REAL MPC POLICY                    : NONE"
    )

    print(
        "REAL MPC DECISION LOGIC            : NONE"
    )

    print(
        "LONG/SHORT POLICY INVENTED         : NO"
    )

    print(
        "REAL PA MODEL                      : NONE"
    )

    print(
        "REAL ID MODEL                      : NONE"
    )

    print()
    print(
        "Execution authority                : FALSE"
    )

    print(
        "Broker authority                   : NONE"
    )

    print(
        "Production                         : FALSE"
    )

    print(
        "P01D sovereignty                   : PRESERVED"
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
            "MPC Core V2 Serial library only. "
            "Use --self-test."
        )

    self_test()


if __name__ == "__main__":

    main()
