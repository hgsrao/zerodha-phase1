"""Shared fail-closed broker-position scope for the V34 P02 multi-position
CNC candidate.

Forked from broker_exposure_scope.py (used by the single-position MIS
production engine) rather than parameterized in place, so any future change
here can never be mistaken for something that could affect the currently
running production bot - see BRAIN_RESEARCH_SPEC_V17_MULTIPOS_CNC_ENGINE.md
for the full rationale.

The only semantic change from the original: which product this engine
manages is a parameter (`managed_product`), and for this candidate the
roles invert relative to the original file - CNC positions are the ones
this engine manages, and MIS becomes the ignored set. That inversion is
deliberate: the original function ignores CNC because it exists to protect
a same-day MIS-only engine from mistaking someone else's delivery holding
for its own exposure; here CNC *is* our own exposure.
"""
from typing import Any, Dict, List, Tuple

SUPPORTED_PRODUCTS = {"MIS", "CNC"}


def classify_positions(
    positions: List[Dict[str, Any]],
    *,
    managed_product: str = "CNC",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (managed, ignored) broker positions, split by managed_product.

    Fail-closed exactly as the original: any nonzero position reporting a
    product outside {MIS, CNC} raises rather than being silently dropped.
    """
    if managed_product not in SUPPORTED_PRODUCTS:
        raise RuntimeError(
            f"FAIL_CLOSED: Unsupported managed_product '{managed_product}'."
        )
    if not isinstance(positions, list):
        raise RuntimeError("FAIL_CLOSED: Broker positions response is not a list.")
    managed: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            raise RuntimeError("FAIL_CLOSED: Broker position entry is not an object.")
        try:
            quantity = int(position.get("quantity", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("FAIL_CLOSED: Broker position quantity is malformed.") from exc
        if quantity == 0:
            continue
        product = str(position.get("product", "")).strip().upper()
        if product not in SUPPORTED_PRODUCTS:
            raise RuntimeError(
                "FAIL_CLOSED: Nonzero broker position has unsupported or missing "
                f"product '{product or 'MISSING'}'."
            )
        if product == managed_product:
            managed.append(position)
        else:
            ignored.append(position)
    return managed, ignored
