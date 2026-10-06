# R5 existing-work forensic evidence package

Evidence only. No source or test file was modified, repaired, committed or pushed for this package. Probe scripts live outside the repository (session scratchpad) and import the unmodified source; their outputs are embedded verbatim. Where a probe needed a shim, it is labelled **PROBE SHIM**.

## PART 1 — Freeze and identify current state

```
$ pwd
/home/srinivas/projects/zerodha-r5-governor-refactor
[exit status 0]
```
```
$ git branch --show-current
feature/engine-ab-handoff-lifecycle
[exit status 0]
```
```
$ git rev-parse HEAD
411712afaecaeb44a6899a12db94ac547fef0186
[exit status 0]
```
```
$ git status --short
 M revision2_external/broker_adapter_kite.py
 M revision2_external/orchestrator.py
 M revision2_external/paper_execution.py
?? docs/experiment_outputs/steam/after/block1/
?? docs/experiment_outputs/steam/before/block1/
?? docs/r5_remediation_project/
?? outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md
?? outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md
?? outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md
?? outputs/block1_isolation_final/
?? outputs/diagnostics/
?? outputs/r5_remediation/
?? revision2_external/broker_reconciliation.py
?? revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_store.py
?? revision5/engine_b_management.py
?? revision5/handoff_manager.py
?? scripts/diagnostics/
?? tests/test_block1_isolation_harness.py
?? tests/test_broker_reconciliation_lifecycle.py
?? tests/test_combined_cycle_handoff.py
?? tests/test_combined_cycle_runtime.py
?? tests/test_engine_b_management.py
?? tests/test_paper_combined_cycle_adapter.py
[exit status 0]
```
```
$ git diff --stat
 revision2_external/broker_adapter_kite.py | 158 +++++++++++++++++++-----------
 revision2_external/orchestrator.py        |  15 ++-
 revision2_external/paper_execution.py     | 109 +++++++++++++++++++++
 3 files changed, 224 insertions(+), 58 deletions(-)
[exit status 0]
```
```
$ git diff --name-status
M	revision2_external/broker_adapter_kite.py
M	revision2_external/orchestrator.py
M	revision2_external/paper_execution.py
[exit status 0]
```
### Full diff of the three modified tracked source files

```
$ git diff -- revision2_external/orchestrator.py revision2_external/paper_execution.py revision2_external/broker_adapter_kite.py
diff --git a/revision2_external/broker_adapter_kite.py b/revision2_external/broker_adapter_kite.py
index 774e464..00f1812 100644
--- a/revision2_external/broker_adapter_kite.py
+++ b/revision2_external/broker_adapter_kite.py
@@ -1,27 +1,8 @@
-"""Box 10 (UnifiedExecution) -- real kiteconnect + tenacity broker adapter.
-
-runtime/operating_mode.py already has a KiteBrokerAdapter class, but it's
-an empty stub (environment="live", nothing else). This implements it for
-real against the official kiteconnect SDK -- the actual Zerodha broker
-this project targets, which speaks a REST + WebSocket API, not FIX (see
-this branch's own earlier review of the original 10-box proposal).
-
-tenacity provides retry/backoff for Zerodha's documented rate limits (10
-order placements/second, 3 modifications/second) -- transient network or
-429-style failures get retried with exponential backoff; a genuine
-rejection (bad symbol, insufficient margin) is NOT retried, since retrying
-a rejection just resubmits the same invalid order.
-
-NOT wired into calibration: calibration is an offline, historical exercise
-against frozen CSV bars -- there is no live broker call anywhere in that
-path, and this module is never imported by the calibration engine built in
-this branch. It exists as real, tested infrastructure for a future live
-milestone, gated the same way runtime/operating_mode.py's own
-OperatingMode.LIVE already is (StartupGate fails closed without
-live_trading_enabled=True, a signing key, and durable_db=True). No real
-Zerodha credentials exist in this environment; tests here use a mocked
-KiteConnect client to prove the retry/backoff and order-translation logic,
-not real order placement.
+"""Mockable Kite adapter with conservative reconciliation after ambiguous writes.
+
+Order acceptance is not a fill. Placement and conversion writes are never
+blindly retried; read-only history lookups may retry network failures.
+No adapter is instantiated or account contacted by calibration.
 """
 
 from __future__ import annotations
@@ -51,49 +32,114 @@ _retry_transient = retry(
 class KiteConnectBrokerAdapter(BrokerAdapter):
     environment = "live"
 
-    def __init__(self, api_key: str, access_token: str, account_id: Optional[str] = None) -> None:
+    def __init__(self, api_key: str = "", access_token: str = "", account_id: Optional[str] = None, *, client=None) -> None:
         super().__init__(account_id=account_id)
-        self.client = KiteConnect(api_key=api_key)
-        self.client.set_access_token(access_token)
+        self.client = client if client is not None else KiteConnect(api_key=api_key)
+        if client is None:
+            self.client.set_access_token(access_token)
 
-    @_retry_transient
-    def place_order(
-        self, symbol: str, side: str, quantity: int, order_type: str,
-        limit_price: Optional[float] = None, exchange: str = "NSE", product: str = "MIS",
-    ) -> Dict[str, Any]:
-        if side not in _SIDE_TO_TRANSACTION_TYPE:
-            return {"passed": False, "reason": f"invalid side: {side}"}
-        if order_type not in _ORDER_TYPE_MAP:
-            return {"passed": False, "reason": f"invalid order_type: {order_type}"}
-        if quantity <= 0:
-            return {"passed": False, "reason": "quantity must be positive"}
-
-        kwargs: Dict[str, Any] = dict(
-            variety=self.client.VARIETY_REGULAR,
-            exchange=exchange, tradingsymbol=symbol,
-            transaction_type=_SIDE_TO_TRANSACTION_TYPE[side],
-            quantity=int(quantity), order_type=_ORDER_TYPE_MAP[order_type],
-            product=product,
-        )
-        if order_type == "LIMIT":
+    def positions(self):
+        return self.client.positions()
+
+    def orders(self):
+        return self.client.orders()
+
+    def holdings(self):
+        return self.client.holdings()
+
+    def margins(self):
+        return self.client.margins()
+
+    def snapshot(self):
+        try:
+            return {"passed": True, "positions": self.positions(), "orders": self.orders(),
+                    "holdings": self.holdings(), "margins": self.margins()}
+        except Exception as exc:
+            return {"passed": False, "reason": str(exc)}
+
+    def place_order(self, symbol: str, side: str, quantity: int, order_type: str,
+                    limit_price: Optional[float] = None, exchange: str = "NSE", product: str = "MIS",
+                    correlation_id: Optional[str] = None):
+        if side not in _SIDE_TO_TRANSACTION_TYPE or order_type not in _ORDER_TYPE_MAP or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
+            return {"passed": False, "reason": "invalid order arguments"}
+        kwargs = dict(variety=self.client.VARIETY_REGULAR, exchange=exchange, tradingsymbol=symbol,
+                      transaction_type=side, quantity=int(quantity), order_type=order_type, product=product)
+        if correlation_id:
+            if not correlation_id.isalnum() or len(correlation_id) > 20:
+                return {"passed": False, "reason": "correlation_id must be alphanumeric and at most 20 characters"}
+            kwargs['tag'] = correlation_id
+            try:
+                matches = [o for o in self.orders() if o.get('tag') == correlation_id]
+            except Exception as exc:
+                return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": str(exc)}
+            if matches:
+                return self._correlated_receipt(matches, kwargs)
+        if order_type == 'LIMIT':
             if limit_price is None or limit_price <= 0:
                 return {"passed": False, "reason": "LIMIT order requires a positive limit_price"}
-            kwargs["price"] = float(limit_price)
-
+            kwargs['price'] = float(limit_price)
         try:
-            order_id = self.client.place_order(**kwargs)
-        except NetworkException:
-            raise  # let tenacity retry
+            oid = self.client.place_order(**kwargs)
+            return {"passed": True, "accepted": True, "filled": False, "order_id": oid, "status": "ACCEPTED"}
+        except (NetworkException, TimeoutError, ConnectionError) as exc:
+            if correlation_id:
+                try:
+                    matches = [o for o in self.orders() if o.get('tag') == correlation_id]
+                    if matches:
+                        return self._correlated_receipt(matches, kwargs)
+                except Exception:
+                    pass
+            return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": str(exc)}
         except KiteException as exc:
-            return {"passed": False, "reason": f"broker rejected order: {exc}"}
-        return {"passed": True, "order_id": order_id}
+            return {"passed": False, "accepted": False, "reason": str(exc)}
+
+    def _correlated_receipt(self, matches, request):
+        fields = ('exchange', 'tradingsymbol', 'transaction_type', 'quantity', 'order_type', 'product')
+        if len(matches) != 1 or any(str(matches[0].get(k)) != str(request[k]) for k in fields):
+            return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": "correlation collision or duplicate orders"}
+        o = matches[0]
+        status = o.get('status', 'UNKNOWN')
+        accepted = status in ('OPEN', 'TRIGGER PENDING', 'COMPLETE', 'PUT ORDER REQ RECEIVED', 'VALIDATION PENDING', 'OPEN PENDING', 'MODIFY PENDING', 'MODIFY VALIDATION PENDING', 'CANCEL PENDING', 'AMO REQ RECEIVED')
+        return {"passed": accepted, "accepted": accepted, "filled": status == 'COMPLETE',
+                "filled_quantity": int(o.get('filled_quantity', 0)), "order_id": o.get('order_id'),
+                "status": status, "reconciled": True, "retry_allowed": False}
+
+    def convert_position(self, symbol, quantity, old_product='MIS', new_product='CNC', exchange='NSE',
+                         transaction_type='BUY', correlation_id=None):
+        # Conversion is acknowledged only after broker product/quantity confirmation.
+        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or transaction_type not in ('BUY', 'SELL') or old_product == new_product:
+            return {"passed": False, "product_confirmed": False, "retry_allowed": False, "reason": "invalid conversion arguments"}
+        try:
+            before = self.positions().get('net', [])
+            signed = int(quantity) if transaction_type == 'BUY' else -int(quantity)
+            old = sum(int(p.get('quantity', 0)) for p in before if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == old_product)
+            new = sum(int(p.get('quantity', 0)) for p in before if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == new_product)
+            if quantity <= 0 or old * signed <= 0 or abs(old) < quantity:
+                return {"passed": False, "product_confirmed": False, "reason": "source position insufficient", "correlation_id": correlation_id}
+            try:
+                accepted = self.client.convert_position(exchange=exchange, tradingsymbol=symbol,
+                    transaction_type=transaction_type, position_type='day', quantity=int(quantity),
+                    old_product=old_product, new_product=new_product)
+            except (NetworkException, TimeoutError, ConnectionError):
+                accepted = None
+            after = self.positions().get('net', [])
+            aold = sum(int(p.get('quantity', 0)) for p in after if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == old_product)
+            anew = sum(int(p.get('quantity', 0)) for p in after if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == new_product)
+            confirmed = aold == old - signed and anew == new + signed
+            return {"passed": confirmed, "accepted": accepted, "product_confirmed": confirmed,
+                    "correlation_id": correlation_id, "retry_allowed": False,
+                    "confirmed_product": new_product if confirmed else None, "confirmed_quantity": quantity if confirmed else None}
+        except Exception as exc:
+            return {"passed": False, "product_confirmed": False, "retry_allowed": False, "reason": str(exc), "correlation_id": correlation_id}
 
     @_retry_transient
-    def order_status(self, order_id: str) -> Dict[str, Any]:
+    def order_status(self, order_id: str):
         try:
             history = self.client.order_history(order_id)
         except NetworkException:
             raise
         except KiteException as exc:
             return {"passed": False, "reason": str(exc)}
-        return {"passed": True, "history": history}
+        latest = history[-1] if history else {}
+        return {"passed": bool(history), "history": history, "status": latest.get('status'),
+                "filled": latest.get('status') == 'COMPLETE', "filled_quantity": latest.get('filled_quantity', 0)}
diff --git a/revision2_external/orchestrator.py b/revision2_external/orchestrator.py
index 40bc054..9f0d006 100644
--- a/revision2_external/orchestrator.py
+++ b/revision2_external/orchestrator.py
@@ -138,7 +138,9 @@ class Revision2ExternalEngineOrchestrator:
         real_plant_dcs: Optional[Any] = None,
         paper_journal: Optional[Any] = None,
         governor_authority: str = "advisory",
+        combined_cycle_runtime: Optional[Any] = None,
     ) -> None:
+        self.combined_cycle_runtime = combined_cycle_runtime
         # ``real_plant_dcs``: an optional, already-constructed native R5 ``CentralPlantMasterDCS``.
         # Default None -- unchanged behaviour: this replay engine does not instantiate a real plant,
         # so protection state uses the explicit SYMBOL_TRIPS_ONLY fallback (see protection_snapshot.py).
@@ -1064,11 +1066,15 @@ class Revision2ExternalEngineOrchestrator:
             position_id=trade["trade_id"], symbol=symbol, direction=trade["side"],
             initial_risk_r=abs(entry - float(trade["stop_price"])), anchor_price=entry,
             initial_stop_price=float(trade["stop_price"]), created_bar_timestamp=pd.Timestamp(timestamp))
+        if getattr(self, "combined_cycle_runtime", None) is not None:
+            self.combined_cycle_runtime.register_fill(self._position_lifecycle[trade["trade_id"]], trade, self.broker)
 
     def _close_position_lifecycle(self, trade: Dict[str, Any]) -> None:
         record = self._position_lifecycle.get(trade.get("trade_id"))
         if record is not None and record.is_open:
             self._position_lifecycle[trade["trade_id"]] = lifecycle.close_position(record)
+            if getattr(self, "combined_cycle_runtime", None) is not None:
+                self.combined_cycle_runtime.close(self._position_lifecycle[trade["trade_id"]], trade)
 
     def _owner_engine(self, trade: Dict[str, Any]) -> str:
         """Owning engine of an open trade; a trade without a lifecycle record is Engine A (intraday MIS)."""
@@ -1096,6 +1102,9 @@ class Revision2ExternalEngineOrchestrator:
         if state is not None:
             state.bars_held = int(held_bars)
         trade["_terminal_bar"] = dict(bar)
+        if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
+                self, symbol, timestamp, bar, session_last_bar):
+            return
         studies_direction = (chart_studies_audit or {}).get("direction")
         if studies_direction is not None and studies_direction != (1 if trade["side"] == "BUY" else -1):
             chart_studies_confidence = 0.0
@@ -1562,7 +1571,7 @@ class Revision2ExternalEngineOrchestrator:
                 self.consumed_parameters.update(
                     upstream.consumed_parameters
                 )
-                if not upstream.admitted:
+                if not upstream.admitted and not (getattr(self, "combined_cycle_runtime", None) is not None and symbol in self.open_trades):
                     continue
                 in_window = self._in_trading_window(str(timestamp))
 
@@ -1619,7 +1628,7 @@ class Revision2ExternalEngineOrchestrator:
                     chart_studies_confidence, composite_result,
                 )
 
-                if symbol in self.open_trades or not in_window or self._execution_halted:
+                if symbol in self.open_trades or not upstream.admitted or not in_window or self._execution_halted:
                     continue
                 if next_ts.date() != event_ts.date() or next_ts.strftime("%H:%M") >= str(self.safety_contract.values["no_entry_cutoff_time"]):
                     continue
@@ -2105,6 +2114,8 @@ class Revision2ExternalEngineOrchestrator:
                 self.paper_journal.checkpoint(self, timestamp)
 
         for symbol in list(self.open_trades.keys()):
+            if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.persist_end_of_run(self, symbol):
+                continue
             bars = symbol_bars[symbol]
             final_close = float(bars.iloc[len(bars) - 1]["close"])
             self._execute_exit(symbol, bars.iloc[len(bars) - 1].get("timestamp", ""), self.open_trades[symbol], final_close, "end_of_run_reconciliation")
diff --git a/revision2_external/paper_execution.py b/revision2_external/paper_execution.py
index 8a04975..84e1ecb 100644
--- a/revision2_external/paper_execution.py
+++ b/revision2_external/paper_execution.py
@@ -7,6 +7,8 @@ class CostedPaperBrokerAdapter(PaperBrokerAdapter):
         super().__init__(*args, **kwargs)
         self.booked_costs = 0.0
         self.cost_ledger = []
+        self._conversion_receipts = {}
+        self._contingent_protection = {}
 
     def place_order(self, symbol, side, quantity, order_type, market_price,
                     config=None, parameter_registry=None):
@@ -23,6 +25,113 @@ class CostedPaperBrokerAdapter(PaperBrokerAdapter):
             result["ack_elapsed_seconds"] = 0.0
         return result
 
+    def request_product_conversion(self, request_id, symbol, quantity,
+                                   from_product="MIS", to_product="CNC", timestamp=None):
+        """Paper-only idempotent conversion; never a second fill or cost event."""
+        import pandas as pd
+        request = (symbol, int(quantity), from_product, to_product)
+        previous = self._conversion_receipts.get(request_id)
+        if previous:
+            if tuple(previous["request"]) != request:
+                raise ValueError("Conversion request identity changed")
+            return dict(previous)
+        position = self.get_position(symbol)
+        passed = (from_product == "MIS" and to_product == "CNC"
+                  and int(quantity) > 0 and position["quantity"] == int(quantity)
+                  and position.get("product", "MIS") == from_product)
+        if passed:
+            position["product"] = to_product
+        receipt = {"request_id": request_id, "request": list(request),
+                   "status": "ACKNOWLEDGED" if passed else "REJECTED",
+                   "product": position.get("product", "MIS"),
+                   "quantity": int(position["quantity"]),
+                   "timestamp": pd.Timestamp(timestamp).isoformat() if timestamp is not None else None,
+                   "environment": "paper"}
+        self._conversion_receipts[request_id] = receipt
+        return dict(receipt)
+
+    def conversion_receipt(self, request_id):
+        receipt = self._conversion_receipts.get(request_id)
+        return dict(receipt) if receipt else None
+
+    def ensure_protection(self, symbol, stop_price, quantity, product="MIS"):
+        """Track simulated contingent protection; orchestrator executes gap/bar stops.
+
+        This is not an exchange order and offers no real overnight guarantee.
+        """
+        import math
+        if not math.isfinite(float(stop_price)) or stop_price <= 0 or int(quantity) <= 0:
+            raise ValueError("Invalid paper protection")
+        position = self.get_position(symbol)
+        if abs(position["quantity"]) != int(quantity) or position.get("product", "MIS") != product:
+            raise ValueError("Paper protection does not match position")
+        oid = "paper-protection-" + symbol
+        self._contingent_protection[oid] = {
+            "order_id": oid, "tradingsymbol": symbol, "exchange": "NSE",
+            "product": product, "quantity": int(quantity), "pending_quantity": int(quantity),
+            "filled_quantity": 0, "transaction_type": "SELL" if position["quantity"] > 0 else "BUY",
+            "order_type": "SL-M", "status": "TRIGGER PENDING", "validity": "SIMULATED",
+            "trigger_price": float(stop_price), "environment": "paper"}
+        return oid
+
+    def snapshot(self):
+        """JSON-safe paper state and broker-truth-shaped reconciliation view."""
+        from dataclasses import asdict
+        import json
+        orders = []
+        serialized_orders = {}
+        for oid, order in self.orders.items():
+            row = asdict(order)
+            row["state"] = order.state.value
+            serialized_orders[oid] = row
+            orders.append({"order_id": oid, "tradingsymbol": order.symbol, "exchange": "NSE",
+                           "product": self.get_position(order.symbol).get("product", "MIS"),
+                           "status": "COMPLETE" if order.filled_quantity == order.quantity else "REJECTED",
+                           "quantity": order.quantity, "filled_quantity": order.filled_quantity})
+        for oid, protection in self._contingent_protection.items():
+            if self.get_position(protection["tradingsymbol"])["quantity"]:
+                orders.append(dict(protection))
+        payload = {"passed": True, "environment": "paper",
+                   "positions": [{"tradingsymbol": symbol, "exchange": "NSE", **position,
+                                  "product": position.get("product", "MIS")}
+                                 for symbol, position in self.positions.items()],
+                   "orders": orders, "holdings": [], "margins": {},
+                   "paper_state": {"positions": self.positions, "orders": serialized_orders,
+                                   "fills": self.fills, "realized_pnl": self.realized_pnl,
+                                   "booked_costs": self.booked_costs, "cost_ledger": self.cost_ledger,
+                                   "conversions": self._conversion_receipts,
+                                   "protection": self._contingent_protection,
+                                   "slippage_fraction": self.slippage_fraction}}
+        return json.loads(json.dumps(payload))
+
+    def restore_snapshot(self, snapshot):
+        """Restore only explicitly identified paper state into an empty paper adapter."""
+        import copy
+        import math
+        from runtime.operating_mode import PaperOrder, OrderState
+        if self.orders or self.fills or self.positions:
+            raise ValueError("Restore requires an empty paper broker")
+        if snapshot.get("environment") != "paper" or not snapshot.get("passed"):
+            raise ValueError("Not an authenticated paper snapshot")
+        state = copy.deepcopy(snapshot["paper_state"])
+        if state["slippage_fraction"] != self.slippage_fraction:
+            raise ValueError("Paper fill model changed")
+        for p in state["positions"].values():
+            if not math.isfinite(float(p["avg_price"])) or int(p["quantity"]) != p["quantity"]:
+                raise ValueError("Invalid paper position snapshot")
+        orders = {}
+        for oid, row in state["orders"].items():
+            row["state"] = OrderState(row["state"])
+            orders[oid] = PaperOrder(**row)
+        self.positions = state["positions"]
+        self.orders = orders
+        self.fills = state["fills"]
+        self.realized_pnl = state["realized_pnl"]
+        self.booked_costs = state["booked_costs"]
+        self.cost_ledger = state["cost_ledger"]
+        self._conversion_receipts = state["conversions"]
+        self._contingent_protection = state["protection"]
+
 
 class ReplayIntentLedger:
     """Event-time deduplication, never wall-clock based."""
[exit status 0]
```
### SHA256 of audited source/test files (initial evidence set)

```
2a45ddc3d33a59065d130b33514d39f5536c1133b654170cc8ed75ff9b6f6705  revision2_external/broker_adapter_kite.py
e29348cad9b352a415226793171853aec37fa2ae817cc5fe76b6d7e4f5b53b55  revision2_external/broker_reconciliation.py
c32672308254fc73169f4924216b90b5e587cfbc3a0f6d879207592ab7810a87  revision2_external/orchestrator.py
2f301d0fb261675fc9f0cac6e5af4171d9d8ff22107f2a03735a2a717d1eaa15  revision2_external/paper_execution.py
8432563f9f0bd16a9bd4a2156dcb6cf27ccd7561d906616b73629d60173d1ec9  revision5/combined_cycle_runtime.py
44a1f15de7db53a1ae0fd1ea989960bf82ec49d09fe38d984ed6f757945d6cc3  revision5/combined_cycle_store.py
10489260037910cf153312c11ce048b1b8c56c5a28c31c9f7d1e771fc37d9a1b  revision5/engine_b_management.py
91139eeae0cf8294064e13c5e6b54bfb3d82b42c623482701f185b4e23acdce9  revision5/handoff_manager.py
34b8f94b9026604fe51fa5159f5bfc9ebaff26b687f10711d592b0af8c8da5e3  scripts/diagnostics/block1_arm_worker.py
274373ddc267e5f9bee8c340337827d60e1151c9b202e7e82fbe0e0cb6600455  scripts/diagnostics/block1_audit_report.py
f2d33e9643bf65a0a764f7be5a202314e24f128aa655757f07ab8aab2f9eb8bd  scripts/diagnostics/block1_isolation_harness.py
4c253cfa6b9064cb45faed99d32dd845f6b0e2290f31be00570faab5668e16ab  scripts/diagnostics/isolation_lib.py
bc0e3ffc02fcce2fc0cfef0d4c35ca30bd52b33c13252f67998f7d0f2a45645b  tests/test_block1_isolation_harness.py
3ff724cdc6d957f21a1f62cb69179d6a193abf5e9d385c791b2eeb6bd8569001  tests/test_broker_reconciliation_lifecycle.py
114b69a80f073eeec1db1ff23c7dbfb97de52dbb4ed7dc15bf0c8c7aec2f3a5b  tests/test_combined_cycle_handoff.py
3a542b035d8d360b8ff4d488292bb72335e523f573ee902b8a08fb470995b9fd  tests/test_combined_cycle_runtime.py
7d0546e8dbd09fda80d6dd84c4b94a0d6293390348b74a223a3456d64927e7d1  tests/test_engine_b_management.py
5e4d4516e8d27576664a0b5b024c11393b880f0ad69afd04eb4a403dc59819b8  tests/test_paper_combined_cycle_adapter.py
```
Untracked, non-source (listed in Part 16): outputs/, docs/, harness data. Initial `git status --short` and `git diff --stat` were also saved before any probe ran:

```
 M revision2_external/broker_adapter_kite.py
 M revision2_external/orchestrator.py
 M revision2_external/paper_execution.py
?? docs/experiment_outputs/steam/after/block1/
?? docs/experiment_outputs/steam/before/block1/
?? docs/r5_remediation_project/
?? outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md
?? outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md
?? outputs/block1_isolation_final/
?? outputs/diagnostics/
?? outputs/r5_remediation/
?? revision2_external/broker_reconciliation.py
?? revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_store.py
?? revision5/engine_b_management.py
?? revision5/handoff_manager.py
?? scripts/diagnostics/
?? tests/test_block1_isolation_harness.py
?? tests/test_broker_reconciliation_lifecycle.py
?? tests/test_combined_cycle_handoff.py
?? tests/test_combined_cycle_runtime.py
?? tests/test_engine_b_management.py
?? tests/test_paper_combined_cycle_adapter.py

 revision2_external/broker_adapter_kite.py | 158 +++++++++++++++++++-----------
 revision2_external/orchestrator.py        |  15 ++-
 revision2_external/paper_execution.py     | 109 +++++++++++++++++++++
 3 files changed, 224 insertions(+), 58 deletions(-)
```
## PART 2 — What was actually built

#### FILE: `revision5/handoff_manager.py`
- PURPOSE: durable two-phase A->B ownership transfer (request, resolve) plus qualification gate
- LINES: 117
- PUBLIC CLASSES: HandoffConfig, ConversionRequest, HandoffManager
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: HandoffManager.qualifies, HandoffManager.request, HandoffManager.resolve
- STATE MUTATED: SQLite rows `positions` (lifecycle record) and `conversions` (request/receipt) via the store; no in-memory engine state
- EXTERNAL SIDE EFFECTS: none (no broker call; docstring: 'sends no orders and realizes no P&L')
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): ./revision5/combined_cycle_runtime.py
- CALLEES: CombinedCycleStore._save/.load/.connection, position_lifecycle.request_transfer/acknowledge_transfer/reject_transfer, uuid4
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_combined_cycle_runtime.py, tests/test_combined_cycle_handoff.py
- tests named in this package's Part 13: tests/test_combined_cycle_handoff.py; indirectly tests/test_combined_cycle_runtime.py

#### FILE: `revision5/combined_cycle_store.py`
- PURPOSE: crash-safe SQLite persistence of lifecycle records, trade dicts, protection receipts and conversion rows
- LINES: 90
- PUBLIC CLASSES: RuntimeSnapshot, CombinedCycleStore
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: CombinedCycleStore.close, CombinedCycleStore.load, CombinedCycleStore.list_open, CombinedCycleStore.save, CombinedCycleStore.requests
- STATE MUTATED: SQLite file (WAL, synchronous=FULL): tables `positions`, `conversions`
- EXTERNAL SIDE EFFECTS: disk writes; opens a sqlite3 connection at construction
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): ./revision5/handoff_manager.py
- CALLEES: sqlite3, json, pandas, position_lifecycle
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_combined_cycle_runtime.py, tests/test_combined_cycle_handoff.py
- tests named in this package's Part 13: tests/test_combined_cycle_handoff.py, tests/test_combined_cycle_runtime.py

#### FILE: `revision5/combined_cycle_runtime.py`
- PURPOSE: bridge that runs reconciliation, stop ratchet, handoff, Engine B management and protective exits per bar via an orchestrator hook
- LINES: 198
- PUBLIC CLASSES: CombinedCycleReconciliationError, CombinedCycleRuntimeConfig, CombinedCycleRuntime
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: CombinedCycleRuntime.register_fill, CombinedCycleRuntime.close, CombinedCycleRuntime.reconcile, CombinedCycleRuntime.restore, CombinedCycleRuntime.handle_bar, CombinedCycleRuntime.persist_end_of_run
- STATE MUTATED: engine.open_trades[...] (via _sync/restore), engine._position_lifecycle, trade dict keys (controller_exit_pending removed, engine_b_exit_pending set), durable store rows
- EXTERNAL SIDE EFFECTS: calls broker.ensure_protection, broker.request_product_conversion and engine._execute_exit (paper broker order submission); raises CombinedCycleReconciliationError
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): ./revision2_external/orchestrator.py
- CALLEES: CombinedCycleStore, HandoffManager, EngineBController, broker (get_position, snapshot, ensure_protection, request_product_conversion, conversion_receipt), engine._execute_exit
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_combined_cycle_runtime.py
- tests named in this package's Part 13: tests/test_combined_cycle_runtime.py

#### FILE: `revision5/engine_b_management.py`
- PURPOSE: Engine B positional management policy: structural reversal, trailing stop proposal, session horizon
- LINES: 111
- PUBLIC CLASSES: EngineBPolicy, EngineBDecision, EngineBController
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: EngineBController.export_state, EngineBController.restore_state, EngineBController.evaluate
- STATE MUTATED: controller._states (in-memory per position_id), exportable via export_state
- EXTERNAL SIDE EFFECTS: none
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): none
- CALLEES: pandas only
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_combined_cycle_runtime.py, tests/test_engine_b_management.py
- tests named in this package's Part 13: tests/test_engine_b_management.py; indirectly tests/test_combined_cycle_runtime.py

#### FILE: `revision2_external/broker_reconciliation.py`
- PURPOSE: read-only comparison of runtime position records with a broker snapshot (positions, holdings, protective orders)
- LINES: 83
- PUBLIC CLASSES: ReconciliationReceipt, BrokerReconciliationService
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: ReconciliationReceipt.to_dict, BrokerReconciliationService.reconcile
- STATE MUTATED: none (pure function over inputs)
- EXTERNAL SIDE EFFECTS: none
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): none
- CALLEES: none
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_paper_combined_cycle_adapter.py, tests/test_broker_reconciliation_lifecycle.py
- tests named in this package's Part 13: tests/test_paper_combined_cycle_adapter.py, tests/test_broker_reconciliation_lifecycle.py

#### FILE: `revision2_external/orchestrator.py`
- PURPOSE: replay orchestrator; this diff adds the optional `combined_cycle_runtime` hooks (plus the committed Phase 1 lifecycle helpers)
- LINES: 2259
- PUBLIC CLASSES: ExternalEngineStartupNotCertifiedError, PositionReconciliationError, Revision2ExternalEngineOrchestrator
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: Revision2ExternalEngineOrchestrator.prepare_market_data, Revision2ExternalEngineOrchestrator.build_clock, Revision2ExternalEngineOrchestrator.rank_simultaneous_entry_candidates, Revision2ExternalEngineOrchestrator.run
- STATE MUTATED: engine.open_trades, engine._position_lifecycle, completed_trades, broker (via runtime)
- EXTERNAL SIDE EFFECTS: paper broker orders (existing)
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): ./canonical_parameter_registry.py, ./scripts/run_r5_step5_candidate.py, ./scripts/deploy_48_optimized.py, ./scripts/run_infy_maruti_3year_real.py, ./scripts/production_backtest_complete.py, ./scripts/run_master_control_maruti.py, ./scripts/analyze_chart_studies_intraday.py, ./scripts/run_revision3_external_48symbol_FULL_3YEAR_calibration.py, ./scripts/test_10box_optimized.py, ./scripts/run_external_no_pid_one_signal_trace.py
- CALLEES: combined_cycle_runtime.register_fill/close/handle_bar/persist_end_of_run
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_revision2_sensitivity.py, tests/test_r5_governor_authority.py, tests/test_revision2_optimizer.py, tests/test_revision5_engine_applicability.py, tests/test_revision2_calibration_supervisor.py, tests/test_r5_h1_h5_remediation.py, tests/test_r5_governor_refactor.py, tests/test_combined_cycle_runtime.py, tests/test_revision5_bb09_bb10_parameterization.py, tests/test_revision2_pipeline.py
- tests named in this package's Part 13: tests/test_combined_cycle_runtime.py (via __new__), tests/test_position_lifecycle_contract.py

#### FILE: `revision2_external/paper_execution.py`
- PURPOSE: paper-only conversion, simulated contingent protection, snapshot/restore for CostedPaperBrokerAdapter
- LINES: 152
- PUBLIC CLASSES: CostedPaperBrokerAdapter, ReplayIntentLedger
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: CostedPaperBrokerAdapter.place_order, CostedPaperBrokerAdapter.request_product_conversion, CostedPaperBrokerAdapter.conversion_receipt, CostedPaperBrokerAdapter.ensure_protection, CostedPaperBrokerAdapter.snapshot, CostedPaperBrokerAdapter.restore_snapshot, ReplayIntentLedger.seen_recent, ReplayIntentLedger.record
- STATE MUTATED: adapter.positions[symbol]['product'], _conversion_receipts, _contingent_protection, and via restore_snapshot all adapter state
- EXTERNAL SIDE EFFECTS: none (paper)
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): ./scripts/run_external_no_pid_one_signal_trace.py, ./scripts/run_external_preentry_deferral_one_signal_shadow.py, ./revision2_external/orchestrator.py, ./revision2_external/final_exit_authority_audit.py
- CALLEES: runtime.operating_mode.PaperBrokerAdapter, pandas, json
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_revision5_bb09_bb10_parameterization.py, tests/test_revision5_plant_control.py, tests/test_paper_combined_cycle_adapter.py, tests_external/test_audit_remediation.py
- tests named in this package's Part 13: tests/test_paper_combined_cycle_adapter.py

#### FILE: `revision2_external/broker_adapter_kite.py`
- PURPOSE: mockable Kite adapter: order placement with tag reconciliation, position conversion with confirmation, read-only snapshot
- LINES: 145
- PUBLIC CLASSES: KiteConnectBrokerAdapter
- PUBLIC FUNCTIONS: none
- PUBLIC METHODS: KiteConnectBrokerAdapter.positions, KiteConnectBrokerAdapter.orders, KiteConnectBrokerAdapter.holdings, KiteConnectBrokerAdapter.margins, KiteConnectBrokerAdapter.snapshot, KiteConnectBrokerAdapter.place_order, KiteConnectBrokerAdapter.convert_position, KiteConnectBrokerAdapter.order_status
- STATE MUTATED: none locally; delegates to `client`
- EXTERNAL SIDE EFFECTS: REAL BROKER WRITES when `client` is a live KiteConnect: client.place_order, client.convert_position (guarded only by caller construction)
- CALLERS FOUND (production, by module-name grep outside tests/outputs/docs): ./revision2_external/orchestrator.py
- CALLEES: KiteConnect client methods: place_order, orders, positions, holdings, margins, convert_position, order_history
- TESTS THAT DIRECTLY EXERCISE IT (module-name grep in tests/, tests_external/): tests/test_broker_reconciliation_lifecycle.py, tests_external/test_broker_adapter_kite.py
- tests named in this package's Part 13: tests/test_broker_reconciliation_lifecycle.py, tests_external/test_broker_adapter_kite.py

### Code excerpts: state transitions and side effects (verbatim, current working tree)

**HandoffManager.request (A_OPEN -> TRANSFER_REQUESTED, one SQLite transaction)** `revision5/handoff_manager.py:55-76`
```python
   55      def request(self, position_id, quantity, timestamp, *, current_r, mfe_r, trend_aligned, completed_bar=True):
   56          snapshot = self.store.load(position_id)
   57          existing = self.store.connection.execute('SELECT payload FROM conversions WHERE position_id=?',(position_id,)).fetchone()
   58          if existing:
   59              return ConversionRequest(**{k:v for k,v in json.loads(existing[0],object_hook=_hook).items() if k in ConversionRequest.__dataclass_fields__})
   60          if not self.qualifies(snapshot.record,timestamp,current_r,mfe_r,trend_aligned,completed_bar):
   61              return None
   62          if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity <= 0:
   63              raise ValueError('positive integer quantity required')
   64          request = ConversionRequest(uuid4().hex, position_id,snapshot.record.symbol,quantity,self._local(timestamp))
   65          db = self.store.connection
   66          db.execute('BEGIN IMMEDIATE')
   67          try:
   68              self.store._save(request_transfer(snapshot.record),snapshot.trade,snapshot.protection,snapshot.revision)
   69              db.execute('INSERT INTO conversions VALUES (?,?,?)',(request.request_id,position_id,json.dumps(asdict(request),default=_default)))
   70              db.execute('COMMIT')
   71          except BaseException:
   72              db.execute('ROLLBACK')
   73              raise
   74          return request
   75  
   76      def resolve(self, request_id, status, product=None, quantity=None, timestamp=None):
```
**HandoffManager.resolve (TRANSFER_REQUESTED -> B_OPEN / A_OPEN / CLOSED_RACE)** `revision5/handoff_manager.py:76-117`
```python
   76      def resolve(self, request_id, status, product=None, quantity=None, timestamp=None):
   77          db = self.store.connection
   78          db.execute('BEGIN IMMEDIATE')
   79          try:
   80              row = db.execute('SELECT payload FROM conversions WHERE request_id=?',(request_id,)).fetchone()
   81              if row is None:
   82                  raise KeyError(request_id)
   83              receipt = json.loads(row[0],object_hook=_hook)
   84              snapshot = self.store.load(receipt['position_id'])
   85              if receipt['status'] != 'PENDING':
   86                  db.execute('COMMIT')
   87                  return snapshot
   88              record = snapshot.record
   89              outcome = status.upper()
   90              if outcome in ('UNKNOWN','PENDING'):
   91                  db.execute('COMMIT')
   92                  return snapshot
   93              if outcome not in ('ACKNOWLEDGED','REJECTED','TIMEOUT'):
   94                  raise ValueError('unknown conversion outcome')
   95              if record.lifecycle_state == CLOSED:
   96                  receipt['status'] = 'CLOSED_RACE'
   97              elif record.lifecycle_state != TRANSFER_REQUESTED:
   98                  raise ValueError('conversion has no pending lifecycle')
   99              elif outcome == 'ACKNOWLEDGED':
  100                  if timestamp is None or product != 'CNC' or quantity != receipt['quantity']:
  101                      raise ValueError('acknowledgement requires exact product, quantity and timestamp')
  102                  stamp = self._local(timestamp)
  103                  if stamp.date() != receipt['requested_at'].date() or stamp < receipt['requested_at'] or stamp.strftime('%H:%M') > self.config.deadline:
  104                      raise ValueError('late/invalid acknowledgement: reconcile adapter before closing')
  105                  record = acknowledge_transfer(record)
  106                  receipt['status'] = outcome
  107              else:
  108                  record = reject_transfer(record)
  109                  receipt['status'] = outcome
  110              receipt['resolved_at'] = timestamp
  111              self.store._save(record,snapshot.trade,snapshot.protection,snapshot.revision)
  112              db.execute('UPDATE conversions SET payload=? WHERE request_id=?',(json.dumps(receipt,default=_default),request_id))
  113              db.execute('COMMIT')
  114              return self.store.load(record.position_id)
  115          except BaseException:
  116              db.execute('ROLLBACK')
  117              raise
```
**CombinedCycleStore.save/_save (optimistic revision, durable legality checks)** `revision5/combined_cycle_store.py:60-90`
```python
   60      def save(self, record, trade, protection, expected_revision=None):
   61          self.connection.execute('BEGIN IMMEDIATE')
   62          try:
   63              revision = self._save(record, trade, protection, expected_revision)
   64              self.connection.execute('COMMIT')
   65              return revision
   66          except BaseException:
   67              self.connection.execute('ROLLBACK')
   68              raise
   69  
   70      def _save(self, record, trade, protection, expected_revision=None):
   71          row = self.connection.execute('SELECT revision FROM positions WHERE id=?', (record.position_id,)).fetchone()
   72          actual = row[0] if row else 0
   73          if expected_revision is not None and actual != expected_revision:
   74              raise ValueError('stale snapshot revision')
   75          if row:
   76              old = self.load(record.position_id).record
   77              for field in ('symbol','direction','anchor_price','initial_risk_r','created_bar_timestamp'):
   78                  if getattr(old, field) != getattr(record, field):
   79                      raise ValueError('position identity/risk cannot change')
   80              if record.lifecycle_state != old.lifecycle_state and record.lifecycle_state not in LEGAL_TRANSITIONS[old.lifecycle_state]:
   81                  raise ValueError('illegal durable lifecycle transition')
   82              if (record.current_stop_price < old.current_stop_price if record.direction == 'BUY'
   83                      else record.current_stop_price > old.current_stop_price):
   84                  raise ValueError('durable stop cannot loosen')
   85          payload = json.dumps(dict(record=asdict(record), trade=trade, protection=protection), default=_default, allow_nan=False)
   86          self.connection.execute('INSERT OR REPLACE INTO positions VALUES (?,?,?)', (record.position_id,payload,actual+1))
   87          return actual+1
   88  
   89      def requests(self):
   90          return [json.loads(row[0], object_hook=_hook) for row in self.connection.execute('SELECT payload FROM conversions')]
```
**store schema (UNIQUE position_id on conversions)** `revision5/combined_cycle_store.py:41-44`
```python
   41          self.connection.executescript('''CREATE TABLE IF NOT EXISTS positions
   42              (id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL);
   43              CREATE TABLE IF NOT EXISTS conversions
   44              (request_id TEXT PRIMARY KEY, position_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);''')
```
**CombinedCycleRuntime.register_fill / close** `revision5/combined_cycle_runtime.py:34-41`
```python
   34      def register_fill(self, record, trade, broker):
   35          protective_id=broker.ensure_protection(record.symbol,record.current_stop_price,int(trade['quantity']),record.product)
   36          self.store.save(record, dict(trade), {'protective_order_id':protective_id,'broker_snapshot':broker.snapshot(),'stop':record.current_stop_price,'target':float(trade['target_price']),
   37                                                'sessions':[], 'engine_b_state':self.engine_b.export_state()},expected_revision=0)
   38  
   39      def close(self, record, trade):
   40          snapshot=self.store.load(record.position_id)
   41          self.store.save(record,dict(trade),snapshot.protection,snapshot.revision)
```
**CombinedCycleRuntime.handle_bar and persist_end_of_run (complete)** `revision5/combined_cycle_runtime.py:93-198`
```python
   93      def handle_bar(self, engine, symbol, timestamp, bar, session_last_bar):
   94          trade=engine.open_trades[symbol]
   95          snapshot=self.store.load(trade['trade_id'])
   96          record=snapshot.record
   97          if record.lifecycle_state==A_OPEN:
   98              effective_stop=float(trade.get('governor_stop_price',trade['stop_price']))
   99              exit_state=engine._exit_controller_states.get(symbol)
  100              if not getattr(engine,'_governor_full',False) and getattr(engine,'closed_loop_mode',None)=='active_paper' and exit_state is not None:
  101                  effective_stop=float(exit_state.current_stop_price)
  102              if (effective_stop>record.current_stop_price if record.direction=='BUY' else effective_stop<record.current_stop_price):
  103                  record=tighten_stop(record,effective_stop)
  104                  protection=dict(snapshot.protection);protection['stop']=effective_stop
  105                  protection['protective_order_id']=engine.broker.ensure_protection(symbol,effective_stop,int(trade['quantity']),record.product)
  106                  protection['broker_snapshot']=engine.broker.snapshot()
  107                  self.store.save(record,dict(trade),protection,snapshot.revision)
  108                  snapshot=self.store.load(record.position_id)
  109                  engine._position_lifecycle[record.position_id]=record
  110          if record.lifecycle_state==TRANSFER_REQUESTED:
  111              from revision5.handoff_manager import ConversionRequest
  112              data=next(x for x in self.store.requests() if x['position_id']==record.position_id)
  113              request=ConversionRequest(**{k:v for k,v in data.items() if k in ConversionRequest.__dataclass_fields__})
  114              snapshot=self._receipt(engine,request,timestamp); self._sync(engine,snapshot); record=snapshot.record
  115          self.reconcile(engine,snapshot)
  116          # Preserve mandatory portfolio protection before every discretionary controller.
  117          halt=min(float(engine.config.require('drawdown_halt_threshold')),float(engine.safety_contract.values['safety_drawdown_halt_threshold']))
  118          if engine._current_drawdown()>=halt:
  119              engine._execute_exit(symbol,timestamp,trade,float(bar['close']),'forced_close_drawdown_halt');return True
  120          if engine._micom_trip is not None and engine._micom_trip['ansi_code']!='MICOM_INPUT_UNAVAILABLE':
  121              engine._execute_exit(symbol,timestamp,trade,float(bar['close']),f"micom_trip:{engine._micom_trip['ansi_code']}");return True
  122          stop=record.current_stop_price
  123          target=float(trade['target_price'])
  124          price,reason=None,None
  125          if record.direction=='BUY':
  126              if float(bar['open'])<=stop: price,reason=float(bar['open']),'stop_gap'
  127              elif float(bar['open'])>=target: price,reason=float(bar['open']),'target_gap'
  128              elif float(bar['low'])<=stop: price,reason=stop,'stop'
  129              elif float(bar['high'])>=target: price,reason=target,'target'
  130          else:
  131              if float(bar['open'])>=stop: price,reason=float(bar['open']),'stop_gap'
  132              elif float(bar['open'])<=target: price,reason=float(bar['open']),'target_gap'
  133              elif float(bar['high'])>=stop: price,reason=stop,'stop'
  134              elif float(bar['low'])<=target: price,reason=target,'target'
  135          if price is not None:
  136              engine._execute_exit(symbol,timestamp,trade,price,reason);return True
  137          protection=dict(snapshot.protection)
  138          history=protection.get('history',[])
  139          ts=pd.Timestamp(timestamp)
  140          if not history or pd.Timestamp(history[-1]['timestamp'])<ts:
  141              history=history+[dict(timestamp=ts,high=float(bar['high']),low=float(bar['low']),close=float(bar['close']))]
  142          protection['history']=history[-max(100,self.engine_b.policy.structural_window+1):]
  143          day=ts.date().isoformat()
  144          sessions=list(protection.get('sessions',[]))
  145          if day not in sessions: sessions.append(day)
  146          protection['sessions']=sessions
  147          mfe=max(float(protection.get('mfe_r',0)),(float(bar['high'])-record.anchor_price)/record.initial_risk_r)
  148          protection['mfe_r']=mfe
  149          if record.lifecycle_state==A_OPEN:
  150              # Causal trend reference consists exclusively of prior completed closes.
  151              prior=[item['close'] for item in history[:-1]][-self.engine_b.policy.structural_window:]
  152              aligned=len(prior)==self.engine_b.policy.structural_window and float(bar['close'])>=sum(prior)/len(prior)
  153              current_r=(float(bar['close'])-record.anchor_price)/record.initial_risk_r
  154              self.store.save(record,dict(trade),protection,snapshot.revision)
  155              request=self.handoff.request(record.position_id,int(trade['quantity']),ts,current_r=current_r,mfe_r=mfe,trend_aligned=bool(aligned))
  156              if request is None: return False
  157              if request.status=='PENDING':
  158                  engine.broker.request_product_conversion(request.request_id,symbol,request.quantity,from_product='MIS',to_product='CNC',timestamp=ts)
  159                  snapshot=self._receipt(engine,request,ts)
  160                  self._sync(engine,snapshot);record=snapshot.record
  161                  if record.lifecycle_state!=B_OPEN: return False
  162                  trade.pop('controller_exit_pending',None)
  163                  protection=dict(snapshot.protection)
  164                  protection['protective_order_id']=engine.broker.ensure_protection(symbol,record.current_stop_price,int(trade['quantity']),record.product)
  165                  protection['broker_snapshot']=engine.broker.snapshot()
  166                  self.store.save(record,dict(trade),protection,snapshot.revision)
  167                  snapshot=self.store.load(record.position_id)
  168                  self.reconcile(engine,snapshot)
  169              else: return False
  170          else:
  171              self.store.save(record,dict(trade),protection,snapshot.revision)
  172          snapshot=self.store.load(record.position_id)
  173          protection=dict(snapshot.protection)
  174          pending=trade.get('engine_b_exit_pending')
  175          if pending is not None and pd.Timestamp(pending['timestamp'])<ts:
  176              engine._execute_exit(symbol,timestamp,trade,float(bar['open']),pending['reason']);return True
  177          frame=pd.DataFrame(protection['history']).set_index('timestamp')
  178          decision=self.engine_b.evaluate(record,frame,ts,len(protection['sessions']),session_last_bar=session_last_bar)
  179          if self.config.strict_session_boundary and session_last_bar and len(protection['sessions'])>=self.engine_b.policy.max_sessions:
  180              engine._execute_exit(symbol,timestamp,trade,float(bar['close']),'ENGINE_B_MAX_SESSIONS');return True
  181          if decision.action=='EXIT':
  182              trade['engine_b_exit_pending']={'timestamp':ts,'reason':decision.reason}
  183          # This bar's protective tests already completed; proposed stop arms next bar.
  184          record=tighten_stop(record,decision.proposed_stop_price)
  185          protection['stop']=record.current_stop_price
  186          protection['engine_b_state']=self.engine_b.export_state()
  187          protection['protective_order_id']=engine.broker.ensure_protection(symbol,record.current_stop_price,int(trade['quantity']),record.product)
  188          protection['broker_snapshot']=engine.broker.snapshot()
  189          self.store.save(record,dict(trade),protection,snapshot.revision)
  190          engine._position_lifecycle[record.position_id]=record
  191          return True
  192  
  193      def persist_end_of_run(self, engine, symbol):
  194          snapshot=self.store.load(engine.open_trades[symbol]['trade_id'])
  195          if self.config.end_of_run_disposition!='PERSIST' or snapshot.record.lifecycle_state!=B_OPEN:
  196              return False
  197          self.reconcile(engine,snapshot)
  198          return True
```
**paper conversion (sets position['product'])** `revision2_external/paper_execution.py:28-55`
```python
   28      def request_product_conversion(self, request_id, symbol, quantity,
   29                                     from_product="MIS", to_product="CNC", timestamp=None):
   30          """Paper-only idempotent conversion; never a second fill or cost event."""
   31          import pandas as pd
   32          request = (symbol, int(quantity), from_product, to_product)
   33          previous = self._conversion_receipts.get(request_id)
   34          if previous:
   35              if tuple(previous["request"]) != request:
   36                  raise ValueError("Conversion request identity changed")
   37              return dict(previous)
   38          position = self.get_position(symbol)
   39          passed = (from_product == "MIS" and to_product == "CNC"
   40                    and int(quantity) > 0 and position["quantity"] == int(quantity)
   41                    and position.get("product", "MIS") == from_product)
   42          if passed:
   43              position["product"] = to_product
   44          receipt = {"request_id": request_id, "request": list(request),
   45                     "status": "ACKNOWLEDGED" if passed else "REJECTED",
   46                     "product": position.get("product", "MIS"),
   47                     "quantity": int(position["quantity"]),
   48                     "timestamp": pd.Timestamp(timestamp).isoformat() if timestamp is not None else None,
   49                     "environment": "paper"}
   50          self._conversion_receipts[request_id] = receipt
   51          return dict(receipt)
   52  
   53      def conversion_receipt(self, request_id):
   54          receipt = self._conversion_receipts.get(request_id)
   55          return dict(receipt) if receipt else None
```
**paper contingent protection** `revision2_external/paper_execution.py:57-76`
```python
   57      def ensure_protection(self, symbol, stop_price, quantity, product="MIS"):
   58          """Track simulated contingent protection; orchestrator executes gap/bar stops.
   59  
   60          This is not an exchange order and offers no real overnight guarantee.
   61          """
   62          import math
   63          if not math.isfinite(float(stop_price)) or stop_price <= 0 or int(quantity) <= 0:
   64              raise ValueError("Invalid paper protection")
   65          position = self.get_position(symbol)
   66          if abs(position["quantity"]) != int(quantity) or position.get("product", "MIS") != product:
   67              raise ValueError("Paper protection does not match position")
   68          oid = "paper-protection-" + symbol
   69          self._contingent_protection[oid] = {
   70              "order_id": oid, "tradingsymbol": symbol, "exchange": "NSE",
   71              "product": product, "quantity": int(quantity), "pending_quantity": int(quantity),
   72              "filled_quantity": 0, "transaction_type": "SELL" if position["quantity"] > 0 else "BUY",
   73              "order_type": "SL-M", "status": "TRIGGER PENDING", "validity": "SIMULATED",
   74              "trigger_price": float(stop_price), "environment": "paper"}
   75          return oid
   76
```
**Kite place_order (current)** `revision2_external/broker_adapter_kite.py:60-92`
```python
   60      def place_order(self, symbol: str, side: str, quantity: int, order_type: str,
   61                      limit_price: Optional[float] = None, exchange: str = "NSE", product: str = "MIS",
   62                      correlation_id: Optional[str] = None):
   63          if side not in _SIDE_TO_TRANSACTION_TYPE or order_type not in _ORDER_TYPE_MAP or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
   64              return {"passed": False, "reason": "invalid order arguments"}
   65          kwargs = dict(variety=self.client.VARIETY_REGULAR, exchange=exchange, tradingsymbol=symbol,
   66                        transaction_type=side, quantity=int(quantity), order_type=order_type, product=product)
   67          if correlation_id:
   68              if not correlation_id.isalnum() or len(correlation_id) > 20:
   69                  return {"passed": False, "reason": "correlation_id must be alphanumeric and at most 20 characters"}
   70              kwargs['tag'] = correlation_id
   71              try:
   72                  matches = [o for o in self.orders() if o.get('tag') == correlation_id]
   73              except Exception as exc:
   74                  return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": str(exc)}
   75              if matches:
   76                  return self._correlated_receipt(matches, kwargs)
   77          if order_type == 'LIMIT':
   78              if limit_price is None or limit_price <= 0:
   79                  return {"passed": False, "reason": "LIMIT order requires a positive limit_price"}
   80              kwargs['price'] = float(limit_price)
   81          try:
   82              oid = self.client.place_order(**kwargs)
   83              return {"passed": True, "accepted": True, "filled": False, "order_id": oid, "status": "ACCEPTED"}
   84          except (NetworkException, TimeoutError, ConnectionError) as exc:
   85              if correlation_id:
   86                  try:
   87                      matches = [o for o in self.orders() if o.get('tag') == correlation_id]
   88                      if matches:
   89                          return self._correlated_receipt(matches, kwargs)
   90                  except Exception:
   91                      pass
   92              return {"passed": False, "ambiguous": True, "retry_allowed": False, "reason": str(exc)}
```
**Kite convert_position (current)** `revision2_external/broker_adapter_kite.py:107-136`
```python
  107      def convert_position(self, symbol, quantity, old_product='MIS', new_product='CNC', exchange='NSE',
  108                           transaction_type='BUY', correlation_id=None):
  109          # Conversion is acknowledged only after broker product/quantity confirmation.
  110          if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or transaction_type not in ('BUY', 'SELL') or old_product == new_product:
  111              return {"passed": False, "product_confirmed": False, "retry_allowed": False, "reason": "invalid conversion arguments"}
  112          try:
  113              before = self.positions().get('net', [])
  114              signed = int(quantity) if transaction_type == 'BUY' else -int(quantity)
  115              old = sum(int(p.get('quantity', 0)) for p in before if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == old_product)
  116              new = sum(int(p.get('quantity', 0)) for p in before if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == new_product)
  117              if quantity <= 0 or old * signed <= 0 or abs(old) < quantity:
  118                  return {"passed": False, "product_confirmed": False, "reason": "source position insufficient", "correlation_id": correlation_id}
  119              try:
  120                  accepted = self.client.convert_position(exchange=exchange, tradingsymbol=symbol,
  121                      transaction_type=transaction_type, position_type='day', quantity=int(quantity),
  122                      old_product=old_product, new_product=new_product)
  123              except (NetworkException, TimeoutError, ConnectionError):
  124                  accepted = None
  125              after = self.positions().get('net', [])
  126              aold = sum(int(p.get('quantity', 0)) for p in after if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == old_product)
  127              anew = sum(int(p.get('quantity', 0)) for p in after if p.get('tradingsymbol') == symbol and p.get('exchange') == exchange and p.get('product') == new_product)
  128              confirmed = aold == old - signed and anew == new + signed
  129              return {"passed": confirmed, "accepted": accepted, "product_confirmed": confirmed,
  130                      "correlation_id": correlation_id, "retry_allowed": False,
  131                      "confirmed_product": new_product if confirmed else None, "confirmed_quantity": quantity if confirmed else None}
  132          except Exception as exc:
  133              return {"passed": False, "product_confirmed": False, "retry_allowed": False, "reason": str(exc), "correlation_id": correlation_id}
  134  
  135      @_retry_transient
  136      def order_status(self, order_id: str):
```
**orchestrator lifecycle helpers and hook** `revision2_external/orchestrator.py:1062-1097`
```python
 1062      def _register_position_lifecycle(self, symbol: str, trade: Dict[str, Any], timestamp) -> None:
 1063          """Every replay fill starts as an Engine A / MIS position."""
 1064          entry = float(trade["entry_price"])
 1065          self._position_lifecycle[trade["trade_id"]] = lifecycle.open_position(
 1066              position_id=trade["trade_id"], symbol=symbol, direction=trade["side"],
 1067              initial_risk_r=abs(entry - float(trade["stop_price"])), anchor_price=entry,
 1068              initial_stop_price=float(trade["stop_price"]), created_bar_timestamp=pd.Timestamp(timestamp))
 1069          if getattr(self, "combined_cycle_runtime", None) is not None:
 1070              self.combined_cycle_runtime.register_fill(self._position_lifecycle[trade["trade_id"]], trade, self.broker)
 1071  
 1072      def _close_position_lifecycle(self, trade: Dict[str, Any]) -> None:
 1073          record = self._position_lifecycle.get(trade.get("trade_id"))
 1074          if record is not None and record.is_open:
 1075              self._position_lifecycle[trade["trade_id"]] = lifecycle.close_position(record)
 1076              if getattr(self, "combined_cycle_runtime", None) is not None:
 1077                  self.combined_cycle_runtime.close(self._position_lifecycle[trade["trade_id"]], trade)
 1078  
 1079      def _owner_engine(self, trade: Dict[str, Any]) -> str:
 1080          """Owning engine of an open trade; a trade without a lifecycle record is Engine A (intraday MIS)."""
 1081          record = self._position_lifecycle.get(trade.get("trade_id"))
 1082          return record.owner_engine if record is not None else lifecycle.ENGINE_A
 1083  
 1084      def _transition_position_lifecycle(self, trade: Dict[str, Any], new_state: str, timestamp) -> None:
 1085          """Apply one contract transition and record it; illegal transitions raise."""
 1086          before = self._position_lifecycle[trade["trade_id"]]
 1087          after = lifecycle.transition(before, new_state)
 1088          self._position_lifecycle[trade["trade_id"]] = after
 1089          self._record_controller_event("POSITION_LIFECYCLE_TRANSITION", timestamp, trade.get("symbol", before.symbol), {
 1090              "trade_id": trade.get("trade_id"), "from_state": before.lifecycle_state, "to_state": after.lifecycle_state,
 1091              "owner_engine": after.owner_engine, "product": after.product})
 1092  
 1093      def _maybe_exit(
 1094          self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool,
 1095          chart_studies_confidence: float, chart_studies_audit: Optional[Dict[str, Any]] = None,
 1096      ) -> None:
 1097          trade = self.open_trades.get(symbol)
```
**hook in _maybe_exit** `revision2_external/orchestrator.py:1105-1108`
```python
 1105          if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
 1106                  self, symbol, timestamp, bar, session_last_bar):
 1107              return
 1108          studies_direction = (chart_studies_audit or {}).get("direction")
```
**end-of-run hook** `revision2_external/orchestrator.py:2116-2120`
```python
 2116          for symbol in list(self.open_trades.keys()):
 2117              if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.persist_end_of_run(self, symbol):
 2118                  continue
 2119              bars = symbol_bars[symbol]
 2120              final_close = float(bars.iloc[len(bars) - 1]["close"])
```
## PART 3 — Ownership state machine (as implemented)

**legal-transition table** `revision5/position_lifecycle.py:55-62`
```python
   55  LEGAL_TRANSITIONS: Dict[str, FrozenSet[str]] = {
   56      A_OPEN: frozenset({TRANSFER_REQUESTED, CLOSED}),
   57      TRANSFER_REQUESTED: frozenset({B_OPEN, A_OPEN, CLOSED}),   # ack / reject-or-timeout / forced close
   58      B_OPEN: frozenset({CLOSED}),
   59      CLOSED: frozenset(),
   60  }
   61  
   62
```
**owner/product required by each state** `revision5/position_lifecycle.py:49-54`
```python
   49  _STATE_OWNERSHIP = {
   50      A_OPEN: (ENGINE_A, PRODUCT_MIS),
   51      TRANSFER_REQUESTED: (ENGINE_A, PRODUCT_MIS),
   52      B_OPEN: (ENGINE_B, PRODUCT_CNC),
   53  }
   54
```
**transition()** `revision5/position_lifecycle.py:132-145`
```python
  132  def transition(record: PositionLifecycleRecord, new_state: str) -> PositionLifecycleRecord:
  133      """Return the record in ``new_state`` with owner and product set to what that state requires.
  134  
  135      Raises ``PositionLifecycleError`` for any transition not in ``LEGAL_TRANSITIONS``."""
  136      if new_state not in LIFECYCLE_STATES:
  137          raise PositionLifecycleError(f"unknown lifecycle state {new_state!r}")
  138      if new_state not in LEGAL_TRANSITIONS[record.lifecycle_state]:
  139          hint = (" (a transfer must be requested and acknowledged first)"
  140                  if (record.lifecycle_state, new_state) == (A_OPEN, B_OPEN) else "")
  141          raise PositionLifecycleError(f"illegal transition {record.lifecycle_state} -> {new_state}{hint}")
  142      owner, product = _STATE_OWNERSHIP.get(new_state, (record.owner_engine, record.product))
  143      return replace(record, lifecycle_state=new_state, owner_engine=owner, product=product)
  144  
  145
```
**reject_transfer()** `revision5/position_lifecycle.py:155-160`
```python
  155  def reject_transfer(record: PositionLifecycleRecord) -> PositionLifecycleRecord:
  156      """Engine B declined or the request timed out: the position stays with Engine A as ``A_OPEN``."""
  157      if record.lifecycle_state != TRANSFER_REQUESTED:
  158          raise PositionLifecycleError(f"only a TRANSFER_REQUESTED position can be rejected, state is {record.lifecycle_state}")
  159      return transition(record, A_OPEN)
  160
```
```
$ /home/srinivas/.venvs/zerodha-phase1-r5/bin/python - <<'EOF'
import sys; sys.path.insert(0,'.')
from revision5 import position_lifecycle as lc
S=lc.LIFECYCLE_STATES
print('states:',S)
print('terminal:',[s for s in S if not lc.LEGAL_TRANSITIONS[s]])
for a in S:
    for b in S:
        print(f'{a:>19} -> {b:<19}', 'LEGAL' if b in lc.LEGAL_TRANSITIONS[a] else 'ILLEGAL')
print({k:v for k,v in lc._STATE_OWNERSHIP.items()})
EOF
states: ('A_OPEN', 'TRANSFER_REQUESTED', 'B_OPEN', 'CLOSED')
terminal: ['CLOSED']
             A_OPEN -> A_OPEN              ILLEGAL
             A_OPEN -> TRANSFER_REQUESTED  LEGAL
             A_OPEN -> B_OPEN              ILLEGAL
             A_OPEN -> CLOSED              LEGAL
 TRANSFER_REQUESTED -> A_OPEN              LEGAL
 TRANSFER_REQUESTED -> TRANSFER_REQUESTED  ILLEGAL
 TRANSFER_REQUESTED -> B_OPEN              LEGAL
 TRANSFER_REQUESTED -> CLOSED              LEGAL
             B_OPEN -> A_OPEN              ILLEGAL
             B_OPEN -> TRANSFER_REQUESTED  ILLEGAL
             B_OPEN -> B_OPEN              ILLEGAL
             B_OPEN -> CLOSED              LEGAL
             CLOSED -> A_OPEN              ILLEGAL
             CLOSED -> TRANSFER_REQUESTED  ILLEGAL
             CLOSED -> B_OPEN              ILLEGAL
             CLOSED -> CLOSED              ILLEGAL
{'A_OPEN': ('ENGINE_A', 'MIS'), 'TRANSFER_REQUESTED': ('ENGINE_A', 'MIS'), 'B_OPEN': ('ENGINE_B', 'CNC')}
[exit status 0]
```
Ownership meaning: `A_OPEN`, `TRANSFER_REQUESTED` -> owner ENGINE_A / product MIS; `B_OPEN` -> ENGINE_B / CNC; `CLOSED` keeps the last owner/product and is terminal (`_STATE_OWNERSHIP` above, enforced by `PositionLifecycleRecord.__post_init__`). `TRANSFER_REJECTED` is not a state: `reject_transfer` returns `TRANSFER_REQUESTED -> A_OPEN`.

### Production call sites that change lifecycle state

```
$ grep -rn "open_position(\|close_position(\|request_transfer(\|acknowledge_transfer(\|reject_transfer(\|lifecycle.transition(\|_transition_position_lifecycle(\|tighten_stop(\|_position_lifecycle\[" --include=*.py revision2_external revision5 | grep -v 'position_lifecycle.py'
revision2_external/continuous_exit_controller_with_grid.py:157:    def open_position(
revision2_external/closed_loop_dry_run.py:76:    state = controller.open_position("BUY", 100.0, 95.0, 110.0, max_hold_bars=20)
revision2_external/closed_loop_dry_run.py:163:    state = controller.open_position("BUY", 105.0, 100.0, 115.0, max_hold_bars=20)
revision2_external/orchestrator.py:1065:        self._position_lifecycle[trade["trade_id"]] = lifecycle.open_position(
revision2_external/orchestrator.py:1070:            self.combined_cycle_runtime.register_fill(self._position_lifecycle[trade["trade_id"]], trade, self.broker)
revision2_external/orchestrator.py:1075:            self._position_lifecycle[trade["trade_id"]] = lifecycle.close_position(record)
revision2_external/orchestrator.py:1077:                self.combined_cycle_runtime.close(self._position_lifecycle[trade["trade_id"]], trade)
revision2_external/orchestrator.py:1084:    def _transition_position_lifecycle(self, trade: Dict[str, Any], new_state: str, timestamp) -> None:
revision2_external/orchestrator.py:1086:        before = self._position_lifecycle[trade["trade_id"]]
revision2_external/orchestrator.py:1087:        after = lifecycle.transition(before, new_state)
revision2_external/orchestrator.py:1088:        self._position_lifecycle[trade["trade_id"]] = after
revision2_external/orchestrator.py:2107:                    self._exit_controller_states[symbol] = self.exit_controller.open_position(
revision2_external/continuous_exit_controller.py:258:    def open_position(
revision5/handoff_manager.py:68:            self.store._save(request_transfer(snapshot.record),snapshot.trade,snapshot.protection,snapshot.revision)
revision5/handoff_manager.py:105:                record = acknowledge_transfer(record)
revision5/handoff_manager.py:108:                record = reject_transfer(record)
revision5/combined_cycle_runtime.py:44:        engine._position_lifecycle[snapshot.record.position_id]=snapshot.record
revision5/combined_cycle_runtime.py:79:            engine._position_lifecycle[snapshot.record.position_id]=snapshot.record
revision5/combined_cycle_runtime.py:103:                record=tighten_stop(record,effective_stop)
revision5/combined_cycle_runtime.py:109:                engine._position_lifecycle[record.position_id]=record
revision5/combined_cycle_runtime.py:184:        record=tighten_stop(record,decision.proposed_stop_price)
revision5/combined_cycle_runtime.py:190:        engine._position_lifecycle[record.position_id]=record
[exit status 0]
```
| CALLER | FILE:LINE | OLD -> NEW | CONDITION | PERSISTED? | BROKER ACTION | FAILURE CONSEQUENCE |
|---|---|---|---|---|---|---|
| `_register_position_lifecycle` | revision2_external/orchestrator.py:1062 | (none) -> A_OPEN (ENGINE_A/MIS) | every replay fill | YES only if a runtime is attached (`runtime.register_fill`, line below); otherwise memory only | AFTER: fill and trade dict already exist | see Part 4 probes: exception leaves broker filled + open_trades set |
| `_close_position_lifecycle` | revision2_external/orchestrator.py:1072 | A_OPEN/TRANSFER_REQUESTED/B_OPEN -> CLOSED | record exists and is open | YES only with runtime (`runtime.close`) | AFTER: exit order already filled | see Part 5 probe: broker flat, completed_trades appended, open_trades retained |
| `_transition_position_lifecycle` | revision2_external/orchestrator.py:1084 | any legal -> any | caller supplies new_state | NO (memory only) | none | PRODUCTION CALLERS: none (grep above); only tests call it |
| `HandoffManager.request` | revision5/handoff_manager.py:68 | A_OPEN -> TRANSFER_REQUESTED | `qualifies(...)` true | YES (same SQLite transaction as the conversions row) | BEFORE conversion (runtime then calls request_product_conversion) | ROLLBACK on any exception; no broker action yet |
| `HandoffManager.resolve` (ACKNOWLEDGED) | revision5/handoff_manager.py:105 | TRANSFER_REQUESTED -> B_OPEN | exact product CNC, exact quantity, same-day, <= deadline | YES | AFTER broker conversion | ROLLBACK; broker may already be CNC (Part 6 probe) |
| `HandoffManager.resolve` (REJECTED/TIMEOUT) | revision5/handoff_manager.py:108 | TRANSFER_REQUESTED -> A_OPEN | outcome REJECTED or TIMEOUT | YES | AFTER broker receipt | ROLLBACK |
| `HandoffManager.resolve` (CLOSED_RACE) | revision5/handoff_manager.py:96 | CLOSED stays CLOSED | record already CLOSED | YES (receipt status only) | none | none |
| `CombinedCycleRuntime.handle_bar` stop ratchet | revision5/combined_cycle_runtime.py:103 | state unchanged (stop tightened) | A_OPEN and stop tightens | YES | BEFORE: ensure_protection re-issued | exception propagates out of _maybe_exit |

### Invariant evidence (code and executed probes, not assertions)

1. **A owns intraday**: `_owner_engine` returns ENGINE_A for any trade lacking a record or with an A-state record; `force_close_time`/`mis_session_close` gated on it:
**revision2_external/orchestrator.py** `revision2_external/orchestrator.py:1079-1082`
```python
 1079      def _owner_engine(self, trade: Dict[str, Any]) -> str:
 1080          """Owning engine of an open trade; a trade without a lifecycle record is Engine A (intraday MIS)."""
 1081          record = self._position_lifecycle.get(trade.get("trade_id"))
 1082          return record.owner_engine if record is not None else lifecycle.ENGINE_A
```
**revision2_external/orchestrator.py** `revision2_external/orchestrator.py:1114-1119`
```python
 1114          owned_by_engine_a = self._owner_engine(trade) == lifecycle.ENGINE_A
 1115          if owned_by_engine_a and (pd.Timestamp(timestamp).strftime("%H:%M")
 1116                                    >= self.entry_decision_engine.config.force_close_time):
 1117              self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "force_close_time")
 1118              return
 1119          halt_dd = min(float(self.config.require("drawdown_halt_threshold")),
```
2. **A may hand off exactly once**: `conversions.position_id` is UNIQUE (store DDL above) and `request()` returns the existing row. Executed:
```json
{
 "request_twice_same_id": [
  true,
  "PENDING",
  "PENDING",
  1
 ],
 "after_reject": "A_OPEN",
 "request_after_reject": {
  "same_request_id": true,
  "status": "REJECTED",
  "lifecycle": "A_OPEN",
  "rows": 1
 }
}
```
   After a REJECTED outcome the position is `A_OPEN` again, but `request()` returns the old REJECTED row (same request_id, rows=1), and `handle_bar` only proceeds when `request.status=='PENDING'`:
**revision5/combined_cycle_runtime.py** `revision5/combined_cycle_runtime.py:155-159`
```python
  155              request=self.handoff.request(record.position_id,int(trade['quantity']),ts,current_r=current_r,mfe_r=mfe,trend_aligned=bool(aligned))
  156              if request is None: return False
  157              if request.status=='PENDING':
  158                  engine.broker.request_product_conversion(request.request_id,symbol,request.quantity,from_product='MIS',to_product='CNC',timestamp=ts)
  159                  snapshot=self._receipt(engine,request,ts)
```
3. **B owns after handoff / A cannot regain ownership**: `LEGAL_TRANSITIONS[B_OPEN] == {CLOSED}` (matrix above); `reject_transfer` raises unless state is TRANSFER_REQUESTED; the store re-checks legality durably:
**store durable legality check** `revision5/combined_cycle_store.py:80-80`
```python
   80              if record.lifecycle_state != old.lifecycle_state and record.lifecycle_state not in LEGAL_TRANSITIONS[old.lifecycle_state]:
```
4. **CLOSED cannot reopen**: `LEGAL_TRANSITIONS[CLOSED]` is empty; `resolve` maps an acknowledgement on a CLOSED record to `CLOSED_RACE` without a transition (`test_closed_race_never_resurrects`). Note the in-memory dict `engine._position_lifecycle[trade_id]` is overwritten unconditionally by `_register_position_lifecycle` (line above); the durable store refuses a second `register_fill` for an existing id via `expected_revision=0`.
5. **Duplicate handoff impossible/idempotent**: `resolve` twice with the same or a conflicting outcome is a no-op (executed):
```json
{
 "first": "B_OPEN",
 "second_same": "B_OPEN",
 "third_conflicting_reject": "B_OPEN",
 "revision_unchanged_after_repeats": true
}
```
## PART 4 — Entry/fill forensic trace (real orchestrator execution path)

**arrow: signal** `revision2_external/orchestrator.py:1582-1583`
```python
 1582                  signal, trace = self.pa.evaluate(snapshot, self.config)
 1583                  self._record(trace)
```
**arrow: upstream admission (supervisory bridge)** `revision2_external/orchestrator.py:1566-1568`
```python
 1566                  upstream = self.supervisory_bridge.evaluate_upstream_admission(
 1567                      symbol=symbol,
 1568                      runtime_parameters=self.config.as_dict(),
```
**arrow: ID decision** `revision2_external/orchestrator.py:1636-1636`
```python
 1636                  decision, trace = self.id_box.evaluate(signal, self.config, latest_close=float(bars.iloc[bar_idx]["close"]))
```
**arrow: plan (MPC)** `revision2_external/orchestrator.py:1681-1681`
```python
 1681                  plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
```
**arrow: governor entry decision** `revision2_external/orchestrator.py:1825-1826`
```python
 1825                  governor_entry = self._governor_entry(
 1826                      symbol, timestamp, bar_idx, plan.side, signal, decision, composite_result,
```
**arrow: sizing** `revision2_external/orchestrator.py:1901-1902`
```python
 1901                  quantity, trace = self.position_manager.size(
 1902                      plan, equity_now, size_mult, self.config, symbol=symbol,
```
**arrow: paper plant cap (second)** `revision2_external/orchestrator.py:1921-1921`
```python
 1921                      quantity = self._paper_plant_entry_limit(symbol, quantity, cap_price, timestamp)
```
**arrow: order/fill simulation** `revision2_external/orchestrator.py:2008-2013`
```python
 2008                  fill = self.broker.place_order(
 2009                      symbol=symbol, side=order.side, quantity=quantity, order_type=order.order_type,
 2010                      market_price=float(pid_info["execution_market_price"]),
 2011                      config=self.safety_contract.as_dict(), parameter_registry=self.registry,
 2012                  )
 2013                  funnel["orders_submitted"] += 1
```
**arrow: fill accepted -> post-fill checks -> trade id** `revision2_external/orchestrator.py:2034-2056`
```python
 2034                  if fill["passed"]:
 2035                      actual_quantity = int(fill["filled_quantity"])
 2036                      post_fill = self.entry_decision_engine.evaluate_post_fill(
 2037                          target_price=float(pid_info["execution_market_price"]), fill_price=float(fill["filled_price"]),
 2038                          expected_qty=quantity, actual_qty=actual_quantity,
 2039                          elapsed_seconds=float(fill["ack_elapsed_seconds"]),
 2040                          expected_position=actual_quantity * (1 if order.side == "BUY" else -1),
 2041                          actual_position=self.broker.get_position(symbol)["quantity"],
 2042                      )
 2043                      self._post_fill_checks.append({
 2044                          "candidate_id": candidate_id, **post_fill,
 2045                          "decisions": [asdict(d) for d in post_fill["decisions"]],
 2046                      })
 2047                      if not post_fill["passed"]:
 2048                          # A fill already happened: retain it in the ledger and halt NEW entries.
 2049                          # Existing protective exits remain enabled.
 2050                          self._execution_halted = True
 2051                      if actual_quantity <= 0:
 2052                          raise RuntimeError("Paper fill has no reconcilable positive quantity")
 2053                      quantity = actual_quantity
 2054                      self.entry_candidate_observations.dispose(candidate_id, "FILLED", "paper_fill")
 2055                      funnel["fills"] += 1
 2056                      self._trade_sequence += 1
```
**arrows: trade dict -> open_trades -> lifecycle registration -> governor init -> exit controller** `revision2_external/orchestrator.py:2087-2109`
```python
 2087                      self.open_trades[symbol] = {
 2088                          "side": plan.side, "entry_price": fill["filled_price"], "stop_price": plan.stop_price,
 2089                          "target_price": plan.target_price, "quantity": quantity,
 2090                          "minimum_hold_bars": plan.minimum_hold_bars, "maximum_hold_bars": plan.maximum_hold_bars,
 2091                          "exit_confidence_threshold": decision.timing_quality, "entry_timestamp": str(next_ts),
 2092                          "entry_atr": float(atr), "planned_entry_price": float(plan.entry_price),
 2093                          "planned_stop_price": float(plan.stop_price), "planned_target_price": float(plan.target_price),
 2094                          "candidate_id": candidate_id, "trade_id": f"trade-{self._trade_sequence}",
 2095                          "controller_exit_pending": None,
 2096                          "closed_loop": closed_loop_snapshot,
 2097                      }
 2098                      self.open_trades[symbol].update({
 2099                          "governor_stop_price": float(plan.stop_price), "governor_mfe_r": 0.0,
 2100                          "governor_entry_conviction": governor_entry.get("entry_absolute_conviction"),
 2101                      })
 2102                      self._register_position_lifecycle(symbol, self.open_trades[symbol], next_ts)
 2103                      _, governor = self._governor_for(symbol)
 2104                      if governor is not None:
 2105                          governor.begin_position(hard_stop_r=-1.0, position_id=f"trade-{self._trade_sequence}")
 2106                      entry_bar_index[symbol] = bar_idx + 1
 2107                      self._exit_controller_states[symbol] = self.exit_controller.open_position(
 2108                          plan.side, fill["filled_price"], plan.stop_price, plan.target_price, plan.maximum_hold_bars,
 2109                      )
```
**arrow: lifecycle registration** `revision2_external/orchestrator.py:1062-1071`
```python
 1062      def _register_position_lifecycle(self, symbol: str, trade: Dict[str, Any], timestamp) -> None:
 1063          """Every replay fill starts as an Engine A / MIS position."""
 1064          entry = float(trade["entry_price"])
 1065          self._position_lifecycle[trade["trade_id"]] = lifecycle.open_position(
 1066              position_id=trade["trade_id"], symbol=symbol, direction=trade["side"],
 1067              initial_risk_r=abs(entry - float(trade["stop_price"])), anchor_price=entry,
 1068              initial_stop_price=float(trade["stop_price"]), created_bar_timestamp=pd.Timestamp(timestamp))
 1069          if getattr(self, "combined_cycle_runtime", None) is not None:
 1070              self.combined_cycle_runtime.register_fill(self._position_lifecycle[trade["trade_id"]], trade, self.broker)
 1071
```
**arrow: runtime.register_fill -> protection -> persistence** `revision5/combined_cycle_runtime.py:34-37`
```python
   34      def register_fill(self, record, trade, broker):
   35          protective_id=broker.ensure_protection(record.symbol,record.current_stop_price,int(trade['quantity']),record.product)
   36          self.store.save(record, dict(trade), {'protective_order_id':protective_id,'broker_snapshot':broker.snapshot(),'stop':record.current_stop_price,'target':float(trade['target_price']),
   37                                                'sessions':[], 'engine_b_state':self.engine_b.export_state()},expected_revision=0)
```
### Exception boundaries after the fill (executed against the REAL orchestrator on Block 1 data)

Probe `real_run.py`: real `Revision2ExternalEngineOrchestrator.run()` on Block 1 TITAN (trial-007 parameters, full authority, PAPER_APPLY). Raw observed state after the exception:

**A. `_register_position_lifecycle` raises (wrapper injects RuntimeError)** — exception tail: `RuntimeError: PROBE: lifecycle registration failed`
```json
{
 "state_after": {
  "broker_positions": {
   "TITAN": {
    "quantity": -5,
    "avg_price": 3559.5837999999994
   }
  },
  "open_trades": {
   "TITAN": {
    "trade_id": "trade-1",
    "quantity": 5
   }
  },
  "completed_trades": 0,
  "lifecycle_records": {},
  "exit_controller_states": [],
  "governor_inner_states": {},
  "fills": 1,
  "orders": 1
 }
}
```
**B. `runtime.register_fill` raises (runtime attached; wrapper injects RuntimeError)** — exception tail: `RuntimeError: PROBE: register_fill failed`
```json
{
 "state_after": {
  "broker_positions": {
   "TITAN": {
    "quantity": -5,
    "avg_price": 3559.5837999999994
   }
  },
  "open_trades": {
   "TITAN": {
    "trade_id": "trade-1",
    "quantity": 5
   }
  },
  "completed_trades": 0,
  "lifecycle_records": {
   "trade-1": "A_OPEN"
  },
  "exit_controller_states": [],
  "governor_inner_states": {},
  "fills": 1,
  "orders": 1
 },
 "store_positions": []
}
```
**C. runtime attached, NO injected fault** — exception tail: `revision5.combined_cycle_runtime.CombinedCycleReconciliationError: broker quantity/product differs from durable owner`
```json
{
 "state_after": {
  "broker_positions": {
   "TITAN": {
    "quantity": -5,
    "avg_price": 3559.5837999999994
   }
  },
  "open_trades": {
   "TITAN": {
    "trade_id": "trade-1",
    "quantity": 5
   }
  },
  "completed_trades": 0,
  "lifecycle_records": {
   "trade-1": "A_OPEN"
  },
  "exit_controller_states": [
   "TITAN"
  ],
  "governor_inner_states": {
   "CSTG2_CONSUMER_AUTO": [
    "trade-1"
   ]
  },
  "fills": 1,
  "orders": 1
 },
 "store_positions": [
  [
   "trade-1",
   "A_OPEN"
  ]
 ]
}
```
| Scenario | WHAT HAS ALREADY MUTATED | WHAT HAS NOT MUTATED | CAN THE POSITION BE LOST? | CAN IT BE DUPLICATED? | CAN LOCAL/BROKER STATE DIVERGE? |
|---|---|---|---|---|---|
| A | broker position filled (-5 TITAN), `open_trades['TITAN']` set, `_trade_sequence` advanced | `_position_lifecycle` (empty), `exit_controller_states` (empty), governor `begin_position` (none), runtime store (no runtime) | the exception propagates out of `run()`; the open trade has no exit controller state and no lifecycle record | no code path re-submits the entry; `run()` aborts | broker filled and ledger open agree; lifecycle/governor/exit-controller state is absent |
| B | broker filled, `open_trades` set, in-memory lifecycle `A_OPEN`, paper `ensure_protection` (not reached in this probe because register_fill was replaced) | durable store empty (`store_positions: []`), exit controller state, governor `begin_position` | durable record never written -> after a process restart the position would not be known to the store | no | memory says A_OPEN, store has no row, broker holds the position |
| C | broker filled, `open_trades` set, in-memory lifecycle A_OPEN, durable row A_OPEN, protection registered | nothing further: `handle_bar` raises on the first bar after the fill | position stays open in the broker; run aborts at the first `_maybe_exit` | no | durable and memory agree; run is aborted by `CombinedCycleReconciliationError('broker quantity/product differs from durable owner')` |
Evidence for scenario C's cause: the paper broker's real position dict has no `product` key until a conversion; `reconcile` compares `position.get('product')` to the record's product:
**revision5/combined_cycle_runtime.py** `revision5/combined_cycle_runtime.py:47-51`
```python
   47      def reconcile(self, engine, snapshot):
   48          position=engine.broker.get_position(snapshot.record.symbol)
   49          expected=float(snapshot.trade['quantity'])*(1 if snapshot.record.direction=='BUY' else -1)
   50          if abs(float(position.get('quantity',0))-expected)>1e-6 or position.get('product')!=snapshot.record.product:
   51              raise CombinedCycleReconciliationError('broker quantity/product differs from durable owner')
```
```
$ grep -n "product" runtime/operating_mode.py | head -5; echo '--- orchestrator assigns product to positions?'; grep -n "'product'\|\"product\"" revision2_external/orchestrator.py | head
--- orchestrator assigns product to positions?
1091:            "owner_engine": after.owner_engine, "product": after.product})
[exit status 0]
```
(The probe `broker_positions` shows `{'quantity': -5, 'avg_price': ...}` with no `product`.) Pre-existing boundaries between the fill and `open_trades` insertion (unchanged by this work): `evaluate_post_fill`, `raise RuntimeError('Paper fill has no reconcilable positive quantity')`, `closed_loop.entry_snapshot`:
**revision2_external/orchestrator.py** `revision2_external/orchestrator.py:2050-2050`
```python
 2050                          self._execution_halted = True
```
## PART 5 — Exit forensic trace

**start of _execute_exit (precheck, broker exit)** `revision2_external/orchestrator.py:880-888`
```python
  880      def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
  881          self._assert_paper_plant_broker()
  882          self._verify_broker_position_reconciles(symbol, trade)
  883          close_side = "SELL" if trade["side"] == "BUY" else "BUY"
  884          self._exit_orders_submitted += 1
  885          result = self.broker.place_order(
  886              symbol=symbol, side=close_side, quantity=trade["quantity"], order_type="MARKET",
  887              market_price=exit_price, config=self.safety_contract.as_dict(), parameter_registry=self.registry,
  888          )
```
**authoritative close bookkeeping (source order)** `revision2_external/orchestrator.py:967-992`
```python
  967              # ---- 1. Authoritative close bookkeeping --------------------------------------
  968              # The broker position is already flat.  Everything that keeps this engine's ledger,
  969              # P&L and protection state consistent with the broker happens first, before any
  970              # research telemetry can raise.  A later defect still propagates, but can no longer
  971              # leave a flat broker position recorded as open.
  972              self.completed_trades.append(completed)
  973              _pnl = float(completed.get("net_pnl", 0.0))
  974              if _pnl < 0:
  975                  _c_losses = self.symbol_consecutive_losses.get(symbol, 0) + 1
  976                  self.symbol_consecutive_losses[symbol] = _c_losses
  977                  self.symbol_cooldown_until_bar[symbol] = (
  978                      getattr(self, "_current_bar_idx", 0) + BAY_LOSS_COOLDOWN_BARS
  979                  )
  980                  if _c_losses >= 2:
  981                      self.symbol_tripped[symbol] = True
  982              elif _pnl > 0:
  983                  # Any profitable exit breaks consecutive loss streak
  984                  self.symbol_consecutive_losses[symbol] = 0
  985              self._equity_curve.append(self._equity())
  986              self._close_position_lifecycle(trade)
  987              del self.open_trades[symbol]
  988              self._exit_controller_states.pop(symbol, None)
  989              _, governor = self._governor_for(symbol)
  990              if governor is not None:
  991                  governor.confirm_position_closed(trade.get("trade_id"))
  992              self._record_mtm(timestamp)
```
**realized-R / dispatcher feedback (after the above)** `revision2_external/orchestrator.py:1003-1021`
```python
 1003              # ---- 2. Authoritative realized-R close feedback -------------------------------
 1004              # Plant-level dispatch feedback (BLOCKER 2) and local governor feedback: after the
 1005              # authoritative exit fill, after position/ledger reconciliation and after the engine
 1006              # ledger above already records the trade as closed.  Exactly-once receipts still apply;
 1007              # an exception here propagates with the ledger already consistent with the broker.
 1008              if state is not None:
 1009                  risk = abs(float(trade["entry_price"]) - float(state.initial_stop_price))
 1010                  if risk > 0.0:
 1011                      try:
 1012                          bay_id = _r5_bay_for_symbol(symbol)
 1013                      except KeyError:
 1014                          # Symbol outside the certified 48-symbol R5 topology (e.g. a synthetic
 1015                          # test-only symbol): there is no R5 bay to feed, exactly like the native
 1016                          # plant's own UNMAPPED_SYMBOL admission path.  Never invent a bay mapping.
 1017                          bay_id = None
 1018                      if bay_id is not None:
 1019                          realized_r = self.exit_controller._r_multiple(state, float(result["filled_price"]))
 1020                          self._register_realized_r_close_feedback(
 1021                              symbol=symbol, trade=trade, bay_id=bay_id, realized_r=realized_r, reason=reason)
```
Source order: `_verify_broker_position_reconciles` -> `broker.place_order` (exit) -> build `completed` -> `completed_trades.append` -> loss-streak bookkeeping -> `_equity_curve.append` -> **`_close_position_lifecycle`** (runtime.close -> store.save CLOSED) -> `del open_trades[symbol]` -> `_exit_controller_states.pop` -> `governor.confirm_position_closed` -> `_record_mtm` -> controller event -> realized-R feedback (`merit_source.register_trade`, `bay.register_outcome`) -> telemetry.
The code's own comment states the intent of section 1: 'A later defect still propagates, but can no longer leave a flat broker position recorded as open.' Probe `exit_probe.py` (real orchestrator + real runtime + real store + real paper broker; the store's `save` raises when the lifecycle record is CLOSED):

```json
{
 "normal_exit": {
  "exception": null,
  "effect_order": [
   "broker.place_order(SELL)",
   "completed_trades.append",
   "_close_position_lifecycle",
   "store.save(state=CLOSED)",
   "open_trades.__delitem__",
   "dispatcher.register_trade"
  ],
  "state_after": {
   "broker_position": {
    "quantity": 0,
    "avg_price": 100.05
   },
   "completed_trades": 1,
   "open_trades": [],
   "lifecycle_state": "CLOSED",
   "durable_state": "CLOSED",
   "exit_controller_state_present": false
  }
 },
 "exit_with_failing_runtime_close": {
  "exception": "RuntimeError: PROBE: durable close failed",
  "effect_order": [
   "broker.place_order(SELL)",
   "completed_trades.append",
   "_close_position_lifecycle",
   "store.save(state=CLOSED)"
  ],
  "state_after": {
   "broker_position": {
    "quantity": 0,
    "avg_price": 100.05
   },
   "completed_trades": 1,
   "open_trades": [
    "INFY"
   ],
   "lifecycle_state": "CLOSED",
   "durable_state": "A_OPEN",
   "exit_controller_state_present": true
  },
  "second_close_attempt": {
   "exception": "PositionReconciliationError: INFY: ledger open_trades expects a BUY position of at least 10.0 to close, but the broker holds 0.0; refusing to execute an exit that would open or flip a position instead of closing the existing one",
   "broker_position_before": {
    "quantity": 0,
    "avg_price": 100.05
   },
   "broker_position_after": {
    "quantity": 0,
    "avg_price": 100.05
   },
   "completed_trades": 1
  }
 }
}
```
Reading of the raw output: in `exit_with_failing_runtime_close` the broker position is 0 (flat), `completed_trades` has 1 entry, `open_trades` still contains INFY, in-memory lifecycle is CLOSED while the durable record is A_OPEN, the exit-controller state remains, and the dispatcher feedback and governor cleanup that follow `del open_trades` in source order did not run (absent from `effect_order`). A second `_execute_exit` is refused by the pre-existing `_verify_broker_position_reconciles` (exception text above) and does not change the broker position or completed_trades.
## PART 6 — A -> B handoff forensic trace

Executed probe `handoff_probe.py` (real `Engine`, real `CombinedCycleRuntime`, real `CombinedCycleStore`, real paper broker). **PROBE SHIM:** after the paper fill the probe sets `orch.broker.positions['INFY']['product']='MIS'`, because the real paper fill carries no product key (Part 4 scenario C); the unit tests' mock Broker supplies the same value. Without the shim the first `handle_bar` raises.
**handoff branch of handle_bar (complete)** `revision5/combined_cycle_runtime.py:149-171`
```python
  149          if record.lifecycle_state==A_OPEN:
  150              # Causal trend reference consists exclusively of prior completed closes.
  151              prior=[item['close'] for item in history[:-1]][-self.engine_b.policy.structural_window:]
  152              aligned=len(prior)==self.engine_b.policy.structural_window and float(bar['close'])>=sum(prior)/len(prior)
  153              current_r=(float(bar['close'])-record.anchor_price)/record.initial_risk_r
  154              self.store.save(record,dict(trade),protection,snapshot.revision)
  155              request=self.handoff.request(record.position_id,int(trade['quantity']),ts,current_r=current_r,mfe_r=mfe,trend_aligned=bool(aligned))
  156              if request is None: return False
  157              if request.status=='PENDING':
  158                  engine.broker.request_product_conversion(request.request_id,symbol,request.quantity,from_product='MIS',to_product='CNC',timestamp=ts)
  159                  snapshot=self._receipt(engine,request,ts)
  160                  self._sync(engine,snapshot);record=snapshot.record
  161                  if record.lifecycle_state!=B_OPEN: return False
  162                  trade.pop('controller_exit_pending',None)
  163                  protection=dict(snapshot.protection)
  164                  protection['protective_order_id']=engine.broker.ensure_protection(symbol,record.current_stop_price,int(trade['quantity']),record.product)
  165                  protection['broker_snapshot']=engine.broker.snapshot()
  166                  self.store.save(record,dict(trade),protection,snapshot.revision)
  167                  snapshot=self.store.load(record.position_id)
  168                  self.reconcile(engine,snapshot)
  169              else: return False
  170          else:
  171              self.store.save(record,dict(trade),protection,snapshot.revision)
```
| Step | FUNCTION | FILE:LINE | STATE BEFORE -> AFTER | PERSISTENCE | FAILURE BEHAVIOR |
|---|---|---|---|---|---|
| qualification | `HandoffManager.qualifies` | revision5/handoff_manager.py:47 | A_OPEN -> A_OPEN | none | returns False |
| request | `HandoffManager.request` | revision5/handoff_manager.py:55 | A_OPEN -> TRANSFER_REQUESTED | SQLite tx (record + conversions row) | ROLLBACK, re-raise |
| product conversion | `broker.request_product_conversion` | revision5/combined_cycle_runtime.py:158 -> revision2_external/paper_execution.py:28 | broker product MIS -> CNC | paper adapter memory only | `ValueError` on changed identity |
| confirmation | `CombinedCycleRuntime._receipt` | revision5/combined_cycle_runtime.py:85 | TRANSFER_REQUESTED | none | raises `CombinedCycleReconciliationError` if receipt missing/UNKNOWN/PENDING |
| lifecycle transition | `HandoffManager.resolve` | revision5/handoff_manager.py:76 | TRANSFER_REQUESTED -> B_OPEN | SQLite tx (record + receipt) | ROLLBACK, re-raise |
| B management | `EngineBController.evaluate` + stop/protection re-issue | revision5/engine_b_management.py:61; revision5/combined_cycle_runtime.py:178 | B_OPEN | store.save | exceptions propagate |

Executed results:
```json
{
 "normal_handoff_after_15:10": {
  "memory_lifecycle": "B_OPEN",
  "durable_lifecycle": "B_OPEN",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "b6600f3f",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [
   "INFY"
  ],
  "completed": []
 },
 "conversion_ok_persistence_fails": {
  "exception": "RuntimeError: PROBE: durable write failed after conversion",
  "state": {
   "memory_lifecycle": "A_OPEN",
   "durable_lifecycle": "TRANSFER_REQUESTED",
   "owner": "ENGINE_A",
   "broker_product": "CNC",
   "conversion_rows": [
    [
     "5805d19d",
     "PENDING"
    ]
   ],
   "open_trades": [
    "INFY"
   ],
   "completed": []
  }
 },
 "conversion_ok_persistence_fails_next_bar": {
  "exception": "CombinedCycleReconciliationError: paper contingent protection quantity/product differs",
  "state": {
   "memory_lifecycle": "B_OPEN",
   "durable_lifecycle": "B_OPEN",
   "owner": "ENGINE_B",
   "broker_product": "CNC",
   "conversion_rows": [
    [
     "5805d19d",
     "ACKNOWLEDGED"
    ]
   ],
   "open_trades": [
    "INFY"
   ],
   "completed": []
  }
 },
 "ambiguous_confirmation": {
  "exception": "CombinedCycleReconciliationError: conversion outcome unknown; reconciliation required",
  "state": {
   "memory_lifecycle": "A_OPEN",
   "durable_lifecycle": "TRANSFER_REQUESTED",
   "owner": "ENGINE_A",
   "broker_product": "CNC",
   "conversion_rows": [
    [
     "142f90c1",
     "PENDING"
    ]
   ],
   "open_trades": [
    "INFY"
   ],
   "completed": []
  }
 }
}
```
Answers (each tied to code/probe above):
1. **One-phase or two-phase?** Two-phase: `request()` commits TRANSFER_REQUESTED and the conversions row before any broker call; `resolve()` commits B_OPEN only after a receipt. The broker conversion happens between the two commits (RT line of `request_product_conversion`).
2. **Conversion succeeds but persistence fails?** Probe `conversion_ok_persistence_fails`: exception propagates out of `_maybe_exit`; broker product is CNC, durable lifecycle is TRANSFER_REQUESTED, conversion row PENDING, **in-memory lifecycle remains A_OPEN** (memory and durable differ). On the next bar the receipt is re-read and the durable state becomes B_OPEN, then `reconcile` raises `paper contingent protection quantity/product differs` because the protective order still carries product MIS (probe `..._next_bar`).
3. **Persistence succeeds but confirmation ambiguous?** Probe `ambiguous_confirmation`: `_receipt` raises `conversion outcome unknown; reconciliation required`; state stays TRANSFER_REQUESTED/PENDING, broker product CNC, exception escapes `_maybe_exit` (aborts the replay).
4. **Can `request()` execute twice?** The second call returns the existing request (same id, one row) — see Part 3 item 2 probe.
5. **Can `resolve()` execute twice?** Yes but idempotent: a repeat or a conflicting outcome returns the stored snapshot with unchanged revision — Part 3 item 5 probe.
6. **Can B start before confirmed conversion?** B_OPEN is reachable only through `resolve(ACKNOWLEDGED)` (`acknowledge_transfer`, line cited above). `evaluate()` additionally raises unless `owner_engine==ENGINE_B and state==B_OPEN`.
7. **Can A continue managing after B_OPEN?** The A-only stop ratchet is inside `if record.lifecycle_state==A_OPEN:`; after B_OPEN, `handle_bar` returns True and legacy exits do not run (Part 7 probe).
8. **Is B exposure/margin reserved anywhere?**
```
$ grep -n -i 'reserve\|margin\|budget\|capital\|exposure' revision5/handoff_manager.py revision5/combined_cycle_runtime.py revision5/combined_cycle_store.py revision5/engine_b_management.py revision2_external/paper_execution.py | head; echo "(matches above, if any)"
revision5/combined_cycle_runtime.py:116:        # Preserve mandatory portfolio protection before every discretionary controller.
revision2_external/paper_execution.py:98:                   "orders": orders, "holdings": [], "margins": {},
(matches above, if any)
[exit status 0]
```
9. **Is CNC/delivery costing used anywhere?**
```
$ grep -n -i 'cnc\|delivery\|leg_cost\|stamp\|dp_charge\|booked_costs' revision5/handoff_manager.py revision5/combined_cycle_runtime.py revision5/engine_b_management.py revision2_external/paper_execution.py revision2/transaction_costs.py | head -20
revision5/handoff_manager.py:33:    requested_at: pd.Timestamp
revision5/handoff_manager.py:43:    def _local(timestamp):
revision5/handoff_manager.py:44:        stamp = pd.Timestamp(timestamp)
revision5/handoff_manager.py:45:        return stamp.tz_localize('Asia/Kolkata') if stamp.tzinfo is None else stamp.tz_convert('Asia/Kolkata')
revision5/handoff_manager.py:47:    def qualifies(self, record, timestamp, current_r, mfe_r, trend_aligned, completed_bar=True):
revision5/handoff_manager.py:48:        clock = self._local(timestamp).strftime('%H:%M')
revision5/handoff_manager.py:55:    def request(self, position_id, quantity, timestamp, *, current_r, mfe_r, trend_aligned, completed_bar=True):
revision5/handoff_manager.py:60:        if not self.qualifies(snapshot.record,timestamp,current_r,mfe_r,trend_aligned,completed_bar):
revision5/handoff_manager.py:64:        request = ConversionRequest(uuid4().hex, position_id,snapshot.record.symbol,quantity,self._local(timestamp))
revision5/handoff_manager.py:76:    def resolve(self, request_id, status, product=None, quantity=None, timestamp=None):
revision5/handoff_manager.py:100:                if timestamp is None or product != 'CNC' or quantity != receipt['quantity']:
revision5/handoff_manager.py:101:                    raise ValueError('acknowledgement requires exact product, quantity and timestamp')
revision5/handoff_manager.py:102:                stamp = self._local(timestamp)
revision5/handoff_manager.py:103:                if stamp.date() != receipt['requested_at'].date() or stamp < receipt['requested_at'] or stamp.strftime('%H:%M') > self.config.deadline:
revision5/handoff_manager.py:110:            receipt['resolved_at'] = timestamp
revision5/combined_cycle_runtime.py:74:                    if previous is None or pd.Timestamp(value['last_timestamp'])>pd.Timestamp(previous['last_timestamp']):
revision5/combined_cycle_runtime.py:85:    def _receipt(self, engine, request, timestamp):
revision5/combined_cycle_runtime.py:91:        return self.handoff.resolve(request.request_id,receipt['status'],receipt.get('product'),receipt.get('quantity'),receipt.get('timestamp',timestamp))
revision5/combined_cycle_runtime.py:93:    def handle_bar(self, engine, symbol, timestamp, bar, session_last_bar):
revision5/combined_cycle_runtime.py:114:            snapshot=self._receipt(engine,request,timestamp); self._sync(engine,snapshot); record=snapshot.record
[exit status 0]
```
## PART 7 — Engine B authority trace

**orchestrator _maybe_exit control flow (start)** `revision2_external/orchestrator.py:1093-1116`
```python
 1093      def _maybe_exit(
 1094          self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool,
 1095          chart_studies_confidence: float, chart_studies_audit: Optional[Dict[str, Any]] = None,
 1096      ) -> None:
 1097          trade = self.open_trades.get(symbol)
 1098          if trade is None:
 1099              return
 1100          # Completed fill bar is elapsed bar 0; the clock owns this counter.
 1101          state = self._exit_controller_states.get(symbol)
 1102          if state is not None:
 1103              state.bars_held = int(held_bars)
 1104          trade["_terminal_bar"] = dict(bar)
 1105          if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
 1106                  self, symbol, timestamp, bar, session_last_bar):
 1107              return
 1108          studies_direction = (chart_studies_audit or {}).get("direction")
 1109          if studies_direction is not None and studies_direction != (1 if trade["side"] == "BUY" else -1):
 1110              chart_studies_confidence = 0.0
 1111          # Intraday square-off duties belong to Engine A only.  An Engine B (B_OPEN / CNC) position
 1112          # bypasses force_close_time and the MIS session close, but stays under every protective exit
 1113          # below: drawdown halt, MiCOM trip, hard/governor stop, target, max hold and governor exits.
 1114          owned_by_engine_a = self._owner_engine(trade) == lifecycle.ENGINE_A
 1115          if owned_by_engine_a and (pd.Timestamp(timestamp).strftime("%H:%M")
 1116                                    >= self.entry_decision_engine.config.force_close_time):
```
`handle_bar` return semantics (line numbers from the file above): `return False` at the end of the handoff branch when no request/pending/non-B outcome (A_OPEN, falls through to the legacy path); `return True` after protective exits (`_execute_exit(...);return True`) and unconditionally at the end of the B management path (`return True`). HANDOFF_PENDING = lifecycle `TRANSFER_REQUESTED`: resolved first in `handle_bar` (`if record.lifecycle_state==TRANSFER_REQUESTED:` -> `_receipt`), then falls to the shared protective checks and, if still not B_OPEN after the request, `return False`.
**TRANSFER_REQUESTED branch** `revision5/combined_cycle_runtime.py:110-115`
```python
  110          if record.lifecycle_state==TRANSFER_REQUESTED:
  111              from revision5.handoff_manager import ConversionRequest
  112              data=next(x for x in self.store.requests() if x['position_id']==record.position_id)
  113              request=ConversionRequest(**{k:v for k,v in data.items() if k in ConversionRequest.__dataclass_fields__})
  114              snapshot=self._receipt(engine,request,timestamp); self._sync(engine,snapshot); record=snapshot.record
  115          self.reconcile(engine,snapshot)
```
Executed authority probe (real `_maybe_exit`; B trades created by the real handoff path; see PROBE SHIM note in Part 6):

```json
{
 "B: 15:25 force_close_time": {
  "memory_lifecycle": "B_OPEN",
  "durable_lifecycle": "B_OPEN",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "e0129451",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [
   "INFY"
  ],
  "completed": []
 },
 "B: session_last_bar (mis_session_close)": {
  "memory_lifecycle": "B_OPEN",
  "durable_lifecycle": "B_OPEN",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "ab94ca27",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [
   "INFY"
  ],
  "completed": []
 },
 "B: held_bars=999999 (max_hold ceiling 375)": {
  "memory_lifecycle": "B_OPEN",
  "durable_lifecycle": "B_OPEN",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "a06956b9",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [
   "INFY"
  ],
  "completed": []
 },
 "B: stop touched": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "b16ce1d2",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [],
  "completed": [
   "stop_gap"
  ]
 },
 "B: opening gap through stop": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "f1f3edcc",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [],
  "completed": [
   "stop_gap"
  ]
 },
 "B: drawdown halt": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "1ac23f81",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [],
  "completed": [
   "forced_close_drawdown_halt"
  ]
 },
 "B: MiCOM trip": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "d8e04070",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [],
  "completed": [
   "micom_trip:ANSI_X"
  ]
 },
 "B: governor_authority=full (governor never consulted)": {
  "memory_lifecycle": "B_OPEN",
  "durable_lifecycle": "B_OPEN",
  "owner": "ENGINE_B",
  "broker_product": "CNC",
  "conversion_rows": [
   [
    "2bd04997",
    "ACKNOWLEDGED"
   ]
  ],
  "open_trades": [
   "INFY"
  ],
  "completed": []
 },
 "A_OPEN (no handoff yet): 15:25": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_A",
  "broker_product": "MIS",
  "conversion_rows": [],
  "open_trades": [],
  "completed": [
   "force_close_time"
  ]
 },
 "A_OPEN: session_last_bar": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_A",
  "broker_product": "MIS",
  "conversion_rows": [],
  "open_trades": [],
  "completed": [
   "mis_session_close"
  ]
 },
 "A_OPEN: max_hold": {
  "memory_lifecycle": "CLOSED",
  "durable_lifecycle": "CLOSED",
  "owner": "ENGINE_A",
  "broker_product": "MIS",
  "conversion_rows": [],
  "open_trades": [],
  "completed": [
   "max_hold"
  ]
 }
}
```
| EXIT AUTHORITY | ENGINE A (legacy path, runtime attached, not handed off) | ENGINE B (B_OPEN) | ACTUAL CALL PATH | TEST |
|---|---|---|---|---|
| force_close_time 15:25 | EXIT:force_close_time | no exit | A: `revision2_external/orchestrator.py:1115`; B: skipped because `handle_bar` returned True | tests/test_position_lifecycle_contract.py (A and B), test_combined_cycle_runtime.py (B session close) |
| session close (mis_session_close) | EXIT:mis_session_close | no exit | A: `revision2_external/orchestrator.py:1300` | same |
| max hold (375) | EXIT:max_hold | no exit | A: `revision2_external/orchestrator.py:1305`; B: not evaluated in `handle_bar` | tests/test_position_lifecycle_contract.py asserts B max_hold only WITHOUT the runtime |
| hard stop / stop gap | legacy block lines 1279-1298 | EXIT:stop_gap; EXIT:stop_gap | B: `revision5/combined_cycle_runtime.py:122` block | test_combined_cycle_runtime.py (gap, next-bar stop) |
| target / target gap | same legacy block | same `handle_bar` block (price,reason) | same | partially (stop only asserted) |
| drawdown halt | line 1122 | EXIT:forced_close_drawdown_halt | B: `handle_bar` explicit copy | tests/test_position_lifecycle_contract.py (without runtime) |
| MiCOM trip | line 1127 | EXIT:micom_trip:ANSI_X | B: `handle_bar` explicit copy | same |
| governor HOLD/EXIT (position_decision) | `_governor_position_step` at line 1310 | no exit | B: never called (returns before) | none for B |
| ratchet / trailing | exit-controller / governor ratchet in legacy path | `EngineBController` proposed stop via `tighten_stop`, armed next bar | `handle_bar` end | test_engine_b_management.py, test_combined_cycle_runtime.py |
| structural reversal | not applicable | `EngineBController.evaluate` -> `engine_b_exit_pending` | `handle_bar` | test_engine_b_management.py (unit); runtime test none |
| session boundary (3 sessions) | not applicable | `ENGINE_B_MAX_SESSIONS` | `handle_bar` + `evaluate` | test_engine_b_management.py, test_combined_cycle_runtime.py (third session) |
| square-off (15:15 native) | not called (Part 10) | not called | none | none |
| saturation / regime-stressed exits | legacy path (lines after governor step) | not evaluated | none for B | none for B |
## PART 8 — Restart/restore forensics

```
$ grep -rn "\.restore(\|restore_snapshot(\|list_open(\|\.load(\|BrokerReconciliationService\|\.reconcile(" --include=*.py revision2_external revision5 scripts run_*.py runtime 2>/dev/null | grep -v '^scripts/diagnostics'
revision2_external/paper_execution.py:107:    def restore_snapshot(self, snapshot):
revision2_external/broker_reconciliation.py:17:class BrokerReconciliationService:
revision5/handoff_manager.py:56:        snapshot = self.store.load(position_id)
revision5/handoff_manager.py:84:            snapshot = self.store.load(receipt['position_id'])
revision5/handoff_manager.py:114:            return self.store.load(record.position_id)
revision5/combined_cycle_store.py:56:    def list_open(self):
revision5/combined_cycle_store.py:57:        return [self.load(row[0]) for row in self.connection.execute('SELECT id FROM positions')
revision5/combined_cycle_store.py:58:                if self.load(row[0]).record.lifecycle_state != CLOSED]
revision5/combined_cycle_store.py:76:            old = self.load(record.position_id).record
revision5/combined_cycle_runtime.py:40:        snapshot=self.store.load(record.position_id)
revision5/combined_cycle_runtime.py:63:        snapshots=self.store.list_open()
revision5/combined_cycle_runtime.py:65:            self.reconcile(engine,snapshot)
revision5/combined_cycle_runtime.py:95:        snapshot=self.store.load(trade['trade_id'])
revision5/combined_cycle_runtime.py:108:                snapshot=self.store.load(record.position_id)
revision5/combined_cycle_runtime.py:115:        self.reconcile(engine,snapshot)
revision5/combined_cycle_runtime.py:167:                snapshot=self.store.load(record.position_id)
revision5/combined_cycle_runtime.py:168:                self.reconcile(engine,snapshot)
revision5/combined_cycle_runtime.py:172:        snapshot=self.store.load(record.position_id)
revision5/combined_cycle_runtime.py:194:        snapshot=self.store.load(engine.open_trades[symbol]['trade_id'])
revision5/combined_cycle_runtime.py:197:        self.reconcile(engine,snapshot)
scripts/run_r5_step5_candidate.py:442:    manifest = DatasetManifest.load(
scripts/deploy_48_optimized.py:30:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_infy_maruti_3year_real.py:37:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/production_backtest_complete.py:36:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_master_control_maruti.py:81:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_curve_entry_pid_shadow_week.py:61:    manifest = DatasetManifest.load(str(MANIFEST)); verified = verify_manifest(manifest)
scripts/analyze_chart_studies_intraday.py:35:    manifest = DatasetManifest.load(str(MANIFEST))
scripts/run_revision3_external_48symbol_FULL_3YEAR_calibration.py:43:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/test_10box_optimized.py:23:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_external_no_pid_one_signal_trace.py:75:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/run_external_horizon_paper_48symbol.py:102:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/convergence_final.py:24:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/compare_3year_calibration_results.py:28:                    return json.load(f)
scripts/compare_3year_calibration_results.py:41:            ckpt = json.load(f)
scripts/run_inhouse_engine_48symbol_FULL_3YEAR_calibration.py:43:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/deploy_48symbol_production.py:32:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_infy_maruti_one_day_real.py:37:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_revision3_inhouse_48symbol_FULL_3YEAR_calibration.py:43:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/parse_calibration_diagnostics.py:58:                data = json.load(f)
scripts/parse_calibration_diagnostics.py:74:                data = json.load(f)
scripts/run_curve_pid_setpoint_shadow_intraday.py:38:    manifest = DatasetManifest.load(str(MANIFEST)); verified = verify_manifest(manifest)
scripts/test_3symbol_calibration.py:12:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/test_48symbol_calibration.py:32:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_sealed_month.py:66:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_sealed_month.py:260:    ok, msg = ledger.reconcile(bar_data)
scripts/run_sealed_month.py:301:ok, msg = ledger.reconcile(final_bar_data)
scripts/run_inhouse_engine_48symbol_1month_calibration.py:59:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_external_pypfopt_48symbol_month.py:144:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/create_synthetic_nifty_vix.py:35:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/geometry_audit_shadow.py:45:    manifest = DatasetManifest.load(str(MANIFEST))
scripts/run_external_closed_loop_intraday_day.py:31:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/test_revision3_single_symbol_diagnostic.py:53:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/test_revision3_single_symbol_diagnostic.py:201:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/quick_diagnostic.py:18:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/test_five_layer_infy.py:40:    manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_curve_entry_pid_shadow_48symbol_intraday.py:66:    a = p.parse_args(); manifest = DatasetManifest.load(str(MANIFEST)); verified = verify_manifest(manifest)
scripts/baseline_engines_full_trace_single_symbol.py:270:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/test_atr_bypass_simple.py:51:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/test_grid_sync_with_pid.py:220:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/simulate_hmm_risk_hysteresis_2025.py:87:    manifest = DatasetManifest.load(str(MANIFEST))
scripts/monitor_full_3year_calibrations.py:25:            return json.load(f)
scripts/run_maruti_peer_pooled_september.py:32:    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
scripts/run_infy_6month_real.py:29:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/baseline_granular_stage_tracer.py:145:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/trace_all_ten_boxes_one_trade.py:30:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_revision2_calibration.py:68:    manifest = DatasetManifest.load(str(args.manifest))
scripts/parameter_optimization_loop.py:38:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_price_volume_study_reversal_shadow_48symbol.py:30:    m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
scripts/run_external_sizing_calibration.py:163:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/run_june_2025_interaction_holdout.py:15: m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
scripts/deploy_all_48_symbols.py:36:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/test_complete_grid_sync_integration.py:88:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/pid_controller_detailed_tracer.py:44:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_maruti_3year_instrumented.py:87:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_curve_synchronizer_shadow_intraday.py:29:    manifest = DatasetManifest.load(str(MANIFEST))
scripts/run_external_engine_48symbol_FULL_3YEAR_calibration.py:48:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_one_day_48symbol.py:54:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_one_day_48symbol.py:229:        ok, msg = ledger.reconcile(bar_data)
scripts/run_one_day_48symbol.py:267:ok, msg = ledger.reconcile(final_bar_data)
scripts/run_maruti_peer_pooled_dynamic_target_ray_repro.py:34:    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
scripts/run_breakout_retest_sealed.py:42: windows={'train':(a.train_start,a.validation_start),'validation':(a.validation_start,a.test_start),'test':(a.test_start,a.end)};m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
scripts/run_infy_3year_instrumented.py:87:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_2025_joint_probability_holdout.py:60:    manifest = DatasetManifest.load(str(MANIFEST))
scripts/run_curve_entry_pid_shadow_intraday.py:20:    a = p.parse_args(); manifest = DatasetManifest.load(str(MANIFEST)); checked = verify_manifest(manifest)
scripts/trace_composite_study_signal_real_infy.py:19:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/watch_calibration_results.py:105:                data = json.load(f)
scripts/run_breakout_continuation_sealed.py:43: windows={'train':(a.train_start,a.validation_start),'validation':(a.validation_start,a.test_start),'test':(a.test_start,a.end)};m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
scripts/infy_diagnostic.py:43:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/fast_convergence_tuning.py:33:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/diagnose_saturation_streaks_real_infy.py:43:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/trace_boxes_1_to_5_multi_signal.py:45:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_study_entry_shadow_intraday.py:30:    manifest = DatasetManifest.load(str(MANIFEST))
scripts/monitor_all_calibrations_dashboard.py:75:            return json.load(f)
scripts/run_causal_mean_reversion_shadow.py:167:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/sweep_atr_droop_multiplier.py:30:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/test_10box_system.py:36:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_external_closed_loop_intraday_48symbol.py:46:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/run_external_preentry_deferral_one_signal_shadow.py:54:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/run_external_entry_expectancy_collection.py:81:    manifest = DatasetManifest.load(str(MANIFEST_PATH))
scripts/run_reversal_cohort_shadow_48symbol.py:27: m=DatasetManifest.load(str(MANIFEST));v=verify_manifest(m)
scripts/backtest_with_causal_grid_gate.py:41:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_external_engine_calibration_smoke.py:42:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_maruti_peer_pooled_dynamic_target_walkforward.py:145:    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
scripts/test_revision04.py:19:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/trace_box6_mpc_infy.py:34:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_2025_feature_discovery.py:14: m=DatasetManifest.load(str(MANIFEST)); verified=verify_manifest(m)
scripts/validate_sealed_system.py:54:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/validate_sealed_system.py:199:    ok, msg = ledger.reconcile(bar_data)
scripts/quick_48symbol_validation.py:28:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_infy_3year_inhouse_instrumented.py:81:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_external_engine_48symbol_1month_calibration.py:63:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/poc_infy_1000_daily.py:36:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/run_r5_paper_replay.py:55:    manifest = DatasetManifest.load(str(ROOT/'revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json'))
scripts/screen_symbols_for_volatility.py:29:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/attribute_external_entry_exhaustion.py:192:    manifest = DatasetManifest.load(args.manifest)
scripts/trace_closed_loop_one_trade.py:26:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/run_external_grid_shadow_month.py:69:    stock_manifest = DatasetManifest.load(args.stock_manifest)
scripts/run_external_grid_shadow_month.py:79:    context_manifest = ExogenousContextManifest.load(args.context_manifest)
scripts/run_five_layer_infy_live.py:37:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/fix_infy_atr_pid.py:48:manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
scripts/test_atr_bypass_with_grid_sync.py:150:        manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
scripts/verify_pid_saturation_fix.py:54:    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
[exit status 0]
```
```
$ echo '--- the same names in tests:'; grep -rln "restore(\|restore_snapshot\|BrokerReconciliationService" --include=*.py tests tests_external
--- the same names in tests:
tests/test_combined_cycle_runtime.py
tests/test_paper_combined_cycle_adapter.py
tests/test_broker_reconciliation_lifecycle.py
[exit status 0]
```
Production callers of `CombinedCycleRuntime.restore`, `restore_snapshot`, `BrokerReconciliationService.reconcile`: none outside the defining modules (`CombinedCycleRuntime.restore` calls `self.reconcile` and `store.list_open`; no orchestrator or runner calls `restore`).
| Restart state | IMPLEMENTED CODE | UNIT-TESTED BEHAVIOR | ACTUALLY WIRED RUNTIME BEHAVIOR |
|---|---|---|---|
| A_OPEN | `restore` reconciles broker vs durable record, re-inserts into `engine.open_trades`, bumps `_trade_sequence` | `test_restart_reconciles_before_restoring...` (mock Broker, record already B_OPEN) | none: `restore` is never called; a fresh replay starts with empty `open_trades` |
| HANDOFF_PENDING (TRANSFER_REQUESTED) | `restore` loads it; first `handle_bar` reads the broker receipt and resolves | `test_restart_duplicate_receipt_and_snapshot` (store+manager only, no runtime) | none |
| B_OPEN | `restore` + `persist_end_of_run(PERSIST)` | `test_restart_reconciles_before_restoring_and_does_not_repeat_conversion` (mock Broker) | none |
| CLOSED | `list_open` excludes CLOSED; `test_actual_orchestrator_hook_handoff_day2_gap` asserts `not runtime.store.list_open()` | unit | none |
## PART 9 — Standard runner connectivity

### Production instantiations of the orchestrator (excluding tests/, tests_external/, outputs/, docs/)
```
$ grep -rn "Revision2ExternalEngineOrchestrator(" --include=*.py . | grep -v '^./tests\|^./tests_external\|^./outputs\|^./docs\|__pycache__'
./scripts/run_r5_step5_candidate.py:705:    orch = Revision2ExternalEngineOrchestrator(
./scripts/deploy_48_optimized.py:72:orch = Revision2ExternalEngineOrchestrator(list(symbol_bars.keys()), registry, starting_equity=1_000_000.0)
./scripts/run_infy_maruti_3year_real.py:47:    orch = Revision2ExternalEngineOrchestrator(SYMBOLS, registry, starting_equity=1_000_000.0)
./scripts/production_backtest_complete.py:77:orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/run_master_control_maruti.py:118:        orch = Revision2ExternalEngineOrchestrator(
./scripts/run_external_no_pid_one_signal_trace.py:97:        seed_engine = Revision2ExternalEngineOrchestrator(
./scripts/run_external_no_pid_one_signal_trace.py:111:    engine = Revision2ExternalEngineOrchestrator(
./scripts/run_external_horizon_paper_48symbol.py:118:    report = Revision2ExternalEngineOrchestrator(
./scripts/convergence_final.py:65:orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/convergence_final.py:95:    orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/deploy_48symbol_production.py:86:orch = Revision2ExternalEngineOrchestrator(list(symbol_bars.keys()), registry, starting_equity=1_000_000.0)
./scripts/run_infy_maruti_one_day_real.py:50:    orch = Revision2ExternalEngineOrchestrator(SYMBOLS, registry, starting_equity=1_000_000.0)
./scripts/run_external_pypfopt_48symbol_month.py:156:    orchestrator = Revision2ExternalEngineOrchestrator(
./scripts/run_external_closed_loop_intraday_day.py:44:    orchestrator = Revision2ExternalEngineOrchestrator(
./scripts/quick_diagnostic.py:42:orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/baseline_engines_full_trace_single_symbol.py:90:        tracer.log("Instantiate", "RUNNING", f"Create Revision2ExternalEngineOrchestrator([{symbol}])")
./scripts/baseline_engines_full_trace_single_symbol.py:91:        orch = Revision2ExternalEngineOrchestrator(
./scripts/test_atr_bypass_simple.py:73:        orch = Revision2ExternalEngineOrchestrator(
./scripts/test_grid_sync_with_pid.py:253:        orch = Revision2ExternalEngineOrchestrator(
./scripts/run_maruti_peer_pooled_september.py:41:        report = Revision2ExternalEngineOrchestrator([symbol], CanonicalParameterRegistry(), telemetry_mode="compact").run({symbol: bars}, warmup=60)
./scripts/run_maruti_peer_pooled_september.py:50:    report = Revision2ExternalEngineOrchestrator(
./scripts/run_infy_6month_real.py:37:    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
./scripts/baseline_granular_stage_tracer.py:67:        orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/trace_all_ten_boxes_one_trade.py:38:    orch = Revision2ExternalEngineOrchestrator([SYMBOL], registry, starting_equity=1_000_000.0)
./scripts/parameter_optimization_loop.py:80:orch_baseline = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/parameter_optimization_loop.py:128:                    orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/run_external_sizing_calibration.py:136:    orchestrator = Revision2ExternalEngineOrchestrator(
./scripts/deploy_all_48_symbols.py:91:orch = Revision2ExternalEngineOrchestrator(
./scripts/test_complete_grid_sync_integration.py:117:        orch = Revision2ExternalEngineOrchestrator(
./scripts/pid_controller_detailed_tracer.py:52:    orch = Revision2ExternalEngineOrchestrator([SYMBOL], CanonicalParameterRegistry(), starting_equity=1_000_000.0)
./scripts/run_maruti_3year_instrumented.py:104:    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/run_external_engine_48symbol_FULL_3YEAR_calibration.py:102:        orch = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
./scripts/trace_boxes_7_to_10_multi_signal.py:54:    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
./scripts/run_infy_3year_instrumented.py:104:    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/diagnostics/block1_arm_worker.py:169:    orch = orch_module.Revision2ExternalEngineOrchestrator(
./scripts/infy_diagnostic.py:59:orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/fast_convergence_tuning.py:70:orch_base = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/fast_convergence_tuning.py:99:            orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/fast_convergence_tuning.py:146:orch_final = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=1_000_000.0)
./scripts/diagnose_saturation_streaks_real_infy.py:51:    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
./scripts/sweep_atr_droop_multiplier.py:41:    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
./scripts/run_external_closed_loop_intraday_48symbol.py:72:    orchestrator = Revision2ExternalEngineOrchestrator(
./scripts/run_external_entry_expectancy_collection.py:97:    report = Revision2ExternalEngineOrchestrator(
./scripts/backtest_with_causal_grid_gate.py:85:orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/run_maruti_peer_pooled_dynamic_target_walkforward.py:60:    return Revision2ExternalEngineOrchestrator(
./scripts/run_maruti_peer_pooled_dynamic_target_walkforward.py:80:        seed_report = Revision2ExternalEngineOrchestrator(
./scripts/r5_governor_trace.py:323:    orch = Revision2ExternalEngineOrchestrator(
./scripts/r5_governor_trace.py:361:    orch = Revision2ExternalEngineOrchestrator(
./scripts/trace_box6_mpc_infy.py:50:    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/quick_48symbol_validation.py:81:orch = Revision2ExternalEngineOrchestrator(list(symbol_bars.keys()), registry, starting_equity=1_000_000.0)
./scripts/run_external_engine_48symbol_1month_calibration.py:102:        orch = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
./scripts/poc_infy_1000_daily.py:80:orch = Revision2ExternalEngineOrchestrator(['INFY'], registry, starting_equity=100_000.0)
./scripts/run_r5_paper_replay.py:89:        orch = Revision2ExternalEngineOrchestrator(symbols,grid_context_provider=provider,
./scripts/trace_closed_loop_one_trade.py:33:    orch = Revision2ExternalEngineOrchestrator([SYMBOL], registry, starting_equity=1_000_000.0)
./scripts/run_external_grid_shadow_month.py:98:    engine = Revision2ExternalEngineOrchestrator(
./scripts/run_five_layer_infy_live.py:86:orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./scripts/fix_infy_atr_pid.py:100:orch_baseline = Revision2ExternalEngineOrchestrator(
./scripts/fix_infy_atr_pid.py:143:orch_fixed = Revision2ExternalEngineOrchestrator(
./scripts/test_atr_bypass_with_grid_sync.py:185:        orch = Revision2ExternalEngineOrchestrator(
./scripts/verify_pid_saturation_fix.py:63:    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
./revision3_external/orchestrator.py:30:        self._base_engine = Revision2ExternalEngineOrchestrator(
[exit status 0]
```
### Does any caller pass a runtime?
```
$ grep -rn "combined_cycle_runtime" --include=*.py --include=*.sh --include=*.json . | grep -v '^./tests/\|^./outputs\|^./docs\|__pycache__'
./revision2_external/orchestrator.py:141:        combined_cycle_runtime: Optional[Any] = None,
./revision2_external/orchestrator.py:143:        self.combined_cycle_runtime = combined_cycle_runtime
./revision2_external/orchestrator.py:1069:        if getattr(self, "combined_cycle_runtime", None) is not None:
./revision2_external/orchestrator.py:1070:            self.combined_cycle_runtime.register_fill(self._position_lifecycle[trade["trade_id"]], trade, self.broker)
./revision2_external/orchestrator.py:1076:            if getattr(self, "combined_cycle_runtime", None) is not None:
./revision2_external/orchestrator.py:1077:                self.combined_cycle_runtime.close(self._position_lifecycle[trade["trade_id"]], trade)
./revision2_external/orchestrator.py:1105:        if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
./revision2_external/orchestrator.py:1574:                if not upstream.admitted and not (getattr(self, "combined_cycle_runtime", None) is not None and symbol in self.open_trades):
./revision2_external/orchestrator.py:2117:            if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.persist_end_of_run(self, symbol):
[exit status 0]
```
### Calls to register_fill / handle_bar / close / persist_end_of_run / restore
```
$ grep -rn "\.register_fill(\|\.handle_bar(\|persist_end_of_run(\|combined_cycle_runtime\.close(\|\.restore(" --include=*.py revision2_external revision5 scripts runtime run_*.py 2>/dev/null
revision2_external/orchestrator.py:1070:            self.combined_cycle_runtime.register_fill(self._position_lifecycle[trade["trade_id"]], trade, self.broker)
revision2_external/orchestrator.py:1077:                self.combined_cycle_runtime.close(self._position_lifecycle[trade["trade_id"]], trade)
revision2_external/orchestrator.py:1105:        if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
revision2_external/orchestrator.py:2117:            if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.persist_end_of_run(self, symbol):
revision5/combined_cycle_runtime.py:193:    def persist_end_of_run(self, engine, symbol):
[exit status 0]
```
Classification of the production calls found: all four (register_fill, close, handle_bar, persist_end_of_run) are **behind the optional hook** `if getattr(self, 'combined_cycle_runtime', None) is not None` in `orchestrator.py` (lines in the diff of Part 1); there is no direct call from any runner, and no production call to `restore` at all. `scripts/run_r5_step5_candidate.py:705` (the Step-5 replay worker) constructs the orchestrator without the argument:
**scripts/run_r5_step5_candidate.py** `scripts/run_r5_step5_candidate.py:705-716`
```python
  705      orch = Revision2ExternalEngineOrchestrator(
  706          symbols,
  707          registry,
  708          calibration_overrides=params,
  709          starting_equity=equity,
  710          grid_context_provider=provider,
  711          real_plant_dcs=plant,
  712          plant_control_mode="PAPER_APPLY",
  713          closed_loop_mode="active_paper",
  714          telemetry_mode="compact",
  715          # V1 predates the key and ran with the advisory default.
  716          governor_authority=protocol["engine"].get("governor_authority", "advisory"),
```
## PART 10 — HRSG / machine / dispatch forensics

### HRSG
- RUNTIME STATE: **DEFINED_NOT_CALLED**
- ACTUATION EFFECT: none in replay: the plant constructs `self.hrsg` in `__init__`, but nothing calls `apply_hrsg_balance`/`record_hrsg_return_snapshot`
- DEFINITION:
```
$ grep -n "class HeatRecoverySteamGenerator\|self.hrsg = \|def apply_hrsg_balance\|def record_hrsg_return_snapshot" revision5/hrsg.py revision5/ccpp_unified_plant.py
revision5/hrsg.py:63:class HeatRecoverySteamGenerator:
revision5/ccpp_unified_plant.py:1269:        self.hrsg = HeatRecoverySteamGenerator(
revision5/ccpp_unified_plant.py:1850:    def apply_hrsg_balance(
revision5/ccpp_unified_plant.py:1941:    def record_hrsg_return_snapshot(
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -rn "apply_hrsg_balance(\|record_hrsg_return_snapshot(\|hrsg_status(" --include=*.py . | grep -v '^./tests\|^./tests_external\|^./outputs\|^./docs\|__pycache__' | grep -v 'def '; echo '(none above = no production caller)'
./revision5/ccpp_unified_plant.py:2162:        self.apply_hrsg_balance(
(none above = no production caller)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "apply_hrsg_balance\|record_hrsg_return_snapshot" --include=*.py tests
tests/test_revision5_hrsg_integration.py
[exit status 0]
```
### steam turbine bays (steam system as bays)
- RUNTIME STATE: **ACTIVE**
- ACTUATION EFFECT: steam-turbine-labelled bays are routed through the same bay governors/admission caps as the other bays in replay (symbol->bay mapping)
- DEFINITION:
```
$ grep -n "STEAM_TURBINE\|BPSTG\|CSTG" revision5/topology.py revision5/ccpp_unified_plant.py | head -8
revision5/topology.py:10:    CSTG1: Banking / Financial Services / Insurance
revision5/topology.py:11:    CSTG2: Auto / Consumer / FMCG / Retail / Aviation
revision5/topology.py:12:    BPSTG: Healthcare / Pharmaceuticals
revision5/topology.py:22:CSTG1_BFSI = "CSTG1_BFSI"
revision5/topology.py:23:CSTG2_CONSUMER_AUTO = "CSTG2_CONSUMER_AUTO"
revision5/topology.py:24:BPSTG_HEALTHCARE = "BPSTG_HEALTHCARE"
revision5/topology.py:55:    CSTG1_BFSI: (
revision5/topology.py:69:    CSTG2_CONSUMER_AUTO: (
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -n "_governor_for\|_bay_governors\|BPSTG\|CSTG" revision2_external/orchestrator.py | head -6
360:        #   * self._bay_governors[bay_id].register_trade(r)              -- local governor feedback
364:        self._bay_governors: Dict[str, BayTurbineClosedLoopGovernor] = {
373:            self._bay_governors = {key: bay.governor for key, bay in self.real_plant_dcs.bays.items()}
604:                    result = self._bay_governors[bay_id].cap_dispatch_entry(
623:    def _governor_for(self, symbol: str):
625:        return bay_id, (self._bay_governors.get(bay_id) if bay_id is not None else None)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "BPSTG" --include=*.py tests | head -3
tests/test_revision5_governor.py
tests/test_revision5_machine_archetypes.py
tests/test_r5_ccpp_mark_v.py
[exit status 0]
```
### TurbineMachineModel / machine dynamics
- RUNTIME STATE: **DEFINED_NOT_CALLED**
- ACTUATION EFFECT: none in replay
- DEFINITION:
```
$ grep -n "def install_machine_dynamics\|def run_machine_scan\|def configure_unit_machine\|def run_unit_machine_scan\|self.machine_model = " revision5/ccpp_unified_plant.py
724:    def install_machine_dynamics(
758:        self.machine_model = TurbineMachineModel(
768:    def run_machine_scan(
1582:    def configure_unit_machine(
1603:    def run_unit_machine_scan(
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -rn "install_machine_dynamics(\|run_machine_scan(\|configure_unit_machine(\|run_unit_machine_scan(" --include=*.py . | grep -v '^./tests\|^./tests_external\|^./outputs\|^./docs\|__pycache__' | grep -v 'def '; echo '(none above = no production caller)'
./revision5/ccpp_unified_plant.py:1312:            bay.install_machine_dynamics(
./revision5/ccpp_unified_plant.py:1596:        ].install_machine_dynamics(
./revision5/ccpp_unified_plant.py:1636:        result = bay.run_machine_scan(
(none above = no production caller)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "run_machine_scan\|configure_unit_machine\|run_unit_machine_scan" --include=*.py tests
tests/test_revision5_machine_dynamics.py
[exit status 0]
```
### startup synchronization
- RUNTIME STATE: **DEFINED_NOT_CALLED**
- ACTUATION EFFECT: none in replay (`PlantGridSynchronizer` in plant_control.py is a different, wired component)
- DEFINITION:
```
$ grep -n "def install_synchronizing_equipment\|def run_synchronizing_control\|def _startup_inputs" revision5/ccpp_unified_plant.py
365:    def install_synchronizing_equipment(
440:    def _startup_inputs(
506:    def run_synchronizing_control(
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -rn "install_synchronizing_equipment(\|run_synchronizing_control(" --include=*.py . | grep -v '^./tests\|^./tests_external\|^./outputs\|^./docs\|__pycache__' | grep -v 'def '; echo '(none above = no production caller)'
./revision5/ccpp_unified_plant.py:1404:        ].install_synchronizing_equipment(
./revision5/ccpp_unified_plant.py:1505:        result = bay.run_synchronizing_control(
(none above = no production caller)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "run_synchronizing_control\|install_synchronizing_equipment" --include=*.py tests
[exit status 1]
```
### grid relay (MiCOM)
- RUNTIME STATE: **ACTIVE**
- ACTUATION EFFECT: orchestrator evaluates the relay and can trip/close the intertie, which feeds `_micom_trip` -> fleet exits
- DEFINITION:
```
$ grep -n "self.grid_relay = \|def evaluate_grid_intertie" revision5/ccpp_unified_plant.py revision5/relay_coordination.py | head -4
revision5/ccpp_unified_plant.py:1245:        self.grid_relay = (
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -n "grid_relay" revision2_external/orchestrator.py | head
539:        relay = self.real_plant_dcs.grid_relay
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "grid_relay" --include=*.py tests | head -3
tests/test_r5_governor_authority.py
tests/test_revision5_native_dynamic_controls.py
tests/test_r5_paper_apply.py
[exit status 0]
```
### electrical network
- RUNTIME STATE: **ACTIVE**
- ACTUATION EFFECT: orchestrator opens/closes the grid intertie on the relay trip
- DEFINITION:
```
$ grep -n "self.electrical_network = " revision5/ccpp_unified_plant.py
1255:        self.electrical_network = (
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -n "electrical_network\|open_grid_intertie\|close_grid_intertie" revision2_external/orchestrator.py | head
564:        network = self.real_plant_dcs.electrical_network
568:                self.real_plant_dcs.open_grid_intertie(
574:            self.real_plant_dcs.close_grid_intertie()
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "electrical_network" --include=*.py tests | head -3
tests/test_r5_governor_authority.py
tests/test_revision5_electrical_network.py
tests/test_revision5_machine_dynamics.py
[exit status 0]
```
### DynamicBayLoadDispatcher
- RUNTIME STATE: **ACTIVE**
- ACTUATION EFFECT: merit weights feed `SectorDispatchController`; `register_trade` per close
- DEFINITION:
```
$ grep -n "class DynamicBayLoadDispatcher" revision5/ccpp_unified_plant.py
88:class DynamicBayLoadDispatcher:
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -n "merit_source\|dispatcher" revision2_external/orchestrator.py | head
361:        #   * self.plant_control.dispatch_controller.merit_source.register_trade(bay_id, r)
374:            self.plant_control.dispatch_controller.merit_source = self.real_plant_dcs.dispatcher
872:        self.plant_control.dispatch_controller.merit_source.register_trade(bay_id, realized_r)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "DynamicBayLoadDispatcher" --include=*.py tests | head -3
tests/test_revision5_plant_control.py
tests/test_r5_paper_apply_blocker_closure.py
[exit status 0]
```
### PlantControlChain
- RUNTIME STATE: **ACTIVE**
- ACTUATION EFFECT: grid->ECS->dispatch references; in `PAPER_APPLY` they cap entry quantity (`cap_dispatch_entry`); SHADOW mode is information-only
- DEFINITION:
```
$ grep -n "class PlantControlChain" revision5/plant_control.py
439:class PlantControlChain:
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -n "PlantControlChain\|plant_control.evaluate\|_paper_plant_entry_limit" revision2_external/orchestrator.py | head
74:    BayStatus, PlantControlChain, PlantControlError, PlantControlSnapshot, PlantControlMode,
338:        self.plant_control = PlantControlChain(
506:            snapshot = self.plant_control.evaluate(
585:    def _paper_plant_entry_limit(self, symbol, quantity, entry_price, timestamp):
1921:                    quantity = self._paper_plant_entry_limit(symbol, quantity, cap_price, timestamp)
1999:                    quantity = self._paper_plant_entry_limit(symbol, quantity, cap_price, timestamp)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "PlantControlChain" --include=*.py tests | head -3
tests/test_revision5_plant_control.py
[exit status 0]
```
### Engine-A interlocks (evaluate_entry / entry_allowed / on_trade_filled / build_engine_a_squareoff_intents)
- RUNTIME STATE: **DEFINED_NOT_CALLED**
- ACTUATION EFFECT: none: the replay uses its own admission and the safety-contract 15:20/15:25 times
- DEFINITION:
```
$ grep -n "def evaluate_entry\|def on_trade_filled\|def build_engine_a_squareoff_intents\|def entry_allowed" revision5/ccpp_unified_plant.py revision5/engine_state.py
revision5/ccpp_unified_plant.py:2056:    def evaluate_entry(
revision5/ccpp_unified_plant.py:2365:    def on_trade_filled(
revision5/ccpp_unified_plant.py:2426:    def build_engine_a_squareoff_intents(
revision5/engine_state.py:235:    def entry_allowed(
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -rn "evaluate_entry(\|on_trade_filled(\|build_engine_a_squareoff_intents(\|entry_allowed(\|EngineStateStore(" --include=*.py . | grep -v '^./tests\|^./tests_external\|^./outputs\|^./docs\|__pycache__' | grep -v 'def '; echo '(none above = no production caller)'
./revision5/ccpp_unified_plant.py:1279:        self.state_store = EngineStateStore(
./revision5/ccpp_unified_plant.py:2119:            self.state_store.entry_allowed(
(none above = no production caller)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "evaluate_entry\|on_trade_filled\|build_engine_a_squareoff_intents" --include=*.py tests
tests/test_r5_governor_authority.py
tests/test_revision5_closed_loop_unit_controls.py
tests/test_revision5_electrical_network.py
tests/test_revision5_bb09_bb10_parameterization.py
tests/test_revision5_ccpp_plant.py
tests/test_revision5_plant_control.py
tests/test_revision5_bb03_bb04_bridge.py
tests/test_revision5_hrsg_integration.py
[exit status 0]
```
### fleet-loading controller / PID
- RUNTIME STATE: **ABSENT**
- ACTUATION EFFECT: no module in the source tree; a copy exists only under `outputs/r5_remediation/phase1_flat_bar/sources/neutral/alternate/` (generated snapshot, never imported). My previous report said the code lives only in a separate checkout; the file listing above shows a snapshot copy inside this tree.
- DEFINITION:
```
$ ls revision5/fleet_loading_controller.py 2>&1; find . -name 'fleet_loading_controller.py' -not -path './.git/*' | head
ls: cannot access 'revision5/fleet_loading_controller.py': No such file or directory
./outputs/r5_remediation/phase1_flat_bar/sources/neutral/alternate/fleet_loading_controller.py
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py
[exit status 0]
```
- PRODUCTION CALLERS:
```
$ grep -rn "fleet_loading\|FleetLoading" --include=*.py . | grep -v '^./outputs/r5_remediation/phase1_flat_bar/sources/neutral/alternate/\|^./docs\|__pycache__' | head; echo '(none above = no import/caller)'
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:37:STATE_SCHEMA = "fleet_loading_controller/1"
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:54:class FleetLoadingPolicy:
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:84:class FleetLoadingTelemetry:
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:104:class FleetLoadingController:
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:105:    def __init__(self, policy: FleetLoadingPolicy) -> None:
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:106:        if not isinstance(policy, FleetLoadingPolicy):
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:107:            raise TypeError("policy must be a FleetLoadingPolicy")
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:114:    def policy(self) -> FleetLoadingPolicy:
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:118:               capacity_pu: float, protection_tripped: bool = False) -> FleetLoadingTelemetry:
./outputs/r5_remediation/phase1_flat_bar/sources/baseline/alternate/fleet_loading_controller.py:131:            return FleetLoadingTelemetry(error, self._integral, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, True)
(none above = no import/caller)
[exit status 0]
```
- TEST-ONLY CALLERS:
```
$ grep -rln "fleet_loading" --include=*.py tests | head
[exit status 0]
```
## PART 11 — Broker adapter change forensics

`git diff` of the file is in Part 1. BEFORE (HEAD blob) place_order for reference:
```
$ git show HEAD:revision2_external/broker_adapter_kite.py | sed -n '51,90p'
class KiteConnectBrokerAdapter(BrokerAdapter):
    environment = "live"

    def __init__(self, api_key: str, access_token: str, account_id: Optional[str] = None) -> None:
        super().__init__(account_id=account_id)
        self.client = KiteConnect(api_key=api_key)
        self.client.set_access_token(access_token)

    @_retry_transient
    def place_order(
        self, symbol: str, side: str, quantity: int, order_type: str,
        limit_price: Optional[float] = None, exchange: str = "NSE", product: str = "MIS",
    ) -> Dict[str, Any]:
        if side not in _SIDE_TO_TRANSACTION_TYPE:
            return {"passed": False, "reason": f"invalid side: {side}"}
        if order_type not in _ORDER_TYPE_MAP:
            return {"passed": False, "reason": f"invalid order_type: {order_type}"}
        if quantity <= 0:
            return {"passed": False, "reason": "quantity must be positive"}

        kwargs: Dict[str, Any] = dict(
            variety=self.client.VARIETY_REGULAR,
            exchange=exchange, tradingsymbol=symbol,
            transaction_type=_SIDE_TO_TRANSACTION_TYPE[side],
            quantity=int(quantity), order_type=_ORDER_TYPE_MAP[order_type],
            product=product,
        )
        if order_type == "LIMIT":
            if limit_price is None or limit_price <= 0:
                return {"passed": False, "reason": "LIMIT order requires a positive limit_price"}
            kwargs["price"] = float(limit_price)

        try:
            order_id = self.client.place_order(**kwargs)
        except NetworkException:
            raise  # let tenacity retry
        except KiteException as exc:
            return {"passed": False, "reason": f"broker rejected order: {exc}"}
        return {"passed": True, "order_id": order_id}

[exit status 0]
```
Executed side-by-side probe (`kite_probe.py`): HEAD blob vs current file, both against a `MagicMock` Kite client (no network, no credentials, no broker):
```json
{
 "S1 normal MARKET BUY accepted": {
  "OLD": {
   "result": {
    "passed": true,
    "order_id": "ORDER123"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  },
  "CURRENT": {
   "result": {
    "passed": true,
    "accepted": true,
    "filled": false,
    "order_id": "ORDER123",
    "status": "ACCEPTED"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  }
 },
 "S2 NetworkException x2 then success": {
  "OLD": {
   "result": {
    "passed": true,
    "order_id": "ORDER456"
   },
   "raised": null,
   "client.place_order calls": 3,
   "seconds": 1.5
  },
  "CURRENT": {
   "result": {
    "passed": false,
    "ambiguous": true,
    "retry_allowed": false,
    "reason": "timeout"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  }
 },
 "S3 NetworkException always (no correlation id)": {
  "OLD": {
   "result": null,
   "raised": "NetworkException: timeout",
   "client.place_order calls": 4,
   "seconds": 3.5
  },
  "CURRENT": {
   "result": {
    "passed": false,
    "ambiguous": true,
    "retry_allowed": false,
    "reason": "timeout"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  }
 },
 "S4 NetworkException with correlation_id and broker shows the order": {
  "OLD": {
   "result": null,
   "raised": "TypeError: KiteConnectBrokerAdapter.place_order() got an unexpected keyword argument 'correlation_id'",
   "client.place_order calls": 0,
   "seconds": 0.0
  },
  "CURRENT": {
   "result": {
    "passed": true,
    "accepted": true,
    "filled": true,
    "filled_quantity": 5,
    "order_id": "1",
    "status": "COMPLETE",
    "reconciled": true,
    "retry_allowed": false
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  }
 },
 "S5 broker rejection (KiteException)": {
  "OLD": {
   "result": {
    "passed": false,
    "reason": "broker rejected order: insufficient margin"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  },
  "CURRENT": {
   "result": {
    "passed": false,
    "accepted": false,
    "reason": "insufficient margin"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  }
 },
 "S6 float quantity 1.9": {
  "OLD": {
   "result": {
    "passed": true,
    "order_id": "<MagicMock name='mock.place_order()' id='128281100696272'>"
   },
   "raised": null,
   "client.place_order calls": 1,
   "seconds": 0.0
  },
  "CURRENT": {
   "result": {
    "passed": false,
    "reason": "invalid order arguments"
   },
   "raised": null,
   "client.place_order calls": 0,
   "seconds": 0.0
  }
 },
 "S7 convert_position MIS->CNC (OLD has no such method)": {
  "OLD": {
   "result": null,
   "raised": "AttributeError: 'KiteConnectBrokerAdapter' object has no attribute 'convert_position'",
   "client.place_order calls": 0,
   "seconds": 0.0
  },
  "CURRENT": {
   "result": {
    "passed": true,
    "accepted": true,
    "product_confirmed": true,
    "correlation_id": null,
    "retry_allowed": false,
    "confirmed_product": "CNC",
    "confirmed_quantity": 10
   },
   "raised": null,
   "client.place_order calls": 0,
   "seconds": 0.0
  }
 },
 "S8 snapshot() (OLD has no such method)": {
  "OLD": {
   "result": null,
   "raised": "AttributeError: 'KiteConnectBrokerAdapter' object has no attribute 'snapshot'",
   "client.place_order calls": 0,
   "seconds": 0.0
  },
  "CURRENT": {
   "result": {
    "passed": true,
    "positions": {
     "net": []
    },
    "orders": [],
    "holdings": [],
    "margins": {}
   },
   "raised": null,
   "client.place_order calls": 0,
   "seconds": 0.0
  }
 }
}
```
| Behaviour | OLD | CURRENT |
|---|---|---|
| place_order network failure | retried by tenacity (S2: 3 client calls then success; S3: 4 calls then raises NetworkException) | no retry: 1 client call, returns `{passed:false, ambiguous:true, retry_allowed:false}` (S2, S3) |
| tag/correlation reconciliation | absent (`correlation_id` kwarg -> TypeError, S4) | reads `orders()` by tag before and after a network error; returns the broker's order status (S4) |
| convert_position | absent (AttributeError, S7) | position-diff confirmation; one client call; `retry_allowed:false` (S7) |
| snapshot | absent (S8) | positions/orders/holdings/margins dict (S8) |
| float quantity | submitted (S6: client.place_order called once) | rejected, client not called (S6) |
| return shape on success | `{passed, order_id}` (S1) | `{passed, accepted, filled:false, order_id, status:'ACCEPTED'}` (S1) |
### The three failing tests: old expectation vs new implementation (no conclusion drawn)

```
$ PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -p no:cacheprovider -p no:warnings tests_external/test_broker_adapter_kite.py 2>&1 | tail -70
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collected 6 items

tests_external/test_broker_adapter_kite.py F..FF.                        [100%]

=================================== FAILURES ===================================
_____________ test_valid_market_order_is_translated_and_submitted ______________

    def test_valid_market_order_is_translated_and_submitted():
        adapter, client = _adapter()
        client.place_order.return_value = "ORDER123"
        result = adapter.place_order("INFY", "BUY", 10, "MARKET")
>       assert result == {"passed": True, "order_id": "ORDER123"}
E       AssertionError: assert {'passed': Tr...RDER123', ...} == {'passed': Tr...': 'ORDER123'}
E         
E         Omitting 2 identical items, use -vv to show
E         Left contains 3 more items:
E         {'accepted': True, 'filled': False, 'status': 'ACCEPTED'}
E         Use -v to get more diff

tests_external/test_broker_adapter_kite.py:24: AssertionError
__________ test_network_exception_is_retried_and_eventually_succeeds ___________

    def test_network_exception_is_retried_and_eventually_succeeds():
        adapter, client = _adapter()
        client.place_order.side_effect = [NetworkException("timeout"), NetworkException("timeout"), "ORDER456"]
        result = adapter.place_order("INFY", "BUY", 5, "MARKET")
>       assert result == {"passed": True, "order_id": "ORDER456"}
E       AssertionError: assert {'passed': Fa...n': 'timeout'} == {'passed': Tr...': 'ORDER456'}
E         
E         Differing items:
E         {'passed': False} != {'passed': True}
E         Left contains 3 more items:
E         {'ambiguous': True, 'reason': 'timeout', 'retry_allowed': False}
E         Right contains 1 more item:
E         {'order_id': 'ORDER456'}
E         Use -v to get more diff

tests_external/test_broker_adapter_kite.py:50: AssertionError
______________ test_network_exception_gives_up_after_max_attempts ______________

    def test_network_exception_gives_up_after_max_attempts():
        adapter, client = _adapter()
        client.place_order.side_effect = NetworkException("timeout")
>       with pytest.raises(NetworkException):
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       Failed: DID NOT RAISE NetworkException

tests_external/test_broker_adapter_kite.py:57: Failed
=========================== short test summary info ============================
FAILED tests_external/test_broker_adapter_kite.py::test_valid_market_order_is_translated_and_submitted
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_is_retried_and_eventually_succeeds
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_gives_up_after_max_attempts
========================= 3 failed, 3 passed in 0.13s ==========================
[exit status 0]
```
| Test | OLD EXPECTATION (test source) | NEW IMPLEMENTATION | TEST FAILURE | SAFETY CONSEQUENCE of the implementation difference |
|---|---|---|---|---|
| test_valid_market_order_is_translated_and_submitted | result == `{passed:True, order_id:'ORDER123'}` | extra keys `accepted`, `filled`, `status` | AssertionError (dict inequality, 3 extra items) | order acceptance is now distinguished from fill (`filled:false`) |
| test_network_exception_is_retried_and_eventually_succeeds | NetworkException x2 then success -> `{passed:True, order_id:'ORDER456'}` | 1 client call; `{passed:False, ambiguous:True, retry_allowed:False}` | AssertionError | a network error on a write is never resubmitted by the adapter; the caller receives an ambiguous result with no order id |
| test_network_exception_gives_up_after_max_attempts | `pytest.raises(NetworkException)` after retries | no exception; ambiguous dict | `Failed: DID NOT RAISE NetworkException` | callers must handle a returned ambiguous dict instead of an exception |
```
$ sed -n '1,60p' tests_external/test_broker_adapter_kite.py
import sys
sys.path.insert(0, ".")

from unittest.mock import MagicMock, patch

import pytest
from kiteconnect.exceptions import InputException, NetworkException

from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter


def _adapter():
    with patch("revision2_external.broker_adapter_kite.KiteConnect") as MockClient:
        instance = MockClient.return_value
        instance.VARIETY_REGULAR = "regular"
        adapter = KiteConnectBrokerAdapter(api_key="fake", access_token="fake")
        return adapter, instance


def test_valid_market_order_is_translated_and_submitted():
    adapter, client = _adapter()
    client.place_order.return_value = "ORDER123"
    result = adapter.place_order("INFY", "BUY", 10, "MARKET")
    assert result == {"passed": True, "order_id": "ORDER123"}
    kwargs = client.place_order.call_args.kwargs
    assert kwargs["tradingsymbol"] == "INFY"
    assert kwargs["transaction_type"] == "BUY"
    assert kwargs["quantity"] == 10
    assert kwargs["order_type"] == "MARKET"


def test_invalid_side_fails_closed_without_calling_the_broker():
    adapter, client = _adapter()
    result = adapter.place_order("INFY", "HOLD", 10, "MARKET")
    assert result["passed"] is False
    client.place_order.assert_not_called()


def test_limit_order_requires_a_positive_price():
    adapter, client = _adapter()
    result = adapter.place_order("INFY", "BUY", 10, "LIMIT", limit_price=None)
    assert result["passed"] is False
    client.place_order.assert_not_called()


def test_network_exception_is_retried_and_eventually_succeeds():
    adapter, client = _adapter()
    client.place_order.side_effect = [NetworkException("timeout"), NetworkException("timeout"), "ORDER456"]
    result = adapter.place_order("INFY", "BUY", 5, "MARKET")
    assert result == {"passed": True, "order_id": "ORDER456"}
    assert client.place_order.call_count == 3


def test_network_exception_gives_up_after_max_attempts():
    adapter, client = _adapter()
    client.place_order.side_effect = NetworkException("timeout")
    with pytest.raises(NetworkException):
        adapter.place_order("INFY", "BUY", 5, "MARKET")
    assert client.place_order.call_count == 4  # stop_after_attempt(4)

[exit status 0]
```
## PART 12 — Test evidence

Pytest commands I executed earlier in this session (from the conversation record; no shell-history file was consulted):
```
pytest -q tests/test_position_lifecycle_contract.py -p no:warnings                                  -> 67 passed (Phase 1)
pytest -q tests tests_external --ignore=tests/legacy_quarantine -p no:warnings   -> 904 passed, 7 skipped (Phase 1, before the uncommitted work)
pytest -v -p no:cacheprovider -p no:warnings <7 focused files incl. tests_external/test_broker_adapter_kite.py> -> 105 passed, 3 failed
pytest -q -p no:cacheprovider -p no:warnings tests tests_external --ignore=tests/legacy_quarantine        -> 3 failed, 936 passed, 7 skipped, 146 subtests passed in 507.51s
```
Log of the 936/3/7 run (still on disk), tail:
```
$ tail -8 /tmp/claude-1000/audit_full_suite.log

tests_external/test_broker_adapter_kite.py:57: Failed
=========================== short test summary info ============================
FAILED tests_external/test_broker_adapter_kite.py::test_valid_market_order_is_translated_and_submitted
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_is_retried_and_eventually_succeeds
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_gives_up_after_max_attempts
3 failed, 936 passed, 7 skipped, 146 subtests passed in 507.51s (0:08:27)
done
[exit status 0]
```
### Focused rerun now (no broker writes; mock/paper only)

```
$ PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -v -p no:cacheprovider -p no:warnings tests/test_position_lifecycle_contract.py tests/test_combined_cycle_handoff.py tests/test_combined_cycle_runtime.py tests/test_engine_b_management.py tests/test_paper_combined_cycle_adapter.py tests/test_broker_reconciliation_lifecycle.py tests_external/test_broker_adapter_kite.py 2>&1 | grep -v 'test_position_lifecycle_contract.py::.*PASSED'
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 108 items

tests/test_combined_cycle_handoff.py::test_restart_duplicate_receipt_and_snapshot PASSED [ 62%]
tests/test_combined_cycle_handoff.py::test_invalid_ack_rolls_back_unknown_and_rejection PASSED [ 63%]
tests/test_combined_cycle_handoff.py::test_closed_race_never_resurrects PASSED [ 64%]
tests/test_combined_cycle_handoff.py::test_stop_revision_and_serialization_faults PASSED [ 65%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:09-1-1-True-True] PASSED [ 66%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:15-1-1-True-True] PASSED [ 67%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-0.74-1-True-True] PASSED [ 68%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-1-0.74-True-True] PASSED [ 69%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-1-1-False-True] PASSED [ 70%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-1-1-True-False] PASSED [ 71%]
tests/test_combined_cycle_handoff.py::test_late_ack_requires_reconciliation PASSED [ 72%]
tests/test_combined_cycle_runtime.py::test_actual_orchestrator_hook_handoff_day2_gap PASSED [ 73%]
tests/test_combined_cycle_runtime.py::test_new_stop_effective_next_bar_not_same_low PASSED [ 74%]
tests/test_combined_cycle_runtime.py::test_third_session_close_and_product_mismatch PASSED [ 75%]
tests/test_combined_cycle_runtime.py::test_restart_reconciles_before_restoring_and_does_not_repeat_conversion PASSED [ 75%]
tests/test_engine_b_management.py::test_explicit_opt_in_and_owner PASSED [ 76%]
tests/test_engine_b_management.py::test_cross_session_hold_no_intraday_clock_or_micro_exit PASSED [ 77%]
tests/test_engine_b_management.py::test_structural_reversal_requires_confirmation_and_restart PASSED [ 78%]
tests/test_engine_b_management.py::test_trailing_monotonic_after_gap_and_next_bar PASSED [ 79%]
tests/test_engine_b_management.py::test_atr_and_causal_reference PASSED  [ 80%]
tests/test_engine_b_management.py::test_session_boundary_horizon_exits_without_fourth_session PASSED [ 81%]
tests/test_paper_combined_cycle_adapter.py::test_conversion_is_not_a_fill_and_duplicate_is_idempotent PASSED [ 82%]
tests/test_paper_combined_cycle_adapter.py::test_restart_preserves_costs_conversion_and_contingent_protection PASSED [ 83%]
tests/test_paper_combined_cycle_adapter.py::test_conversion_rejection_does_not_change_position PASSED [ 84%]
tests/test_broker_reconciliation_lifecycle.py::test_network_after_accept_reconciles_partial_without_duplicate PASSED [ 85%]
tests/test_broker_reconciliation_lifecycle.py::test_ambiguous_without_receipt_never_retries PASSED [ 86%]
tests/test_broker_reconciliation_lifecycle.py::test_duplicate_receipts_block PASSED [ 87%]
tests/test_broker_reconciliation_lifecycle.py::test_conversion_requires_product_quantity_confirmation PASSED [ 87%]
tests/test_broker_reconciliation_lifecycle.py::test_restart_expired_day_stop_blocks_risk PASSED [ 88%]
tests/test_broker_reconciliation_lifecycle.py::test_unknown_broker_position_blocks_risk PASSED [ 89%]
tests/test_broker_reconciliation_lifecycle.py::test_settled_holdings_are_reconciled_and_unknown_holdings_block PASSED [ 90%]
tests/test_broker_reconciliation_lifecycle.py::test_stop_trigger_and_pending_qty_must_cover_without_reversal PASSED [ 91%]
tests/test_broker_reconciliation_lifecycle.py::test_network_conversion_ack_confirms_without_retry PASSED [ 92%]
tests/test_broker_reconciliation_lifecycle.py::test_mixed_holdings_and_net_cannot_be_guessed PASSED [ 93%]
tests/test_broker_reconciliation_lifecycle.py::test_float_order_quantity_rejected_without_submission PASSED [ 94%]
tests_external/test_broker_adapter_kite.py::test_valid_market_order_is_translated_and_submitted FAILED [ 95%]
tests_external/test_broker_adapter_kite.py::test_invalid_side_fails_closed_without_calling_the_broker PASSED [ 96%]
tests_external/test_broker_adapter_kite.py::test_limit_order_requires_a_positive_price PASSED [ 97%]
tests_external/test_broker_adapter_kite.py::test_network_exception_is_retried_and_eventually_succeeds FAILED [ 98%]
tests_external/test_broker_adapter_kite.py::test_network_exception_gives_up_after_max_attempts FAILED [ 99%]
tests_external/test_broker_adapter_kite.py::test_broker_rejection_is_not_retried PASSED [100%]

=================================== FAILURES ===================================
_____________ test_valid_market_order_is_translated_and_submitted ______________

    def test_valid_market_order_is_translated_and_submitted():
        adapter, client = _adapter()
        client.place_order.return_value = "ORDER123"
        result = adapter.place_order("INFY", "BUY", 10, "MARKET")
>       assert result == {"passed": True, "order_id": "ORDER123"}
E       AssertionError: assert {'passed': Tr...RDER123', ...} == {'passed': Tr...': 'ORDER123'}
E         
E         Omitting 2 identical items, use -vv to show
E         Left contains 3 more items:
E         {'accepted': True, 'filled': False, 'status': 'ACCEPTED'}
E         
E         Full diff:
E           {...
E         
E         ...Full output truncated (6 lines hidden), use '-vv' to show

tests_external/test_broker_adapter_kite.py:24: AssertionError
__________ test_network_exception_is_retried_and_eventually_succeeds ___________

    def test_network_exception_is_retried_and_eventually_succeeds():
        adapter, client = _adapter()
        client.place_order.side_effect = [NetworkException("timeout"), NetworkException("timeout"), "ORDER456"]
        result = adapter.place_order("INFY", "BUY", 5, "MARKET")
>       assert result == {"passed": True, "order_id": "ORDER456"}
E       AssertionError: assert {'passed': Fa...n': 'timeout'} == {'passed': Tr...': 'ORDER456'}
E         
E         Differing items:
E         {'passed': False} != {'passed': True}
E         Left contains 3 more items:
E         {'ambiguous': True, 'reason': 'timeout', 'retry_allowed': False}
E         Right contains 1 more item:
E         {'order_id': 'ORDER456'}...
E         
E         ...Full output truncated (12 lines hidden), use '-vv' to show

tests_external/test_broker_adapter_kite.py:50: AssertionError
______________ test_network_exception_gives_up_after_max_attempts ______________

    def test_network_exception_gives_up_after_max_attempts():
        adapter, client = _adapter()
        client.place_order.side_effect = NetworkException("timeout")
>       with pytest.raises(NetworkException):
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       Failed: DID NOT RAISE NetworkException

tests_external/test_broker_adapter_kite.py:57: Failed
=========================== short test summary info ============================
FAILED tests_external/test_broker_adapter_kite.py::test_valid_market_order_is_translated_and_submitted
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_is_retried_and_eventually_succeeds
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_gives_up_after_max_attempts
======================== 3 failed, 105 passed in 1.04s =========================
[exit status 0]
```
## PART 13 — Test quality audit

Collected test inventory (function names, verbatim):
```
$ grep -n "^def test_" tests/test_combined_cycle_handoff.py tests/test_combined_cycle_runtime.py tests/test_engine_b_management.py tests/test_paper_combined_cycle_adapter.py tests/test_broker_reconciliation_lifecycle.py
tests/test_combined_cycle_handoff.py:19:def test_restart_duplicate_receipt_and_snapshot(tmp_path):
tests/test_combined_cycle_handoff.py:34:def test_invalid_ack_rolls_back_unknown_and_rejection(tmp_path):
tests/test_combined_cycle_handoff.py:43:def test_closed_race_never_resurrects(tmp_path):
tests/test_combined_cycle_handoff.py:50:def test_stop_revision_and_serialization_faults(tmp_path):
tests/test_combined_cycle_handoff.py:61:def test_qualification_gate(tmp_path,time,r,mfe,aligned,completed):
tests/test_combined_cycle_handoff.py:66:def test_late_ack_requires_reconciliation(tmp_path):
tests/test_combined_cycle_runtime.py:49:def test_actual_orchestrator_hook_handoff_day2_gap(tmp_path):
tests/test_combined_cycle_runtime.py:59:def test_new_stop_effective_next_bar_not_same_low(tmp_path):
tests/test_combined_cycle_runtime.py:68:def test_third_session_close_and_product_mismatch(tmp_path):
tests/test_combined_cycle_runtime.py:75:def test_restart_reconciles_before_restoring_and_does_not_repeat_conversion(tmp_path):
tests/test_engine_b_management.py:16:def test_explicit_opt_in_and_owner():
tests/test_engine_b_management.py:20:def test_cross_session_hold_no_intraday_clock_or_micro_exit():
tests/test_engine_b_management.py:26:def test_structural_reversal_requires_confirmation_and_restart():
tests/test_engine_b_management.py:38:def test_trailing_monotonic_after_gap_and_next_bar():
tests/test_engine_b_management.py:49:def test_atr_and_causal_reference():
tests/test_engine_b_management.py:57:def test_session_boundary_horizon_exits_without_fourth_session():
tests/test_paper_combined_cycle_adapter.py:15:def test_conversion_is_not_a_fill_and_duplicate_is_idempotent():
tests/test_paper_combined_cycle_adapter.py:27:def test_restart_preserves_costs_conversion_and_contingent_protection():
tests/test_paper_combined_cycle_adapter.py:45:def test_conversion_rejection_does_not_change_position():
tests/test_broker_reconciliation_lifecycle.py:13:def test_network_after_accept_reconciles_partial_without_duplicate():
tests/test_broker_reconciliation_lifecycle.py:19:def test_ambiguous_without_receipt_never_retries():
tests/test_broker_reconciliation_lifecycle.py:24:def test_duplicate_receipts_block():
tests/test_broker_reconciliation_lifecycle.py:29:def test_conversion_requires_product_quantity_confirmation():
tests/test_broker_reconciliation_lifecycle.py:38:def test_restart_expired_day_stop_blocks_risk():
tests/test_broker_reconciliation_lifecycle.py:48:def test_unknown_broker_position_blocks_risk():
tests/test_broker_reconciliation_lifecycle.py:52:def test_settled_holdings_are_reconciled_and_unknown_holdings_block():
tests/test_broker_reconciliation_lifecycle.py:59:def test_stop_trigger_and_pending_qty_must_cover_without_reversal():
tests/test_broker_reconciliation_lifecycle.py:70:def test_network_conversion_ack_confirms_without_retry():
tests/test_broker_reconciliation_lifecycle.py:77:def test_mixed_holdings_and_net_cannot_be_guessed():
tests/test_broker_reconciliation_lifecycle.py:82:def test_float_order_quantity_rejected_without_submission():
[exit status 0]
```
| FILE | PRODUCTION FUNCTION EXERCISED | MOCKED | REAL | WHAT IT DOES NOT PROVE |
|---|---|---|---|---|
| test_combined_cycle_handoff.py (6 tests) | `HandoffManager.request/resolve/qualifies`, `CombinedCycleStore.save/load` | none | store (SQLite in tmp_path), lifecycle contract, manager | no broker, no orchestrator; trade payload is `{'qty':..,'entry_time':..}`, not a real trade dict |
| test_combined_cycle_runtime.py (4 tests) | `CombinedCycleRuntime.handle_bar/restore/persist_end_of_run`, `Orchestrator._maybe_exit` (hook), `_register_position_lifecycle`, `_close_position_lifecycle` | `Broker` class (explicit `product` in get_position, canned receipts), `engine._execute_exit` replaced by a lambda, config/safety_contract `SimpleNamespace`, drawdown lambda | real store, manager, EngineBController, lifecycle; `Orchestrator.__new__` (constructor never run) | real paper broker, real `_execute_exit`, real `run()`, real trade dict, session loop; the broker product assumption that fails in the real replay |
| test_engine_b_management.py (6 tests) | `EngineBController.evaluate/export_state/restore_state` | none | controller + lifecycle | nothing about orchestrator/broker/persistence of the real trade |
| test_paper_combined_cycle_adapter.py (3 tests) | `CostedPaperBrokerAdapter.request_product_conversion/ensure_protection/snapshot/restore_snapshot`, `BrokerReconciliationService.reconcile` | none | paper adapter; `Revision2ExternalEngineOrchestrator(['TITAN'])` constructed normally only to read `safety_contract` | no replay; no `run()`; delivery costs |
| test_broker_reconciliation_lifecycle.py (11 tests) | `KiteConnectBrokerAdapter.place_order/convert_position`, `BrokerReconciliationService.reconcile` | Kite client (`Mock`) | adapter logic, reconciliation service | any real broker behavior; interaction with the runtime |
Direct answers (with evidence):
```
$ echo '--- tests passing combined_cycle_runtime into a normally constructed orchestrator:'; grep -rn "combined_cycle_runtime=" tests tests_external | head; echo '(none)'; echo '--- tests calling .run( on an orchestrator together with the runtime:'; grep -ln "CombinedCycleRuntime" tests/*.py tests_external/*.py | xargs grep -ln "\.run(" ; echo '(none)'; echo '--- __new__ usage:'; grep -n "__new__" tests/test_combined_cycle_runtime.py
--- tests passing combined_cycle_runtime into a normally constructed orchestrator:
tests/test_combined_cycle_runtime.py:29:    engine.combined_cycle_runtime=runtime;engine._position_lifecycle={};engine._exit_controller_states={};engine._micom_trip=None
(none)
--- tests calling .run( on an orchestrator together with the runtime:
(none)
--- __new__ usage:
28:    engine=Revision2ExternalEngineOrchestrator.__new__(Revision2ExternalEngineOrchestrator)
[exit status 0]
```
- Real orchestrator instantiated normally (constructor): yes in `tests/test_paper_combined_cycle_adapter.py` (only for `safety_contract`) and `tests/test_position_lifecycle_contract.py` (Phase 1, `_maybe_exit`/`_execute_exit` called directly, no runtime). Not with the runtime.
- `Orchestrator.run()` with the runtime: **no test**. `run()` without the runtime is covered by older tests (e.g. tests_external/test_orchestrator_end_to_end.py).
- Real trade dictionary through persistence: **no test** (tests use hand-written trade dicts); this audit's probes serialized real ones (Part 14).
- Persist-and-restore of a real trade dict: **no test**.
- A -> pending -> B -> exit across session/restart in one test: `test_actual_orchestrator_hook_handoff_day2_gap` (handoff, day-2 gap exit, mock Broker) and `test_restart_reconciles_before_restoring...` (restore, mock Broker) are separate; no single test combines pending, B, exit and restart through the real adapter.
## PART 14 — Serialization forensics

The runtime writes the live trade dict with `json.dumps(..., default=_default, allow_nan=False)`:
**revision5/combined_cycle_store.py** `revision5/combined_cycle_store.py:18-35`
```python
   18  def _default(value):
   19      if isinstance(value, (pd.Timestamp, datetime)):
   20          return {"__timestamp__": value.isoformat()}
   21      if isinstance(value, date):
   22          return {'__date__': value.isoformat()}
   23      if hasattr(value, 'item'):
   24          return value.item()
   25      raise TypeError(f"Unsupported durable value: {type(value).__name__}")
   26  
   27  
   28  def _hook(value):
   29      if set(value) == {'__timestamp__'}:
   30          return pd.Timestamp(value['__timestamp__'])
   31      if set(value) == {'__date__'}:
   32          return date.fromisoformat(value['__date__'])
   33      return value
   34  
   35
```
**revision5/combined_cycle_store.py** `revision5/combined_cycle_store.py:85-85`
```python
   85          payload = json.dumps(dict(record=asdict(record), trade=trade, protection=protection), default=_default, allow_nan=False)
```
Probe (`real_run.py baseline`, 8 symbols, Block 1, real `run()`): after every real `_maybe_exit` the live `open_trades[symbol]` dict was serialized with the store's exact `_default` and `allow_nan=False`.
Result: **49 of 49 serializations succeeded; failures: none**; trades in the run: 7. Key -> observed value types across all observations (includes `_terminal_bar`, `controller_exit_pending`, `closed_loop`):
```json
{
 "side": [
  "str"
 ],
 "entry_price": [
  "float"
 ],
 "stop_price": [
  "float"
 ],
 "target_price": [
  "float"
 ],
 "quantity": [
  "int"
 ],
 "minimum_hold_bars": [
  "int"
 ],
 "maximum_hold_bars": [
  "int"
 ],
 "exit_confidence_threshold": [
  "float"
 ],
 "entry_timestamp": [
  "str"
 ],
 "entry_atr": [
  "float"
 ],
 "planned_entry_price": [
  "float"
 ],
 "planned_stop_price": [
  "float"
 ],
 "planned_target_price": [
  "float"
 ],
 "candidate_id": [
  "str"
 ],
 "trade_id": [
  "str"
 ],
 "controller_exit_pending": [
  "NoneType",
  "dict"
 ],
 "closed_loop": [
  "dict"
 ],
 "governor_stop_price": [
  "float"
 ],
 "governor_mfe_r": [
  "float"
 ],
 "governor_entry_conviction": [
  "NoneType"
 ],
 "_terminal_bar": [
  "dict"
 ]
}
```
Not covered by the probe: numpy arrays (none observed), NaN/Infinity (none observed in these 7 trades), `engine_b_exit_pending` (holds a `pd.Timestamp`, handled by `_default`, only exists after handoff) and any trade that reaches the runtime path (Part 4 scenario C aborts at the first bar). Whether tests serialize the real object: no (Part 13). `register_fill` did serialize a real trade at the fill in scenario C (store row `trade-1 A_OPEN` exists).
## PART 15 — Live-safety evidence

```
$ grep -rn "live_trading_enabled\|OperatingMode.LIVE" --include=*.py . | grep -v '^./outputs\|^./docs\|__pycache__\|^./tests/\|^./tests_external/'
./revision2/orchestrator.py:104:            live_trading_enabled=False,
./revision2/portfolio_orchestrator.py:171:            live_trading_enabled=False,
./runtime/operating_mode.py:185:    live_trading_enabled: bool = False
./runtime/operating_mode.py:268:        if config.operating_mode is not OperatingMode.RESEARCH and config.operating_mode is not OperatingMode.BACKTEST and config.operating_mode is not OperatingMode.PAPER and config.operating_mode is not OperatingMode.LIVE:
./runtime/operating_mode.py:284:        if config.operating_mode == OperatingMode.LIVE and broker.environment != "live":
./runtime/operating_mode.py:296:        if config.operating_mode == OperatingMode.PAPER and config.live_trading_enabled:
./runtime/operating_mode.py:305:        if config.operating_mode == OperatingMode.LIVE:
./runtime/operating_mode.py:306:            if not config.live_trading_enabled:
./runtime/contract_validator.py:37:        elif config.operating_mode == OperatingMode.LIVE:
[exit status 0]
```
```
$ grep -rn "KiteConnectBrokerAdapter(\|broker_adapter_kite" --include=*.py --include=*.sh . | grep -v '^./outputs\|^./docs\|__pycache__'
./tests_external/test_broker_adapter_kite.py:9:from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter
./tests_external/test_broker_adapter_kite.py:13:    with patch("revision2_external.broker_adapter_kite.KiteConnect") as MockClient:
./tests_external/test_broker_adapter_kite.py:16:        adapter = KiteConnectBrokerAdapter(api_key="fake", access_token="fake")
./tests/test_broker_reconciliation_lifecycle.py:3:from revision2_external.broker_adapter_kite import KiteConnectBrokerAdapter
./tests/test_broker_reconciliation_lifecycle.py:15:    r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',10,'MARKET',correlation_id='abc')
./tests/test_broker_reconciliation_lifecycle.py:21:    r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',10,'MARKET',correlation_id='abc')
./tests/test_broker_reconciliation_lifecycle.py:26:    r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',10,'MARKET',correlation_id='abc')
./tests/test_broker_reconciliation_lifecycle.py:34:    assert KiteConnectBrokerAdapter(client=c).convert_position('TITAN',10)['product_confirmed']
./tests/test_broker_reconciliation_lifecycle.py:36:    assert not KiteConnectBrokerAdapter(client=c).convert_position('TITAN',10)['passed']
./tests/test_broker_reconciliation_lifecycle.py:74:    r=KiteConnectBrokerAdapter(client=c).convert_position('TITAN',10,correlation_id='transfer1')
./tests/test_broker_reconciliation_lifecycle.py:83:    c=client();r=KiteConnectBrokerAdapter(client=c).place_order('TITAN','BUY',1.9,'MARKET')
./revision2_external/orchestrator.py:18:  10. UnifiedExecution      -> kiteconnect+tenacity  (revision2_external.broker_adapter_kite;
./revision2_external/broker_adapter_kite.py:32:class KiteConnectBrokerAdapter(BrokerAdapter):
[exit status 0]
```
```
$ grep -rn "\.place_order(\|\.convert_position(" --include=*.py . | grep -v '^./outputs\|^./docs\|__pycache__\|^./tests/\|^./tests_external/'
./scripts/trace_all_ten_boxes_one_trade.py:128:    # Box 10: broker.place_order(...) -> fill
./scripts/trace_boxes_7_to_10_multi_signal.py:121:        fill = broker.place_order(symbol="INFY", side=side, quantity=qty, order_type="MARKET",
./revision4_upstox_sandbox/revision4_engine.py:84:                    broker_client.place_order(
./revision4_upstox_sandbox/run_real_bars_sandbox.py:108:                    client.place_order(
./blocks/block_7c_unified_execution_CORRECTED.py:159:            broker_order_id = kite_broker.place_order(
./revision2_external/orchestrator.py:885:        result = self.broker.place_order(
./revision2_external/orchestrator.py:2008:                fill = self.broker.place_order(
./revision2_external/paper_execution.py:17:        result = super().place_order(symbol, side, quantity, order_type, market_price,
./revision2_external/broker_adapter_kite.py:82:            oid = self.client.place_order(**kwargs)
./revision2_external/broker_adapter_kite.py:120:                accepted = self.client.convert_position(exchange=exchange, tradingsymbol=symbol,
./revision2/orchestrator.py:180:        result = self.broker.place_order(
./revision2/orchestrator.py:492:            fill = self.broker.place_order(
./revision2/portfolio_orchestrator.py:227:        result = self.broker.place_order(
./revision2/portfolio_orchestrator.py:543:                fill = self.broker.place_order(
[exit status 0]
```
```
$ grep -rln "KiteConnect(\|import kiteconnect\|from kiteconnect" --include=*.py . | grep -v '^./outputs\|^./docs\|__pycache__\|^./tests'
./download_nifty.py
./scripts/download_nifty50_kite_3year.py
./scripts/download_real_nifty_zerodha.py
./scripts/download_nifty_with_token.py
./scripts/download_nifty_from_zerodha.py
./revision2_external/broker_adapter_kite.py
[exit status 0]
```
```
$ git diff --quiet HEAD -- runtime/ && echo 'runtime/ (StartupGate, OperatingMode) unchanged vs HEAD'
runtime/ (StartupGate, OperatingMode) unchanged vs HEAD
[exit status 0]
```
**StartupGate LIVE guard (unchanged)** `runtime/operating_mode.py:305-318`
```python
  305          if config.operating_mode == OperatingMode.LIVE:
  306              if not config.live_trading_enabled:
  307                  passed = False
  308                  reasons.append("live trading disabled")
  309              if not signing_key:
  310                  passed = False
  311                  reasons.append("missing signing key")
  312              if not durable_db:
  313                  passed = False
  314                  reasons.append("durable database required")
  315              if getattr(broker, "account_id", None) != config.broker_account_id:
  316                  passed = False
  317                  reasons.append("broker account mismatch")
  318              if config.unresolved_reconciliation:
```
Call graph to a Kite write: `KiteConnectBrokerAdapter.place_order` -> `client.place_order`; `KiteConnectBrokerAdapter.convert_position` -> `client.convert_position`. Callers of those adapter methods outside tests: none (grep above). Other `.place_order(` call sites in the grep are not `KiteConnectBrokerAdapter`: `self.broker.place_order` in the orchestrators (paper broker), a legacy `blocks/block_7c_unified_execution_CORRECTED.py` that takes an injected `kite_broker` object with a different signature, and `revision4_upstox_sandbox` (Upstox sandbox client); no non-test module imports the block_7c or Upstox modules (grep in this audit: none), but their own entry points were not searched beyond importers (UNVERIFIED for those legacy modules). `KiteConnectBrokerAdapter` is imported only by `tests_external/test_broker_adapter_kite.py` and `tests/test_broker_reconciliation_lifecycle.py` (mock client). The orchestrator/standard runners use `CostedPaperBrokerAdapter` (`self.broker = CostedPaperBrokerAdapter(...)` in the orchestrator constructor) and `_assert_paper_plant_broker` rejects non-paper brokers in PAPER_APPLY. The `scripts/download_*` files import `kiteconnect` for market-data download only (no `place_order`/`convert_position` in the non-test grep above). Guards between a standard runner and a Kite write: there is no path; the only guard on the adapter itself is none (it will submit if someone constructs it with a live client); `StartupGate` governs the `LIVE` operating mode elsewhere and is unchanged.
**Final classification: PROVEN_UNREACHABLE_FROM_STANDARD_R5_PATH.** Basis: zero non-test importers of `broker_adapter_kite`, zero non-test callers of `.place_order(`/`.convert_position(` on a Kite client, `runtime/` unchanged. Separate fact (not the standard path): any external caller that constructs `KiteConnectBrokerAdapter` with a real client can issue orders through the new write methods; that route is REACHABLE_WITH_EXPLICIT_OPT_IN by construction.
## PART 16 — Generated / extraneous material in the dirty tree

Source and tests under evaluation are the 3 modified files, 5 new `revision*` modules and 5 new test files (Part 1 hashes). Everything else untracked:

```
$ git status --short | grep -v '^ M' 
?? docs/experiment_outputs/steam/after/block1/
?? docs/experiment_outputs/steam/before/block1/
?? docs/r5_remediation_project/
?? outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md
?? outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md
?? outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md
?? outputs/block1_isolation_final/
?? outputs/diagnostics/
?? outputs/r5_remediation/
?? revision2_external/broker_reconciliation.py
?? revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_store.py
?? revision5/engine_b_management.py
?? revision5/handoff_manager.py
?? scripts/diagnostics/
?? tests/test_block1_isolation_harness.py
?? tests/test_broker_reconciliation_lifecycle.py
?? tests/test_combined_cycle_handoff.py
?? tests/test_combined_cycle_runtime.py
?? tests/test_engine_b_management.py
?? tests/test_paper_combined_cycle_adapter.py
[exit status 0]
```
| Path | Classification |
|---|---|
| `outputs/block1_isolation_final/` | generated result (harness run, 1 repeat) |
| `outputs/diagnostics/` | diagnostic (earlier audit/architecture/phase reports; this file is also written under outputs/) |
| `outputs/r5_remediation/phase1_flat_bar/ (incl. sources/baseline, sources/neutral, sources/neutral/alternate/)` | duplicate source snapshot + generated experiment output (contains copies of the current modules and a fleet_loading_controller.py copy) |
| `outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md, outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md` | Claude receipt / work order |
| `docs/r5_remediation_project/ (claude/, archive/reviews/, archive/*.xlsx/json, audits/)` | Claude receipt + project documentation |
| `docs/r5_remediation_project/*.md/json plans` | project documentation |
| `docs/experiment_outputs/steam/{before,after}/block1/` | generated result (older steam-bay experiment) |
| `scripts/diagnostics/, tests/test_block1_isolation_harness.py` | harness belonging elsewhere |
| `outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md` | this package (the only intentional artifact) |
Unknown: none identified.
## PART 17 — Claim challenge (attempted falsification of the previous audit)

| ID | PREVIOUS CLAIM | CODE EVIDENCE | TEST EVIDENCE | VERDICT |
|---|---|---|---|---|
| C01 | ownership lifecycle implemented | `revision5/position_lifecycle.py` matrix (Part 3); Part 3 probes | tests/test_position_lifecycle_contract.py 67 passed (Part 12) | **PROVEN** |
| C02 | Engine-A exits implemented | `owned_by_engine_a` gating lines (Part 3); probe: A_OPEN at 15:25 -> `force_close_time`, session_last_bar -> `mis_session_close`, max_hold -> `max_hold` (Part 7) | tests/test_position_lifecycle_contract.py | **PROVEN** |
| C03 | A->B handoff partial | handoff request/resolve exist and execute (Part 6) but: no exposure/margin reservation, no delivery costing (greps Part 6), and the real replay aborts at the first bar after a fill (Part 4 C) | test_combined_cycle_handoff.py, test_combined_cycle_runtime.py (mock broker) | **PROVEN** |
| C04 | Engine-B management partial | controller exists and works on synthetic frames; never reached in a real replay (Part 4 C) | test_engine_b_management.py (unit) | **PROVEN** |
| C05 | broker reconciliation partial | `BrokerReconciliationService` has no production caller (Part 8); runtime uses its own `reconcile`; Kite `convert_position`/`snapshot` exercised only against mocks (Part 11) | test_broker_reconciliation_lifecycle.py, test_paper_combined_cycle_adapter.py | **PROVEN** |
| C06 | post-fill atomicity defective | exit side: probe shows broker flat + completed_trades appended + open_trades retained + memory CLOSED vs durable A_OPEN (Part 5). entry side: probes A/B (Part 4). Note: a second close attempt is refused by the pre-existing `_verify_broker_position_reconciles`, so duplicate-close/flip is NOT possible | none test this | **PROVEN** |
| C07 | restart recovery partial | `restore` implemented; zero production callers (Part 8) | test_combined_cycle_runtime.py restart test (mock Broker) | **PROVEN** |
| C08 | combined runtime complete in design | methods exist, but the design assumes `broker.get_position()['product']`, which the real paper adapter does not supply before conversion; the real replay aborts at the first bar (Part 4 C) | tests use a mock Broker that supplies it | **PARTIALLY_PROVEN** |
| C09 | store implemented | unit behavior (Part 13); real trade dict persisted at fill in the real run (`store_positions: trade-1 A_OPEN`, Part 4 C); 49/49 real-dict serializations (Part 14) | test_combined_cycle_handoff.py | **PROVEN** |
| C10 | handoff manager implemented | Part 3/6 probes: request once, resolve idempotent, late/invalid ack rejected | test_combined_cycle_handoff.py | **PROVEN** |
| C11 | standard runner does not invoke runtime | Part 9: no non-test caller passes `combined_cycle_runtime` | none | **PROVEN** |
| C12 | HRSG disconnected | Part 10: no production caller of `apply_hrsg_balance`/`record_hrsg_return_snapshot`; object is constructed in the plant `__init__` | tests/test_revision5_hrsg_integration.py | **PROVEN** |
| C13 | machine dynamics disconnected | Part 10: no production caller | tests/test_revision5_machine_dynamics.py | **PROVEN** |
| C14 | Engine-A interlocks disconnected | Part 10: no production caller of `evaluate_entry`/`on_trade_filled`/`build_engine_a_squareoff_intents`/`EngineStateStore(` | tests/test_revision5_ccpp_plant.py | **PROVEN** |
| C15 | BrokerReconciliationService tests-only | Part 8 grep | tests only | **PROVEN** |
| C16 | 936 passed / 3 failed / 7 skipped | log tail embedded in Part 12 (`3 failed, 936 passed, 7 skipped, 146 subtests passed in 507.51s`); the full suite was NOT rerun in this task | — | **PROVEN** |
| C17 | three failures caused by adapter semantic changes | Part 11 probe S1/S2/S3 reproduce exactly the three assertions' differences (extra keys; no retry; no raise) | tests_external/test_broker_adapter_kite.py | **PROVEN** |
| C18 | B bypasses legacy exit authorities | Part 7 probe: B survives 15:25, session close, held_bars=999999 and governor_authority=full; B still exits on stop, gap, drawdown, MiCOM | tests/test_position_lifecycle_contract.py (without runtime) and test_combined_cycle_runtime.py (partial) | **PROVEN** |
| C19 | real trade serialization untested | no test serializes a real trade dict (Part 13); probe shows real dicts DO serialize (49/49, Part 14), so no failure was found | — | **PROVEN (as 'untested'; no failure found by probe)** |
| C20 | duplicate reconciliation exists | `CombinedCycleRuntime.reconcile` and `BrokerReconciliationService.reconcile` each check position quantity/product and protective-order status/quantity/trigger independently (code in Part 2) | tests for each | **PROVEN** |
| C21 | .75R/15:10 defaults exist | `HandoffConfig`: `minimum_r: float = .75`, `start='15:10'`, `deadline='15:14'` | test_combined_cycle_handoff.py::test_qualification_gate | **PROVEN** |
| C22 | Engine-B costs/margin are not modeled | Part 6 greps (items 8, 9): no reserve/margin/delivery-cost match in the new modules; `CostedPaperBrokerAdapter.place_order` always books `leg_cost` | — | **PROVEN** |
| C23 | protection is simulated only | `ensure_protection` docstring and code: paper dict `_contingent_protection`, status 'TRIGGER PENDING', validity 'SIMULATED'; stop execution is by `handle_bar`/bars | test_paper_combined_cycle_adapter.py | **PROVEN** |
| C24 | cross-session replay conflicts with sealed block protocol | see protocol excerpt below: carry between blocks and post-boundary rows are forbidden; nothing forbids multi-session carry inside one block's consecutive sessions | — | **PARTIALLY_PROVEN** |
| C25 | nothing enables live trading | Part 15 greps and unchanged `runtime/` | — | **PROVEN** |
| C26 | Phase 1 note: `max_hold` and governor exits still apply to Engine B trades | true only without the runtime (B_OPEN cannot exist then); with the runtime `handle_bar` bypasses both (Part 7) | — | **DISPROVEN** |
| C27 | fleet-loading controller is not in this tree | a copy exists at `outputs/r5_remediation/phase1_flat_bar/sources/neutral/alternate/fleet_loading_controller.py` (Part 10); not in `revision5/`, never imported | — | **PARTIALLY_PROVEN** |

Protocol fields behind C24:
```
$ /home/srinivas/.venvs/zerodha-phase1-r5/bin/python - <<'EOF'
import json
p=json.load(open('revision5/step5_sealed_calibration_protocol_v2.json'))['block_execution_contract']
for k in ('state_carry_between_blocks','fresh_orchestrator_per_block','grid_rows_after_block_end_parsed','stock_post_block_boundary_rows_per_symbol','stock_rows_after_boundary_sentinel_parsed','target_sessions_are_scored_in_full','stock_post_block_boundary_row_on_trading_clock'):
    print(k, '=', p[k])
EOF
state_carry_between_blocks = False
fresh_orchestrator_per_block = True
grid_rows_after_block_end_parsed = False
stock_post_block_boundary_rows_per_symbol = 1
stock_rows_after_boundary_sentinel_parsed = False
target_sessions_are_scored_in_full = True
stock_post_block_boundary_row_on_trading_clock = False
[exit status 0]
```
Counts: {'PROVEN': 23, 'PARTIALLY_PROVEN': 3, 'DISPROVEN': 1, 'UNVERIFIED': 0}

## PART 18 — Final evidence package: end-of-audit state and integrity check

```
$ git status --short
 M revision2_external/broker_adapter_kite.py
 M revision2_external/orchestrator.py
 M revision2_external/paper_execution.py
?? docs/experiment_outputs/steam/after/block1/
?? docs/experiment_outputs/steam/before/block1/
?? docs/r5_remediation_project/
?? outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md
?? outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md
?? outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md
?? outputs/block1_isolation_final/
?? outputs/diagnostics/
?? outputs/r5_remediation/
?? revision2_external/broker_reconciliation.py
?? revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_store.py
?? revision5/engine_b_management.py
?? revision5/handoff_manager.py
?? scripts/diagnostics/
?? tests/test_block1_isolation_harness.py
?? tests/test_broker_reconciliation_lifecycle.py
?? tests/test_combined_cycle_handoff.py
?? tests/test_combined_cycle_runtime.py
?? tests/test_engine_b_management.py
?? tests/test_paper_combined_cycle_adapter.py
```
```
$ git diff --stat
 revision2_external/broker_adapter_kite.py | 158 +++++++++++++++++++-----------
 revision2_external/orchestrator.py        |  15 ++-
 revision2_external/paper_execution.py     | 109 +++++++++++++++++++++
 3 files changed, 224 insertions(+), 58 deletions(-)
```
```
$ git diff --name-status
M	revision2_external/broker_adapter_kite.py
M	revision2_external/orchestrator.py
M	revision2_external/paper_execution.py
```
```
$ git branch --show-current
feature/engine-ab-handoff-lifecycle
```
```
$ git rev-parse HEAD
411712afaecaeb44a6899a12db94ac547fef0186
```
Final SHA256 of the same audited files:
```
2a45ddc3d33a59065d130b33514d39f5536c1133b654170cc8ed75ff9b6f6705  revision2_external/broker_adapter_kite.py
e29348cad9b352a415226793171853aec37fa2ae817cc5fe76b6d7e4f5b53b55  revision2_external/broker_reconciliation.py
c32672308254fc73169f4924216b90b5e587cfbc3a0f6d879207592ab7810a87  revision2_external/orchestrator.py
2f301d0fb261675fc9f0cac6e5af4171d9d8ff22107f2a03735a2a717d1eaa15  revision2_external/paper_execution.py
8432563f9f0bd16a9bd4a2156dcb6cf27ccd7561d906616b73629d60173d1ec9  revision5/combined_cycle_runtime.py
44a1f15de7db53a1ae0fd1ea989960bf82ec49d09fe38d984ed6f757945d6cc3  revision5/combined_cycle_store.py
10489260037910cf153312c11ce048b1b8c56c5a28c31c9f7d1e771fc37d9a1b  revision5/engine_b_management.py
91139eeae0cf8294064e13c5e6b54bfb3d82b42c623482701f185b4e23acdce9  revision5/handoff_manager.py
34b8f94b9026604fe51fa5159f5bfc9ebaff26b687f10711d592b0af8c8da5e3  scripts/diagnostics/block1_arm_worker.py
274373ddc267e5f9bee8c340337827d60e1151c9b202e7e82fbe0e0cb6600455  scripts/diagnostics/block1_audit_report.py
f2d33e9643bf65a0a764f7be5a202314e24f128aa655757f07ab8aab2f9eb8bd  scripts/diagnostics/block1_isolation_harness.py
4c253cfa6b9064cb45faed99d32dd845f6b0e2290f31be00570faab5668e16ab  scripts/diagnostics/isolation_lib.py
bc0e3ffc02fcce2fc0cfef0d4c35ca30bd52b33c13252f67998f7d0f2a45645b  tests/test_block1_isolation_harness.py
3ff724cdc6d957f21a1f62cb69179d6a193abf5e9d385c791b2eeb6bd8569001  tests/test_broker_reconciliation_lifecycle.py
114b69a80f073eeec1db1ff23c7dbfb97de52dbb4ed7dc15bf0c8c7aec2f3a5b  tests/test_combined_cycle_handoff.py
3a542b035d8d360b8ff4d488292bb72335e523f573ee902b8a08fb470995b9fd  tests/test_combined_cycle_runtime.py
7d0546e8dbd09fda80d6dd84c4b94a0d6293390348b74a223a3456d64927e7d1  tests/test_engine_b_management.py
5e4d4516e8d27576664a0b5b024c11393b880f0ad69afd04eb4a403dc59819b8  tests/test_paper_combined_cycle_adapter.py
```
Comparison with the initial hashes (Part 1): **IDENTICAL for all 18 files**
`git status --short` entries added since the initial snapshot: ['?? outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md']; removed: none. (The single added entry is this report, `outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md`, the only intended artifact; `outputs/` contains tracked files, so the new file appears as its own status line.)

**SOURCE/TEST CONTENT CHANGED BY THIS AUDIT: NO**
