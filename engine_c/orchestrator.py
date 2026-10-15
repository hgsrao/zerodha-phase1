"""Engine C orchestrator: the shared R5 decision engine driven by completed Backtrader bars.

Every decision (PA/ID/MPC, closed loop, governor, CCPP plant, safety gates, sizing, exits) is the
inherited ``Revision2ExternalEngineOrchestrator`` code.  This class overrides only the
execution-port seams that class exposes, plus the bookkeeping Backtrader's asynchronous order
lifecycle needs:

* causal clock: R5 sees one completed bar per symbol per Backtrader cycle and never a future row;
  ``next_ts`` is the calendar successor and the planning reference is the decision bar's close
  (the next bar's open is unknown when R5 decides);
* approved intents become Backtrader market orders; Backtrader executes them at the OPEN of the
  symbol's next bar (its default, non-cheating market-order semantics);
* R5 books an entry or a close only from Backtrader execution bits delivered by ``notify_order``;
* one working order per symbol; entries reserve concurrency, gross, sector and bay capacity while
  they are working, because R5's own checks only count booked positions;
* broker position, realized P&L, commissions and cash are read from Backtrader's broker.

The offline paper broker is replaced by a stub that raises on any order or position access.
Nothing here imports Engine A's paper execution or Engine B's Nautilus code.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from revision2.portfolio_orchestrator import _ClockEvent
from revision2.transaction_costs import leg_cost
from revision2_external.data_certification_pandera import certify_bars
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.plant_control import PlantControlError
from revision5.topology import SYMBOL_TO_BAY

from engine_c.reconciliation import FillLedger, Reconciler

BAR_SPAN = pd.Timedelta(minutes=1)
MAX_CONSECUTIVE_EXIT_REJECTIONS = 2


class EngineCExecutionError(RuntimeError):
    """Raised when anything tries to execute through the offline paper broker in Engine C."""


class _DisabledPaperBroker:
    """Keeps the read-only slippage band the shared code uses for plant quotes and caps;
    every execution or position access fails closed."""
    environment = "paper"

    def __init__(self, slippage_fraction: float):
        self.slippage_fraction = float(slippage_fraction)

    def _refuse(self, *_, **__):
        raise EngineCExecutionError("Engine C executes only through Backtrader; the paper broker is disabled")

    place_order = get_position = unrealized_pnl = _refuse

    @property
    def realized_pnl(self):
        self._refuse()

    @property
    def booked_costs(self):
        self._refuse()


class EngineCOrchestrator(Revision2ExternalEngineOrchestrator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.broker = _DisabledPaperBroker(self.broker.slippage_fraction)
        self.port = None
        self._started = False
        self._working: Dict[int, Dict[str, Any]] = {}          # bt order ref -> record
        self._order_ids: Dict[int, str] = {}                    # bt order ref -> run-local order id
        self._symbol_working: Dict[str, int] = {}               # symbol -> bt order ref
        self._exit_rejections: Counter = Counter()
        self.counts: Counter = Counter()
        self.order_audit: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.entry_fills: List[Dict[str, Any]] = []
        self.capacity_reservations: List[Dict[str, Any]] = []
        self.max_working_per_symbol = 0
        self.chronology_errors: List[str] = []
        self.session_carry_exits: List[Dict[str, Any]] = []
        self.ended_refs: set = set()
        self.ledger = FillLedger(self.starting_equity)
        self.reconciler = Reconciler(self)

    # ---- wiring --------------------------------------------------------------------------
    def attach_port(self, port) -> None:
        self.port = port

    def _assert_paper_plant_broker(self):
        if self.port is None or not getattr(self.port, "offline", False):
            raise PlantControlError("Engine C requires an attached offline Backtrader port")

    # ---- causal clock (Backtrader bars in) -----------------------------------------------
    def begin(self, warmup_frames: Dict[str, pd.DataFrame]) -> None:
        """Warm-up calibration on completed bars only, exactly like run()'s warm-up.  No order
        can be created before this returns."""
        lengths = {len(f) for f in warmup_frames.values()}
        if len(lengths) != 1:
            raise ValueError(f"warm-up frames must have equal length, got {sorted(lengths)}")
        self._prepare_run(warmup_frames, warmup=lengths.pop())
        self.ledger = FillLedger(self.port.cash())      # the broker's cash before any order exists
        self._started = True

    def on_completed_bars(self, rows: Dict[str, Dict[str, Any]]) -> None:
        """One Backtrader cycle: the completed bars delivered at one timestamp -> one R5 tick."""
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
        timestamp = events[0].timestamp
        self.reconciler.check("tick_start")
        self._session_carry_guard(timestamp, events)
        self._process_tick(timestamp, events)

    def _session_carry_guard(self, timestamp, events) -> None:
        """An intraday position still open when a NEW session's first bar arrives means the data's
        previous session ended before R5's force-close bar (a short or truncated session).
        Backtrader cannot fill in the gap, so the position is flattened at the first opportunity
        (the next bar's open) and the event is reported; it is never back-dated."""
        for event in events:
            trade = self.open_trades.get(event.symbol)
            if trade is None or event.symbol in self._symbol_working:
                continue
            if pd.Timestamp(trade["entry_timestamp"]).date() < pd.Timestamp(timestamp).date():
                bar = self._run.symbol_bars[event.symbol].iloc[event.bar_idx]
                self.session_carry_exits.append({"symbol": event.symbol, "trade_id": trade.get("trade_id"),
                                                 "entry_timestamp": trade["entry_timestamp"],
                                                 "detected_at": str(timestamp)})
                self._execute_exit(event.symbol, timestamp, trade, float(bar["open"]), "engine_c_session_carry_exit")

    def _next_bar_timestamp(self, bars: pd.DataFrame, bar_idx: int) -> pd.Timestamp:
        if bar_idx != len(bars) - 1:
            raise RuntimeError("Engine C decides only on the latest completed bar")
        return pd.Timestamp(bars.iloc[bar_idx]["timestamp"]) + BAR_SPAN

    def _entry_reference_price(self, bars: pd.DataFrame, bar_idx: int) -> float:
        # The next bar's open is unknown at decision time: plan on the decision bar's close.
        if bar_idx != len(bars) - 1:
            raise RuntimeError("Engine C plans only on the latest completed bar")
        return float(bars.iloc[bar_idx]["close"])

    def _entries_suspended(self, symbol: str) -> bool:
        return symbol in self._symbol_working or bool(self.reconciler.breaks)

    def _maybe_exit(self, symbol, *args, **kwargs):
        if symbol in self._symbol_working:      # an order for this symbol is still working
            return None
        return super()._maybe_exit(symbol, *args, **kwargs)

    # ---- Backtrader is the broker of record --------------------------------------------
    def _broker_position(self, symbol: str) -> Dict[str, Any]:
        return {"quantity": float(self.port.net_position(symbol))}

    def _broker_realized_pnl(self) -> float:
        return self.port.realized_gross_pnl()

    def _equity(self) -> float:
        # Accounting equity: R5 capital plus Backtrader's realized P&L net of Backtrader's
        # commissions.  (Backtrader cash also moves with open-position notional.)
        return self.starting_equity + self.port.realized_gross_pnl() - self.port.commissions_total()

    def _pending_entries(self) -> List[Dict[str, Any]]:
        return [r for r in self._working.values() if r["kind"] == "entry" and not r.get("booked")]

    def _gross_exposure_notional(self) -> float:
        # Working entries already hold capacity: count them at their reserved notional.
        return super()._gross_exposure_notional() + sum(r["reserved_notional"] for r in self._pending_entries())

    # ---- order intents out ---------------------------------------------------------------
    def _register(self, order, record: Dict[str, Any]) -> None:
        ref = int(order.ref)
        self._order_ids[ref] = f"C{len(self._order_ids) + 1:05d}"
        record.setdefault("refs", []).append(ref)
        record["submitted_label"] = str(self.port.now_label())
        self._working[ref] = record
        self._symbol_working[record["symbol"]] = ref
        self.max_working_per_symbol = max(self.max_working_per_symbol,
                                          sum(1 for r in self._working.values() if r["symbol"] == record["symbol"]))
        self._audit(ref, record, "SUBMIT", quantity=int(order.size if order.size > 0 else -order.size))

    def _capacity_breach(self, symbol: str, quantity: int, price: float) -> Optional[str]:
        """Capacity R5's admission could not see: entries still working at the broker."""
        pending = self._pending_entries()
        if not pending:
            return None
        notional = quantity * price
        if len(self.open_trades) + len(pending) >= int(self._run.max_concurrent):
            return "max_concurrent_positions"
        equity = self._equity()
        sector = self.sector_map.get(symbol, "Unclassified")
        sector_notional = sum(t["quantity"] * self._last_close.get(s, t["entry_price"])
                              for s, t in self.open_trades.items()
                              if self.sector_map.get(s, "Unclassified") == sector)
        sector_notional += sum(r["reserved_notional"] for r in pending
                               if self.sector_map.get(r["symbol"], "Unclassified") == sector)
        if sector_notional + notional > equity * float(self._run.sector_cap_fraction):
            return "sector_exposure_cap"
        if self._plant_wired:
            bay_id = SYMBOL_TO_BAY.get(symbol)
            bay = self.real_plant_dcs.bays.get(bay_id) if bay_id else None
            if bay is not None:
                bay_notional = sum(t["quantity"] * self._last_close.get(s, t["entry_price"])
                                   for s, t in self.open_trades.items() if SYMBOL_TO_BAY.get(s) == bay_id)
                bay_notional += sum(r["reserved_notional"] for r in pending if SYMBOL_TO_BAY.get(r["symbol"]) == bay_id)
                if bay_notional + notional > float(bay.effective_allocation) + 1e-6:
                    return "plant_bay_capacity"
        return None

    def _submit_entry_order(self, intent: Dict[str, Any]) -> None:
        symbol, side, quantity = intent["symbol"], intent["order"].side, int(intent["quantity"])
        reference = float(intent["plan"].entry_price)
        self.decisions.append({"candidate_id": intent["candidate_id"], "symbol": symbol, "side": side,
                               "decision_timestamp": str(intent["timestamp"]), "quantity": quantity,
                               "reference_price": reference})
        breach = self._capacity_breach(symbol, quantity, reference)
        if breach is not None:
            self.counts["entry_capacity_rejections"] += 1
            self.capacity_reservations.append({"candidate_id": intent["candidate_id"], "symbol": symbol,
                                               "reason": breach, "working_entries": len(self._pending_entries()),
                                               "open_trades": len(self.open_trades)})
            self.decisions[-1]["disposition"] = f"engine_c_pending_capacity:{breach}"
            self.entry_candidate_observations.dispose(intent["candidate_id"], "REJECTED",
                                                      f"engine_c_pending_capacity:{breach}")
            self._run.funnel["portfolio_cap_rejections"] += 1
            return
        self._run.funnel["orders_submitted"] += 1
        self.counts["entry_orders_submitted"] += 1
        self.decisions[-1]["disposition"] = "submitted"
        order = self.port.submit_market(symbol, side, quantity)
        self._register(order, {"kind": "entry", "symbol": symbol, "side": side, "intent": intent,
                               "requested": quantity, "filled": 0, "notional": 0.0, "commission": 0.0,
                               "reserved_notional": quantity * reference, "fills": []})

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        self._assert_paper_plant_broker()
        self._verify_broker_position_reconciles(symbol, trade)
        self._exit_orders_submitted += 1
        self.counts["exit_orders_submitted"] += 1
        record = {"kind": "exit", "symbol": symbol, "side": "SELL" if trade["side"] == "BUY" else "BUY",
                  "trade": trade, "reason": reason, "decision_timestamp": str(timestamp),
                  "decision_price": float(exit_price), "requested": int(trade["quantity"]), "filled": 0,
                  "notional": 0.0, "commission": 0.0, "child_orders": 0, "fills": []}
        self._submit_exit_child(record)

    def _submit_exit_child(self, record: Dict[str, Any]) -> None:
        record["child_orders"] += 1
        order = self.port.submit_market(record["symbol"], record["side"], record["requested"] - record["filled"])
        self._register(order, record)

    # ---- Backtrader order notifications in ------------------------------------------------
    def _audit(self, ref: int, record: Optional[Dict[str, Any]], event: str, **detail) -> None:
        self.counts[f"event_{event.lower()}"] += 1
        self.order_audit.append({"order_id": self._order_ids.get(ref), "bt_ref": ref, "event": event,
                                 "kind": record["kind"] if record else None,
                                 "side": record["side"] if record else None,
                                 "symbol": record["symbol"] if record else None,
                                 "label": str(self.port.now_label()), **detail})

    def on_bt_order(self, order) -> None:
        """Backtrader notify_order (a clone of the order at notification time)."""
        ref = int(order.ref)
        record = self._working.get(ref)
        status = order.getstatusname(order.status).upper()
        if status in ("SUBMITTED", "ACCEPTED"):
            self._audit(ref, record, status)
            return
        for bit in order.executed.iterpending():
            self._on_execution(ref, record, bit, status)
        if record is None:
            if status not in ("PARTIAL", "COMPLETED"):
                self._audit(ref, None, status)
            return
        if status == "COMPLETED":
            if not record.get("booked"):
                self._complete(ref, record)
        elif status == "PARTIAL":
            if record["kind"] == "entry" and not record.get("booked"):
                # Entry IOC semantics: cancel the unfilled remainder natively; book what filled.
                if self.port.cancel(ref):
                    self.counts["entry_partial_fills"] += 1
                    self._complete(ref, record, keep_working=True)
            else:
                self.counts["exit_partial_fill_events"] += 1    # residual stays working at the broker
        elif status in ("CANCELED", "EXPIRED", "MARGIN", "REJECTED"):
            reason = (order.info.get("reject_reason") if hasattr(order.info, "get") else None) or status.lower()
            self._audit(ref, record, status, reason=reason)
            self._closed_unfilled(ref, record, status, reason)

    def _on_execution(self, ref: int, record: Optional[Dict[str, Any]], bit, status: str) -> None:
        quantity = int(round(abs(bit.size)))
        label = self.port.label_of(bit.dt)
        row = {"quantity": quantity, "price": float(bit.price), "commission": float(bit.comm),
               "pnl": float(bit.pnl), "fill_label": str(label)}
        self._audit(ref, record, "FILLED", partial=status == "PARTIAL", **row)
        if record is None:
            self.reconciler.record_break("orphan_fill", ref=ref, **row)
            return
        record["filled"] += quantity
        record["notional"] += quantity * float(bit.price)
        record["commission"] += float(bit.comm)
        record["last_fill_label"] = label
        record["fills"].append(row)
        self.ledger.apply(record["symbol"], record["side"], quantity, float(bit.price))
        self.reconciler.check_symbol(record["symbol"], f"{record['kind']}_execution")

    def _release(self, ref: int, record: Dict[str, Any]) -> None:
        self._working.pop(ref, None)
        if self._symbol_working.get(record["symbol"]) == ref:
            del self._symbol_working[record["symbol"]]

    def _closed_unfilled(self, ref: int, record: Dict[str, Any], status: str, reason: str) -> None:
        if record.get("booked"):
            self._release(ref, record)          # cancel confirmation of a booked IOC remainder
            return
        self._release(ref, record)
        if record["kind"] == "entry":
            self.counts[f"entry_orders_{status.lower()}"] += 1
            self._on_entry_order_result(record["intent"], {"passed": False, "order_id": f"bt-{ref}",
                                                           "reasons": [f"backtrader_{status.lower()}: {reason}"]})
        elif record["filled"] > 0:
            self.counts["exit_residual_resubmitted"] += 1
            self._submit_exit_child(record)     # never leave a partially closed position unattended
        else:
            self.counts[f"exit_orders_{status.lower()}"] += 1
            self._exit_rejections[record["symbol"]] += 1
            if self._exit_rejections[record["symbol"]] >= MAX_CONSECUTIVE_EXIT_REJECTIONS:
                self._execution_halted = True   # protective exits stay enabled, new entries stop
        self.reconciler.check_symbol(record["symbol"], f"{record['kind']}_{status.lower()}")

    def _complete(self, ref: int, record: Dict[str, Any], keep_working: bool = False) -> None:
        record["booked"] = True
        if not keep_working:
            self._release(ref, record)
        else:                                    # remainder cancel confirmation still to come
            if self._symbol_working.get(record["symbol"]) == ref:
                del self._symbol_working[record["symbol"]]
        quantity = int(record["filled"])
        price = record["notional"] / quantity
        if record["kind"] == "entry":
            self._complete_entry(ref, record, quantity, price)
        else:
            self._exit_rejections[record["symbol"]] = 0
            self.counts["positions_closed"] += 1
            trade = record["trade"]
            self._on_exit_order_result(record["symbol"], record["last_fill_label"], trade, {
                "passed": True, "order_id": f"bt-{ref}", "filled_price": price, "filled_quantity": quantity,
                "cost": record["commission"]}, record["reason"])
            closed = self.completed_trades[-1]
            fills = trade.get("engine_c_entry_fills", []) + record["fills"]
            closed.update({
                "decision_timestamp": trade.get("decision_timestamp"),
                "exit_decision_timestamp": record["decision_timestamp"],
                "exit_decision_price": record["decision_price"],
                "exit_child_orders": record["child_orders"], "exit_fills": len(record["fills"]),
                "entry_fills": len(trade.get("engine_c_entry_fills", [])),
                "backtrader_commission": sum(f["commission"] for f in fills),
                "engine_c_per_fill_costs": sum(leg_cost(f["price"], f["quantity"], s) for f, s in
                                               [(f, trade["side"]) for f in trade.get("engine_c_entry_fills", [])]
                                               + [(f, record["side"]) for f in record["fills"]]),
            })
        self.reconciler.check_symbol(record["symbol"], f"{record['kind']}_booked")

    def _complete_entry(self, ref: int, record: Dict[str, Any], quantity: int, price: float) -> None:
        """Book a Backtrader-confirmed entry.  The plan was built on the decision bar's close;
        the position is booked at Backtrader's actual average fill price with the planned stop and
        target DISTANCES re-anchored to the fill.  Quantity can only shrink (partial fill).  A fill
        beyond the planned stop (an opening gap), a breach of the rupee loss cap or of the bay's
        HRSG-effective capacity arms the existing R5 plant-trip exit; nothing is ever upsized."""
        intent = record["intent"]
        planned = intent["plan"]
        sign = 1.0 if planned.side == "BUY" else -1.0
        risk = abs(float(planned.entry_price) - float(planned.stop_price))
        reward = abs(float(planned.target_price) - float(planned.entry_price))
        plan = replace(planned, entry_price=price, stop_price=price - sign * risk, target_price=price + sign * reward)
        fill_label = record["last_fill_label"]
        decision_close = pd.Timestamp(intent["timestamp"]) + BAR_SPAN
        elapsed = max(0.0, (fill_label - decision_close).total_seconds())
        self.counts["entry_fills"] += 1
        self._on_entry_order_result({**intent, "plan": plan, "next_ts": fill_label}, {
            "passed": True, "order_id": f"bt-{ref}", "filled_price": price, "filled_quantity": quantity,
            "ack_elapsed_seconds": elapsed, "cost": record["commission"]})
        symbol = record["symbol"]
        trade = self.open_trades.get(symbol)
        if trade is None:
            self.reconciler.record_break("entry_fill_not_booked", symbol=symbol, ref=ref)
            return
        max_loss = float(self.config.require("max_loss_per_trade_rupees"))
        worst_loss = quantity * risk
        breaches = []
        if sign * (price - float(planned.stop_price)) <= 0.0:
            breaches.append("ENGINE_C_FILL_BEYOND_PLANNED_STOP")
        if worst_loss > max_loss + 1e-6:
            breaches.append("ENGINE_C_FILL_RUPEE_RISK_EXCEEDED")
        bay_detail: Dict[str, Any] = {}
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
                    breaches.append("ENGINE_C_FILL_EXCEEDS_BAY_CAPACITY")
        row = {"trade_id": trade.get("trade_id"), "candidate_id": intent["candidate_id"], "symbol": symbol,
               "side": planned.side, "decision_timestamp": str(intent["timestamp"]),
               "decision_available_at": str(decision_close), "fill_label": str(fill_label),
               "fill_latency_seconds": elapsed, "requested_quantity": record["requested"],
               "filled_quantity": quantity, "reference_price": float(planned.entry_price), "fill_price": price,
               "slippage_vs_reference": sign * (price - float(planned.entry_price)),
               "planned_stop": float(planned.stop_price), "planned_target": float(planned.target_price),
               "anchored_stop": float(plan.stop_price), "anchored_target": float(plan.target_price),
               "risk_per_share": risk, "worst_case_loss_rupees": worst_loss, "max_loss_per_trade_rupees": max_loss,
               "breaches": breaches, **bay_detail}
        trade["decision_timestamp"] = str(intent["timestamp"])
        trade["engine_c_entry_fills"] = list(record["fills"])
        self.entry_fills.append(row)
        self._record_controller_event("ENGINE_C_NEXT_OPEN_FILL", fill_label, symbol, row)
        for reason in breaches:
            self._arm_plant_trip(symbol, fill_label, trade, reason)

    def _break(self, kind: str, **detail) -> None:
        """Fail closed: halt new entries and cancel every working entry at the broker."""
        self._execution_halted = True
        for ref, record in list(self._working.items()):
            if record["kind"] == "entry" and not record.get("booked"):
                self.port.cancel(ref)

    # ---- end of replay --------------------------------------------------------------------
    def expected_position(self, symbol: str) -> int:
        """R5's view of the broker position: the booked trade plus whatever a still-working
        order for this symbol has filled (an entry is booked once complete; an exit removes the
        trade only once the whole position is flat)."""
        trade = self.open_trades.get(symbol)
        expected = 0 if trade is None else int(trade["quantity"]) * (1 if trade["side"] == "BUY" else -1)
        for record in self._working.values():
            if record["symbol"] != symbol or record.get("booked"):
                continue
            signed = int(record["filled"]) * (1 if record["side"] == "BUY" else -1)
            expected += signed
        return expected

    def _dispose_at_end(self) -> None:
        """Settle orders still working when the data ended.  Strategy.stop() cancelled every order
        Backtrader still held in its matching queue; an order submitted on the final bar never
        reached a broker cycle at all (Backtrader leaves it SUBMITTED).  No further bar can fill
        either kind, so neither is ever booked as filled."""
        for ref, record in list(self._working.items()):
            if record.get("booked"):
                self._release(ref, record)
                continue
            self.ended_refs.add(ref)
            self.counts[f"{record['kind']}_unfilled_at_end"] += 1
            self._audit(ref, record, "UNFILLED_AT_END", filled=record["filled"],
                        status_at_end=self.port.order_status(ref))
            if record["kind"] == "entry":
                if record["filled"] > 0:
                    self._complete(ref, record)
                else:
                    self._release(ref, record)
                    self._on_entry_order_result(record["intent"], {"passed": False, "order_id": f"bt-{ref}",
                                                                   "reasons": ["backtrader_unfilled: end_of_data"]})
            else:
                # The position (or its unfilled remainder) stays open and is reported as open.
                record["canceled_at_end"] = True

    def finish(self) -> Dict[str, Any]:
        self._dispose_at_end()
        self.reconciler.check("finish")
        ledger_gross_error = None
        try:
            report = self._finalize_run()
        except AssertionError:
            # _finalize_run asserts broker gross realized P&L == the R5 trade ledger's gross.
            ledger_gross_error = {"backtrader_gross": self._broker_realized_pnl(),
                                  "r5_ledger_gross": sum(t["pnl"] for t in self.completed_trades)}
            self.reconciler.record_break("ledger_vs_backtrader_gross_mismatch", **ledger_gross_error)
            report = None
        final = self.reconciler.final()
        return {"r5_report": report, "ledger_gross_error": ledger_gross_error, "final_reconciliation": final}
