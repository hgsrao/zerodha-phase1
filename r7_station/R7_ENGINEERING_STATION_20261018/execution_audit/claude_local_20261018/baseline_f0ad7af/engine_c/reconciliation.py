"""Engine C reconciliation: Backtrader-native accounting vs the R5 fill-derived ledger.

Three independent views are compared:

* Backtrader (the broker of record): ``broker.getposition`` size and average price, broker cash
  and value, and the P&L and commission Backtrader computed on every execution bit;
* ``FillLedger``: an R5-side ledger rebuilt from the fills R5 received, with positions, average
  prices, realized P&L and cash computed here and commissions from the R5 cost model
  (``revision2.transaction_costs.leg_cost``) -- not from Backtrader's commission scheme;
* the R5 trade ledger (``open_trades`` / ``completed_trades``) that the shared decision engine
  books from those fills.

Checks run after every execution, after every booking or order disposal, at every tick start and
at replay completion.  A mismatch outside tolerance is recorded with its values and makes the
orchestrator fail closed (new entries halted, working entries cancelled).  Differences that are a
documented accounting convention (the per-fill brokerage cap on partial fills) are reported as
``explained`` with their amount; they are never folded into a pass silently.
"""
from __future__ import annotations

from typing import Any, Dict, List

from revision2.transaction_costs import leg_cost

TOLERANCES = {
    "quantity": 0,              # shares, exact
    "price": 1e-6,              # INR per share
    "money": 1e-4,              # INR, float accumulation over a replay
}


class FillLedger:
    """Position/cash/P&L arithmetic rebuilt from fills, independent of Backtrader's broker."""

    def __init__(self, starting_cash: float):
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.positions: Dict[str, Dict[str, float]] = {}
        self.realized = 0.0
        self.commission = 0.0
        self.fills = 0

    def apply(self, symbol: str, side: str, quantity: int, price: float) -> None:
        signed = quantity if side == "BUY" else -quantity
        pos = self.positions.setdefault(symbol, {"qty": 0, "avg": 0.0})
        qty, avg = int(pos["qty"]), float(pos["avg"])
        if qty == 0 or (qty > 0) == (signed > 0):
            total = abs(qty) + quantity
            pos["avg"] = (abs(qty) * avg + quantity * price) / total
            pos["qty"] = qty + signed
        else:
            closed = min(quantity, abs(qty))
            self.realized += closed * (price - avg) * (1 if qty > 0 else -1)
            remaining = qty + signed
            pos["qty"] = remaining
            if remaining == 0:
                pos["avg"] = 0.0
            elif (remaining > 0) != (qty > 0):          # flipped through zero: new position opens at price
                pos["avg"] = price
        self.cash -= signed * price
        charge = leg_cost(price, quantity, side)
        self.commission += charge
        self.cash -= charge
        self.fills += 1

    def qty(self, symbol: str) -> int:
        return int(self.positions.get(symbol, {}).get("qty", 0))

    def avg(self, symbol: str) -> float:
        return float(self.positions.get(symbol, {}).get("avg", 0.0))


class Reconciler:
    def __init__(self, r5):
        self.r5 = r5
        self.checks = 0
        self.breaks: List[Dict[str, Any]] = []
        self.by_context: Dict[str, int] = {}

    @property
    def port(self):
        return self.r5.port

    def record_break(self, kind: str, **detail) -> None:
        self.breaks.append({"kind": kind, "label": str(self.port.now_label()) if self.port else None, **detail})
        self.r5._break(kind)

    def _count(self, context: str) -> None:
        self.checks += 1
        key = context.split(":")[0]
        self.by_context[key] = self.by_context.get(key, 0) + 1

    # ---- per symbol --------------------------------------------------------------------
    def check_symbol(self, symbol: str, context: str) -> None:
        self._count(context)
        held = self.port.net_position(symbol)
        rebuilt = self.r5.ledger.qty(symbol)
        expected = self.r5.expected_position(symbol)
        if not held == rebuilt == expected:
            self.record_break("position_mismatch", symbol=symbol, context=context, backtrader=held,
                              fill_ledger=rebuilt, r5=expected)
            return
        if held == 0:
            return
        bt_avg, ledger_avg = self.port.position_price(symbol), self.r5.ledger.avg(symbol)
        if abs(bt_avg - ledger_avg) > TOLERANCES["price"]:
            self.record_break("average_price_mismatch", symbol=symbol, context=context,
                              backtrader=bt_avg, fill_ledger=ledger_avg)
        trade = self.r5.open_trades.get(symbol)
        working = any(r["symbol"] == symbol and not r.get("booked") for r in self.r5._working.values())
        if trade is not None and not working and abs(float(trade["entry_price"]) - bt_avg) > TOLERANCES["price"]:
            self.record_break("r5_entry_price_mismatch", symbol=symbol, context=context,
                              backtrader=bt_avg, r5=float(trade["entry_price"]))

    # ---- account level -----------------------------------------------------------------
    def check(self, context: str) -> None:
        for symbol in self.r5.symbols:
            self.check_symbol(symbol, context)
        self._count(context + ":account")
        ledger = self.r5.ledger
        pairs = {
            "cash": (self.port.cash(), ledger.cash),
            "realized_gross": (self.port.realized_gross_pnl(), ledger.realized),
            "commission": (self.port.commissions_total(), ledger.commission),
        }
        for name, (native, rebuilt) in pairs.items():
            if abs(native - rebuilt) > TOLERANCES["money"]:
                self.record_break(f"{name}_mismatch", context=context, backtrader=native, fill_ledger=rebuilt,
                                  difference=native - rebuilt)
        working_bt = set(self.port.working_refs()) - self.r5.ended_refs   # settled by _dispose_at_end
        working_r5 = {ref for ref, r in self.r5._working.items() if not r.get("booked") and not r.get("canceled_at_end")}
        if working_bt != working_r5:
            self.record_break("outstanding_orders_mismatch", context=context,
                              backtrader=sorted(working_bt), r5=sorted(working_r5))

    # ---- replay completion -------------------------------------------------------------
    def final(self) -> Dict[str, Any]:
        r5, port = self.r5, self.port
        completed, open_trades = r5.completed_trades, r5.open_trades
        working_exit_realized = sum(f["pnl"] for r in r5._working.values()
                                    if r["kind"] == "exit" and not r.get("booked") for f in r["fills"])
        r5_gross = sum(t["pnl"] for t in completed)
        bt_realized = port.realized_gross_pnl()

        # R5 trade-ledger costs: completed round trips plus the entry leg of every open trade.
        r5_costs = sum(t["costs"] for t in completed) + sum(
            leg_cost(float(t["entry_price"]), int(t["quantity"]), t["side"]) for t in open_trades.values())
        bt_commission = port.commissions_total()
        per_fill_r5_model = r5.ledger.commission
        cap_effect = per_fill_r5_model - r5_costs       # per-fill vs per-leg brokerage cap (partial fills)

        unrealized_bt = port.unrealized_pnl()
        unrealized_r5 = sum((port.last_close(s) - float(t["entry_price"])) * int(t["quantity"])
                            * (1 if t["side"] == "BUY" else -1) for s, t in open_trades.items())
        value = port.value()
        rebuilt_value = r5.ledger.cash + sum(r5.ledger.qty(s) * port.last_close(s) for s in r5.symbols)
        equity_identity = r5.ledger.starting_cash + bt_realized - bt_commission + unrealized_bt

        closed_cap_effect = sum(float(t.get("engine_c_per_fill_costs", t["costs"])) - float(t["costs"]) for t in completed)
        bt_trades = port.closed_trades
        rows = {
            "realized_gross_vs_r5_trades": {"backtrader": bt_realized, "r5": r5_gross + working_exit_realized,
                                            "note": "Backtrader execution-bit P&L vs R5 trade-ledger gross "
                                                    "(+ realized P&L of exits still working)"},
            "commission_vs_r5_fill_model": {"backtrader": bt_commission, "r5": per_fill_r5_model,
                                            "note": "Backtrader commission scheme vs R5 leg_cost, both per fill"},
            "commission_vs_r5_trade_ledger": {"backtrader": bt_commission, "r5": r5_costs, "explained": cap_effect,
                                              "note": "R5 trade ledger charges leg_cost on each aggregated leg; a "
                                                      "partially filled order is charged per fill (Rs 20 brokerage "
                                                      "cap per fill).  Explained = per-fill minus per-leg charges."},
            "unrealized": {"backtrader": unrealized_bt, "r5": unrealized_r5},
            "cash": {"backtrader": port.cash(), "r5": r5.ledger.cash},
            "value": {"backtrader": value, "r5": rebuilt_value},
            "equity_identity": {"backtrader": value, "r5": equity_identity,
                                "note": "value == start + realized - commission + unrealized"},
            "closed_trade_count": {"backtrader": len(bt_trades), "r5": len(completed), "tolerance": 0},
            "closed_trade_gross": {"backtrader": sum(t["pnl"] for t in bt_trades), "r5": r5_gross},
            "closed_trade_net": {"backtrader": sum(t["pnlcomm"] for t in bt_trades),
                                 "r5": sum(t["net_pnl"] for t in completed), "explained": -closed_cap_effect,
                                 "note": "Backtrader pnlcomm carries per-fill commissions"},
        }
        for name, row in rows.items():
            row.setdefault("tolerance", TOLERANCES["money"])
            row["difference"] = row["backtrader"] - row["r5"]
            explained = float(row.get("explained", 0.0))
            if abs(row["difference"]) <= row["tolerance"]:
                row["status"] = "PASS"
            elif explained and abs(row["difference"] - explained) <= row["tolerance"]:
                row["status"] = "EXPLAINED"
            else:
                row["status"] = "BREAK"
                self.breaks.append({"kind": f"final_{name}",
                                    **{k: row[k] for k in ("backtrader", "r5", "difference")}})
        open_bt = sorted(s for s in r5.symbols if port.net_position(s) != 0)
        if open_bt != sorted(open_trades):
            self.breaks.append({"kind": "final_open_positions_mismatch", "backtrader": open_bt, "r5": sorted(open_trades)})
        return {
            "rows": rows,
            "open_positions": {s: {"quantity": port.net_position(s), "average_price": port.position_price(s),
                                   "last_close": port.last_close(s)} for s in open_bt},
            "outstanding_orders_at_end": port.outstanding_at_end,
            "tolerances": TOLERANCES,
            "status": "PASS" if not self.breaks else "BREAK",
        }
