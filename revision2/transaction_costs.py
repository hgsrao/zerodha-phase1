"""Canonical Zerodha NSE equity-intraday charges (rate card 2026-09-04)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EquityIntradayCharges:
    turnover: float
    brokerage: float
    stt: float
    exchange_transaction_charge: float
    sebi_charge: float
    stamp_duty: float
    gst: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_transaction_charge + self.sebi_charge + self.stamp_duty + self.gst

    def as_dict(self):
        values = asdict(self)
        values["total"] = self.total
        return values


def equity_intraday_leg(price: float, quantity: int, side: str) -> EquityIntradayCharges:
    if price <= 0 or quantity <= 0 or side not in {"BUY", "SELL"}:
        raise ValueError("positive price/quantity and BUY or SELL are required")
    turnover = float(price) * int(quantity)
    brokerage = min(20.0, turnover * 0.0003)
    exchange = turnover * 0.0000307
    sebi = turnover * 0.000001
    stt = turnover * 0.00025 if side == "SELL" else 0.0
    stamp = turnover * 0.00003 if side == "BUY" else 0.0
    gst = 0.18 * (brokerage + exchange + sebi)
    return EquityIntradayCharges(turnover, brokerage, stt, exchange, sebi, stamp, gst)
