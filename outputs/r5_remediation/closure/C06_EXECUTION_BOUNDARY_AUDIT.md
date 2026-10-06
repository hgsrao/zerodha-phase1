# C06 execution boundary: gated component, not live release

Implemented `revision2_external/live_execution_boundary.py` against the actual `KiteConnectBrokerAdapter.place_order`, `orders`, `positions`, `snapshot`, `verify_account_identity`, and `adapter.client.order_history` APIs. The last API is explicitly the actual SDK client surface; no invented adapter method. Default offline mode permits argument validation only and cannot submit. Enabled mode is an opt-in composition primitive and was exercised exclusively against a fake SDK client.

The official order documentation states successful placement does not imply execution, and the order book/history lives for only one day: https://kite.trade/docs/connect/v3/orders/ . Therefore ACCEPTED is never represented as a fill and unresolved intents survive a missing readback indefinitely rather than being resubmitted.

Before placement: explicit account and live environment; actual profile identity verification; account-bound durable cursor certificate less than five seconds old from a composition-provided reader; fresh actual broker snapshot reconciliation against owned records and protective orders via BrokerReconciliationService; delivery SELL requires current-day broker-reported authorised holdings quantity. User DDPI booleans are unsupported. An account with DDPI but lacking this broker readback shape remains blocked. Protection verifier currently supports actual pending SL/SL-M orders, not standalone active GTT; overnight GTT admission therefore remains blocked until a verified GTT reconciliation composition is implemented.

Correlated intent is committed into SQLite WAL/FULL before submission. Identity collision fails. Any subsequent request with the same tag reads its durable receipt and never resubmits, including UNKNOWN. Unresolved intents block fresh risk. Failed/ambiguous receipt and terminal cancellation with partial fill are retained. Broker authorization trip codes are raised and persisted, including retries. Reconciliation checks exact order geometry/identity, read-only SDK history, monotonic cumulative filled quantity, executed average price, COMPLETE quantity equality, and reads physical positions alongside execution history.

## Verification

`/home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -q tests/test_r5_live_execution_boundary.py`

13 tests passed. Covers acceptance versus fill, partial/completed fill, duplicate polling, process/store reopen without resubmit, timeout/readback, missing cursor/offline/stale cursor, unprotected broker truth, invalid quantity/product/limit geometry, identity collision, regressing fill, lack of broker delivery authorization, cancelled partial fill, and persisted authorization trip.

No credentials, real broker requests, or live orders were accessed. No existing broker adapter or orchestrator file was edited.

## Integration limitations and acceptance status

C06 is PARTIAL. The current orchestrator is deliberately CostedPaperBrokerAdapter-only and expects synchronous costed fills. It cannot consume this async live contract safely by swapping broker instances. Required execution composition still includes transactional ownership/fill application, reconciliation of netted multiple orders and holdings, cost capture, partial exit ownership, durable event-consumer acknowledgement, protection installation after entry/partial fills, cancellation policy, and certified direct live runtime/controller recovery cursor production. `fill_delta` is observational telemetry: the durable cumulative receipt is not an atomic downstream controller application acknowledgement and cannot alone claim exactly-once plant mutation. The cursor reader must be backed by verified durable runtime restoration, not a caller-created timestamp or flag. Passing a fake cursor in tests validates the boundary protocol, not actual recovery readiness.

The new order journal does not replace conversion/GTT journals. Existing `StateRecoveryJournal.reserve_conversion/reserve_protection` remain in the adapter, unchanged, and must share account-scoped recovery coordination at application assembly. This component submits regular entry/exit orders only, and never independently converts positions or re-arms GTTs.
