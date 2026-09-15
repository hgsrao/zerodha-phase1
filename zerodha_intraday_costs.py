"""Real Zerodha equity-intraday (MIS) statutory cost schedule.

Companion to zerodha_delivery_costs.py, for the product type scalping would
actually have to use (MIS, margin intraday square-off - a position held for
seconds to minutes cannot be a delivery/CNC trade; Zerodha auto-squares off
any MIS position same-day regardless of intent). Verified 2026-08-14:

- Brokerage: the LOWER of 0.03% or Rs 20, per executed order, EACH side.
- STT: 0.025% of value, SELL side only (delivery is 0.1% both sides - this
  is intraday's one real cost advantage over delivery).
- NSE exchange transaction charge: 0.00297% of traded value, each side
  (same exchange-level rate as delivery - this does not depend on product
  type).
- SEBI turnover charge: Rs 10/crore (0.0001%), each side.
- GST: 18% on (brokerage + exchange transaction charge + SEBI charge) only.
- Stamp duty: 0.003% of value, BUY side only (5x lower than delivery's
  0.015%).
- No DP charge: MIS positions never touch the demat account, so the flat
  delivery DP fee does not apply here at all.

This module makes no broker calls; it is a pure fee calculator.
"""

from __future__ import annotations

from dataclasses import dataclass

BROKERAGE_RATE = 0.0003
BROKERAGE_CAP = 20.0
STT_SELL_RATE = 0.00025
EXCHANGE_TXN_RATE = 0.0000297
SEBI_RATE = 0.0000001
GST_RATE = 0.18
STAMP_DUTY_RATE = 0.00003


def _brokerage(value: float) -> float:
    return min(value * BROKERAGE_RATE, BROKERAGE_CAP)


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    gst: float
    stamp_duty: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.gst + self.stamp_duty


def buy_cost(value: float) -> CostBreakdown:
    if value < 0:
        raise ValueError("trade value cannot be negative")
    brokerage = _brokerage(value)
    exchange_txn = value * EXCHANGE_TXN_RATE
    sebi = value * SEBI_RATE
    gst = (brokerage + exchange_txn + sebi) * GST_RATE
    stamp_duty = value * STAMP_DUTY_RATE
    return CostBreakdown(brokerage, 0.0, exchange_txn, sebi, gst, stamp_duty)


def sell_cost(value: float) -> CostBreakdown:
    if value < 0:
        raise ValueError("trade value cannot be negative")
    brokerage = _brokerage(value)
    stt = value * STT_SELL_RATE
    exchange_txn = value * EXCHANGE_TXN_RATE
    sebi = value * SEBI_RATE
    gst = (brokerage + exchange_txn + sebi) * GST_RATE
    return CostBreakdown(brokerage, stt, exchange_txn, sebi, gst, 0.0)


def round_trip_bps_equivalent(trade_value: float) -> float:
    """Statutory-only round-trip cost as a bps-of-trade-value figure.
    Excludes slippage and the bid-ask spread - both matter more for
    scalping than for anything else in this project, and neither is a
    statutory cost this module can look up."""
    if trade_value <= 0:
        return 0.0
    total = buy_cost(trade_value).total + sell_cost(trade_value).total
    return total / trade_value * 10_000


def breakeven_move_pct(trade_value: float) -> float:
    """The minimum favorable price move, as a percentage, needed just to
    cover round-trip statutory costs on a single trade of this size -
    before slippage, spread, or any profit at all."""
    return round_trip_bps_equivalent(trade_value) / 10_000 * 100
