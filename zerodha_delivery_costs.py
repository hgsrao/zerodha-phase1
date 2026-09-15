"""Real Zerodha equity-delivery (CNC) statutory cost schedule.

Every research module so far has modeled trading friction as a flat
"cost_bps_per_side + slippage_bps_per_side" guess (10 bps/side, 20 bps round
trip). That number was never checked against what Zerodha and Indian
regulators actually charge for a delivery trade. This module replaces the
guess with the real, checkable fee schedule (verified 2026-08-14 against
Zerodha's published charges):

- Brokerage: Rs 0 for equity delivery (Zerodha's standard offer).
- STT (Securities Transaction Tax): 0.1% on BOTH the buy and the sell value.
- NSE exchange transaction charge: 0.00297% of traded value, each side.
- SEBI turnover charge: Rs 10 per crore (0.0001%), each side.
- GST: 18%, charged on (brokerage + exchange transaction charge + SEBI
  charge) only - never on STT or stamp duty, which are themselves taxes.
- Stamp duty: 0.015% of traded value, BUY side only.
- DP (depository participant) charge: a FLAT Rs 13.50 + 18% GST = Rs 15.93
  per scrip per day on the SELL side, regardless of quantity or value. This
  is the piece a percentage-only cost model cannot represent at all, and it
  is exactly the kind of cost that matters more, proportionally, the
  smaller a single position is - which is why this exists: to check the
  edge at a realistic position size, not just at institutional scale.

This module makes no broker calls; it is a pure fee calculator.
"""

from __future__ import annotations

from dataclasses import dataclass

BROKERAGE = 0.0
STT_RATE = 0.001
EXCHANGE_TXN_RATE = 0.0000297
SEBI_RATE = 0.0000001
GST_RATE = 0.18
STAMP_DUTY_RATE = 0.00015
DP_CHARGE_INCL_GST = 13.50 * (1 + GST_RATE)


@dataclass(frozen=True)
class CostBreakdown:
    stt: float
    exchange_txn: float
    sebi: float
    gst: float
    stamp_duty: float
    dp_charge: float

    @property
    def total(self) -> float:
        return self.stt + self.exchange_txn + self.sebi + self.gst + self.stamp_duty + self.dp_charge


def buy_cost(value: float) -> CostBreakdown:
    if value < 0:
        raise ValueError("trade value cannot be negative")
    stt = value * STT_RATE
    exchange_txn = value * EXCHANGE_TXN_RATE
    sebi = value * SEBI_RATE
    gst = (BROKERAGE + exchange_txn + sebi) * GST_RATE
    stamp_duty = value * STAMP_DUTY_RATE
    return CostBreakdown(stt, exchange_txn, sebi, gst, stamp_duty, 0.0)


def sell_cost(value: float, *, charge_dp: bool = True) -> CostBreakdown:
    if value < 0:
        raise ValueError("trade value cannot be negative")
    stt = value * STT_RATE
    exchange_txn = value * EXCHANGE_TXN_RATE
    sebi = value * SEBI_RATE
    gst = (BROKERAGE + exchange_txn + sebi) * GST_RATE
    dp_charge = DP_CHARGE_INCL_GST if charge_dp else 0.0
    return CostBreakdown(stt, exchange_txn, sebi, gst, 0.0, dp_charge)


def round_trip_bps_equivalent(trade_value: float) -> float:
    """Statutory-only round-trip cost as a bps-of-trade-value figure, for
    comparison against the flat bps assumptions used elsewhere. Excludes
    slippage (not a statutory cost - see the research modules for that)."""
    if trade_value <= 0:
        return 0.0
    total = buy_cost(trade_value).total + sell_cost(trade_value).total
    return total / trade_value * 10_000
