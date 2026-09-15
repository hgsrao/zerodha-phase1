"""V11 -> P02 Rebalance Bridge — R1: the immutable `TargetPortfolio` object.

Implements exactly `V34_V11_P02_REBALANCE_INVARIANTS_SPEC.md` (R0, frozen)
sections 1 and 10, and nothing beyond them — no diff computation, no
`RebalancePlan`, no sequencing, no P02 calls. This module answers one
question only: "given a dated V11 signal, what is the immutable,
reproducible `TargetPortfolio`?"

No V11 ranking/momentum logic is duplicated here (R0's release boundary,
binding) — this module calls `external_momentum_shadow.evaluate_external_momentum()`
directly and treats its output as the sole source of truth for both the
selected symbols and their quantities. Per R0 §1: "the bridge does not
re-derive share counts from weights" — `TargetPosition.quantity` is V11's
own `paper_quantity`, unmodified. `weight` is a reporting-only field,
never used for sizing.

This module makes no broker/network calls, imports no P02 file, and
cannot itself cause an order — it produces a value object only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

from external_momentum_shadow import EXTERNAL_UNIVERSE, evaluate_external_momentum
from portfolio_brain_v9 import SECTORS


class TargetPortfolioConstructionError(RuntimeError):
    """The V11 signal snapshot could not be turned into a trustworthy
    TargetPortfolio - fail closed rather than emit a partial/wrong one."""


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    quantity: int
    sector: str
    weight: Decimal  # reporting only - never used to size anything (R0 §1)


@dataclass(frozen=True)
class TargetPortfolio:
    target_id: str
    signal_date: date
    generated_at: datetime
    positions: Mapping[str, TargetPosition]  # MappingProxyType - logically immutable
    universe_version: str
    source_model_version: str


def compute_universe_version(sector_lookup: Mapping[str, str]) -> str:
    """A hash of the actual universe + sector mapping used to build a
    TargetPortfolio (R0 §1's reproducibility requirement: a future
    universe/sector change must produce a visibly different version)."""
    payload = {
        "universe": list(EXTERNAL_UNIVERSE),
        "sectors": dict(sorted(sector_lookup.items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _compute_target_id(*, signal_date: date, quantities: Mapping[str, int], universe_version: str) -> str:
    """R0 §10's exact formula: a deterministic hash of the position set
    (not weight/sector - those are derived/reporting fields whose float
    formatting could vary without the target actually being different)
    plus universe_version, prefixed with the signal date."""
    payload = {
        "positions": {symbol: quantities[symbol] for symbol in sorted(quantities)},
        "universe_version": universe_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"V11_{signal_date.isoformat()}_{digest}"


def build_target_portfolio(
    *,
    quotes: Mapping[str, Mapping[str, Any]],
    signal_date: date,
    positions: int = 4,
    sector_lookup: Mapping[str, str] = SECTORS,
) -> TargetPortfolio:
    """Build an immutable TargetPortfolio from a dated V11 signal snapshot.

    Fails closed (raises TargetPortfolioConstructionError) rather than
    return a partial or best-effort target: an incomplete quote set, a
    short basket, or an unmapped sector are all construction errors, not
    warnings - matching P02's own fail-closed discipline (P02 spec §1),
    applied one layer up rather than inventing a new policy here.
    """
    result = evaluate_external_momentum(quotes, positions=positions)

    if result["status"] != "MARKED":
        raise TargetPortfolioConstructionError(
            f"Cannot build TargetPortfolio: V11 signal status is "
            f"{result['status']!r}, not MARKED (an incomplete quote set "
            "would silently produce a partial target)."
        )

    selected = result["selected"]
    if len(selected) < positions:
        raise TargetPortfolioConstructionError(
            f"Cannot build TargetPortfolio: V11 selected only {len(selected)} "
            f"symbols, fewer than the requested {positions}."
        )

    equity = result["paper_equity"]
    if equity is None or equity <= 0:
        raise TargetPortfolioConstructionError(
            f"Cannot build TargetPortfolio: V11 paper_equity is invalid ({equity!r})."
        )
    equity_decimal = Decimal(str(equity))

    quantities: dict[str, int] = {}
    target_positions: dict[str, TargetPosition] = {}
    for row in selected:
        symbol = row["symbol"]
        sector = sector_lookup.get(symbol)
        if sector is None:
            raise TargetPortfolioConstructionError(
                f"Cannot build TargetPortfolio: {symbol} has no sector mapping "
                "in the supplied sector_lookup - refusing to build a target "
                "with unknown concentration risk."
            )
        quantity = row["paper_quantity"]
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise TargetPortfolioConstructionError(
                f"Cannot build TargetPortfolio: {symbol} has an invalid "
                f"paper_quantity from V11 ({quantity!r})."
            )
        weight = Decimal(str(row["paper_value"])) / equity_decimal
        quantities[symbol] = quantity
        target_positions[symbol] = TargetPosition(
            symbol=symbol, quantity=quantity, sector=sector, weight=weight,
        )

    universe_version = compute_universe_version(sector_lookup)
    target_id = _compute_target_id(
        signal_date=signal_date, quantities=quantities, universe_version=universe_version,
    )

    return TargetPortfolio(
        target_id=target_id,
        signal_date=signal_date,
        generated_at=datetime.now(timezone.utc),
        positions=MappingProxyType(target_positions),
        universe_version=universe_version,
        source_model_version=result["variant_id"],
    )
