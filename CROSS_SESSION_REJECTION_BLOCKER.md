# Cross-Session Rejection Implementation & Testing Blocker

**Status:** BLOCKING orchestrator validation  
**Priority:** High (required for safety contract approval)  
**Evidence:** Diagnostic shows 18 session-gap orders (72% exceed 0.10% tolerance)

---

## 1. What Needs to be Built

### 1A. OrderIntent Enhancement
**File:** `revision4/contracts.py`

Add field to `OrderIntent` dataclass:
```python
authorized_cross_session: bool = False  # If True, order may be filled after session close
```

**Rationale:** Allows explicit override for exceptional cases; defaults to safe (reject)

---

### 1B. Cross-Session Detection Gate
**File:** `revision4/gates_proper.py`

Add new pre-submission gate logic to `evaluate_pre_submission()`:

```
Gate: Cross-Session Order Detection

INPUT:
  - order: OrderIntent
  - timestamp: str (decision bar timestamp, format: "2024-08-01 09:15:00+00:00")
  - bars: Dict[symbol, Bar] (current bar for each symbol)

LOGIC:
  decision_date = pd.Timestamp(timestamp).date().isoformat()
  next_bar = bars[order.symbol]
  next_timestamp = next_bar.timestamp  # Next bar (t+1)
  fill_date = pd.Timestamp(next_timestamp).date().isoformat()
  
  is_cross_session = (decision_date != fill_date)
  
  if is_cross_session and not order.authorized_cross_session:
    return (False, "Cross-session order not authorized (date transition detected)")
  
  if is_cross_session and order.authorized_cross_session:
    # Log for audit (but allow)
    return (True, None)

OUTPUT:
  (bool: pass/reject, str: reject reason or None)
```

**Position in gate sequence:** Early in pre-submission (before liquidity/slippage gates)

---

### 1C. Gate Integration into Orchestrator
**File:** `revision4/timestamp_orchestrator.py`

Modify `evaluate_pre_submission()` call to pass `bars` parameter:

**Current (line ~165):**
```python
ok, reason = self.gate_evaluator.evaluate_pre_submission(
    order, snapshot, candidate.pa_confidence, candidate.id_risk_reward,
    timestamp, self.ledger.peak_equity, max(0.0, -self.ledger.daily_pnl),
    self.config.require("kill_switch_enabled"), bars,
)
```

**Note:** `bars` is already passed in the current code. Gate evaluator must use it.

---

## 2. Test Plan

### Test File: `revision4/tests/test_cross_session_rejection.py`

#### 2.1 Unit Tests (Gates Layer)

```
Test: test_cross_session_rejected_by_default
  - Scenario: Order created 2024-08-01, scheduled for next bar 2024-08-02
  - Input: OrderIntent(authorized_cross_session=False)
  - Expected: Gate rejects with "Cross-session order not authorized"

Test: test_cross_session_allowed_with_authorization_flag
  - Scenario: Same order, but authorized_cross_session=True
  - Input: OrderIntent(authorized_cross_session=True)
  - Expected: Gate passes (returns True, None)

Test: test_intraday_order_always_allowed
  - Scenario: Order created 2024-08-01, next bar same day 2024-08-01
  - Input: OrderIntent(authorized_cross_session=False or True)
  - Expected: Gate passes (cross-session logic not triggered)
```

#### 2.2 Integration Tests (Orchestrator Layer)

```
Test: test_orchestrator_rejects_cross_session_at_eod
  - Setup: SUNPHARMA/August data, one symbol
  - Scenario: Inject order candidate near end-of-day (15:29 bar, fills next day 09:15)
  - Expected:
    * Order rejected at pre-submission gate
    * event_log contains ("timestamp", "GATE_REJECT", "order_id:Cross-session...")
    * Order NOT in orders_submitted
    * Order NOT in fills

Test: test_orchestrator_allows_authorized_cross_session
  - Setup: Same EOD scenario
  - Scenario: OrderIntent with authorized_cross_session=True
  - Expected:
    * Order passes pre-submission gate
    * Order submitted and filled
    * CompletedTrade logged with is_cross_session_fill=True (or audit flag)

Test: test_reconciliation_after_cross_session_rejection
  - Setup: Mixed day (some intraday orders, some rejected session orders)
  - Expected:
    * Ledger reconciles without "ghost" orders
    * Daily P&L excludes rejected orders
    * No dangling reservations
```

#### 2.3 Sealed-Month Validation

```
Test: test_48symbol_august_no_regressions
  - Run: Full orchestrator on 48-symbol August 2024
  - Baseline: Previous successful run (if exists)
  - Expected Changes:
    * Session-gap orders are rejected (18 observed in diagnostic)
    * Intraday orders unaffected
    * Ledger reconciliation still exact
    * Total orders submitted may decrease slightly
```

---

## 3. Acceptance Criteria

- [ ] OrderIntent has `authorized_cross_session` field
- [ ] ProperGateEvaluator detects cross-session orders (date mismatch)
- [ ] Pre-submission rejection works (default behavior)
- [ ] Authorization override works (explicit flag)
- [ ] Orchestrator passes 3+ unit tests
- [ ] Orchestrator passes 3+ integration tests
- [ ] SUNPHARMA/August replay succeeds with cross-session rejections
- [ ] 48-symbol August replay succeeds with no new failures
- [ ] event_log correctly records GATE_REJECT for cross-session
- [ ] Zero changes to existing gate 1-15, 17-18 logic

---

## 4. Implementation Sequence

1. **Add OrderIntent.authorized_cross_session** (contracts.py)
2. **Implement cross-session gate** (gates_proper.py)
3. **Unit tests** (test_gates_proper.py or new file)
4. **Integration tests** (test_cross_session_rejection.py)
5. **Run SUNPHARMA/August validation** (sealed month)
6. **Run 48-symbol August validation** (full orchestration)
7. **Commit with test evidence**

---

## 5. Risk & Rollback

**If cross-session rejection breaks intraday trading:**
- Check date extraction logic (pd.Timestamp parsing)
- Verify next_bar is actually t+1 (not t+2)
- Check timezone handling (all timestamps UTC?)

**If integration tests fail:**
- Verify bars parameter is passed correctly to gate evaluator
- Ensure orchestrator.run() doesn't strip bars before gate evaluation
- Check that event_log GATE_REJECT entries are created

**Rollback:** Remove authorized_cross_session field, revert pre-submission gate logic.

---

## 6. Success Metrics

- ✅ Cross-session orders rejected by default (18 observed in diagnostic)
- ✅ Override flag allows explicit approval (with audit trail)
- ✅ Intraday orders pass unchanged
- ✅ Full 48-symbol August replay succeeds
- ✅ All tests green
- ✅ Safety contract proposal moves to Phase 2

---

## Schedule

**Phase 1 (Immediate):**
- Implement OrderIntent + gate logic
- Unit tests (should complete within 1-2 hours)
- SUNPHARMA/August validation

**Phase 2 (Day 2, if Phase 1 passes):**
- 48-symbol August validation
- Full integration tests
- Commit & merge

**Gated by:** No safety contract changes until both phases pass
