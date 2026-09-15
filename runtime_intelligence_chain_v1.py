"""
STEP 5C - RUNTIME INTELLIGENCE CHAIN V1
=======================================

Runtime constitutional pipeline:

CERTIFIED INFORMATION
        ->
ADMITTED PA OUTPUT
        ->
ADMITTED ID ASSESSMENT
        ->
ADMITTED MPC RECOMMENDATION
        ->
P01D REQUEST

This module DOES NOT:
- predict,
- assess market direction,
- choose thresholds,
- train models,
- place orders,
- authorize execution.

It only validates that every runtime link belongs to an admitted,
promoted model and preserves exact provenance continuity.

P01D remains the sole execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import ast
import hashlib
import json

from black_box_principles_v1 import (
    Action,
    BlackBoxConstitutionV1,
    IDOutput,
    InformationSnapshot,
    Layer,
    ModelIdentity,
    MPCOutput,
    PAOutput,
    P01DRequest,
    PromotionStatus,
)

from model_admission_gate_v1 import (
    AdmittedModel,
    ModelAdmissionGateV1,
)


ROOT = Path(__file__).resolve().parent

CONSTITUTION_FILE = (
    ROOT / "black_box_principles_v1.py"
)

ADMISSION_GATE_FILE = (
    ROOT / "model_admission_gate_v1.py"
)

STEP5B_FREEZE = (
    ROOT
    / "STEP5B_MODEL_ADMISSION_GATE_V1_FREEZE_20260825.json"
)

EXPECTED_CONSTITUTION_SHA256 = (
    "8dd0ef7b1983a636df103dc84e2f52c8"
    "227624cb3cbc3cb4f7558eab3c9785c0"
)

EXPECTED_ADMISSION_GATE_SHA256 = (
    "2ae4699edaf229f8135f0c36480c35d2"
    "6aad81e3dd8b9c9015f534cc99903149"
)

EXPECTED_STEP5B_FREEZE_SHA256 = (
    "543aa95e95f1a66614fb0e65d3796561"
    "1790abcc2829fbd26dcf33963a3758fc"
)


class RuntimeChainError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeEnvelope:
    information: InformationSnapshot

    pa_admission: AdmittedModel
    pa_output: PAOutput

    id_admission: AdmittedModel
    id_output: IDOutput

    mpc_admission: AdmittedModel
    mpc_output: MPCOutput


@dataclass(frozen=True)
class RuntimeChainResult:
    symbol: str

    pa_sha256: str
    id_sha256: str
    mpc_sha256: str

    action: Action

    intelligence_chain_valid: bool

    p01d_request: P01DRequest

    execution_authorized: bool = False


def sha256_file(path: Path) -> str:

    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def verify_frozen_architecture() -> None:

    required = [
        CONSTITUTION_FILE,
        ADMISSION_GATE_FILE,
        STEP5B_FREEZE,
    ]

    for path in required:
        if not path.exists():
            raise RuntimeChainError(
                f"Frozen dependency missing: {path.name}"
            )

    if (
        sha256_file(CONSTITUTION_FILE)
        != EXPECTED_CONSTITUTION_SHA256
    ):
        raise RuntimeChainError(
            "CONSTITUTION HASH MISMATCH"
        )

    if (
        sha256_file(ADMISSION_GATE_FILE)
        != EXPECTED_ADMISSION_GATE_SHA256
    ):
        raise RuntimeChainError(
            "MODEL ADMISSION GATE HASH MISMATCH"
        )

    if (
        sha256_file(STEP5B_FREEZE)
        != EXPECTED_STEP5B_FREEZE_SHA256
    ):
        raise RuntimeChainError(
            "STEP 5B FREEZE HASH MISMATCH"
        )


def admitted_to_identity(
    admitted: AdmittedModel,
) -> ModelIdentity:

    return ModelIdentity(
        layer=admitted.layer,
        name=admitted.model_name,
        version=admitted.model_version,
        sha256=admitted.artifact_sha256,
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )


class RuntimeIntelligenceChainV1:

    @classmethod
    def validate_admission_binding(
        cls,
        admitted: AdmittedModel,
        output_model: ModelIdentity,
        expected_layer: Layer,
    ) -> None:

        if admitted.layer != expected_layer:
            raise RuntimeChainError(
                "ADMITTED MODEL LAYER MISMATCH"
            )

        if output_model.layer != expected_layer:
            raise RuntimeChainError(
                "OUTPUT MODEL LAYER MISMATCH"
            )

        if (
            admitted.artifact_sha256
            != output_model.sha256
        ):
            raise RuntimeChainError(
                "RUNTIME OUTPUT DOES NOT MATCH "
                "ADMITTED MODEL SHA256"
            )

        if (
            admitted.model_name
            != output_model.name
        ):
            raise RuntimeChainError(
                "RUNTIME MODEL NAME MISMATCH"
            )

        if (
            admitted.model_version
            != output_model.version
        ):
            raise RuntimeChainError(
                "RUNTIME MODEL VERSION MISMATCH"
            )

        if admitted.execution_authorized:
            raise RuntimeChainError(
                "ADMITTED INTELLIGENCE MODEL "
                "CANNOT HAVE EXECUTION AUTHORITY"
            )


    @classmethod
    def process(
        cls,
        envelope: RuntimeEnvelope,
    ) -> RuntimeChainResult:

        verify_frozen_architecture()

        # ----------------------------------------------------
        # 1. Information must itself pass the Constitution.
        # ----------------------------------------------------

        BlackBoxConstitutionV1.validate_information(
            envelope.information
        )

        # ----------------------------------------------------
        # 2. Runtime PA output must belong to admitted PA.
        # ----------------------------------------------------

        cls.validate_admission_binding(
            envelope.pa_admission,
            envelope.pa_output.model,
            Layer.PA,
        )

        BlackBoxConstitutionV1.validate_pa(
            envelope.pa_output
        )

        # ----------------------------------------------------
        # 3. Runtime ID must belong to admitted ID and must
        #    reference exactly this PA artifact.
        # ----------------------------------------------------

        cls.validate_admission_binding(
            envelope.id_admission,
            envelope.id_output.model,
            Layer.ID,
        )

        BlackBoxConstitutionV1.validate_id(
            envelope.id_output,
            envelope.pa_output,
        )

        # ----------------------------------------------------
        # 4. Runtime MPC must belong to admitted MPC and must
        #    reference exactly this PA + ID pair.
        # ----------------------------------------------------

        cls.validate_admission_binding(
            envelope.mpc_admission,
            envelope.mpc_output.model,
            Layer.MPC,
        )

        BlackBoxConstitutionV1.validate_mpc(
            envelope.mpc_output,
            envelope.pa_output,
            envelope.id_output,
        )

        # ----------------------------------------------------
        # 5. One symbol throughout entire chain.
        # ----------------------------------------------------

        symbols = {
            envelope.information.symbol,
            envelope.pa_output.symbol,
            envelope.id_output.symbol,
            envelope.mpc_output.symbol,
        }

        if len(symbols) != 1:
            raise RuntimeChainError(
                "RUNTIME SYMBOL CONTINUITY FAILURE"
            )

        # ----------------------------------------------------
        # 6. No admitted model may carry execution authority.
        # ----------------------------------------------------

        for admitted in [
            envelope.pa_admission,
            envelope.id_admission,
            envelope.mpc_admission,
        ]:
            if admitted.execution_authorized:
                raise RuntimeChainError(
                    "INTELLIGENCE EXECUTION AUTHORITY PROHIBITED"
                )

        # ----------------------------------------------------
        # 7. Only Constitution can construct the P01D handoff.
        #    This handoff is NOT execution authorization.
        # ----------------------------------------------------

        request = (
            BlackBoxConstitutionV1
            .handoff_to_p01d(
                envelope.mpc_output,
                envelope.pa_output,
                envelope.id_output,
            )
        )

        if request.execution_authorized:
            raise RuntimeChainError(
                "P01D REQUEST MUST ENTER UNAUTHORIZED"
            )

        return RuntimeChainResult(
            symbol=envelope.mpc_output.symbol,

            pa_sha256=(
                envelope.pa_admission.artifact_sha256
            ),

            id_sha256=(
                envelope.id_admission.artifact_sha256
            ),

            mpc_sha256=(
                envelope.mpc_admission.artifact_sha256
            ),

            action=envelope.mpc_output.action,

            intelligence_chain_valid=True,

            p01d_request=request,

            execution_authorized=False,
        )


# ============================================================
# SECURITY AUDIT
# ============================================================

FORBIDDEN_IMPORTS = {
    "kiteconnect",
}

FORBIDDEN_WRITE_CALLS = {
    "place_order",
    "modify_order",
    "cancel_order",
}


def source_security_audit() -> None:

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
                if item.name in FORBIDDEN_IMPORTS:
                    raise RuntimeError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):
            if node.module in FORBIDDEN_IMPORTS:
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

            if name in FORBIDDEN_WRITE_CALLS:
                raise RuntimeError(
                    "BROKER-WRITE PRIMITIVE PROHIBITED"
                )


# ============================================================
# SELF-TEST SUPPORT
# ============================================================

def write_promotion_record(
    path: Path,
    *,
    layer: Layer,
    name: str,
    version: str,
    artifact: Path,
) -> None:

    record = {
        "schema":
            "BLACK_BOX_MODEL_PROMOTION_RECORD_V1",

        "layer":
            layer.value,

        "model_name":
            name,

        "model_version":
            version,

        "artifact_filename":
            artifact.name,

        "artifact_sha256":
            sha256_file(artifact),

        "promotion_status":
            "PROMOTED",

        "promotion_decision":
            "PASS",

        "validated_out_of_sample":
            True,

        "validation_artifact_sha256":
            "f" * 64,

        "constitution_name":
            "BLACK_BOX_CONSTITUTION_V1",

        "constitution_sha256":
            EXPECTED_CONSTITUTION_SHA256,

        "broker_write_authority":
            False,

        "execution_authority":
            False,

        "revoked":
            False,

        "promoted_at_utc":
            "2026-08-25T12:00:00+00:00",
    }

    path.write_text(
        json.dumps(
            record,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def expect_runtime_rejection(
    envelope: RuntimeEnvelope,
) -> None:

    rejected = False

    try:
        RuntimeIntelligenceChainV1.process(
            envelope
        )

    except (
        RuntimeChainError,
        RuntimeError,
    ):
        rejected = True

    assert rejected


def self_test() -> None:

    source_security_audit()
    verify_frozen_architecture()

    now = datetime.now(
        timezone.utc
    )

    with TemporaryDirectory() as tmp:

        tmp = Path(tmp)

        # ----------------------------------------------------
        # Temporary test artifacts only.
        # No real models are admitted.
        # ----------------------------------------------------

        pa_artifact = tmp / "test_pa.model"
        id_artifact = tmp / "test_id.model"
        mpc_artifact = tmp / "test_mpc.model"

        pa_artifact.write_bytes(
            b"STEP5C_TEST_PA"
        )

        id_artifact.write_bytes(
            b"STEP5C_TEST_ID"
        )

        mpc_artifact.write_bytes(
            b"STEP5C_TEST_MPC"
        )

        pa_record = tmp / "pa_record.json"
        id_record = tmp / "id_record.json"
        mpc_record = tmp / "mpc_record.json"

        write_promotion_record(
            pa_record,
            layer=Layer.PA,
            name="TEST_PA",
            version="1",
            artifact=pa_artifact,
        )

        write_promotion_record(
            id_record,
            layer=Layer.ID,
            name="TEST_ID",
            version="1",
            artifact=id_artifact,
        )

        write_promotion_record(
            mpc_record,
            layer=Layer.MPC,
            name="TEST_MPC",
            version="1",
            artifact=mpc_artifact,
        )

        pa_admitted = (
            ModelAdmissionGateV1.admit(
                pa_record,
                pa_artifact,
                Layer.PA,
            )
        )

        id_admitted = (
            ModelAdmissionGateV1.admit(
                id_record,
                id_artifact,
                Layer.ID,
            )
        )

        mpc_admitted = (
            ModelAdmissionGateV1.admit(
                mpc_record,
                mpc_artifact,
                Layer.MPC,
            )
        )

        pa_identity = admitted_to_identity(
            pa_admitted
        )

        id_identity = admitted_to_identity(
            id_admitted
        )

        mpc_identity = admitted_to_identity(
            mpc_admitted
        )

        info = InformationSnapshot(
            symbol="RELIANCE",

            observation_time_utc=now,
            decision_time_utc=now,

            source_sha256="1" * 64,

            fresh=True,
            complete=True,
        )

        pa = PAOutput(
            model=pa_identity,

            symbol="RELIANCE",

            horizon_minutes=5,

            p_up=0.60,
            p_down=0.20,
            p_flat=0.20,

            expected_return_bps=10.0,
            expected_adverse_bps=5.0,

            confidence=0.75,
        )

        ida = IDOutput(
            model=id_identity,

            symbol="RELIANCE",

            pa_sha256=(
                pa_identity.sha256
            ),

            reliability=0.70,
            regime_match=0.80,
            data_quality=0.95,
            conflict_score=0.10,
        )

        mpc = MPCOutput(
            model=mpc_identity,

            symbol="RELIANCE",

            pa_sha256=(
                pa_identity.sha256
            ),

            id_sha256=(
                id_identity.sha256
            ),

            action=Action.HOLD,

            conviction=0.60,

            recommended_risk_fraction=0.0,
        )

        good = RuntimeEnvelope(
            information=info,

            pa_admission=pa_admitted,
            pa_output=pa,

            id_admission=id_admitted,
            id_output=ida,

            mpc_admission=mpc_admitted,
            mpc_output=mpc,
        )

        result = (
            RuntimeIntelligenceChainV1.process(
                good
            )
        )

        assert result.intelligence_chain_valid
        assert not result.execution_authorized
        assert not (
            result
            .p01d_request
            .execution_authorized
        )

        # ----------------------------------------------------
        # Wrong PA runtime hash must fail.
        # ----------------------------------------------------

        bad_pa_identity = ModelIdentity(
            layer=Layer.PA,
            name="TEST_PA",
            version="1",
            sha256="9" * 64,
            promotion_status=PromotionStatus.PROMOTED,
            validated_out_of_sample=True,
        )

        bad_pa = PAOutput(
            model=bad_pa_identity,

            symbol="RELIANCE",

            horizon_minutes=5,

            p_up=0.60,
            p_down=0.20,
            p_flat=0.20,

            expected_return_bps=10.0,
            expected_adverse_bps=5.0,

            confidence=0.75,
        )

        expect_runtime_rejection(
            RuntimeEnvelope(
                information=info,

                pa_admission=pa_admitted,
                pa_output=bad_pa,

                id_admission=id_admitted,
                id_output=ida,

                mpc_admission=mpc_admitted,
                mpc_output=mpc,
            )
        )

        # ----------------------------------------------------
        # PA/ID provenance mismatch must fail.
        # ----------------------------------------------------

        bad_id = IDOutput(
            model=id_identity,

            symbol="RELIANCE",

            pa_sha256="8" * 64,

            reliability=0.70,
            regime_match=0.80,
            data_quality=0.95,
            conflict_score=0.10,
        )

        expect_runtime_rejection(
            RuntimeEnvelope(
                information=info,

                pa_admission=pa_admitted,
                pa_output=pa,

                id_admission=id_admitted,
                id_output=bad_id,

                mpc_admission=mpc_admitted,
                mpc_output=mpc,
            )
        )

        # ----------------------------------------------------
        # MPC/ID provenance mismatch must fail.
        # ----------------------------------------------------

        bad_mpc = MPCOutput(
            model=mpc_identity,

            symbol="RELIANCE",

            pa_sha256=(
                pa_identity.sha256
            ),

            id_sha256="7" * 64,

            action=Action.HOLD,

            conviction=0.60,

            recommended_risk_fraction=0.0,
        )

        expect_runtime_rejection(
            RuntimeEnvelope(
                information=info,

                pa_admission=pa_admitted,
                pa_output=pa,

                id_admission=id_admitted,
                id_output=ida,

                mpc_admission=mpc_admitted,
                mpc_output=bad_mpc,
            )
        )

        # ----------------------------------------------------
        # Symbol continuity failure must fail.
        # ----------------------------------------------------

        wrong_symbol_mpc = MPCOutput(
            model=mpc_identity,

            symbol="SBIN",

            pa_sha256=(
                pa_identity.sha256
            ),

            id_sha256=(
                id_identity.sha256
            ),

            action=Action.HOLD,

            conviction=0.60,

            recommended_risk_fraction=0.0,
        )

        expect_runtime_rejection(
            RuntimeEnvelope(
                information=info,

                pa_admission=pa_admitted,
                pa_output=pa,

                id_admission=id_admitted,
                id_output=ida,

                mpc_admission=mpc_admitted,
                mpc_output=wrong_symbol_mpc,
            )
        )

        # ----------------------------------------------------
        # Stale information must fail.
        # ----------------------------------------------------

        stale_info = InformationSnapshot(
            symbol="RELIANCE",

            observation_time_utc=now,
            decision_time_utc=now,

            source_sha256="1" * 64,

            fresh=False,
            complete=True,
        )

        expect_runtime_rejection(
            RuntimeEnvelope(
                information=stale_info,

                pa_admission=pa_admitted,
                pa_output=pa,

                id_admission=id_admitted,
                id_output=ida,

                mpc_admission=mpc_admitted,
                mpc_output=mpc,
            )
        )

    print("=" * 98)
    print("STEP 5C - RUNTIME INTELLIGENCE CHAIN V1 SELF TEST")
    print("=" * 98)

    print(
        "Frozen Constitution integrity       : PASS"
    )

    print(
        "Frozen Admission Gate integrity     : PASS"
    )

    print(
        "Step 5B freeze integrity            : PASS"
    )

    print(
        "Certified-information boundary      : PASS"
    )

    print(
        "Admitted PA binding                 : PASS"
    )

    print(
        "PA -> ID provenance continuity      : PASS"
    )

    print(
        "ID -> MPC provenance continuity     : PASS"
    )

    print(
        "Symbol continuity                   : PASS"
    )

    print(
        "Stale-data fail-closed              : PASS"
    )

    print(
        "Wrong-model SHA fail-closed         : PASS"
    )

    print(
        "P01D handoff boundary               : PASS"
    )

    print(
        "P01D request execution authority    : FALSE"
    )

    print(
        "Broker imports                      : NONE"
    )

    print(
        "Broker-write authority              : NONE"
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
        "REAL INTELLIGENCE EXECUTED          : NO"
    )

    print(
        "PRODUCTION                          : FALSE"
    )

    print(
        "P01D SOVEREIGNTY                    : PRESERVED"
    )

    print("=" * 98)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.self_test:
        raise SystemExit(
            "Runtime Intelligence Chain library only. "
            "Use --self-test."
        )

    self_test()


if __name__ == "__main__":
    main()
