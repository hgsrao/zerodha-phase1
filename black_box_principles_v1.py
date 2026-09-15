"""
BLACK BOX CONSTITUTION V1
=========================

Permanent architectural rules:

DATA -> INFORMATION -> PA -> ID -> MPC -> P01D

PA   = predicts
ID   = judges prediction reliability
MPC  = recommends an action
P01D = sole safety / execution authority

This file contains NO trading strategy and NO broker authority.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import argparse
import ast
import math


class Layer(str, Enum):
    PA = "PA"
    ID = "ID"
    MPC = "MPC"


class PromotionStatus(str, Enum):
    RESEARCH = "RESEARCH"
    VALIDATION = "VALIDATION"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class Action(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class ModelIdentity:
    layer: Layer
    name: str
    version: str
    sha256: str
    promotion_status: PromotionStatus
    validated_out_of_sample: bool


@dataclass(frozen=True)
class InformationSnapshot:
    symbol: str
    observation_time_utc: datetime
    decision_time_utc: datetime
    source_sha256: str
    fresh: bool
    complete: bool


@dataclass(frozen=True)
class PAOutput:
    model: ModelIdentity
    symbol: str
    horizon_minutes: int

    p_up: float
    p_down: float
    p_flat: float

    expected_return_bps: float
    expected_adverse_bps: float
    confidence: float

    execution_authorized: bool = False


@dataclass(frozen=True)
class IDOutput:
    model: ModelIdentity
    symbol: str

    pa_sha256: str

    reliability: float
    regime_match: float
    data_quality: float
    conflict_score: float

    execution_authorized: bool = False


@dataclass(frozen=True)
class MPCOutput:
    model: ModelIdentity
    symbol: str

    pa_sha256: str
    id_sha256: str

    action: Action
    conviction: float
    recommended_risk_fraction: float

    execution_authorized: bool = False


@dataclass(frozen=True)
class P01DRequest:
    symbol: str
    recommendation: MPCOutput

    # This means provenance is structurally valid.
    # It does NOT mean trade execution is approved.
    intelligence_provenance_valid: bool

    execution_authorized: bool = False


@dataclass(frozen=True)
class OutcomeFeedback:
    symbol: str

    decision_time_utc: datetime
    outcome_time_utc: datetime

    realized_return_bps: float
    realized_adverse_bps: float

    online_learning_allowed: bool = False


class BlackBoxConstitutionV1:

    @staticmethod
    def probability(name, value):

        if not math.isfinite(value):
            raise RuntimeError(
                f"{name}: non-finite"
            )

        if not 0.0 <= value <= 1.0:
            raise RuntimeError(
                f"{name}: outside [0,1]"
            )


    @staticmethod
    def validate_model(
        model,
        expected_layer,
    ):

        if model.layer != expected_layer:
            raise RuntimeError(
                "LAYER RESPONSIBILITY VIOLATION"
            )

        if (
            model.promotion_status
            != PromotionStatus.PROMOTED
        ):
            raise RuntimeError(
                "UNPROMOTED MODEL PROHIBITED"
            )

        if not model.validated_out_of_sample:
            raise RuntimeError(
                "OOS VALIDATION REQUIRED"
            )

        if len(model.sha256) != 64:
            raise RuntimeError(
                "MODEL PROVENANCE INVALID"
            )


    @staticmethod
    def validate_information(info):

        if (
            info.observation_time_utc
            > info.decision_time_utc
        ):
            raise RuntimeError(
                "FUTURE INFORMATION / LEAKAGE PROHIBITED"
            )

        if not info.fresh:
            raise RuntimeError(
                "STALE DATA - FAIL CLOSED"
            )

        if not info.complete:
            raise RuntimeError(
                "INCOMPLETE DATA - FAIL CLOSED"
            )

        if len(info.source_sha256) != 64:
            raise RuntimeError(
                "SOURCE PROVENANCE INVALID"
            )


    @classmethod
    def validate_pa(cls, pa):

        cls.validate_model(
            pa.model,
            Layer.PA,
        )

        if pa.execution_authorized:
            raise RuntimeError(
                "PA MAY NOT AUTHORIZE EXECUTION"
            )

        if pa.horizon_minutes <= 0:
            raise RuntimeError(
                "INVALID PA HORIZON"
            )

        for name, value in [
            ("p_up", pa.p_up),
            ("p_down", pa.p_down),
            ("p_flat", pa.p_flat),
            ("confidence", pa.confidence),
        ]:
            cls.probability(
                name,
                value,
            )

        total = (
            pa.p_up
            + pa.p_down
            + pa.p_flat
        )

        if not math.isclose(
            total,
            1.0,
            abs_tol=1e-6,
        ):
            raise RuntimeError(
                "PA PROBABILITIES MUST SUM TO 1"
            )


    @classmethod
    def validate_id(
        cls,
        ida,
        pa,
    ):

        cls.validate_pa(pa)

        cls.validate_model(
            ida.model,
            Layer.ID,
        )

        if ida.execution_authorized:
            raise RuntimeError(
                "ID MAY NOT AUTHORIZE EXECUTION"
            )

        if ida.symbol != pa.symbol:
            raise RuntimeError(
                "PA / ID SYMBOL MISMATCH"
            )

        if (
            ida.pa_sha256
            != pa.model.sha256
        ):
            raise RuntimeError(
                "ID / PA PROVENANCE MISMATCH"
            )

        for name, value in [
            ("reliability", ida.reliability),
            ("regime_match", ida.regime_match),
            ("data_quality", ida.data_quality),
            ("conflict_score", ida.conflict_score),
        ]:
            cls.probability(
                name,
                value,
            )


    @classmethod
    def validate_mpc(
        cls,
        mpc,
        pa,
        ida,
    ):

        cls.validate_id(
            ida,
            pa,
        )

        cls.validate_model(
            mpc.model,
            Layer.MPC,
        )

        if mpc.execution_authorized:
            raise RuntimeError(
                "MPC MAY NOT AUTHORIZE EXECUTION"
            )

        if not (
            mpc.symbol
            == pa.symbol
            == ida.symbol
        ):
            raise RuntimeError(
                "PA / ID / MPC SYMBOL MISMATCH"
            )

        if (
            mpc.pa_sha256
            != pa.model.sha256
        ):
            raise RuntimeError(
                "MPC / PA PROVENANCE MISMATCH"
            )

        if (
            mpc.id_sha256
            != ida.model.sha256
        ):
            raise RuntimeError(
                "MPC / ID PROVENANCE MISMATCH"
            )

        cls.probability(
            "conviction",
            mpc.conviction,
        )

        cls.probability(
            "recommended_risk_fraction",
            mpc.recommended_risk_fraction,
        )


    @classmethod
    def handoff_to_p01d(
        cls,
        mpc,
        pa,
        ida,
    ):

        cls.validate_mpc(
            mpc,
            pa,
            ida,
        )

        return P01DRequest(
            symbol=mpc.symbol,
            recommendation=mpc,
            intelligence_provenance_valid=True,

            # CRITICAL:
            # P01D must separately authorize.
            execution_authorized=False,
        )


    @staticmethod
    def validate_feedback(
        feedback,
    ):

        if feedback.online_learning_allowed:
            raise RuntimeError(
                "ONLINE SELF-LEARNING PROHIBITED"
            )

        if (
            feedback.outcome_time_utc
            <= feedback.decision_time_utc
        ):
            raise RuntimeError(
                "INVALID FEEDBACK CHRONOLOGY"
            )


FORBIDDEN_IMPORTS = {
    "kiteconnect",
}

FORBIDDEN_WRITE_CALLS = {
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
                    "BROKER WRITE AUTHORITY PROHIBITED"
                )


def fake_hash(char):
    return char * 64


def self_test():

    security_audit()

    now = datetime.now(
        timezone.utc
    )

    info = InformationSnapshot(
        symbol="RELIANCE",
        observation_time_utc=now,
        decision_time_utc=now,
        source_sha256=fake_hash("1"),
        fresh=True,
        complete=True,
    )

    pa_model = ModelIdentity(
        layer=Layer.PA,
        name="TEST_PA",
        version="1",
        sha256=fake_hash("a"),
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )

    id_model = ModelIdentity(
        layer=Layer.ID,
        name="TEST_ID",
        version="1",
        sha256=fake_hash("b"),
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )

    mpc_model = ModelIdentity(
        layer=Layer.MPC,
        name="TEST_MPC",
        version="1",
        sha256=fake_hash("c"),
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )

    pa = PAOutput(
        model=pa_model,
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
        model=id_model,
        symbol="RELIANCE",

        pa_sha256=pa_model.sha256,

        reliability=0.70,
        regime_match=0.80,
        data_quality=0.95,
        conflict_score=0.10,
    )

    mpc = MPCOutput(
        model=mpc_model,
        symbol="RELIANCE",

        pa_sha256=pa_model.sha256,
        id_sha256=id_model.sha256,

        action=Action.HOLD,
        conviction=0.60,
        recommended_risk_fraction=0.0,
    )

    BlackBoxConstitutionV1.validate_information(
        info
    )

    BlackBoxConstitutionV1.validate_pa(
        pa
    )

    BlackBoxConstitutionV1.validate_id(
        ida,
        pa,
    )

    BlackBoxConstitutionV1.validate_mpc(
        mpc,
        pa,
        ida,
    )

    request = (
        BlackBoxConstitutionV1
        .handoff_to_p01d(
            mpc,
            pa,
            ida,
        )
    )

    assert (
        request.execution_authorized
        is False
    )

    # --------------------------------------------------------
    # Reject unpromoted research.
    # --------------------------------------------------------

    rejected_model = ModelIdentity(
        layer=Layer.PA,
        name="FAILED_RESEARCH",
        version="1",
        sha256=fake_hash("d"),
        promotion_status=PromotionStatus.REJECTED,
        validated_out_of_sample=False,
    )

    rejected = False

    try:
        BlackBoxConstitutionV1.validate_model(
            rejected_model,
            Layer.PA,
        )

    except RuntimeError:
        rejected = True

    assert rejected

    # --------------------------------------------------------
    # Reject future leakage.
    # --------------------------------------------------------

    future_info = InformationSnapshot(
        symbol="RELIANCE",

        observation_time_utc=datetime(
            2030,
            1,
            1,
            tzinfo=timezone.utc,
        ),

        decision_time_utc=now,

        source_sha256=fake_hash("e"),

        fresh=True,
        complete=True,
    )

    rejected = False

    try:
        BlackBoxConstitutionV1.validate_information(
            future_info
        )

    except RuntimeError:
        rejected = True

    assert rejected

    # --------------------------------------------------------
    # Reject online self-learning.
    # --------------------------------------------------------

    feedback = OutcomeFeedback(
        symbol="RELIANCE",

        decision_time_utc=now,

        outcome_time_utc=datetime(
            now.year + 1,
            now.month,
            now.day,
            tzinfo=timezone.utc,
        ),

        realized_return_bps=10.0,
        realized_adverse_bps=5.0,

        online_learning_allowed=True,
    )

    rejected = False

    try:
        BlackBoxConstitutionV1.validate_feedback(
            feedback
        )

    except RuntimeError:
        rejected = True

    assert rejected

    print("=" * 92)
    print("BLACK BOX CONSTITUTION V1 - SELF TEST")
    print("=" * 92)

    print("Promotion gate                    : PASS")
    print("Data freshness / completeness     : PASS")
    print("Future-leakage rejection          : PASS")
    print("PA prediction-only boundary       : PASS")
    print("ID reliability-only boundary      : PASS")
    print("MPC recommendation-only boundary  : PASS")
    print("P01D sole execution boundary      : PASS")
    print("Model provenance                  : PASS")
    print("Unpromoted-model rejection        : PASS")
    print("Online self-learning prohibition  : PASS")
    print("Broker imports                    : NONE")
    print("Broker-write authority            : NONE")

    print()
    print("PA REAL MODEL                     : NOT PROMOTED")
    print("ID REAL MODEL                     : NOT PROMOTED")
    print("MPC REAL POLICY                   : NOT VALIDATED")
    print("PRODUCTION                        : FALSE")
    print("P01D SOVEREIGNTY                  : PRESERVED")

    print("=" * 92)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.self_test:
        raise SystemExit(
            "Architecture module only. Use --self-test."
        )

    self_test()


if __name__ == "__main__":
    main()
