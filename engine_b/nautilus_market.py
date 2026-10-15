"""NSE instrument, bar and fee definitions for the Engine B Nautilus backtest venue."""
from __future__ import annotations

from typing import Dict, List

import pandas as pd
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler

from revision2.transaction_costs import leg_cost

NSE = Venue("NSE")
INR = Currency.from_str("INR")
EXCHANGE_TZ = "Asia/Kolkata"
BAR_SPAN = pd.Timedelta(minutes=1)
PRICE_PRECISION = 2
TICK = "0.05"


def instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId(Symbol(symbol), NSE)


def nse_equity(symbol: str, max_quantity: int | None = None) -> Equity:
    return Equity(instrument_id(symbol), Symbol(symbol), INR, PRICE_PRECISION, Price.from_str(TICK),
                  Quantity.from_int(1), 0, 0,
                  max_quantity=Quantity.from_int(max_quantity) if max_quantity else None)


def bar_type(symbol: str) -> BarType:
    return BarType.from_str(f"{symbol}.NSE-1-MINUTE-LAST-EXTERNAL")


def frame_to_bars(symbol: str, frame: pd.DataFrame, instrument: Equity) -> List[Bar]:
    """R5 frames label each 1-minute bar by its OPEN time (exchange local).  Nautilus stamps a
    bar when it is complete, so ts_event = label + 1 minute.  Prices are quantized to the
    instrument precision; the real frozen dataset is already on the 0.05 grid."""
    labels = pd.to_datetime(frame["timestamp"])
    labels = labels.dt.tz_localize(EXCHANGE_TZ) if labels.dt.tz is None else labels.dt.tz_convert(EXCHANGE_TZ)
    if not labels.is_monotonic_increasing or labels.duplicated().any():
        raise ValueError(f"{symbol}: bar timestamps must be strictly increasing")
    data = frame[["open", "high", "low", "close", "volume"]].astype(float).copy()
    data[["open", "high", "low", "close"]] = data[["open", "high", "low", "close"]].round(PRICE_PRECISION)
    data["high"] = data[["open", "high", "low", "close"]].max(axis=1)
    data["low"] = data[["open", "high", "low", "close"]].min(axis=1)
    data.index = pd.DatetimeIndex((labels + BAR_SPAN).dt.tz_convert("UTC"))
    return BarDataWrangler(bar_type(symbol), instrument).process(data)


def bar_label(bar: Bar) -> pd.Timestamp:
    """Inverse of frame_to_bars: the exchange-local open-time label R5 uses for this bar."""
    return pd.Timestamp(bar.ts_event, tz="UTC").tz_convert(EXCHANGE_TZ) - BAR_SPAN


def bar_row(bar: Bar) -> Dict[str, object]:
    return {"timestamp": bar_label(bar), "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close), "volume": float(bar.volume)}


class R5LegCostFeeModel(FeeModel):
    """Nautilus commission per fill = the R5 statutory leg cost of that fill (brokerage,
    exchange charges and sell-side STT from revision2.transaction_costs.leg_cost)."""

    def get_commission(self, order, fill_qty, fill_px, instrument):
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        return Money(leg_cost(float(fill_px), int(fill_qty), side), INR)
