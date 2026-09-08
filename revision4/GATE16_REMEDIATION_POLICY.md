# Gate 16 Slippage Breach Remediation Policy

**Version:** 1.0  
**Date:** 2026-09-08  
**Status:** Governance Decision Pending  

---

## 1. Breach Event Definition

**Gate 16 (Slippage) Breach** occurs when:
```
measured_slippage_pct > contract_tolerance_pct
```

Where:
- `measured_slippage_pct` = absolute difference between entry price intent and actual fill price
- `contract_tolerance_pct` = 0.1000% (frozen, no ad-hoc adjustment)
- Breach is detected in post-fill gate evaluation stage (after fill is booked in ledger)

---

## 2. Immutable Audit Record

On breach detection, emit and permanently record:

```python
SafetyViolation(
    violation_id: str,              # Unique UUID
    timestamp_detected: str,        # ISO format
    gate_name: str,                 # "Gate16Slippage"
    order_id: str,                  # Order that triggered breach
    symbol: str,
    fill_price: float,
    entry_price_intent: float,
    measured_slippage_pct: float,
    tolerance_pct: float,
    breach_magnitude_bps: float,    # (measured - tolerance) in basis points
    position_id: str,               # Position opened by the fill
    position_quantity: float,
    position_direction: int,        # +1 or -1
    ledger_state_hash: str,         # SHA-256 of ledger at breach
    remediation_status: str,        # "INITIATED" → "FLATTENED" → "RECONCILED" or "MANUAL_REVIEW"
)
```

Audit record is:
- Immutable (append-only)
- Cryptographically signed
- Included in sealed run report
- Required for any manual recovery decision

---

## 3. Immediate Freeze on New Entries

On breach detection:

**Halt condition:**
```python
if gate16_breach_detected:
    entry_authorization_enabled = False
    pending_orders_active = False
```

**Actions:**
1. Set system flag: `quarantine_mode = True`
2. Reject all new `submit_order()` calls with reason: `"QUARANTINE_MODE: Gate16 breach remediation active"`
3. Do NOT cancel existing pending orders yet (see section 4)

**Duration:** Until reconciliation complete or manual recovery authorized

---

## 4. Cancellation of Pending Orders

After new entry freeze (section 3), cancel all pending orders:

```python
for order_id in ledger.pending_orders.keys():
    ok, msg = broker.cancel_order(order_id, reason="QUARANTINE_MODE")
    ok, msg = ledger.cancel_order(order_id)
    record_cancellation_event(order_id, reason)
```

**Conditions:**
- Execute AFTER breach is recorded
- Cancel in chronological order of submission (FIFO)
- Record each cancellation in audit trail
- Release reserved capital to cash

**Outcome:**
- Zero pending orders
- Zero reserved cash
- All capital available for flatten execution

---

## 5. Deterministic Flatten Order and Execution Method

Flatten the breached position and all other open positions:

### 5.1 Flatten Sequence

```
For each open position (in order of entry bar index, oldest first):
  1. Identify symbol
  2. Fetch most recent available bar for symbol
  3. Calculate exit price (bar.close)
  4. Calculate canonical exit cost (direction-aware)
  5. Create ExitEvent with:
     - exit_price = bar.close
     - pnl_realized = net P&L (gross - entry_cost - exit_cost)
     - exit_reason = ExitReason.QUARANTINE_FLATTEN
  6. Call ledger.close_position(exit_event)
  7. Record CompletedTrade
  8. If ledger.close_position() fails → escalate to manual review (section 6.2)
```

### 5.2 Execution Method

**Primary:** Market close at session end
```
- Use last available bar close price
- Execute at EOD (not mid-session)
- Capture canonical transaction costs
```

**Fallback (if no close bar available):**
- Use last traded price
- Escalate to manual review for approval

**Cost Model:**
- Entry cost: already paid (from original fill)
- Exit cost: canonical NSE model (brokerage + exchange + STT)
  - Direction-aware: SELL for long, BUY for short
  - Calculated at close price

---

## 6. Reconciliation After Flatten

### 6.1 Reconciliation Conditions

After all positions flattened, verify atomic completion:

```python
reconciliation_ok = (
    len(ledger.positions) == 0 AND
    len(ledger.pending_orders) == 0 AND
    ledger.reserved_cash == 0.0 AND
    all_completed_trades_have_net_pnl()
)
```

### 6.2 Reconciliation Outcomes

**Success Path:**
```
1. Zero open positions
2. Zero pending orders
3. Zero reserved cash
4. All completed trades reconciled
5. Daily P&L = sum(completed_trades.net_pnl) by date
6. Remediation status: RECONCILED
7. Allow manual review decision or proceed to recovery
```

**Failure Path (ledger.close_position returns False):**
```
1. Record failure reason in audit
2. Preserve ledger state at failure
3. Escalate to manual review (section 6.2)
4. Remediation status: MANUAL_REVIEW_REQUIRED
```

---

## 7. Conditions for Manual Recovery vs. Shutdown

### 7.1 Manual Recovery (Permitted)

**Conditions for manual intervention:**

1. **Reconciliation succeeded** (section 6.1, success path)
   - Zero open positions
   - Zero pending orders
   - Zero reserved cash
   - All completed trades recorded

2. **Audit trail complete** (section 2)
   - SafetyViolation record immutable
   - All remediation steps logged
   - Cryptographic integrity verified

3. **Analysis required:**
   - Root cause of slippage (market volatility, execution delay, other)
   - Whether breach was isolated or systemic
   - Impact on calibration or strategy parameters

**Manual recovery decision:**
- Authorized by human reviewer (named individual)
- Decision recorded in audit with timestamp and rationale
- Options:
  - **Resume trading** with adjusted parameters (e.g., wider slippage tolerance, different execution model)
  - **Restart validation** with same or adjusted config
  - **Recalibrate** with lessons learned from breach

### 7.2 Full Shutdown (Required)

**Conditions triggering shutdown:**

1. **Reconciliation failed** (section 6.2, failure path)
   - ledger.close_position() returned False for any position
   - Ledger invariant violated
   - State cannot be recovered programmatically

2. **Multiple breaches** (within single run)
   - More than one Gate 16 breach detected
   - Suggests systemic execution quality issue
   - Indicates strategy or market model misalignment

3. **Audit integrity compromised**
   - SafetyViolation record incomplete or tampered
   - Cryptographic signature verification failed
   - Chain of custody broken

**Shutdown actions:**
- Set `trading_halted = True` (fail-closed)
- Dump full ledger state to disk (encrypted)
- Generate comprehensive incident report
- Notify stakeholders
- Await manual investigation and policy decision
- No automatic recovery permitted

---

## 8. Implementation Checklist

- [ ] SafetyViolation audit record struct and serialization
- [ ] Quarantine mode flag and entry authorization checks
- [ ] Pending order cancellation logic (FIFO, with audit)
- [ ] Flatten determinism (oldest position first, canonical costs)
- [ ] ExitEvent construction with quarantine reason
- [ ] Post-flatten reconciliation validation
- [ ] Manual recovery decision logging
- [ ] Shutdown trigger conditions and actions
- [ ] Integration test: breach → freeze → flatten → reconcile → recovery
- [ ] 48-symbol validation with Gate16 remediation enabled

---

## 9. Testing Requirement

**Integration test must prove:**

1. **Breach detection:** Gate16 post-fill rejects with correct measurement
2. **Freeze:** New entries rejected; pending orders cancelled
3. **Flatten:** All positions closed at market close with canonical costs
4. **Reconciliation:** Zero open/pending/reserved; daily P&L matches
5. **Audit trail:** All events recorded and cryptographically signed
6. **Recovery:** Manual review path functional; shutdown conditions enforced

**Test case:**
- Open 1 long + 1 short position
- Trigger Gate16 breach on next bar fill
- Verify breach recorded
- Verify new entries blocked
- Verify flatten executes
- Verify reconciliation succeeds
- Verify audit trail intact

---

## 10. Governance Decision Points

**Before 48-symbol validation can resume:**

1. ✅ Gate16 threshold remains 0.1000% (no ad-hoc lowering)
2. ⏳ Remediation policy approved (this document)
3. ⏳ Implementation complete
4. ⏳ Integration test green
5. ⏳ Manual recovery approval workflow established

---

**Next Review Date:** Post-48-symbol validation (after policy implementation and testing)
