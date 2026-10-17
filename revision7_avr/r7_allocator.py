"""Portfolio capacity allocation with a droop-inspired sharing characteristic.

This is capital sharing, not physical frequency droop. The common measurement is
portfolio gross utilization U = gross notional / authorized gross limit, taken
from the native position ledger (filled positions) plus pending exposure-increasing
orders. U is never clamped.

Each symbol may increase its same-side exposure by at most its share of the
remaining headroom. Shares are proportional to budget_fraction / R over the whole
configured universe, so a stiffer (smaller R) bay takes a larger share, in the same
1/R proportion that parallel generators share load. Shares are fixed per run and are
independent of evaluation order, so the total allocated increase can never exceed
the headroom measured at the start of the tick.
"""
from dataclasses import dataclass
from .r7_state import number


@dataclass(frozen=True)
class Utilization:
    filled_gross_notional: float
    pending_increase_notional: float
    gross_limit_notional: float

    def __post_init__(self):
        if min(number(self.filled_gross_notional), number(self.pending_increase_notional)) < 0:
            raise ValueError('gross notional must be non-negative')
        if number(self.gross_limit_notional) <= 0:
            raise ValueError('authorized gross limit must be positive')

    @property
    def actual(self):
        """Measured utilization from filled positions. Not clamped: >1 is an overrun."""
        return self.filled_gross_notional / self.gross_limit_notional

    @property
    def projected(self):
        """Conservative utilization including pending exposure-increasing orders."""
        return (self.filled_gross_notional + self.pending_increase_notional) / self.gross_limit_notional

    @property
    def headroom_notional(self):
        return max(0.0, self.gross_limit_notional - self.filled_gross_notional - self.pending_increase_notional)


def droop_shares(configs):
    """Fraction of headroom each symbol may claim: (budget/R) / sum(budget/R)."""
    weights = {c.symbol: number(c.budget_fraction) / number(c.droop_r) for c in configs}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError('no positive allocation weight')
    return {s: w / total for s, w in weights.items()}


def increase_capacity_pu(share, utilization, equity, budget_fraction):
    """Convert a symbol's headroom share into symbol-budget pu."""
    base = number(equity) * number(budget_fraction)
    if base <= 0:
        raise ValueError('symbol budget base must be positive')
    return number(share) * utilization.headroom_notional / base


def approve_target(reference, actual, capacity=None):
    """Approved signed target in symbol-budget pu.

    * A neutral reference approves zero: it can reduce a position, never create one.
    * Reductions, and the risk-reducing part of a reversal, are always approved.
    * Only the same-side increase beyond the current same-side holding is limited
      by capacity, so a bullish reference can never become short and a bearish
      reference can never be pushed further negative by rising utilization.
    """
    r, a = number(reference), number(actual)
    if abs(r) > 1:
        raise ValueError('reference outside [-1, 1]')
    if r == 0:
        return 0.0
    sign = 1.0 if r > 0 else -1.0
    same_side = max(0.0, sign * a)
    if abs(r) <= same_side:
        return r
    increase = abs(r) - same_side
    if capacity is not None:
        increase = min(increase, max(0.0, number(capacity)))
    return sign * (same_side + increase)
