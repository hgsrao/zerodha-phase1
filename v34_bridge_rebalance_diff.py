"""V11 -> P02 Rebalance Bridge — Step A: `RebalanceDiff` (pure logic).

Implements exactly `V34_V11_P02_REBALANCE_INVARIANTS_SPEC.md` (R0, frozen)
§3, and nothing beyond it: the deterministic, offline classification of a
CurrentPortfolio snapshot against a frozen `TargetPortfolio` (R1) into
KEEP / EXIT / ENTER, with `RESIZE` expanded into an EXIT+ENTER pair per
R0's Fact B (P02 has no resize primitive - `request_exit()` always exits
the full held quantity).

Zero broker/network calls. `current_portfolio` is a plain symbol->quantity
mapping the caller already obtained (from a fresh broker snapshot, in the
eventual runner; from dummy test data here) - this module never fetches
it itself. No P02 import, no V11 import beyond the already-built R1
`TargetPortfolio` type. Purely: given two portfolios, what changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import FrozenSet, Mapping

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Deferred: only used for a type hint below. RebalanceDiff itself
    # stores no TargetPortfolio instance (target_id is a plain str), and
    # `from __future__ import annotations` above means the hint is never
    # evaluated at runtime - so importing this at module level would only
    # ever exist to drag TargetPortfolio's own import chain
    # (external_momentum_shadow.py -> brain_research_lab/
    # cross_sectional_brain_v6/walk_forward_v5, a full research/backtest
    # dependency stack) into every caller of this module, including the
    # always-on runner container, which never needs any of it - it only
    # ever deserializes an already-computed RebalanceDiff, never calls
    # compute_rebalance_diff() itself (that is the bridge trigger script's
    # job). Found while scoping the runner's Dockerfile.
    from v34_bridge_target_portfolio import TargetPortfolio


class RebalanceDiffError(RuntimeError):
    """The supplied CurrentPortfolio snapshot could not be trusted enough
    to diff - fail closed rather than compute a diff against malformed
    input, matching P02's own broker-payload discipline one layer up."""


@dataclass(frozen=True)
class RebalanceDiff:
    target_id: str
    computed_from_current: Mapping[str, int]  # MappingProxyType - the exact snapshot diffed against
    keep: FrozenSet[str]
    exits: Mapping[str, int]   # symbol -> quantity to exit (includes RESIZE-exit legs)
    enters: Mapping[str, int]  # symbol -> quantity to enter (includes RESIZE-enter legs)


def _validate_current_portfolio(current_portfolio: Mapping[str, int]) -> dict[str, int]:
    validated: dict[str, int] = {}
    for symbol, quantity in current_portfolio.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise RebalanceDiffError(f"CurrentPortfolio has an invalid symbol key: {symbol!r}.")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise RebalanceDiffError(f"CurrentPortfolio[{symbol!r}] quantity is not an integer: {quantity!r}.")
        if quantity <= 0:
            # A real broker snapshot never reports a zero/negative holding
            # (P02's own classify_positions already filters those out) -
            # seeing one here means the caller passed something that isn't
            # actually a filtered current-holdings snapshot. Fail closed
            # rather than silently drop it.
            raise RebalanceDiffError(
                f"CurrentPortfolio[{symbol!r}] has a non-positive quantity ({quantity!r}) - "
                "a current-holdings snapshot must only contain confirmed nonzero positions."
            )
        validated[symbol] = quantity
    return validated


def compute_rebalance_diff(*, current_portfolio: Mapping[str, int], target: TargetPortfolio) -> RebalanceDiff:
    """R0 §3's exact classification:

        KEEP:   symbol in CURRENT and TARGET, same quantity
        RESIZE: symbol in CURRENT and TARGET, different quantity
                -> expanded into EXIT(current_qty) + ENTER(target_qty)
        EXIT:   symbol in CURRENT, not in TARGET
        ENTER:  symbol in TARGET, not in CURRENT

    ("or TARGET quantity is 0" from R0's EXIT clause is structurally
    unreachable here - R1's build_target_portfolio() already rejects any
    non-positive quantity, so a TargetPortfolio can never contain a
    zero-quantity entry to compare against.)
    """
    current = _validate_current_portfolio(current_portfolio)
    target_positions = {symbol: position.quantity for symbol, position in target.positions.items()}

    keep: set[str] = set()
    exits: dict[str, int] = {}
    enters: dict[str, int] = {}

    for symbol, current_qty in current.items():
        if symbol in target_positions:
            target_qty = target_positions[symbol]
            if current_qty == target_qty:
                keep.add(symbol)
            else:
                # RESIZE: no P02 primitive exists for this (R0 Fact B) -
                # always expanded into a full exit + full re-entry.
                exits[symbol] = current_qty
                enters[symbol] = target_qty
        else:
            exits[symbol] = current_qty

    for symbol, target_qty in target_positions.items():
        if symbol not in current:
            enters[symbol] = target_qty

    return RebalanceDiff(
        target_id=target.target_id,
        computed_from_current=MappingProxyType(current),
        keep=frozenset(keep),
        exits=MappingProxyType(exits),
        enters=MappingProxyType(enters),
    )
