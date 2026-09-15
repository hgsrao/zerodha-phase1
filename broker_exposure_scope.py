"""Shared fail-closed broker-position scope for the MIS trading bot."""
from typing import Any, Dict, List, Tuple


def classify_positions(positions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (blocking bot exposure, ignored intentional CNC holdings)."""
    if not isinstance(positions, list):
        raise RuntimeError("FAIL_CLOSED: Broker positions response is not a list.")
    blocking, ignored_cnc = [], []
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
        if product == "CNC":
            ignored_cnc.append(position)
        elif product == "MIS":
            blocking.append(position)
        else:
            raise RuntimeError(
                "FAIL_CLOSED: Nonzero broker position has unsupported or missing "
                f"product '{product or 'MISSING'}'."
            )
    return blocking, ignored_cnc
