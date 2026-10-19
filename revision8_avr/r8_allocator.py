"""Demand-aware portfolio capacity allocation with droop-weighted sharing.

This is capital sharing, not physical frequency droop. The common measurement is
portfolio gross utilization U = gross notional / authorized gross limit, taken from
the native position ledger (filled positions) plus conservatively reserved
exposure-increasing orders. U is never clamped.

At each market timestamp every symbol's same-side increase demand is gathered against
one common portfolio state, and headroom H = max(0, G_limit - G_filled - G_pending)
is allocated once:

* Case A: total eligible demand <= H. Every demand is approved in full.
* Case B: demand exceeds H. Demand-capped weighted water-filling with commissioned
  weights w_i = B_i / R_i. A symbol never receives more than it asked for; unclaimed
  share is redistributed among symbols with unmet demand until H is exhausted or all
  demand is met. The total never exceeds H.

The result depends only on the set of demands, never on symbol iteration order.
"""
from dataclasses import dataclass
from .r8_state import number


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


def droop_weights(configs):
    """Commissioned sharing weights w_i = budget_i / R_i."""
    weights = {c.symbol: number(c.budget_fraction) / number(c.droop_r) for c in configs}
    if any(w <= 0 for w in weights.values()):
        raise ValueError('allocation weights must be positive')
    return weights


def increase_demand_pu(reference, actual):
    """Same-side exposure increase requested by the reference, in symbol-budget pu.

    Zero for a neutral reference, for a reduction, and for an opposing reference while
    a position is open (a reversal must first close through native execution)."""
    r, a = number(reference), number(actual)
    if r == 0:
        return 0.0
    sign = 1.0 if r > 0 else -1.0
    if sign * a < 0:
        return 0.0
    return max(0.0, abs(r) - sign * a)


def allocate_headroom(demands, weights, headroom):
    """Demand-capped weighted allocation of headroom (all quantities in notional).

    ``demands`` and ``weights`` are dicts keyed by symbol. Returns (allocations, case)
    where case is 'FITS', 'OVERSUBSCRIBED' or 'NO_DEMAND'."""
    h = max(0.0, number(headroom))
    asked = {s: max(0.0, number(d)) for s, d in demands.items()}
    total = sum(asked.values())
    if total == 0:
        return {s: 0.0 for s in asked}, 'NO_DEMAND'
    if total <= h:
        return dict(asked), 'FITS'
    alloc = {s: 0.0 for s in asked}
    active = sorted(s for s, d in asked.items() if d > 0)
    remaining = h
    while active and remaining > 1e-9:
        wsum = sum(number(weights[s]) for s in active)
        capped = [s for s in active if asked[s] - alloc[s] <= remaining * weights[s] / wsum]
        if not capped:
            for s in active:
                alloc[s] += remaining * weights[s] / wsum
            remaining = 0.0
            break
        for s in capped:
            grant = asked[s] - alloc[s]
            alloc[s] += grant
            remaining -= grant
        active = [s for s in active if s not in capped]
    return alloc, 'OVERSUBSCRIBED'


def approve_target(reference, actual, capacity=None):
    """Approved signed target in symbol-budget pu.

    * A neutral reference approves zero: it can reduce a position, never create one.
    * Reductions are always approved.
    * An opposing reference while a position is open approves only flat (zero): it
      authorizes closing, never a direct reversal into new opposite exposure.
    * Only the same-side increase beyond the current holding is limited by capacity,
      so a bullish reference can never become short, a bearish reference never long,
      and rising utilization never pushes a target further from flat.
    """
    r, a = number(reference), number(actual)
    if abs(r) > 1:
        raise ValueError('reference outside [-1, 1]')
    if r == 0:
        return 0.0
    sign = 1.0 if r > 0 else -1.0
    if sign * a < 0:
        return 0.0
    same_side = sign * a
    if abs(r) <= same_side:
        return r
    increase = abs(r) - same_side
    if capacity is not None:
        increase = min(increase, max(0.0, number(capacity)))
    return sign * (same_side + increase)
