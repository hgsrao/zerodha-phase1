# R5-D01 independent acceptance audit

Read-only check. No production or test file was modified, nothing was committed or pushed, no live-broker code was run, D02 was not started. Probes were run from the session scratchpad and import the unmodified sources; the only file written into the repository is this report. Terminology: REAL = the repository's actual implementation; UNIT FIXTURE / MOCK / SHIM are used only as defined in the D01 protocol.
## CHECK 1 — Exact diff

### `git diff -- revision2_external/paper_execution.py` (tracked file; includes the PRE-EXISTING uncommitted change)
```
$ git diff -- revision2_external/paper_execution.py
diff --git a/revision2_external/paper_execution.py b/revision2_external/paper_execution.py
index 8a04975..66e61d0 100644
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
@@ -15,6 +17,9 @@ class CostedPaperBrokerAdapter(PaperBrokerAdapter):
         result = super().place_order(symbol, side, quantity, order_type, market_price,
                                      config, parameter_registry)
         if result["passed"]:
+            # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
+            # already read an absent product as MIS; a converted position keeps its explicit product.
+            self.positions[symbol].setdefault("product", "MIS")
             cost = leg_cost(result["filled_price"], result["filled_quantity"], side)
             self.booked_costs += cost
             self.cost_ledger.append({"order_id": result["order_id"], "cost": cost})
@@ -23,6 +28,113 @@ class CostedPaperBrokerAdapter(PaperBrokerAdapter):
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
### `git diff -- tests/test_r5_d01_product_reconciliation.py`
```
$ git diff -- tests/test_r5_d01_product_reconciliation.py; echo '(empty output: the test file is untracked, so git diff shows nothing; its full text:)'; cat -n tests/test_r5_d01_product_reconciliation.py
(empty output: the test file is untracked, so git diff shows nothing; its full text:)
     1	"""R5-D01 regression and negative-detection tests.
     2	
     3	Defect: a real paper fill left the broker position without the ``product`` that
     4	``CombinedCycleRuntime.reconcile`` compares against the lifecycle record, so the runtime aborted on the first
     5	bar after every fill.
     6	
     7	Classification of what these tests use (see the D01 report):
     8	  REAL PAPER BROKER   ``CostedPaperBrokerAdapter`` (and, in one negative test, the real parent ``PaperBrokerAdapter``)
     9	  REAL RUNTIME/STORE  ``CombinedCycleRuntime`` + ``CombinedCycleStore`` (SQLite file), ``HandoffManager``, ``EngineBController``
    10	  NORMAL ORCHESTRATOR ``Revision2ExternalEngineOrchestrator`` constructed normally (no ``__new__``); ``run()`` is NOT called here
    11	  SYNTHETIC_UNIT_FIXTURE the trade dict and its insertion into ``open_trades`` (the inputs ``run()`` would build).
    12	No mock, no shim, no monkey-patch.  Nothing here is replay evidence; the replay proof is the separate real-run command in
    13	outputs/R5_D01_evidence/.
    14	"""
    15	import pytest
    16	
    17	from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
    18	from revision5.combined_cycle_runtime import CombinedCycleReconciliationError, CombinedCycleRuntime
    19	from revision5.combined_cycle_store import CombinedCycleStore
    20	from revision5.engine_b_management import EngineBController, EngineBPolicy
    21	from revision5.handoff_manager import HandoffConfig, HandoffManager
    22	from runtime.operating_mode import PaperBrokerAdapter
    23	
    24	SYMBOL = "INFY"
    25	QTY = 10
    26	
    27	
    28	def build(tmp_path):
    29	    store = CombinedCycleStore(tmp_path / "cycle.db")
    30	    runtime = CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)),
    31	                                   EngineBController(EngineBPolicy(enabled=True)))
    32	    return Engine([SYMBOL], combined_cycle_runtime=runtime), runtime, store
    33	
    34	
    35	def fill(orch, side, quantity=QTY, via_parent=False):
    36	    """A real paper fill.  ``via_parent`` calls the real parent PaperBrokerAdapter.place_order directly."""
    37	    order = PaperBrokerAdapter.place_order if via_parent else type(orch.broker).place_order
    38	    result = order(orch.broker, SYMBOL, side, quantity, "MARKET", 100.0, orch.safety_contract.as_dict(), orch.registry)
    39	    assert result["passed"]
    40	    return result
    41	
    42	
    43	def open_registered(orch, side="BUY", via_parent=False):
    44	    """Fill, then register through the production path (_register_position_lifecycle -> runtime.register_fill)."""
    45	    entry = fill(orch, side, via_parent=via_parent)["filled_price"]
    46	    stop, target = (entry - 5, entry + 10) if side == "BUY" else (entry + 5, entry - 10)
    47	    trade = dict(side=side, entry_price=entry, quantity=QTY, stop_price=stop, target_price=target,       # SYNTHETIC_UNIT_FIXTURE
    48	                 minimum_hold_bars=2, maximum_hold_bars=375, entry_timestamp="2024-02-13 09:33", entry_atr=1.0,
    49	                 planned_entry_price=entry, planned_stop_price=stop, planned_target_price=target,
    50	                 trade_id="trade-1", candidate_id="candidate-1")
    51	    orch.open_trades[SYMBOL] = trade
    52	    orch._register_position_lifecycle(SYMBOL, trade, "2024-02-13 09:33")
    53	    return trade
    54	
    55	
    56	def reconcile(orch, runtime, store):
    57	    runtime.reconcile(orch, store.load("trade-1"))
    58	
    59	
    60	def test_concrete_broker_is_the_paper_implementation(tmp_path):
    61	    orch, _, _ = build(tmp_path)
    62	    assert type(orch.broker).__qualname__ == "CostedPaperBrokerAdapter"
    63	    assert orch.broker.environment == "paper"
    64	
    65	
    66	@pytest.mark.parametrize("side", ["BUY", "SELL"])
    67	def test_real_fill_carries_explicit_mis_product(tmp_path, side):
    68	    orch, _, _ = build(tmp_path)
    69	    fill(orch, side)
    70	    assert orch.broker.get_position(SYMBOL)["product"] == "MIS"
    71	    assert [p["product"] for p in orch.broker.snapshot()["positions"] if p["tradingsymbol"] == SYMBOL] == ["MIS"]
    72	
    73	
    74	def test_fill_does_not_overwrite_a_converted_product(tmp_path):
    75	    orch, _, _ = build(tmp_path)
    76	    fill(orch, "BUY")
    77	    assert orch.broker.request_product_conversion("c1", SYMBOL, QTY, timestamp="2024-02-13 15:10")["status"] == "ACKNOWLEDGED"
    78	    fill(orch, "BUY", quantity=5)
    79	    assert orch.broker.get_position(SYMBOL)["product"] == "CNC"
    80	
    81	
    82	@pytest.mark.parametrize("side", ["BUY", "SELL"])
    83	def test_reconcile_accepts_the_genuine_state_a_real_fill_creates(tmp_path, side):
    84	    orch, runtime, store = build(tmp_path)
    85	    open_registered(orch, side)
    86	    reconcile(orch, runtime, store)                      # must not raise
    87	    assert store.load("trade-1").record.product == "MIS"
    88	
    89	
    90	# ----------------------------------------------------------------------------- negative detection preserved
    91	
    92	def test_wrong_quantity_is_still_rejected(tmp_path):
    93	    orch, runtime, store = build(tmp_path)
    94	    open_registered(orch, "BUY")
    95	    fill(orch, "SELL", quantity=4)                       # real partial exit at the broker
    96	    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
    97	        reconcile(orch, runtime, store)
    98	
    99	
   100	def test_wrong_product_contradicting_the_lifecycle_is_still_rejected(tmp_path):
   101	    orch, runtime, store = build(tmp_path)
   102	    open_registered(orch, "BUY")                         # lifecycle A_OPEN / MIS
   103	    assert orch.broker.request_product_conversion("c1", SYMBOL, QTY, timestamp="2024-02-13 15:10")["status"] == "ACKNOWLEDGED"
   104	    assert store.load("trade-1").record.lifecycle_state == "A_OPEN"
   105	    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
   106	        reconcile(orch, runtime, store)                  # broker CNC vs lifecycle MIS
   107	
   108	
   109	def test_missing_broker_position_is_still_rejected(tmp_path):
   110	    orch, runtime, store = build(tmp_path)
   111	    open_registered(orch, "BUY")
   112	    fill(orch, "SELL")                                   # real full exit at the broker
   113	    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
   114	        reconcile(orch, runtime, store)
   115	
   116	
   117	def test_a_position_with_no_product_is_not_defaulted_by_the_runtime(tmp_path):
   118	    """The real parent PaperBrokerAdapter fills without a product key.  The runtime must still fail closed."""
   119	    orch, runtime, store = build(tmp_path)
   120	    open_registered(orch, "BUY", via_parent=True)
   121	    assert "product" not in orch.broker.get_position(SYMBOL)
   122	    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
   123	        reconcile(orch, runtime, store)
[exit status 0]
```
### Isolating the D01 hunk
The pre-existing uncommitted change is the +109-line block (conversion, protection, snapshot, restore, `_conversion_receipts`/`_contingent_protection` init). The D01 hunk is the three lines below, the only difference between the saved BEFORE copy and the current file:
```
$ diff /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/R5_D01_evidence/before/paper_execution.py.BEFORE revision2_external/paper_execution.py
19a20,22
>             # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
>             # already read an absent product as MIS; a converted position keeps its explicit product.
>             self.positions[symbol].setdefault("product", "MIS")
[exit status 1]
```
Current line numbers: comment lines 20-21, assignment line **22**.
```python
   14                      config=None, parameter_registry=None):
   15          if order_type != "MARKET":
   16              return {"passed": False, "reasons": ["Replay supports MARKET orders only"]}
   17          result = super().place_order(symbol, side, quantity, order_type, market_price,
   18                                       config, parameter_registry)
   19          if result["passed"]:
   20              # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
   21              # already read an absent product as MIS; a converted position keeps its explicit product.
   22              self.positions[symbol].setdefault("product", "MIS")
   23              cost = leg_cost(result["filled_price"], result["filled_quantity"], side)
   24              self.booked_costs += cost
   25              self.cost_ledger.append({"order_id": result["order_id"], "cost": cost})
   26              result["cost"] = cost
   27              # This adapter acknowledges synchronously in simulated event time.
   28              result["ack_elapsed_seconds"] = 0.0
```
### When does the assignment execute? (code + executed probe `hunk_semantics.py`)
It sits inside `if result["passed"]:` (line 19) after `super().place_order(...)` returned. Execution requires: order_type == MARKET (otherwise the method returns at line 16), the parent order passing its gate and filling. Executed on the real `CostedPaperBrokerAdapter`:
```json
{
 "A rejected (order_type LIMIT -> early return)": {
  "passed": false,
  "positions": {}
 },
 "B rejected/non-filled (quantity 0)": {
  "passed": false,
  "reasons": [
   "order quantity must be a positive integer"
  ],
  "positions": {}
 },
 "C MIS lifecycle: entry / add / partial exit / flatten": {
  "entry": {
   "quantity": 10,
   "avg_price": 100.05,
   "product": "MIS"
  },
  "add_same_direction": {
   "quantity": 15,
   "avg_price": 100.05,
   "product": "MIS"
  },
  "partial_reduction": {
   "quantity": 11,
   "avg_price": 100.05,
   "product": "MIS"
  },
  "flatten": {
   "quantity": 0,
   "avg_price": 100.05,
   "product": "MIS"
  }
 },
 "D exit on a position that has no product (created through the real parent class)": {
  "before_exit": {
   "quantity": 10,
   "avg_price": 100.05
  },
  "after_partial_exit": {
   "quantity": 6,
   "avg_price": 100.05,
   "product": "MIS"
  }
 },
 "E CNC position: add same direction, then flatten": {
  "after_add": {
   "quantity": 15,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "after_flatten": {
   "quantity": 0,
   "avg_price": 100.05,
   "product": "CNC"
  }
 },
 "F SELL larger than the long position (flip)": {
  "quantity": -15,
  "avg_price": 99.95,
  "product": "MIS"
 }
}
```
Answers:
* **Assigned only after a PASSED/FILLED paper order?** Yes (case A and B: rejected orders leave `positions` empty).
* **Can rejected/non-filled orders mutate product?** No: LIMIT order (early return) and quantity-0 order (parent rejects) created no position and no product.
* **Can an exit mutate product?** An exit runs the same line, but `setdefault` never changes an existing value (case C: MIS stays MIS through partial exit and flatten; case E: CNC stays CNC). It can only ADD `MIS` to a position that has no product (case D: a position created through the real parent class gained `product: MIS` on a later exit).
* **Can a zero-quantity position retain product?** Yes. After flatten the position dict remains with `quantity: 0` and its product (case C: MIS; case E: CNC). Nothing resets it (see Check 3).
## CHECK 2 — Contract challenge: does 'absent paper product means MIS' hold?

```
$ grep -rn "product" revision2_external/paper_execution.py revision5/combined_cycle_runtime.py revision5/handoff_manager.py revision5/engine_b_management.py revision2_external/broker_reconciliation.py runtime/operating_mode.py | cut -c1-190
revision2_external/paper_execution.py:20:            # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
revision2_external/paper_execution.py:21:            # already read an absent product as MIS; a converted position keeps its explicit product.
revision2_external/paper_execution.py:22:            self.positions[symbol].setdefault("product", "MIS")
revision2_external/paper_execution.py:31:    def request_product_conversion(self, request_id, symbol, quantity,
revision2_external/paper_execution.py:32:                                   from_product="MIS", to_product="CNC", timestamp=None):
revision2_external/paper_execution.py:35:        request = (symbol, int(quantity), from_product, to_product)
revision2_external/paper_execution.py:42:        passed = (from_product == "MIS" and to_product == "CNC"
revision2_external/paper_execution.py:44:                  and position.get("product", "MIS") == from_product)
revision2_external/paper_execution.py:46:            position["product"] = to_product
revision2_external/paper_execution.py:49:                   "product": position.get("product", "MIS"),
revision2_external/paper_execution.py:60:    def ensure_protection(self, symbol, stop_price, quantity, product="MIS"):
revision2_external/paper_execution.py:69:        if abs(position["quantity"]) != int(quantity) or position.get("product", "MIS") != product:
revision2_external/paper_execution.py:74:            "product": product, "quantity": int(quantity), "pending_quantity": int(quantity),
revision2_external/paper_execution.py:91:                           "product": self.get_position(order.symbol).get("product", "MIS"),
revision2_external/paper_execution.py:99:                                  "product": position.get("product", "MIS")}
revision5/combined_cycle_runtime.py:35:        protective_id=broker.ensure_protection(record.symbol,record.current_stop_price,int(trade['quantity']),record.product)
revision5/combined_cycle_runtime.py:50:        if abs(float(position.get('quantity',0))-expected)>1e-6 or position.get('product')!=snapshot.record.product:
revision5/combined_cycle_runtime.py:51:            raise CombinedCycleReconciliationError('broker quantity/product differs from durable owner')
revision5/combined_cycle_runtime.py:58:        if protective.get('product')!=snapshot.record.product or abs(float(protective.get('pending_quantity',protective.get('quantity',0)))-abs(expecte
revision5/combined_cycle_runtime.py:59:            raise CombinedCycleReconciliationError('paper contingent protection quantity/product differs')
revision5/combined_cycle_runtime.py:91:        return self.handoff.resolve(request.request_id,receipt['status'],receipt.get('product'),receipt.get('quantity'),receipt.get('timestamp',timesta
revision5/combined_cycle_runtime.py:105:                protection['protective_order_id']=engine.broker.ensure_protection(symbol,effective_stop,int(trade['quantity']),record.product)
revision5/combined_cycle_runtime.py:158:                engine.broker.request_product_conversion(request.request_id,symbol,request.quantity,from_product='MIS',to_product='CNC',timestamp=ts)
revision5/combined_cycle_runtime.py:164:                protection['protective_order_id']=engine.broker.ensure_protection(symbol,record.current_stop_price,int(trade['quantity']),record.produ
revision5/combined_cycle_runtime.py:187:        protection['protective_order_id']=engine.broker.ensure_protection(symbol,record.current_stop_price,int(trade['quantity']),record.product)
revision5/handoff_manager.py:76:    def resolve(self, request_id, status, product=None, quantity=None, timestamp=None):
revision5/handoff_manager.py:100:                if timestamp is None or product != 'CNC' or quantity != receipt['quantity']:
revision5/handoff_manager.py:101:                    raise ValueError('acknowledgement requires exact product, quantity and timestamp')
revision2_external/broker_reconciliation.py:28:            return (p.get('exchange', 'NSE'), p.get('tradingsymbol', p.get('symbol')), p.get('product'))
[exit status 0]
```
| FILE:LINE | FUNCTION | READ/WRITE | DEFAULT | MEANING |
|---|---|---|---|---|
| revision2_external/paper_execution.py:22 | `CostedPaperBrokerAdapter.place_order` | WRITE (only if absent) | "MIS" | D01: a filled position carries an explicit product; existing value never overwritten |
| runtime/operating_mode.py (`_apply_fill_to_position`, `get_position`) | parent `PaperBrokerAdapter` | neither reads nor writes `product` | none (key absent) | the parent has no product concept; a position is `{quantity, avg_price}` |
| revision2_external/paper_execution.py:44 | `request_product_conversion` (check) | READ | "MIS" | conversion is allowed only from MIS; an absent product counts as the MIS source |
| revision2_external/paper_execution.py:46 | `request_product_conversion` | WRITE | none (explicit `to_product`) | sets CNC on acknowledgement |
| revision2_external/paper_execution.py:49 | `request_product_conversion` (receipt) | READ | "MIS" | receipt reports the position's product |
| revision2_external/paper_execution.py:60-69 | `ensure_protection` | READ | "MIS" | protection must match the position product; absent = MIS |
| revision2_external/paper_execution.py:91 | `snapshot` (orders) | READ | "MIS" | published order view; absent = MIS |
| revision2_external/paper_execution.py:99 | `snapshot` (positions) | READ | "MIS" | published position view; absent = MIS |
| revision2_external/paper_execution.py:110 | `restore_snapshot` | WRITE (verbatim copy of `positions`) | none | restores stored dicts including any product key; no defaulting |
| revision5/combined_cycle_runtime.py:50 | `CombinedCycleRuntime.reconcile` | READ (strict) | NONE | must equal the lifecycle record's product; absent fails closed; UNCHANGED by D01 |
| revision5/combined_cycle_runtime.py:58 | `CombinedCycleRuntime.reconcile` (protection) | READ (strict) | NONE | protective order product must equal the record's |
| revision5/handoff_manager.py:100 | `HandoffManager.resolve` | READ (argument from the broker receipt) | none | acknowledgement requires the receipt product to be CNC |
| revision5/engine_b_management.py | `EngineBController` | no `product` read at all | n/a | uses lifecycle `owner_engine`/state only |
| revision2_external/broker_reconciliation.py:28 | `BrokerReconciliationService.reconcile` key() | READ | none (`p.get('product')`) | strict; tests-only consumer |

Independent semantic evidence for the MIS reading: the adapter's own conversion check, protection check and published snapshot all treat an absent product as MIS (rows above); the live adapter's order default is `product="MIS"` (`git show HEAD:revision2_external/broker_adapter_kite.py`, line 62); the lifecycle contract pins `A_OPEN`/`TRANSFER_REQUESTED` to MIS. I found no consumer that treats an absent product as CNC or as 'unknown'. The reading is not disproved.
### Is `setdefault("product", "MIS")` semantically correct per scenario? (executed: cases C, E, F above and Check 3)
| Scenario | Observed product | Correct? |
|---|---|---|
| fresh first entry | MIS | CORRECT |
| additional same-direction quantity | unchanged (MIS stays MIS, CNC stays CNC) | CORRECT (no overwrite) |
| partial reduction | unchanged | CORRECT |
| full flatten | product retained on the zero-quantity position | questionable but pre-existing representation (the dict is never deleted) |
| re-entry after flatten of a MIS position | MIS | CORRECT |
| post-conversion CNC position | CNC | CORRECT |
| re-entry after a previous CNC position | CNC (stale) | **INCORRECT for a new Engine-A/MIS entry; see Check 3; not caused by D01** |
| direction flip through a larger opposite order | product unchanged (MIS) | acceptable; no flip semantics are defined |
## CHECK 3 — Flat -> reopen (real `CostedPaperBrokerAdapter` only; no shim, no patch, no manual state edits)

Probe `flat_reopen.py` (scratchpad). Steps: BUY 10 (new MIS entry) -> real `request_product_conversion` (MIS->CNC) -> SELL 10 (flatten, quantity 0 confirmed by an assertion) -> BUY 10 again as a NEW Engine-A/MIS entry. The orchestrator is constructed only to obtain the real safety contract and registry the paper broker requires; its broker is used unmodified.
### Current tree (with the D01 hunk)
```
module file: /home/srinivas/projects/zerodha-r5-governor-refactor/revision2_external/paper_execution.py | class: CostedPaperBrokerAdapter | environment: paper
[
 {
  "step": "0 before any order",
  "position": {
   "quantity": 0,
   "avg_price": 0.0
  },
  "snapshot_position_product": null
 },
 {
  "step": "1 BUY 10 (new Engine-A/MIS entry)",
  "position": {
   "quantity": 10,
   "avg_price": 100.05,
   "product": "MIS"
  },
  "snapshot_position_product": "MIS"
 },
 {
  "step": "2 conversion receipt",
  "receipt_status": "ACKNOWLEDGED"
 },
 {
  "step": "2 after conversion MIS->CNC",
  "position": {
   "quantity": 10,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "snapshot_position_product": "CNC"
 },
 {
  "step": "3 SELL 10 (flatten)",
  "position": {
   "quantity": 0,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "snapshot_position_product": "CNC"
 },
 {
  "step": "4 BUY 10 again = NEW Engine-A/MIS entry",
  "position": {
   "quantity": 10,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "snapshot_position_product": "CNC"
 }
]
RESULT after reopen: product = CNC -> REOPEN_PRODUCT_STALE_CNC
```
### Pre-D01 temp copy (tree copy whose `paper_execution.py` is the saved BEFORE file, sha256 `2f301d0f...`; the probe prints the module path it imported)
```
module file: /tmp/claude-1000/d01_before_tree/revision2_external/paper_execution.py | class: CostedPaperBrokerAdapter | environment: paper
[
 {
  "step": "0 before any order",
  "position": {
   "quantity": 0,
   "avg_price": 0.0
  },
  "snapshot_position_product": null
 },
 {
  "step": "1 BUY 10 (new Engine-A/MIS entry)",
  "position": {
   "quantity": 10,
   "avg_price": 100.05
  },
  "snapshot_position_product": "MIS"
 },
 {
  "step": "2 conversion receipt",
  "receipt_status": "ACKNOWLEDGED"
 },
 {
  "step": "2 after conversion MIS->CNC",
  "position": {
   "quantity": 10,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "snapshot_position_product": "CNC"
 },
 {
  "step": "3 SELL 10 (flatten)",
  "position": {
   "quantity": 0,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "snapshot_position_product": "CNC"
 },
 {
  "step": "4 BUY 10 again = NEW Engine-A/MIS entry",
  "position": {
   "quantity": 10,
   "avg_price": 100.05,
   "product": "CNC"
  },
  "snapshot_position_product": "CNC"
 }
]
RESULT after reopen: product = CNC -> REOPEN_PRODUCT_STALE_CNC
```
**Result: REOPEN_PRODUCT_STALE_CNC — in both trees.**
Newly demonstrated defect candidate **R5-D-STALE-CNC** (registered, NOT fixed): after a position is converted to CNC and flattened, `positions[symbol]` keeps `product: CNC`, and a new entry in the same symbol inherits CNC. Existed before D01: **PRE_EXISTING**. Mechanism: `request_product_conversion` writes the product (line `position["product"] = to_product`), the parent's `_apply_fill_to_position` reuses the same dict via `setdefault` on the symbol and never clears keys, and nothing resets the product when `quantity` returns to 0. D01's `setdefault` does not touch an existing value, so it neither causes nor cures this. Consequence if it occurs in the runtime: a new A_OPEN/MIS record would be reconciled against a CNC broker position and `reconcile` would fail closed (it is detected, not silent). Not exercised by any replay in the evidence (no position was ever converted).
## CHECK 4 — Automated regression gap

```
$ grep -n "\.run(\|Orchestrator\|__new__\|prepare_block" tests/test_r5_d01_product_reconciliation.py
10:  NORMAL ORCHESTRATOR ``Revision2ExternalEngineOrchestrator`` constructed normally (no ``__new__``); ``run()`` is NOT called here
17:from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
[exit status 0]
```
```
$ echo '--- any permanent test combining CombinedCycleRuntime with an orchestrator .run():'; grep -ln "CombinedCycleRuntime" tests/*.py tests_external/*.py | xargs grep -ln "\.run(" ; echo '(none listed above)'; echo '--- permanent tests passing combined_cycle_runtime= into the normal constructor:'; grep -rn "combined_cycle_runtime=" tests tests_external ; echo '(only the D01 test builds Engine([..], combined_cycle_runtime=runtime) -- see next line)'; grep -n "combined_cycle_runtime=runtime" tests/test_r5_d01_product_reconciliation.py
--- any permanent test combining CombinedCycleRuntime with an orchestrator .run():
(none listed above)
--- permanent tests passing combined_cycle_runtime= into the normal constructor:
tests/test_combined_cycle_runtime.py:29:    engine.combined_cycle_runtime=runtime;engine._position_lifecycle={};engine._exit_controller_states={};engine._micom_trip=None
tests/test_r5_d01_product_reconciliation.py:32:    return Engine([SYMBOL], combined_cycle_runtime=runtime), runtime, store
(only the D01 test builds Engine([..], combined_cycle_runtime=runtime) -- see next line)
32:    return Engine([SYMBOL], combined_cycle_runtime=runtime), runtime, store
[exit status 0]
```
* Does ANY permanent D01 test call `Orchestrator.run()`? **NO.**
* Does ANY permanent test reproduce real paper broker + real `CombinedCycleRuntime` + normal Orchestrator + `Orchestrator.run()` + fill + next-bar reconciliation? **NO.** (`tests/test_combined_cycle_runtime.py` uses `Orchestrator.__new__` and a mock Broker; the D01 tests never call `run()`.)
Therefore D01 cannot yet satisfy the originally required permanent integration-regression criterion. The real-replay proof exists only as the external probe command in `outputs/R5_D01_evidence/` (which also depends on data and a symlink root outside the repository).
## CHECK 5 — External replay validity (BEFORE vs AFTER configuration)

| Item | BEFORE | AFTER | Same? |
|---|---|---|---|
| symbol(s) | `['TITAN']` | `['TITAN']` | YES |
| Block-1 slice sha256 (runtime run) | `c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50` | `c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50` | YES |
| Block-1 slice sha256 (baseline run) | `c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50` | `c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50` | YES |
| params file | `/home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_007.json` | `/home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_007.json` | YES |
| params sha256 (Trial 007) | `d563872531a950e721e86058493e489511585fc016a5c1209c486912885a6359` | `d563872531a950e721e86058493e489511585fc016a5c1209c486912885a6359` | YES |
| protocol v2 sha256 | `6861cdd27d7666fc6a1bf94c21e90c330052e8137973f7f07eda538566eb1ca7` | `6861cdd27d7666fc6a1bf94c21e90c330052e8137973f7f07eda538566eb1ca7` | YES |
| loader | `scripts/run_r5_step5_candidate.prepare_block` | `scripts/run_r5_step5_candidate.prepare_block` | YES |
| broker class | `revision2_external.paper_execution.CostedPaperBrokerAdapter` | `revision2_external.paper_execution.CostedPaperBrokerAdapter` | YES |
| runtime class | `CombinedCycleRuntime` | `CombinedCycleRuntime` | YES |
| orchestrator construction | `normal ctor, governor_authority=full, PAPER_APPLY, active_paper, compact (hard-coded in probe)` | `normal ctor, governor_authority=full, PAPER_APPLY, active_paper, compact (hard-coded in probe)` | YES |
| runtime config | `HandoffConfig(enabled=True), EngineBPolicy(enabled=True), defaults (hard-coded in probe)` | `HandoffConfig(enabled=True), EngineBPolicy(enabled=True), defaults (hard-coded in probe)` | YES |
| paper_execution.py sha256 (the only intended difference) | `2f301d0fb261675f...` | `4630aa718441b13f...` | **NO** |
All other audited source/test hashes are identical before and after (`outputs/R5_D01_evidence/before/pre_D01_state.txt` vs `after/audited_hashes_AFTER.txt`).
**Flagged differences (relevant or not):**
1. The BEFORE runs used an earlier revision of the probe script (sha256 `5b05168b...`, printed in the session at the time) whose only difference, as I made it, is the default string of the `DATA_ROOT` line; I edited that line afterwards (current sha256 `c8cb5759...`, stored in `d01_replay_probe.py.sha256`). Both runs were launched with `R5_D01_DATA_ROOT` set, so the default was never used. **The original BEFORE script revision was not saved**, so this cannot be re-verified by diff. Impact on behavior: none expected. It is a provenance gap.
2. The data root is a temp directory of symlinks outside the repository; its listing is in `before/data_root.txt` (same directory for BEFORE and AFTER).
3. The runtime's SQLite store path is a random temp dir per run (not behavior-relevant).
4. The BEFORE and AFTER runs were launched in parallel pairs; no shared state.
5. BEFORE was run only for TITAN; there is no BEFORE run for the 8-symbol set.
Independent re-run now (current code, fresh scratchpad outputs):
```
$ cat /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_runtime_titan.out /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_baseline_titan.out | grep -v '^ORCHESTRATOR'
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: revision5.combined_cycle_runtime.CombinedCycleRuntime
completed=True exception=None reconcile_calls=4 trades=1
exit 0
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: None
completed=True exception=None reconcile_calls=0 trades=1
exit 0
[exit status 0]
```
## CHECK 6 — 8-symbol claim

Raw evidence (`outputs/R5_D01_evidence/after/replay_runtime_8sym.stdout` and `.json`):
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: revision5.combined_cycle_runtime.CombinedCycleRuntime
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=True exception=None reconcile_calls=56 trades=7
exit status: 0
```
```json
{
 "symbols": [
  "TITAN",
  "INFY",
  "TCS",
  "RELIANCE",
  "SBIN",
  "ICICIBANK",
  "HDFCBANK",
  "LT"
 ],
 "completed": true,
 "reconcile_calls": 56,
 "reconcile_errors": [],
 "trades": 7,
 "trade_sides": [
  "SELL"
 ],
 "exit_reasons": [
  "governor_exit:FSR_BELOW_EXIT:FSRN",
  "governor_exit:GOVERNOR_PATH_ERROR"
 ],
 "handoff_requests": 0,
 "open_trades_after": [],
 "store_open_after": [],
 "broker_class": "revision2_external.paper_execution.CostedPaperBrokerAdapter",
 "runtime_class": "CombinedCycleRuntime"
}
```
Claim check: 8 symbols, 56 reconcile calls, 7 trades, all SELL: **confirmed**.
### Does BUY use a different product path than SELL? Code:
```python
   19          if result["passed"]:
   20              # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
   21              # already read an absent product as MIS; a converted position keeps its explicit product.
   22              self.positions[symbol].setdefault("product", "MIS")
   23              cost = leg_cost(result["filled_price"], result["filled_quantity"], side)
   24              self.booked_costs += cost
   25              self.cost_ledger.append({"order_id": result["order_id"], "cost": cost})
```
```python
   47      def reconcile(self, engine, snapshot):
   48          position=engine.broker.get_position(snapshot.record.symbol)
   49          expected=float(snapshot.trade['quantity'])*(1 if snapshot.record.direction=='BUY' else -1)
   50          if abs(float(position.get('quantity',0))-expected)>1e-6 or position.get('product')!=snapshot.record.product:
   51              raise CombinedCycleReconciliationError('broker quantity/product differs from durable owner')
   52          if snapshot.protection.get('stop')!=snapshot.record.current_stop_price:
   53              raise CombinedCycleReconciliationError('durable protective stop inconsistent')
```
```python
   60      def ensure_protection(self, symbol, stop_price, quantity, product="MIS"):
   61          """Track simulated contingent protection; orchestrator executes gap/bar stops.
   62  
   63          This is not an exchange order and offers no real overnight guarantee.
   64          """
   65          import math
   66          if not math.isfinite(float(stop_price)) or stop_price <= 0 or int(quantity) <= 0:
   67              raise ValueError("Invalid paper protection")
   68          position = self.get_position(symbol)
   69          if abs(position["quantity"]) != int(quantity) or position.get("product", "MIS") != product:
   70              raise ValueError("Paper protection does not match position")
   71          oid = "paper-protection-" + symbol
   72          self._contingent_protection[oid] = {
   73              "order_id": oid, "tradingsymbol": symbol, "exchange": "NSE",
   74              "product": product, "quantity": int(quantity), "pending_quantity": int(quantity),
   75              "filled_quantity": 0, "transaction_type": "SELL" if position["quantity"] > 0 else "BUY",
   76              "order_type": "SL-M", "status": "TRIGGER PENDING", "validity": "SIMULATED",
```
* `place_order` assigns the product with no reference to `side`.
* `reconcile` differs by side only in the SIGN of the expected quantity (`expected = quantity * (1 if BUY else -1)`); the product comparison is side-independent.
* `ensure_protection` differs by side only in the protective `transaction_type` (SELL for longs, BUY for shorts); the product comparison is side-independent.
* `request_product_conversion` requires `position["quantity"] == int(quantity)`, which a SHORT (negative quantity) can never satisfy, and the handoff manager qualifies only BUY; this is a handoff matter, outside D01.
**Conclusion:** the product representation and the product part of reconciliation are the same for BUY and SELL, so SELL-only replay does not invalidate the D01 mechanism. However, **BUY has no REAL-REPLAY coverage in the evidence** (all replayed trades were SELL); BUY is covered only at COMPONENT level (`test_real_fill_carries_explicit_mis_product[BUY]`, `test_reconcile_accepts_..._[BUY]`). Marked: BUY real-replay coverage MISSING (not a product-path difference).
## CHECK 7 — Ledger equality claim

What was hashed (probe source):
```
$ grep -n "trade_ledger_sha256\|rep.get(\"trades\"" /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/R5_D01_evidence/d01_replay_probe.py
64:    trades = rep.get("trades", [])
65:    result["trades"] = len(trades); result["trade_ledger_sha256"] = hashlib.sha256(json.dumps(trades, sort_keys=True, default=str).encode()).hexdigest()
[exit status 0]
```
Independent re-run: TITAN runtime ledger `051143f6bd1d5988a0bf97a5b53d23b4a165177e0b84834d5cc728a583a334c1`; TITAN baseline `051143f6bd1d5988a0bf97a5b53d23b4a165177e0b84834d5cc728a583a334c1`; evidence TITAN `051143f6bd1d5988a0bf97a5b53d23b4a165177e0b84834d5cc728a583a334c1`. 8-symbol evidence: runtime `aaee797a9ea74c7f2d6eadbef0383edf598cee085028e44b18fab5234d3c11f2`, baseline `aaee797a9ea74c7f2d6eadbef0383edf598cee085028e44b18fab5234d3c11f2`; the earlier arm-10 8-symbol value was `aaee797a9ea74c7f2d6eadbef0383edf598cee085028e44b18fab5234d3c11f2`.
Verified equal where claimed. **What the hash is:** SHA-256 of `json.dumps(report['trades'], sort_keys=True, default=str)`, i.e. the orchestrator's completed-trade records (symbol, side, entry/exit price and timestamp, quantity, exit reason, pnl, costs, net_pnl, trade/candidate ids, bars_held, planned stop/target, MFE/MAE fields). It does **not** include lifecycle records, the durable SQLite rows, broker snapshots/positions/orders, protection orders, controller events, reconciliation outcomes or the order of runtime calls.
**What equality establishes:** the runtime-attached replay produced the same completed-trade outcomes as the baseline replay (and as the pre-D01 value) for these inputs. **What it does not establish:** equivalence of the lifecycle/runtime/broker state, durability correctness, or correct reconcile behavior beyond 'it did not raise'. Those rest on the reconcile counts, store contents and the component tests.
## CHECK 8 — Fail-closed tests

```
$ sed -n '/negative detection preserved/,$p' tests/test_r5_d01_product_reconciliation.py
# ----------------------------------------------------------------------------- negative detection preserved

def test_wrong_quantity_is_still_rejected(tmp_path):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY")
    fill(orch, "SELL", quantity=4)                       # real partial exit at the broker
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)


def test_wrong_product_contradicting_the_lifecycle_is_still_rejected(tmp_path):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY")                         # lifecycle A_OPEN / MIS
    assert orch.broker.request_product_conversion("c1", SYMBOL, QTY, timestamp="2024-02-13 15:10")["status"] == "ACKNOWLEDGED"
    assert store.load("trade-1").record.lifecycle_state == "A_OPEN"
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)                  # broker CNC vs lifecycle MIS


def test_missing_broker_position_is_still_rejected(tmp_path):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY")
    fill(orch, "SELL")                                   # real full exit at the broker
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)


def test_a_position_with_no_product_is_not_defaulted_by_the_runtime(tmp_path):
    """The real parent PaperBrokerAdapter fills without a product key.  The runtime must still fail closed."""
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY", via_parent=True)
    assert "product" not in orch.broker.get_position(SYMBOL)
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)
[exit status 0]
```
Exact assertions: wrong quantity -> `pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs")` after a real partial SELL; wrong product -> same raise after a real conversion while the lifecycle stays A_OPEN/MIS; missing broker position -> same raise after a real full SELL; missing product -> same raise for a position filled through the real parent `PaperBrokerAdapter.place_order` (asserted `"product" not in get_position(...)` first).
```
$ sha256sum revision5/combined_cycle_runtime.py; grep revision5/combined_cycle_runtime.py /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/R5_D01_evidence/before/pre_D01_state.txt
8432563f9f0bd16a9bd4a2156dcb6cf27ccd7561d906616b73629d60173d1ec9  revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_runtime.py
8432563f9f0bd16a9bd4a2156dcb6cf27ccd7561d906616b73629d60173d1ec9  revision5/combined_cycle_runtime.py
[exit status 0]
```
`reconcile` was not weakened: `combined_cycle_runtime.py` has the identical sha256 before D01 (`8432563f...`) and now.
```
$ sed -n 1,4p /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_tests_after.txt; grep -E 'PASSED|FAILED' /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_tests_after.txt | sed 's/ \[.*//'; tail -n 2 /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_tests_after.txt
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
tests/test_r5_d01_product_reconciliation.py::test_concrete_broker_is_the_paper_implementation PASSED
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY] PASSED
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL] PASSED
tests/test_r5_d01_product_reconciliation.py::test_fill_does_not_overwrite_a_converted_product PASSED
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] PASSED
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] PASSED
tests/test_r5_d01_product_reconciliation.py::test_wrong_quantity_is_still_rejected PASSED
tests/test_r5_d01_product_reconciliation.py::test_wrong_product_contradicting_the_lifecycle_is_still_rejected PASSED
tests/test_r5_d01_product_reconciliation.py::test_missing_broker_position_is_still_rejected PASSED
tests/test_r5_d01_product_reconciliation.py::test_a_position_with_no_product_is_not_defaulted_by_the_runtime PASSED
============================== 10 passed in 0.82s ==============================
exit 0
[exit status 0]
```
## CHECK 9 — Pre-edit detection

Re-run now of the new tests in the temp copy of the tree whose only difference is `paper_execution.py` = BEFORE copy (sha256 `2f301d0f...`, verified), vs the current tree:
| Test | BEFORE | AFTER | Reason |
|---|---|---|---|
| test_concrete_broker_is_the_paper_implementation | PASSED | PASSED | guard test; passes in both (does not depend on the fix) |
| test_real_fill_carries_explicit_mis_product[BUY] | FAILED | PASSED | `KeyError: 'product'` — the real paper fill left no product key |
| test_real_fill_carries_explicit_mis_product[SELL] | FAILED | PASSED | `KeyError: 'product'` — same |
| test_fill_does_not_overwrite_a_converted_product | PASSED | PASSED | guard test; passes in both (does not depend on the fix) |
| test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] | FAILED | PASSED | `CombinedCycleReconciliationError: broker quantity/product differs from durable owner` — the D01 failure itself |
| test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] | FAILED | PASSED | same |
| test_wrong_quantity_is_still_rejected | PASSED | PASSED | guard test; passes in both (does not depend on the fix) |
| test_wrong_product_contradicting_the_lifecycle_is_still_rejected | PASSED | PASSED | guard test; passes in both (does not depend on the fix) |
| test_missing_broker_position_is_still_rejected | PASSED | PASSED | guard test; passes in both (does not depend on the fix) |
| test_a_position_with_no_product_is_not_defaulted_by_the_runtime | PASSED | PASSED | guard test; passes in both (does not depend on the fix) |
The four fail-before/pass-after tests are exactly: `test_real_fill_carries_explicit_mis_product[BUY]`, `[SELL]`, `test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY]`, `[SELL]`. **At least one exercises the real paper-broker product omission directly:** `test_real_fill_carries_explicit_mis_product` asserts `orch.broker.get_position(SYMBOL)["product"] == "MIS"` after a real `CostedPaperBrokerAdapter` fill (fails with `KeyError` before the hunk).
Raw BEFORE run:
```
tests/test_r5_d01_product_reconciliation.py::test_concrete_broker_is_the_paper_implementation PASSED [ 10%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY] FAILED [ 20%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL] FAILED [ 30%]
tests/test_r5_d01_product_reconciliation.py::test_fill_does_not_overwrite_a_converted_product PASSED [ 40%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] FAILED [ 50%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] FAILED [ 60%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_quantity_is_still_rejected PASSED [ 70%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_product_contradicting_the_lifecycle_is_still_rejected PASSED [ 80%]
tests/test_r5_d01_product_reconciliation.py::test_missing_broker_position_is_still_rejected PASSED [ 90%]
tests/test_r5_d01_product_reconciliation.py::test_a_position_with_no_product_is_not_defaulted_by_the_runtime PASSED [100%]
FAILED tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY]
FAILED tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL]
FAILED tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY]
FAILED tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL]
========================= 4 failed, 6 passed in 0.84s ==========================
exit 1
```
## CHECK 10 — Test quality (10 D01 tests)

`orch = Engine([...])` constructs the real orchestrator but `run()` is never called; per the rule that normal construction is not integration, none of the tests is INTEGRATION.
| # | Test | Class | REAL objects | MOCK | SYNTHETIC inputs | Production path exercised |
|---|---|---|---|---|---|---|
| 1 | test_concrete_broker_is_the_paper_implementation | UNIT | orchestrator (constructed), `CostedPaperBrokerAdapter` | none | none | orchestrator constructor -> `type(broker)` and `environment` |
| 2 | test_real_fill_carries_explicit_mis_product[BUY] | COMPONENT | `CostedPaperBrokerAdapter` (+ orchestrator for config) | none | price 100.0, qty 10 | `place_order` -> fill -> position dict + `snapshot()` positions |
| 3 | test_real_fill_carries_explicit_mis_product[SELL] | COMPONENT | same | none | same | same, SELL |
| 4 | test_fill_does_not_overwrite_a_converted_product | COMPONENT | `CostedPaperBrokerAdapter` | none | qty 10 then 5, timestamp string | `place_order` -> `request_product_conversion` -> `place_order` (setdefault no-overwrite) |
| 5 | test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] | COMPONENT | paper broker, runtime, store (SQLite), handoff manager, Engine-B controller, orchestrator `_register_position_lifecycle` | none | **SYNTHETIC_UNIT_FIXTURE trade dict + manual `open_trades` insertion** | real fill -> `_register_position_lifecycle` -> `register_fill` (store save, `ensure_protection`) -> `reconcile` |
| 6 | test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] | COMPONENT | same | none | same | same, SELL |
| 7 | test_wrong_quantity_is_still_rejected | COMPONENT | same stack | none | same fixture | real partial SELL -> `reconcile` raises |
| 8 | test_wrong_product_contradicting_the_lifecycle_is_still_rejected | COMPONENT | same stack | none | same fixture | real conversion -> `reconcile` raises |
| 9 | test_missing_broker_position_is_still_rejected | COMPONENT | same stack | none | same fixture | real full SELL -> `reconcile` raises |
| 10 | test_a_position_with_no_product_is_not_defaulted_by_the_runtime | COMPONENT | same stack + real parent `PaperBrokerAdapter.place_order` | none | same fixture | parent fill without product -> `reconcile` raises |
No mock, shim or monkey-patch is used (`grep` in Check 4/10 shows none).  Limits: no test covers real `run()`, a real bar loop, multiple bars after the fill, the stale-CNC reopen, or restart.
## CHECK 11 — Source integrity

```
$ git diff --check
[exit status 0]
```
```
$ python3 - <<'EOF'
import re
for f in ('tests/test_r5_d01_product_reconciliation.py',):
    bad=[i for i,l in enumerate(open(f).read().split(chr(10)),1) if l!=l.rstrip()]
    print(f, 'lines with trailing whitespace:', bad or 'none')
EOF
tests/test_r5_d01_product_reconciliation.py lines with trailing whitespace: none
[exit status 0]
```
```
$ PYTHONDONTWRITEBYTECODE=1 ~/.venvs/zerodha-phase1-r5/bin/python -m pytest -v -p no:cacheprovider -p no:warnings tests/test_r5_d01_product_reconciliation.py 2>&1 | tail -n 16
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 10 items

tests/test_r5_d01_product_reconciliation.py::test_concrete_broker_is_the_paper_implementation PASSED [ 10%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY] PASSED [ 20%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL] PASSED [ 30%]
tests/test_r5_d01_product_reconciliation.py::test_fill_does_not_overwrite_a_converted_product PASSED [ 40%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] PASSED [ 50%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] PASSED [ 60%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_quantity_is_still_rejected PASSED [ 70%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_product_contradicting_the_lifecycle_is_still_rejected PASSED [ 80%]
tests/test_r5_d01_product_reconciliation.py::test_missing_broker_position_is_still_rejected PASSED [ 90%]
tests/test_r5_d01_product_reconciliation.py::test_a_position_with_no_product_is_not_defaulted_by_the_runtime PASSED [100%]

============================== 10 passed in 0.78s ==============================
[exit status 0]
```
```
$ sha256sum revision2_external/paper_execution.py tests/test_r5_d01_product_reconciliation.py revision5/combined_cycle_runtime.py revision2_external/orchestrator.py > /tmp/claude-1000/acc_end_hashes.txt; diff /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_start_hashes.txt /tmp/claude-1000/acc_end_hashes.txt && echo 'hashes at start of acceptance check == hashes now (paper_execution.py, D01 test, runtime, orchestrator)'
hashes at start of acceptance check == hashes now (paper_execution.py, D01 test, runtime, orchestrator)
[exit status 0]
```
```
$ cd /home/srinivas/projects/zerodha-r5-governor-refactor; xargs -a /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/audited_files.txt sha256sum | diff - /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/R5_D01_evidence/after/audited_hashes_AFTER.txt && echo 'all 18 audited files identical to the post-D01 snapshot'
all 18 audited files identical to the post-D01 snapshot
[exit status 0]
```
```
$ git status --short | grep -v 'outputs/\|docs/' | diff - /tmp/claude-1000/-home-srinivas--config-Claude-scratch-workspaces-b8a4db77-bc5f-4716-ba92-ca91a3db1778-0bfcd37e-13bd-48b7-b3c6-81a5015d4504-scratch-2026-10-05-cf29c3/bdd85e65-61b5-4470-ac68-2fd13ba4a52c/scratchpad/acc_start_status.txt && echo 'git status (outside outputs/ and docs/) unchanged since the start of this check'
git status (outside outputs/ and docs/) unchanged since the start of this check
[exit status 0]
```
The live broker adapter was not instantiated by anything run in this audit (`grep` of the probes: none construct `KiteConnectBrokerAdapter`).
## CHECK 12 — Acceptance decision

External-probe success and permanent regression coverage are scored as separate criteria.
| Original D01 criterion | Verdict | Basis |
|---|---|---|
| Original failure reproduced before change | PASS | external real-replay probe, BEFORE: `CombinedCycleReconciliationError` after 1 reconcile call; plus 4 component tests fail on the BEFORE tree. (Probe-revision provenance gap noted in Check 5.) |
| Root cause demonstrated | PASS | BEFORE JSON shows broker position `{quantity: -5, avg_price: ...}` with no `product`; `reconcile` compares `position.get('product')` strictly; test KeyError on the real fill |
| Explicit product contract established | PARTIAL | absent = MIS is supported by every paper-adapter consumer, the live order default and the lifecycle contract (Check 2); but the contract for product across flatten/reopen is undefined and demonstrably stale-CNC (Check 3) |
| Minimal implementation | PASS | one `setdefault` line (3 lines with comment) in one function, no overwrite, no change to `reconcile` |
| Real paper broker | PASS | `CostedPaperBrokerAdapter`, `environment='paper'` printed before every replay; component tests use the real class |
| Real Orchestrator.run() | PASS | external probe only (not permanent) |
| Runtime attached | PASS | real `CombinedCycleRuntime` + store + handoff + Engine-B objects in the probe |
| Fill occurs | PASS | 1 trade (TITAN), 7 trades (8 symbols), all SELL |
| Post-fill reconciliation succeeds | PASS | 4 and 56 reconcile calls, 0 errors, SELL positions; BUY has no real-replay coverage (component-level only) |
| Incorrect product fails closed | PASS | component tests (wrong product, absent product); `reconcile` byte-identical |
| Quantity mismatch fails closed | PASS | component test |
| Permanent regression test reproduces the real Orchestrator.run() defect | **FAIL** | no permanent test calls `Orchestrator.run()`; the real-replay proof is external (Check 4) |
| Same real replay succeeds afterward | PASS | same loader, slice hash, params, protocol, classes, runtime config; re-run now reproduces `completed=True`, 4 reconcile calls, ledger `051143f6...` |
| No unrelated source/test changes | PASS | only `paper_execution.py` (3 lines) changed in production; all other audited hashes identical; new files limited to the D01 test, report and evidence |

**Decision: ACCEPT_WITH_TEST_GAP.** The production change is minimal, side-agnostic and does not weaken fail-closed reconciliation, and the same real replay now completes. The permanent `Orchestrator.run()` regression criterion is not met, and a pre-existing stale-CNC defect candidate (R5-D-STALE-CNC) is registered separately and untouched.
Residual risks not addressed by D01: the stale-CNC reopen; BUY real-replay coverage; the unsaved BEFORE probe revision; handoff/Engine B/restart paths remain outside D01 and unverified.
