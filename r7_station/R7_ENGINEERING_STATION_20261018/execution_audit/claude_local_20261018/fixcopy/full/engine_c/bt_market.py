"""Backtrader venue definitions for Engine C: NSE data feeds, commission scheme and broker rules."""
from __future__ import annotations

import hashlib
from typing import Dict

import backtrader as bt
import pandas as pd

from engine_c.config import EXCHANGE_TZ, CommissionSchedule

BAR_SPAN = pd.Timedelta(minutes=1)
OHLCV = ["open", "high", "low", "close", "volume"]


def exchange_labels(timestamps: pd.Series) -> pd.Series:
    """R5 bar labels as exchange-local (IST) tz-aware timestamps.  Naive input is taken to be
    exchange-local already (the frozen dataset carries an explicit +05:30 offset)."""
    labels = pd.to_datetime(timestamps)
    return labels.dt.tz_localize(EXCHANGE_TZ) if labels.dt.tz is None else labels.dt.tz_convert(EXCHANGE_TZ)


def bars_digest(frame: pd.DataFrame) -> str:
    """SHA-256 of the canonical bars an engine receives (IST open-time label + OHLCV)."""
    labels = exchange_labels(frame["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    canon = pd.DataFrame({"t": labels.to_numpy(), **{c: frame[c].astype(float).round(6).to_numpy() for c in OHLCV}})
    return hashlib.sha256(canon.to_csv(index=False).encode()).hexdigest()


def frame_to_feed(symbol: str, frame: pd.DataFrame) -> bt.feeds.PandasData:
    """One R5 symbol frame -> one Backtrader feed.

    R5 labels each 1-minute bar by its OPEN time in exchange-local time.  Backtrader keeps the
    same label (naive IST datetime) as the bar's datetime.  A bar's values become available to
    the strategy only once Backtrader delivers it, i.e. at the bar's CLOSE (label + 1 minute);
    a market order submitted then executes at the OPEN of that symbol's next delivered bar."""
    labels = exchange_labels(frame["timestamp"])
    if not labels.is_monotonic_increasing or labels.duplicated().any():
        raise ValueError(f"{symbol}: bar timestamps must be strictly increasing")
    data = frame[OHLCV].astype(float).copy()
    data.index = pd.DatetimeIndex(labels.dt.tz_localize(None).to_numpy(), name="datetime")
    return bt.feeds.PandasData(dataname=data, name=symbol, openinterest=None, tz=None)


def bt_label(data) -> pd.Timestamp:
    """Exchange-local open-time label of the data's CURRENT bar (index 0 only, never ahead)."""
    return pd.Timestamp(data.datetime.datetime(0)).round("s").tz_localize(EXCHANGE_TZ)


def bt_row(data) -> Dict[str, object]:
    return {"timestamp": bt_label(data), "open": float(data.open[0]), "high": float(data.high[0]),
            "low": float(data.low[0]), "close": float(data.close[0]), "volume": float(data.volume[0])}


class NSEEquityIntradayCommission(bt.CommInfoBase):
    """Backtrader commission scheme for NSE intraday cash equity, charged per executed fill.

    Backtrader passes the signed executed size: positive = BUY, negative = SELL."""
    params = (
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_FIXED),
        ("percabs", True),
        ("brokerage_rate", CommissionSchedule.brokerage_rate),
        ("brokerage_cap", CommissionSchedule.brokerage_cap),
        ("exchange_txn_rate", CommissionSchedule.exchange_txn_rate),
        ("stt_sell_rate", CommissionSchedule.stt_sell_rate),
    )

    def _getcommission(self, size, price, pseudoexec):
        turnover = abs(size) * price
        charge = min(self.p.brokerage_cap, self.p.brokerage_rate * turnover) + self.p.exchange_txn_rate * turnover
        if size < 0:
            charge += self.p.stt_sell_rate * turnover
        return charge


class NSEBackBroker(bt.brokers.BackBroker):
    """Backtrader's BackBroker plus one NSE venue rule: an order above the symbol's maximum order
    quantity (the exchange freeze-quantity analogue) is REJECTED through Backtrader's native
    order.reject() lifecycle and never reaches the matching queue."""
    params = (("max_order_quantity", None),)

    def submit(self, order, check=True):
        limit = (self.p.max_order_quantity or {}).get(order.data._name)
        if limit is not None and abs(order.size) > limit:
            order.reject(self)
            order.addinfo(reject_reason=f"quantity {abs(int(order.size))} exceeds venue maximum {limit}")
            self.notify(order)
            return order
        return super().submit(order, check=check)


def build_broker(config) -> NSEBackBroker:
    broker = NSEBackBroker(max_order_quantity=dict(config.max_order_quantity))
    broker.setcash(float(config.starting_cash))
    schedule: CommissionSchedule = config.commission
    broker.addcommissioninfo(NSEEquityIntradayCommission(
        brokerage_rate=schedule.brokerage_rate, brokerage_cap=schedule.brokerage_cap,
        exchange_txn_rate=schedule.exchange_txn_rate, stt_sell_rate=schedule.stt_sell_rate))
    if config.slippage_perc:
        broker.set_slippage_perc(float(config.slippage_perc), slip_open=True, slip_limit=True,
                                 slip_match=True, slip_out=False)
    if config.volume_fill_perc is not None:
        broker.set_filler(bt.broker.fillers.FixedBarPerc(perc=float(config.volume_fill_perc)))
    broker.set_checksubmit(True)       # cash/margin check before acceptance (Order.Margin)
    broker.set_shortcash(True)
    return broker


def side_of(order) -> str:
    return "BUY" if order.isbuy() else "SELL"


def status_name(order) -> str:
    return order.getstatusname(order.status)
