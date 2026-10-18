"""Backtrader Strategy hosting the shared R5 engine; also the R5 execution port."""
from __future__ import annotations

from collections import deque
from typing import Dict, List

import backtrader as bt
import pandas as pd

from engine_c.bt_market import bt_row, status_name
from engine_c.config import EXCHANGE_TZ


class R5BacktraderStrategy(bt.Strategy):
    """Feeds completed bars to R5, submits R5's approved intents as native Backtrader market
    orders and relays Backtrader order and trade notifications back to R5.  It makes no trading
    decision of its own and only ever reads index 0 of a data line (the current bar)."""

    params = (("r5", None), ("warmup", 60))
    offline = True   # only ever run inside a Backtrader Cerebro backtest

    def __init__(self):
        self.r5 = self.p.r5
        self.symbols = tuple(d._name for d in self.datas)
        self._data = {d._name: d for d in self.datas}
        self._seen = {d._name: 0 for d in self.datas}
        self._warm: Dict[str, deque] = {s: deque(maxlen=self.p.warmup) for s in self.symbols}
        self._r5_orders: Dict[int, bt.Order] = {}
        self._label = None
        self.bars_received = 0
        self.warmup_bars = 0
        self.ticks_dispatched = 0
        self.first_decision_label = None
        self.tick_labels: List[str] = []
        self.equity_curve: List[tuple] = []
        self.closed_trades: List[Dict[str, object]] = []
        self.notifications: List[Dict[str, object]] = []
        self.outstanding_at_end: List[Dict[str, object]] = []
        self._realized = 0.0
        self._commission = 0.0
        self.r5.attach_port(self)

    # ---- market data in ---------------------------------------------------------------
    def prenext(self):
        self.next()

    def nextstart(self):
        self.next()

    def next(self):
        rows = {}
        for symbol, data in self._data.items():
            if len(data) > self._seen[symbol]:          # this data delivered a new completed bar
                self._seen[symbol] = len(data)
                rows[symbol] = bt_row(data)
        if not rows:
            return
        labels = {row["timestamp"] for row in rows.values()}
        if len(labels) != 1:
            raise ValueError(f"one Backtrader cycle delivered several bar labels: {sorted(labels)}")
        self._label = labels.pop()
        self.bars_received += len(rows)
        if not self.r5._started:
            for symbol, row in rows.items():
                self._warm[symbol].append(row)
            self.warmup_bars += len(rows)
            if all(len(rows_) >= self.p.warmup for rows_ in self._warm.values()):
                # Every symbol now has `warmup` completed bars: calibrate on the most recent ones.
                self.r5.begin({s: pd.DataFrame(list(w)) for s, w in self._warm.items()})
            return
        if self.first_decision_label is None:
            self.first_decision_label = str(self._label)
        self.ticks_dispatched += 1
        self.tick_labels.append(str(self._label))
        self.r5.on_completed_bars(rows)
        self.equity_curve.append((str(self._label), float(self.broker.getvalue())))

    def stop(self):
        # Orders still working when the data ends cannot fill: cancel them natively and report.
        for ref in sorted(self.working_refs()):
            order = self._r5_orders[ref]
            self.broker.cancel(order)
            # SUBMITTED here means the order was created on the final bar and never reached a
            # broker cycle (Backtrader's cancel only removes orders from its matching queue).
            self.outstanding_at_end.append({"ref": ref, "symbol": order.data._name, "side": "BUY" if order.isbuy() else "SELL",
                                            "size": abs(int(order.size)), "executed": abs(int(order.executed.size)),
                                            "status_after_cancel": status_name(order)})

    # ---- Backtrader notifications -> R5 ----------------------------------------------------
    def notify_order(self, order):
        for bit in order.executed.iterpending():
            self._realized += float(bit.pnl)
            self._commission += float(bit.comm)
        self.notifications.append({"ref": int(order.ref), "status": status_name(order), "label": str(self._label)})
        self.r5.on_bt_order(order)

    def notify_trade(self, trade):
        if trade.isclosed:
            self.closed_trades.append({
                "symbol": trade.data._name, "ref": int(trade.ref), "pnl": float(trade.pnl),
                "pnlcomm": float(trade.pnlcomm), "commission": float(trade.commission),
                "opened": str(self.label_of(trade.dtopen)), "closed": str(self.label_of(trade.dtclose)),
                "bars_held": int(trade.barlen)})

    # ---- execution port (R5 -> Backtrader) ------------------------------------------------
    def now_label(self):
        return self._label

    @staticmethod
    def label_of(dt_float) -> pd.Timestamp:
        return pd.Timestamp(bt.num2date(dt_float)).round("s").tz_localize(EXCHANGE_TZ)

    def submit_market(self, symbol: str, side: str, quantity: int) -> bt.Order:
        data = self._data[symbol]
        method = self.buy if side == "BUY" else self.sell
        order = method(data=data, size=int(quantity), exectype=bt.Order.Market)
        self._r5_orders[int(order.ref)] = order
        return order

    def cancel(self, ref: int) -> bool:
        """Native cancel; True only if Backtrader removed a still-working order from its queue."""
        order = self._r5_orders[int(ref)]
        if not order.alive():
            return False
        return bool(self.broker.cancel(order))

    def order_status(self, ref: int) -> str:
        return status_name(self._r5_orders[int(ref)])

    def working_refs(self):
        return [ref for ref, order in self._r5_orders.items() if order.alive()]

    def net_position(self, symbol: str) -> int:
        size = self.broker.getposition(self._data[symbol]).size
        if size != int(size):
            raise ValueError(f"{symbol}: fractional Backtrader position {size}")
        return int(size)

    def position_price(self, symbol: str) -> float:
        return float(self.broker.getposition(self._data[symbol]).price)

    def last_close(self, symbol: str) -> float:
        return float(self._data[symbol].close[0])

    def unrealized_pnl(self) -> float:
        total = 0.0
        for symbol, data in self._data.items():
            position = self.broker.getposition(data)
            if position.size:
                total += position.size * (float(data.close[0]) - float(position.price))
        return total

    def realized_gross_pnl(self) -> float:
        return self._realized

    def commissions_total(self) -> float:
        return self._commission

    def cash(self) -> float:
        return float(self.broker.getcash())

    def value(self) -> float:
        return float(self.broker.getvalue())
