"""Engine B orchestrator: the shared R5 decision engine driven by completed Nautilus bars.

Every decision (PA/ID/MPC, closed loop, governor, CCPP plant, safety gates, sizing, exits) is the
inherited Revision2ExternalEngineOrchestrator code.  This class only overrides the execution-port
seams the shared orchestrator exposes:

* causal clock: bars arrive one completed bar at a time; ``next_ts`` is the calendar successor
  and the planning reference is the decision bar's close (Option A).  The frame R5 sees never
  contains a future row, so any future access raises instead of leaking;
* order submission goes to Nautilus's SimulatedExchange through the attached port;
* R5 entry/exit bookkeeping runs only from confirmed Nautilus fill events;
* broker position, realized P&L and equity are read from the Nautilus portfolio and account.

The offline paper broker is replaced by a stub that raises on any order or position access, so
no paper fill can be produced or reported as a Nautilus fill.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from revision2.portfolio_orchestrator import _ClockEvent
from revision2_external.data_certification_pandera import certify_bars
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.plant_control import PlantControlError
from revision5.topology import SYMBOL_TO_BAY

BAR_SPAN = pd.Timedelta(minutes=1)
EXCHANGE_TZ = "Asia/Kolkata"
MAX_EXIT_ATTEMPTS_PER_BAR = 3
MAX_CONSECUTIVE_EXIT_REJECTIONS = 2


class EngineBExecutionError(RuntimeError):
    """Raised when anything tries to execute through the offline paper broker in Engine B."""


class _DisabledPaperBroker:
    """Keeps the read-only slippage band the shared code uses for plant quotes and caps;
    every execution or position access fails closed."""
    environment = "paper"

    def __init__(self, slippage_fraction: float):
        self.slippage_fraction = float(slippage_fraction)

    def _refuse(self, *_, **__):
        raise EngineBExecutionError("Engine B executes only through Nautilus; the paper broker is disabled")

    place_order = get_position = unrealized_pnl = _refuse

    @property
    def realized_pnl(self):
        self._refuse()

    @property
    def booked_costs(self):
        self._refuse()


class EngineBOrchestrator(Revision2ExternalEngineOrchestrator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.broker = _DisabledPaperBroker(self.broker.slippage_fraction)
        self.port = None
        self._started = False
        self._inflight: Dict[str, Dict[str, Any]] = {}
        self._symbol_inflight: Dict[str, str] = {}
        self._exit_rejections: Counter = Counter()
        self._working_exits: Dict[str, Dict[str, Any]] = {}   # partially filled exits awaiting liquidity
        self.engine_b_counts: Counter = Counter()
        self.order_audit: List[Dict[str, Any]] = []
        self.option_a_fills: List[Dict[str, Any]] = []
        self.reconciliation_checks = 0
        self.reconciliation_breaks: List[Dict[str, Any]] = []
        self.chronology_errors: List[str] = []

    # ---- wiring --------------------------------------------------------------------------
    def attach_port(self, port) -> None:
        self.port = port

    def _assert_paper_plant_broker(self):
        if self.port is None or not getattr(self.port, "offline", False):
            raise PlantControlError("Engine B requires an attached offline Nautilus backtest port")

    # ---- causal clock (Nautilus bars in) -------------------------------------------------
    def begin(self, warmup_frames: Dict[str, pd.DataFrame]) -> None:
        """Warm-up calibration on the first completed bars only, exactly like run()'s warm-up."""
        lengths = {len(f) for f in warmup_frames.values()}
        if len(lengths) != 1:
            raise ValueError(f"warm-up frames must have equal length, got {sorted(lengths)}")
        self._prepare_run(warmup_frames, warmup=lengths.pop())
        self._started = True

    def on_completed_bars(self, rows: Dict[str, Dict[str, Any]]) -> None:
        """One completed-bar timestamp for one or more symbols -> one shared R5 decision tick."""
        if not self._started:
            raise RuntimeError("on_completed_bars before begin()")
        events = []
        for symbol in sorted(rows):
            frame = self._run.symbol_bars[symbol]
            new, _ = certify_bars(pd.DataFrame([rows[symbol]]))
            label, last = new["timestamp"].iloc[0], frame["timestamp"].iloc[-1]
            if label <= last:
                self.chronology_errors.append(f"{symbol}: {label} <= {last}")
                raise ValueError(f"{symbol}: non-increasing bar {label} after {last}")
            frame = pd.concat([frame, new], ignore_index=True)
            self._run.symbol_bars[symbol] = frame
            session = self._session_bar_index[symbol]
            same_day = label.date() == last.date()
            self._session_bar_index[symbol] = np.append(session, session[-1] + 1 if same_day else 0)
            events.append(_ClockEvent(frame["timestamp"].iloc[-1], symbol, len(frame) - 1))
        if len({e.timestamp for e in events}) != 1:
            raise ValueError("one tick must carry a single bar timestamp")
        for symbol, record in list(self._working_exits.items()):
            # Residual of a partially filled exit: keep working it at this bar's close.  The shared
            # exit logic is not re-entered for this symbol until the position is flat.
            del self._working_exits[symbol]
            del self._symbol_inflight[symbol]
            record["attempts"] = 0
            self._submit_exit_child(record)
        self._reconcile_all("tick_start")
        self._process_tick(events[0].timestamp, events)

    def _next_bar_timestamp(self, bars: pd.DataFrame, bar_idx: int) -> pd.Timestamp:
        if bar_idx != len(bars) - 1:
            raise RuntimeError("Engine B decides only on the latest completed bar")
        return pd.Timestamp(bars.iloc[bar_idx]["timestamp"]) + BAR_SPAN

    def _entry_reference_price(self, bars: pd.DataFrame, bar_idx: int) -> float:
        # Option A: plan on the completed decision bar's close, the latest price known at decision time.
        return float(bars.iloc[bar_idx]["close"])

    def _entries_suspended(self, symbol: str) -> bool:
        return symbol in self._symbol_inflight or bool(self.reconciliation_breaks)

    def _maybe_exit(self, symbol, *args, **kwargs):
        if symbol in self._symbol_inflight:      # an order for this symbol is still working
            return None
        return super()._maybe_exit(symbol, *args, **kwargs)

    # ---- Nautilus is the broker of record ----------------------------------------------
    def _broker_position(self, symbol: str) -> Dict[str, Any]:
        return {"quantity": float(self.port.net_position(symbol))}

    def _broker_realized_pnl(self) -> float:
        return self.port.realized_gross_pnl()

    def _equity(self) -> float:
        return self.port.account_balance()

    # ---- order intents out ---------------------------------------------------------------
    def _register(self, order, record: Dict[str, Any]) -> None:
        coid = str(order.client_order_id)
        record.update(client_order_ids=record.get("client_order_ids", []) + [coid],
                      submitted_ns=self.port.now_ns())
        self._inflight[coid] = record
        self._symbol_inflight[record["symbol"]] = coid
        self._audit(coid, record, "SUBMIT", side=str(order.side_string()), quantity=int(order.quantity))

    def _submit_entry_order(self, intent: Dict[str, Any]) -> None:
        symbol, side, quantity = intent["symbol"], intent["order"].side, int(intent["quantity"])
        self._run.funnel["orders_submitted"] += 1
        self.engine_b_counts["entry_orders_submitted"] += 1
        order = self.port.create_market(symbol, side, quantity, reduce_only=False)
        self._register(order, {"kind": "entry", "symbol": symbol, "intent": intent, "requested": quantity,
                               "filled": 0, "notional": 0.0, "commission": 0.0})
        self.port.submit(order)

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        self._assert_paper_plant_broker()
        self._verify_broker_position_reconciles(symbol, trade)
        self._exit_orders_submitted += 1
        self.engine_b_counts["exit_orders_submitted"] += 1
        self._submit_exit_child({"kind": "exit", "symbol": symbol, "trade": trade, "reason": reason,
                                 "decision_timestamp": str(timestamp), "decision_price": float(exit_price),
                                 "requested": int(trade["quantity"]), "filled": 0, "notional": 0.0,
                                 "commission": 0.0, "attempts": 0, "child_orders": 0})

    def _submit_exit_child(self, record: Dict[str, Any]) -> None:
        close_side = "SELL" if record["trade"]["side"] == "BUY" else "BUY"
        record["attempts"] += 1
        record["child_orders"] += 1
        order = self.port.create_market(record["symbol"], close_side, record["requested"] - record["filled"],
                                        reduce_only=True)
        self._register(order, record)
        self.port.submit(order)

    # ---- Nautilus order events in ---------------------------------------------------------
    def _audit(self, coid: str, record: Optional[Dict[str, Any]], event: str, **detail) -> None:
        self.engine_b_counts[f"event_{event.lower()}"] += 1
        self.order_audit.append({"client_order_id": coid, "event": event,
                                 "kind": record["kind"] if record else None,
                                 "symbol": record["symbol"] if record else None,
                                 "ts_ns": self.port.now_ns(), **detail})

    def on_order_accepted(self, coid: str) -> None:
        self._audit(coid, self._inflight.get(coid), "ACCEPTED")

    def on_order_fill(self, coid: str, quantity: int, price: float, commission: float, ts_event: int) -> None:
        record = self._inflight.get(coid)
        self._audit(coid, record, "FILLED", quantity=quantity, price=price, commission=commission, ts_event=ts_event)
        if record is None:
            self._break("orphan_fill", coid=coid, quantity=quantity, price=price)
            return
        record["filled"] += int(quantity)
        record["notional"] += int(quantity) * float(price)
        record["commission"] += float(commission)
        record["last_fill_ns"] = int(ts_event)
        if record["filled"] >= record["requested"]:
            self._complete(coid, record)
        else:
            self._reconcile(record["symbol"], f"{record['kind']}_partial_fill")

    def on_order_closed_unfilled(self, coid: str, status: str, reason: str) -> None:
        """REJECTED / DENIED / CANCELED / EXPIRED.  A partially filled IOC order ends here too."""
        record = self._inflight.get(coid)
        self._audit(coid, record, status, reason=reason)
        if record is None:
            return
        if record["kind"] == "entry" and record["filled"] > 0:
            self.engine_b_counts["entry_partial_fills"] += 1
            self._complete(coid, record)
        elif record["kind"] == "exit" and (record["filled"] > 0 or record["child_orders"] > 1):
            self.engine_b_counts["exit_partial_fills"] += 1
            self._release(coid, record)
            if record["attempts"] < MAX_EXIT_ATTEMPTS_PER_BAR:
                self._submit_exit_child(record)          # work the residual immediately
            else:
                # Liquidity exhausted for this bar: park the residual, re-submit it next bar.
                self.engine_b_counts["exit_residual_parked"] += 1
                self._working_exits[record["symbol"]] = record
                self._symbol_inflight[record["symbol"]] = f"WORKING_EXIT:{record['symbol']}"
        else:
            self._release(coid, record)
            if record["kind"] == "entry":
                self.engine_b_counts["entry_orders_rejected"] += 1
                self._on_entry_order_result(record["intent"], {"passed": False, "order_id": coid,
                                                               "reasons": [f"nautilus_{status.lower()}: {reason}"]})
            else:
                self.engine_b_counts["exit_orders_rejected"] += 1
                self._exit_rejections[record["symbol"]] += 1
                if self._exit_rejections[record["symbol"]] >= MAX_CONSECUTIVE_EXIT_REJECTIONS:
                    self._execution_halted = True     # protective exits stay enabled, new entries stop
            self._reconcile(record["symbol"], f"{record['kind']}_{status.lower()}")

    def _release(self, coid: str, record: Dict[str, Any]) -> None:
        self._inflight.pop(coid, None)
        if self._symbol_inflight.get(record["symbol"]) == coid:
            del self._symbol_inflight[record["symbol"]]

    def _fill_label(self, record: Dict[str, Any]) -> pd.Timestamp:
        return pd.Timestamp(record["last_fill_ns"], tz="UTC").tz_convert(EXCHANGE_TZ)

    def _complete(self, coid: str, record: Dict[str, Any]) -> None:
        self._release(coid, record)
        quantity = int(record["filled"])
        price = record["notional"] / quantity
        if record["kind"] == "entry":
            self._complete_entry(coid, record, quantity, price)
        else:
            self._exit_rejections[record["symbol"]] = 0
            self.engine_b_counts["positions_closed"] += 1
            self._on_exit_order_result(record["symbol"], self._fill_label(record), record["trade"], {
                "passed": True, "order_id": coid, "filled_price": price, "filled_quantity": quantity,
                "cost": record["commission"]}, record["reason"])
        self._reconcile(record["symbol"], f"{record['kind']}_fill")

    def _complete_entry(self, coid: str, record: Dict[str, Any], quantity: int, price: float) -> None:
        """Option A post-fill treatment.  The plan was built on the decision bar's close; the
        position is booked at Nautilus's actual average fill price with the planned stop and
        target DISTANCES preserved (re-anchored to the fill).  Quantity can only shrink (partial
        fill), so the rupee risk at the stop can only fall.  Any breach of the planned stop, the
        rupee cap or the bay's HRSG-effective capacity arms the existing R5 plant-trip exit for
        the next bar; nothing is ever upsized."""
        intent = record["intent"]
        planned = intent["plan"]
        sign = 1.0 if planned.side == "BUY" else -1.0
        risk = abs(float(planned.entry_price) - float(planned.stop_price))
        reward = abs(float(planned.target_price) - float(planned.entry_price))
        plan = replace(planned, entry_price=price, stop_price=price - sign * risk,
                       target_price=price + sign * reward)
        elapsed = max(0.0, (record["last_fill_ns"] - record["submitted_ns"]) / 1e9)
        self.engine_b_counts["entry_fills"] += 1
        self._on_entry_order_result({**intent, "plan": plan}, {
            "passed": True, "order_id": coid, "filled_price": price, "filled_quantity": quantity,
            "ack_elapsed_seconds": elapsed, "cost": record["commission"]})
        symbol = record["symbol"]
        trade = self.open_trades.get(symbol)
        if trade is None:
            self._break("entry_fill_not_booked", symbol=symbol, coid=coid)
            return
        max_loss = float(self.config.require("max_loss_per_trade_rupees"))
        worst_loss = quantity * risk
        breaches = []
        if sign * (price - float(planned.stop_price)) <= 0.0:
            breaches.append("ENGINE_B_FILL_BEYOND_PLANNED_STOP")
        if worst_loss > max_loss + 1e-6:
            breaches.append("ENGINE_B_FILL_RUPEE_RISK_EXCEEDED")
        bay_detail = {}
        if self._plant_wired:
            bay_id = trade.get("plant_bay_id") or SYMBOL_TO_BAY.get(symbol)
            bay = self.real_plant_dcs.bays.get(bay_id) if bay_id else None
            if bay is not None:
                bay_notional = sum(t["quantity"] * (float(t["entry_price"]) if s == symbol
                                                    else self._last_close.get(s, float(t["entry_price"])))
                                   for s, t in self.open_trades.items() if SYMBOL_TO_BAY.get(s) == bay_id)
                bay_detail = {"bay_id": bay_id, "bay_notional_after_fill": bay_notional,
                              "bay_effective_allocation": float(bay.effective_allocation)}
                if bay_notional > float(bay.effective_allocation) + 1e-6:
                    breaches.append("ENGINE_B_FILL_EXCEEDS_BAY_CAPACITY")
        row = {"trade_id": trade.get("trade_id"), "candidate_id": intent["candidate_id"], "symbol": symbol,
               "side": planned.side, "requested_quantity": record["requested"], "filled_quantity": quantity,
               "reference_price": float(planned.entry_price), "fill_price": price,
               "planned_stop": float(planned.stop_price), "planned_target": float(planned.target_price),
               "anchored_stop": float(plan.stop_price), "anchored_target": float(plan.target_price),
               "risk_per_share": risk, "worst_case_loss_rupees": worst_loss, "max_loss_per_trade_rupees": max_loss,
               "breaches": breaches, **bay_detail}
        self.option_a_fills.append(row)
        self._record_controller_event("ENGINE_B_OPTION_A_FILL", self._fill_label(record), symbol, row)
        for reason in breaches:
            self._arm_plant_trip(symbol, self._fill_label(record), trade, reason)

    # ---- reconciliation -------------------------------------------------------------------
    def _expected_position(self, symbol: str) -> int:
        """R5's view of the Nautilus position: the booked trade plus whatever a still-working
        order for this symbol has already filled (an entry is booked only once complete; an exit
        removes the trade only once the whole position is flat)."""
        trade = self.open_trades.get(symbol)
        expected = 0 if trade is None else int(trade["quantity"]) * (1 if trade["side"] == "BUY" else -1)
        record = self._working_exits.get(symbol) or self._inflight.get(self._symbol_inflight.get(symbol, ""))
        if record is not None:
            if record["kind"] == "entry":
                expected += int(record["filled"]) * (1 if record["intent"]["order"].side == "BUY" else -1)
            else:
                expected -= int(record["filled"]) * (1 if record["trade"]["side"] == "BUY" else -1)
        return expected

    def _reconcile(self, symbol: str, context: str) -> None:
        self.reconciliation_checks += 1
        held = int(self.port.net_position(symbol))
        expected = self._expected_position(symbol)
        if held != expected:
            self._break("position_mismatch", symbol=symbol, context=context, nautilus=held, r5=expected)

    def _reconcile_all(self, context: str) -> None:
        for symbol in self.symbols:
            self._reconcile(symbol, context)

    def _break(self, kind: str, **detail) -> None:
        self.reconciliation_breaks.append({"kind": kind, **detail})
        self._execution_halted = True                 # fail closed: no new entries

    # ---- end of replay --------------------------------------------------------------------
    def finish(self) -> Dict[str, Any]:
        self._reconcile_all("finish")
        ledger_gross_error = None
        try:
            report = self._finalize_run()
        except AssertionError:
            # _finalize_run asserts Nautilus gross realized P&L == the R5 trade ledger's gross.
            ledger_gross_error = {"nautilus_gross": self._broker_realized_pnl(),
                                  "r5_ledger_gross": sum(t["pnl"] for t in self.completed_trades)}
            self._break("ledger_vs_nautilus_gross_mismatch", **ledger_gross_error)
            report = None
        return {"r5_report": report, "ledger_gross_error": ledger_gross_error}
