"""
STEP 2 - MICROSTRUCTURE FEATURE DICTIONARY V1
==============================================

Definitions only.

NO feature is declared predictive.
NO trading threshold is defined.
NO feature weight is fitted.
NO broker authority.
NO execution authority.

Authoritative downstream path:
STEP 2 -> PA -> ID -> MPC -> P01D
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


class FeatureArchitectureError(RuntimeError):
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
        raise FeatureArchitectureError(
            "STEP 6 FREEZE MISSING"
        )

    if sha256_file(STEP6_FREEZE) != EXPECTED_STEP6_FREEZE_SHA256:
        raise FeatureArchitectureError(
            "STEP 6 FREEZE HASH MISMATCH"
        )


def safe_ratio(num: float, den: float):

    if den <= 0:
        return None

    return num / den


def static_obi(
    bid_qty,
    ask_qty,
) -> float | None:

    bid = sum(float(x) for x in bid_qty)
    ask = sum(float(x) for x in ask_qty)

    return safe_ratio(
        bid - ask,
        bid + ask,
    )


def microprice_l1(
    bid_price: float,
    bid_qty: float,
    ask_price: float,
    ask_qty: float,
) -> float | None:

    den = bid_qty + ask_qty

    if den <= 0:
        return None

    return (
        bid_price * ask_qty
        + ask_price * bid_qty
    ) / den


def vamp_equal_depth(
    bid_prices,
    bid_qty,
    ask_prices,
    ask_qty,
) -> float | None:

    if not (
        len(bid_prices)
        == len(bid_qty)
        == len(ask_prices)
        == len(ask_qty)
    ):
        raise ValueError(
            "DEPTH ARRAY LENGTH MISMATCH"
        )

    total_bid = sum(
        float(x)
        for x in bid_qty
    )

    total_ask = sum(
        float(x)
        for x in ask_qty
    )

    den = total_bid + total_ask

    if den <= 0:
        return None

    cross_bid = sum(
        float(p) * float(q)
        for p, q in zip(
            bid_prices,
            ask_qty,
        )
    )

    cross_ask = sum(
        float(p) * float(q)
        for p, q in zip(
            ask_prices,
            bid_qty,
        )
    )

    return (
        cross_bid
        + cross_ask
    ) / den


def spread_bps(
    bid_price: float,
    ask_price: float,
) -> float | None:

    mid = (
        float(bid_price)
        + float(ask_price)
    ) / 2.0

    if mid <= 0:
        return None

    return (
        float(ask_price)
        - float(bid_price)
    ) / mid * 10000.0


FEATURE_DICTIONARY = [

    {
        "feature_id":
            "MID_PRICE_L1",

        "family":
            "PRICE_STATE",

        "definition":
            "(BEST_BID + BEST_ASK) / 2",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "SPREAD_BPS_L1",

        "family":
            "LIQUIDITY_STATE",

        "definition":
            "(BEST_ASK - BEST_BID) / MID * 10000",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "STATIC_OBI_L1_EQUAL_WEIGHT",

        "family":
            "STATIC_ORDER_BOOK_IMBALANCE",

        "definition":
            "(BID_QTY_L1 - ASK_QTY_L1) / "
            "(BID_QTY_L1 + ASK_QTY_L1)",

        "range":
            "[-1,1]",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "STATIC_OBI_L5_EQUAL_WEIGHT",

        "family":
            "STATIC_ORDER_BOOK_IMBALANCE",

        "definition":
            "(SUM_BID_QTY_L1_L5 - SUM_ASK_QTY_L1_L5) / "
            "(SUM_BID_QTY_L1_L5 + SUM_ASK_QTY_L1_L5)",

        "range":
            "[-1,1]",

        "level_weights":
            [1, 1, 1, 1, 1],

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "MICROPRICE_L1",

        "family":
            "IMBALANCE_ADJUSTED_PRICE",

        "definition":
            "(BID_PRICE*ASK_QTY + ASK_PRICE*BID_QTY) / "
            "(BID_QTY + ASK_QTY)",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "VAMP_L5_EQUAL_DEPTH",

        "family":
            "VOLUME_ADJUSTED_PRICE",

        "definition":
            "CROSS_MULTIPLIED_BID_ASK_PRICE_QUANTITY_OVER_TOTAL_DEPTH",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "TOTAL_BID_DEPTH_L5",

        "family":
            "DEPTH_STATE",

        "definition":
            "SUM_BID_QTY_L1_L5",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "TOTAL_ASK_DEPTH_L5",

        "family":
            "DEPTH_STATE",

        "definition":
            "SUM_ASK_QTY_L1_L5",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "ORDER_COUNT_IMBALANCE_L5",

        "family":
            "DISPLAYED_ORDER_COUNT_STATE",

        "definition":
            "(SUM_BID_ORDER_COUNT - SUM_ASK_ORDER_COUNT) / "
            "(SUM_BID_ORDER_COUNT + SUM_ASK_ORDER_COUNT)",

        "observable_from_top5_kite":
            True,

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "SEQUENTIAL_DEPTH_CHANGE",

        "family":
            "STATE_CHANGE",

        "definition":
            "CHANGE_IN_DISPLAYED_TOP5_STATE_BETWEEN_OBSERVATIONS",

        "observable_from_top5_kite":
            True,

        "requires_multiple_observations":
            True,

        "must_not_be_called":
            "TRUE_EXCHANGE_EVENT_OFI",

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "TRUE_EVENT_LEVEL_OFI",

        "family":
            "EVENT_ORDER_FLOW",

        "definition":
            "ADD_CANCEL_EXECUTE_EVENT_BASED_ORDER_FLOW_IMBALANCE",

        "observable_from_top5_kite":
            False,

        "status":
            "NOT_OBSERVABLE_FROM_TOP5_STATE_FEED_ALONE",

        "predictive_claim":
            False,
    },

    {
        "feature_id":
            "DISTANCE_WEIGHTED_OBI_L5",

        "family":
            "STATIC_ORDER_BOOK_IMBALANCE",

        "definition":
            "WEIGHTED_L1_L5_DEPTH_IMBALANCE",

        "level_weights":
            None,

        "status":
            "RESEARCH_CANDIDATE_UNDEFINED",

        "reason":
            "WEIGHTS_MUST_NOT_BE_INVENTED",

        "predictive_claim":
            False,
    },
]


ARCHITECTURE = {

    "schema":
        "STEP2_MICROSTRUCTURE_FEATURE_DICTIONARY_V1",

    "status":
        "CANDIDATE_NOT_FROZEN",

    "serial_path":
        "STEP2 -> PA -> ID -> MPC -> P01D",

    "source_families": [
        "HFTBACKTEST_STATIC_ORDER_BOOK_IMBALANCE",
        "MICROPRICE",
        "VAMP",
        "TOP5_DISPLAYED_DEPTH",
    ],

    "feed_contract": {
        "depth_levels":
            5,

        "full_exchange_order_ids":
            False,

        "native_add_cancel_execute_events":
            False,

        "true_full_book_reconstruction":
            False,
    },

    "rules": {
        "no_predictive_claim":
            True,

        "no_feature_threshold":
            True,

        "no_learned_weights":
            True,

        "no_obi_ofi_name_conflation":
            True,

        "true_event_ofi_requires_richer_feed":
            True,
    },

    "features":
        FEATURE_DICTIONARY,

    "execution_authority":
        False,

    "broker_authority":
        "NONE",

    "production":
        False,
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
                    raise FeatureArchitectureError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):
            if node.module in forbidden_imports:
                raise FeatureArchitectureError(
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
                raise FeatureArchitectureError(
                    "BROKER WRITE PROHIBITED"
                )


def validate_dictionary() -> None:

    ids = [
        item["feature_id"]
        for item in FEATURE_DICTIONARY
    ]

    if len(ids) != len(set(ids)):
        raise FeatureArchitectureError(
            "DUPLICATE FEATURE ID"
        )

    for item in FEATURE_DICTIONARY:

        if item.get(
            "predictive_claim"
        ) is not False:

            raise FeatureArchitectureError(
                "PREDICTIVE CLAIM PROHIBITED"
            )

    weighted = next(
        x for x in FEATURE_DICTIONARY
        if x["feature_id"]
        == "DISTANCE_WEIGHTED_OBI_L5"
    )

    if weighted["level_weights"] is not None:
        raise FeatureArchitectureError(
            "DISTANCE WEIGHTS WERE INVENTED"
        )

    event_ofi = next(
        x for x in FEATURE_DICTIONARY
        if x["feature_id"]
        == "TRUE_EVENT_LEVEL_OFI"
    )

    if event_ofi[
        "observable_from_top5_kite"
    ]:
        raise FeatureArchitectureError(
            "TRUE EVENT OFI FALSELY CLAIMED OBSERVABLE"
        )


def self_test() -> None:

    security_audit()
    verify_step6()
    validate_dictionary()

    bids = [
        10, 20, 30, 40, 50
    ]

    asks = [
        5, 10, 15, 20, 25
    ]

    obi = static_obi(
        bids,
        asks,
    )

    assert abs(
        obi - (1.0 / 3.0)
    ) < 1e-12

    micro = microprice_l1(
        100.0,
        10.0,
        101.0,
        5.0,
    )

    assert abs(
        micro - 100.66666666666667
    ) < 1e-12

    spread = spread_bps(
        100.0,
        101.0,
    )

    assert spread > 0

    vamp = vamp_equal_depth(
        [100, 99, 98, 97, 96],
        bids,
        [101, 102, 103, 104, 105],
        asks,
    )

    assert vamp is not None

    print("=" * 100)
    print("STEP 2 - MICROSTRUCTURE FEATURE DICTIONARY V1 SELF TEST")
    print("=" * 100)
    print()
    print("Step 6 architecture integrity        : PASS")
    print("Static OBI L1/L5 definition          : PASS")
    print("Microprice definition                : PASS")
    print("VAMP definition                      : PASS")
    print("Spread bps definition                : PASS")
    print("Top-5 displayed depth                : SUPPORTED")
    print("True exchange-event OFI              : NOT CLAIMED")
    print("Distance-weighted OBI weights        : UNDEFINED")
    print("Predictive claims                    : NONE")
    print("Feature thresholds                   : NONE")
    print("Broker authority                     : NONE")
    print("Execution authority                  : FALSE")
    print("Production                           : FALSE")
    print("=" * 100)


def emit_candidate():

    json_path = (
        ROOT
        / "STEP2_MICROSTRUCTURE_FEATURE_DICTIONARY_V1_CANDIDATE_20260825.json"
    )

    md_path = (
        ROOT
        / "STEP2_MICROSTRUCTURE_FEATURE_DICTIONARY_V1_CANDIDATE_20260825.md"
    )

    if json_path.exists() or md_path.exists():
        raise FeatureArchitectureError(
            "STEP 2 CANDIDATE ALREADY EXISTS"
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

    md = """# Step 2 — Microstructure Feature Dictionary V1

## Status

**CANDIDATE / NOT FROZEN**

This artifact defines features only.

It does **not** claim that any feature predicts future returns.

### Immediately computable from top-5 displayed depth

- mid price
- spread bps
- static L1 imbalance
- static equal-weight L1-L5 imbalance
- microprice
- VAMP-style L1-L5 adjusted price
- total bid/ask displayed depth
- displayed order-count imbalance
- sequential displayed-depth changes

### Explicit distinction

Static displayed-book imbalance is not automatically classified as
true exchange-event Order Flow Imbalance.

True event-level OFI requires richer event information describing
adds, cancels and executions.

### Explicitly unfrozen

- feature selection
- feature weights
- distance weighting
- normalization windows
- smoothing
- thresholds
- predictive signs
- prediction horizons
- model usage

No feature has trading authority.
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
