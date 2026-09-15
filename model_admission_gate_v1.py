"""
STEP 5B - MODEL ADMISSION GATE V1
=================================

Purpose
-------
Mechanically enforce the Black Box Constitution promotion boundary.

A PA / ID / MPC model may enter the intelligence stack ONLY if:

1. Its declared architectural layer is correct.
2. Promotion status is PROMOTED.
3. Out-of-sample validation passed.
4. Promotion decision is PASS.
5. Artifact SHA256 matches the immutable record.
6. The record explicitly targets Black Box Constitution V1.
7. Constitution SHA256 matches the frozen Step-5A constitution.
8. The model has not been revoked.
9. Broker-write / execution authority is absent.

This module DOES NOT:
- train a model,
- evaluate a model,
- promote a model,
- place an order,
- authorize execution.

P01D remains sovereign.
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
import re

from black_box_principles_v1 import (
    Layer,
    PromotionStatus,
)


FROZEN_CONSTITUTION_SHA256 = (
    "8dd0ef7b1983a636df103dc84e2f52c8"
    "227624cb3cbc3cb4f7558eab3c9785c0"
)

CONSTITUTION_FILE = (
    Path(__file__).resolve().parent
    / "black_box_principles_v1.py"
)


class ModelAdmissionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelAdmissionRecord:
    schema: str

    layer: str
    model_name: str
    model_version: str

    artifact_filename: str
    artifact_sha256: str

    promotion_status: str
    promotion_decision: str

    validated_out_of_sample: bool

    validation_artifact_sha256: str

    constitution_name: str
    constitution_sha256: str

    broker_write_authority: bool
    execution_authority: bool

    revoked: bool

    promoted_at_utc: str


@dataclass(frozen=True)
class AdmittedModel:
    layer: Layer

    model_name: str
    model_version: str

    artifact_path: str
    artifact_sha256: str

    validation_artifact_sha256: str

    constitution_sha256: str

    admitted_at_utc: str

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


def valid_sha256(value: str) -> bool:

    return bool(
        re.fullmatch(
            r"[0-9a-f]{64}",
            str(value).lower(),
        )
    )


def verify_frozen_constitution() -> None:

    if not CONSTITUTION_FILE.exists():
        raise ModelAdmissionError(
            "Frozen constitution file missing"
        )

    actual = sha256_file(
        CONSTITUTION_FILE
    )

    if actual != FROZEN_CONSTITUTION_SHA256:
        raise ModelAdmissionError(
            "BLACK BOX CONSTITUTION HASH MISMATCH"
        )


def load_record(
    path: Path,
) -> ModelAdmissionRecord:

    if not path.exists():
        raise ModelAdmissionError(
            f"Admission record missing: {path}"
        )

    try:
        obj = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return ModelAdmissionRecord(
            **obj
        )

    except Exception as exc:
        raise ModelAdmissionError(
            "Malformed admission record"
        ) from exc


class ModelAdmissionGateV1:

    RECORD_SCHEMA = (
        "BLACK_BOX_MODEL_PROMOTION_RECORD_V1"
    )

    CONSTITUTION_NAME = (
        "BLACK_BOX_CONSTITUTION_V1"
    )

    @classmethod
    def admit(
        cls,
        record_path: Path,
        artifact_path: Path,
        expected_layer: Layer,
    ) -> AdmittedModel:

        # ----------------------------------------------------
        # Constitution itself must still be intact.
        # ----------------------------------------------------

        verify_frozen_constitution()

        record = load_record(
            record_path
        )

        # ----------------------------------------------------
        # Record schema.
        # ----------------------------------------------------

        if (
            record.schema
            != cls.RECORD_SCHEMA
        ):
            raise ModelAdmissionError(
                "UNSUPPORTED PROMOTION RECORD SCHEMA"
            )

        # ----------------------------------------------------
        # Layer identity.
        # ----------------------------------------------------

        try:
            declared_layer = Layer(
                record.layer
            )

        except Exception as exc:
            raise ModelAdmissionError(
                "INVALID DECLARED MODEL LAYER"
            ) from exc

        if (
            declared_layer
            != expected_layer
        ):
            raise ModelAdmissionError(
                "MODEL LAYER MISMATCH"
            )

        # ----------------------------------------------------
        # Promotion status.
        # ----------------------------------------------------

        try:
            promotion_status = (
                PromotionStatus(
                    record.promotion_status
                )
            )

        except Exception as exc:
            raise ModelAdmissionError(
                "INVALID PROMOTION STATUS"
            ) from exc

        if (
            promotion_status
            != PromotionStatus.PROMOTED
        ):
            raise ModelAdmissionError(
                "MODEL NOT PROMOTED"
            )

        if (
            record.promotion_decision
            != "PASS"
        ):
            raise ModelAdmissionError(
                "PROMOTION DECISION IS NOT PASS"
            )

        # ----------------------------------------------------
        # OOS validation is mandatory.
        # ----------------------------------------------------

        if not (
            record.validated_out_of_sample
            is True
        ):
            raise ModelAdmissionError(
                "OUT-OF-SAMPLE VALIDATION REQUIRED"
            )

        if not valid_sha256(
            record.validation_artifact_sha256
        ):
            raise ModelAdmissionError(
                "VALIDATION PROVENANCE INVALID"
            )

        # ----------------------------------------------------
        # Revocation check.
        # ----------------------------------------------------

        if record.revoked:
            raise ModelAdmissionError(
                "MODEL PROMOTION REVOKED"
            )

        # ----------------------------------------------------
        # Black Box Constitution binding.
        # ----------------------------------------------------

        if (
            record.constitution_name
            != cls.CONSTITUTION_NAME
        ):
            raise ModelAdmissionError(
                "MODEL TARGETS WRONG CONSTITUTION"
            )

        if (
            record.constitution_sha256
            != FROZEN_CONSTITUTION_SHA256
        ):
            raise ModelAdmissionError(
                "CONSTITUTION PROVENANCE MISMATCH"
            )

        # ----------------------------------------------------
        # Zero execution authority inside intelligence stack.
        # ----------------------------------------------------

        if record.broker_write_authority:
            raise ModelAdmissionError(
                "BROKER-WRITE AUTHORITY PROHIBITED"
            )

        if record.execution_authority:
            raise ModelAdmissionError(
                "EXECUTION AUTHORITY PROHIBITED"
            )

        # ----------------------------------------------------
        # Artifact identity.
        # ----------------------------------------------------

        if not artifact_path.exists():
            raise ModelAdmissionError(
                "MODEL ARTIFACT MISSING"
            )

        if (
            artifact_path.name
            != record.artifact_filename
        ):
            raise ModelAdmissionError(
                "ARTIFACT FILENAME MISMATCH"
            )

        if not valid_sha256(
            record.artifact_sha256
        ):
            raise ModelAdmissionError(
                "DECLARED ARTIFACT SHA256 INVALID"
            )

        actual_sha = sha256_file(
            artifact_path
        )

        if (
            actual_sha
            != record.artifact_sha256
        ):
            raise ModelAdmissionError(
                "MODEL ARTIFACT HASH MISMATCH"
            )

        # ----------------------------------------------------
        # Promotion timestamp must be valid UTC-aware ISO.
        # ----------------------------------------------------

        try:
            promoted = datetime.fromisoformat(
                record.promoted_at_utc
            )

        except Exception as exc:
            raise ModelAdmissionError(
                "INVALID PROMOTION TIMESTAMP"
            ) from exc

        if promoted.tzinfo is None:
            raise ModelAdmissionError(
                "PROMOTION TIMESTAMP MUST BE TIMEZONE-AWARE"
            )

        # ----------------------------------------------------
        # Successful admission.
        #
        # Still ZERO execution authority.
        # ----------------------------------------------------

        return AdmittedModel(
            layer=declared_layer,

            model_name=record.model_name,
            model_version=record.model_version,

            artifact_path=str(
                artifact_path.resolve()
            ),

            artifact_sha256=actual_sha,

            validation_artifact_sha256=(
                record.validation_artifact_sha256
            ),

            constitution_sha256=(
                record.constitution_sha256
            ),

            admitted_at_utc=datetime.now(
                timezone.utc
            ).isoformat(),

            execution_authorized=False,
        )


# ============================================================
# SOURCE SECURITY AUDIT
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

            if (
                name
                in FORBIDDEN_WRITE_CALLS
            ):
                raise RuntimeError(
                    "BROKER-WRITE PRIMITIVE PROHIBITED"
                )


# ============================================================
# OFFLINE SELF TEST
# ============================================================

def write_record(
    path: Path,
    **overrides,
):

    base = {
        "schema":
            "BLACK_BOX_MODEL_PROMOTION_RECORD_V1",

        "layer":
            "PA",

        "model_name":
            "SELF_TEST_PA",

        "model_version":
            "1.0",

        "artifact_filename":
            "self_test_pa.model",

        "artifact_sha256":
            "",

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
            FROZEN_CONSTITUTION_SHA256,

        "broker_write_authority":
            False,

        "execution_authority":
            False,

        "revoked":
            False,

        "promoted_at_utc":
            "2026-08-25T12:00:00+00:00",
    }

    base.update(
        overrides
    )

    path.write_text(
        json.dumps(
            base,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def expect_rejection(
    record_path: Path,
    artifact_path: Path,
    expected_layer: Layer,
):

    rejected = False

    try:
        ModelAdmissionGateV1.admit(
            record_path,
            artifact_path,
            expected_layer,
        )

    except ModelAdmissionError:
        rejected = True

    assert rejected


def self_test() -> None:

    source_security_audit()

    verify_frozen_constitution()

    with TemporaryDirectory() as tmp:

        tmp = Path(tmp)

        artifact = (
            tmp
            / "self_test_pa.model"
        )

        artifact.write_bytes(
            b"SELF_TEST_MODEL_ARTIFACT_V1"
        )

        artifact_sha = sha256_file(
            artifact
        )

        good_record = (
            tmp
            / "good.json"
        )

        write_record(
            good_record,
            artifact_sha256=artifact_sha,
        )

        admitted = (
            ModelAdmissionGateV1.admit(
                good_record,
                artifact,
                Layer.PA,
            )
        )

        assert (
            admitted.layer
            == Layer.PA
        )

        assert (
            admitted.execution_authorized
            is False
        )

        # ----------------------------------------------------
        # Unpromoted model rejected.
        # ----------------------------------------------------

        unpromoted = (
            tmp
            / "unpromoted.json"
        )

        write_record(
            unpromoted,
            artifact_sha256=artifact_sha,
            promotion_status="VALIDATION",
        )

        expect_rejection(
            unpromoted,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Failed promotion decision rejected.
        # ----------------------------------------------------

        failed = (
            tmp
            / "failed.json"
        )

        write_record(
            failed,
            artifact_sha256=artifact_sha,
            promotion_decision="FAIL",
        )

        expect_rejection(
            failed,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # No OOS validation rejected.
        # ----------------------------------------------------

        no_oos = (
            tmp
            / "no_oos.json"
        )

        write_record(
            no_oos,
            artifact_sha256=artifact_sha,
            validated_out_of_sample=False,
        )

        expect_rejection(
            no_oos,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Wrong model hash rejected.
        # ----------------------------------------------------

        bad_hash = (
            tmp
            / "bad_hash.json"
        )

        write_record(
            bad_hash,
            artifact_sha256=("0" * 64),
        )

        expect_rejection(
            bad_hash,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Layer mismatch rejected.
        # ----------------------------------------------------

        wrong_layer = (
            tmp
            / "wrong_layer.json"
        )

        write_record(
            wrong_layer,
            artifact_sha256=artifact_sha,
            layer="ID",
        )

        expect_rejection(
            wrong_layer,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Wrong constitution rejected.
        # ----------------------------------------------------

        wrong_constitution = (
            tmp
            / "wrong_constitution.json"
        )

        write_record(
            wrong_constitution,
            artifact_sha256=artifact_sha,
            constitution_sha256=("1" * 64),
        )

        expect_rejection(
            wrong_constitution,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Revoked model rejected.
        # ----------------------------------------------------

        revoked = (
            tmp
            / "revoked.json"
        )

        write_record(
            revoked,
            artifact_sha256=artifact_sha,
            revoked=True,
        )

        expect_rejection(
            revoked,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Broker authority rejected.
        # ----------------------------------------------------

        broker_write = (
            tmp
            / "broker_write.json"
        )

        write_record(
            broker_write,
            artifact_sha256=artifact_sha,
            broker_write_authority=True,
        )

        expect_rejection(
            broker_write,
            artifact,
            Layer.PA,
        )

        # ----------------------------------------------------
        # Execution authority rejected.
        # ----------------------------------------------------

        execution = (
            tmp
            / "execution.json"
        )

        write_record(
            execution,
            artifact_sha256=artifact_sha,
            execution_authority=True,
        )

        expect_rejection(
            execution,
            artifact,
            Layer.PA,
        )

    print("=" * 96)
    print("STEP 5B - MODEL ADMISSION GATE V1 SELF TEST")
    print("=" * 96)

    print(
        "Frozen Constitution integrity       : PASS"
    )

    print(
        "Valid promoted-model admission      : PASS"
    )

    print(
        "Layer identity enforcement          : PASS"
    )

    print(
        "Promotion-status enforcement        : PASS"
    )

    print(
        "Promotion-decision enforcement      : PASS"
    )

    print(
        "OOS-validation enforcement         : PASS"
    )

    print(
        "Model SHA256 enforcement            : PASS"
    )

    print(
        "Validation provenance enforcement   : PASS"
    )

    print(
        "Constitution binding               : PASS"
    )

    print(
        "Revocation enforcement              : PASS"
    )

    print(
        "Broker-authority rejection          : PASS"
    )

    print(
        "Execution-authority rejection       : PASS"
    )

    print(
        "Unpromoted-model rejection          : PASS"
    )

    print(
        "Broker imports                      : NONE"
    )

    print(
        "Broker-write authority              : NONE"
    )

    print()
    print(
        "REAL PA MODEL ADMITTED              : NO"
    )

    print(
        "REAL ID MODEL ADMITTED              : NO"
    )

    print(
        "REAL MPC MODEL ADMITTED             : NO"
    )

    print(
        "PRODUCTION                          : FALSE"
    )

    print(
        "P01D SOVEREIGNTY                    : PRESERVED"
    )

    print("=" * 96)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.self_test:
        raise SystemExit(
            "Model Admission Gate library only. "
            "Use --self-test."
        )

    self_test()


if __name__ == "__main__":
    main()
