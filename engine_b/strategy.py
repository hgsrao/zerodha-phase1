"""Nautilus Strategy hosting the shared R5 engine; also the R5 execution port."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

import pandas as pd
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from engine_b.nautilus_market import INR, NSE, bar_label, bar_row, bar_type, instrument_id
from engine_b.orchestrator import EngineBOrchestrator


class R5NautilusStrategyConfig(StrategyConfig, frozen=True):
    symbols: tuple = ("INFY",)
    warmup: int = 60


class R5NautilusStrategy(Strategy):
    """Feeds completed bars to R5, submits R5's approved intents as native market orders and
    relays Nautilus order events back to R5.  It makes no trading decision of its own."""

    offline = True   # only ever attached to a BacktestEngine SimulatedExchange

    def __init__(self, config: R5NautilusStrategyConfig, r5: EngineBOrchestrator):
        super().__init__(config)
        self.r5 = r5
        self.symbols = tuple(config.symbols)
        self._warm: Dict[str, List[dict]] = {s: [] for s in self.symbols}
        self._pending: Dict[pd.Timestamp, Dict[str, dict]] = {}
        self.bars_received = 0
        self.ticks_dispatched = 0
        self.decision_lag_ns: List[int] = []
        r5.attach_port(self)

    # ---- market data in ---------------------------------------------------------------
    def on_start(self) -> None:
        for symbol in self.symbols:
            self.subscribe_bars(bar_type(symbol))

    def on_bar(self, bar) -> None:
        self.bars_received += 1
        # A bar is delivered only once complete: the engine clock is at its close.
        self.decision_lag_ns.append(self.clock.timestamp_ns() - bar.ts_event)
        symbol = bar.bar_type.instrument_id.symbol.value
        row = bar_row(bar)
        if not self.r5._started:
            if len(self._warm[symbol]) < self.config.warmup:
                self._warm[symbol].append(row)
                if all(len(rows) >= self.config.warmup for rows in self._warm.values()):
                    self.r5.begin({s: pd.DataFrame(rows) for s, rows in self._warm.items()})
                return
            if not all(len(rows) >= self.config.warmup for rows in self._warm.values()):
                return   # other symbols still warming up; never decide on a partial universe
        label = bar_label(bar)
        for stale in [t for t in self._pending if t < label]:
            self._dispatch(stale)
        self._pending.setdefault(label, {})[symbol] = row
        if len(self._pending[label]) == len(self.symbols):
            self._dispatch(label)

    def _dispatch(self, label) -> None:
        rows = self._pending.pop(label)
        self.ticks_dispatched += 1
        self.r5.on_completed_bars(rows)

    def on_stop(self) -> None:
        for label in sorted(self._pending):
            self._dispatch(label)

    # ---- execution port (R5 -> Nautilus) ------------------------------------------------
    def now_ns(self) -> int:
        return self.clock.timestamp_ns()

    def create_market(self, symbol: str, side: str, quantity: int, reduce_only: bool):
        return self.order_factory.market(
            instrument_id(symbol), OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            Quantity.from_int(int(quantity)), time_in_force=TimeInForce.IOC, reduce_only=reduce_only)

    def submit(self, order) -> None:
        self.submit_order(order)

    def net_position(self, symbol: str) -> int:
        net = self.portfolio.net_position(instrument_id(symbol))
        if net != net.to_integral_value():
            raise ValueError(f"{symbol}: fractional Nautilus position {net}")
        return int(net)

    def account_balance(self) -> float:
        return float(self.portfolio.account(NSE).balance_total(INR).as_double())

    def realized_gross_pnl(self) -> float:
        """Gross realized P&L from the Nautilus account: balance change (realized P&L net of every
        commission charged) plus every commission charged on this strategy's fills."""
        account = self.portfolio.account(NSE)
        commissions = sum((c.as_decimal() for c in account.commissions().values()), Decimal(0))
        return float(account.balance_total(INR).as_decimal() - account.starting_balances()[INR].as_decimal()
                     + commissions)

    # ---- Nautilus order events -> R5 -----------------------------------------------------
    def on_order_accepted(self, event) -> None:
        self.r5.on_order_accepted(str(event.client_order_id))

    def on_order_filled(self, event) -> None:
        self.r5.on_order_fill(str(event.client_order_id), int(event.last_qty), float(event.last_px),
                              float(event.commission.as_double()), int(event.ts_event))

    def on_order_rejected(self, event) -> None:
        self.r5.on_order_closed_unfilled(str(event.client_order_id), "REJECTED", str(event.reason))

    def on_order_denied(self, event) -> None:
        self.r5.on_order_closed_unfilled(str(event.client_order_id), "DENIED", str(event.reason))

    def on_order_canceled(self, event) -> None:
        self.r5.on_order_closed_unfilled(str(event.client_order_id), "CANCELED", "")

    def on_order_expired(self, event) -> None:
        self.r5.on_order_closed_unfilled(str(event.client_order_id), "EXPIRED", "")
