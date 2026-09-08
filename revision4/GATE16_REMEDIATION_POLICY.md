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

On breach detection, emit and permanently record (append-only, hash-linked):

```python
SafetyViolation(
    violation_id: str,              # Unique UUID
    run_id: str,                    # Run identifier
    config_hash: str,               # Config SHA-256 (frozen)
    dataset_hash: str,              # Dataset SHA-256 (sealed)
    
    # Breach event
    timestamp_detected: str,        # ISO format (decision timestamp)
    timestamp_fill: str,            # Fill timestamp (after decision)
    gate_name: str,                 # "Gate16Slippage"
    
    # Order and fill context
    order_id: str,                  # Order that triggered breach
    fill_id: str,                   # Actual fill event
    symbol: str,
    entry_price_intent: float,      # Price intended at order time
    fill_price: float,              # Actual fill price
    measured_slippage_pct: float,
    tolerance_pct: float,           # 0.1000% (frozen)
    breach_magnitude_bps: float,    # (measured - tolerance) in basis points
    
    # Position context
    position_quantity: float,
    position_direction: int,        # +1 or -1
    
    # Remediation lifecycle
    remediation_status: str,        # "INITIATED" → "FLATTENED" → "RECONCILED" or "SHUTDOWN"
    prior_violation_hash: str,      # SHA-256 of previous violation (chain link)
    
    # Cryptographic proof
    violation_signature: str,       # RSA or ECDSA signature of this record
)
```

**Storage requirements:**
- Append-only database (no updates, only inserts)
- Hash-linked chain (each record includes SHA-256 of prior)
- Cryptographically signed (proof of authorship + immutability)
- Durable write (not in-memory)
- Included in sealed run report with chain verification

**Chain integrity check:**
- On any replay or recovery: verify hash chain is unbroken
- If chain break detected: full shutdown, no recovery permitted
- If signature verification fails: full shutdown, no recovery permitted

---

## 3. Breach Count Threshold and Immediate Action

### 3.1 First Breach in Run

**Trigger:** Gate16 post-fill rejection (slippage > 0.1000%)

**Automatic action:**
```python
if first_gate16_breach_detected:
    quarantine_mode = True
    entry_authorization_enabled = False
    breach_count_this_run = 1
    record_safety_violation(...)  # Section 2
    initiate_remediation()  # Sections 4-5
```

**Outcome:** Quarantine + flatten + manual recovery path

### 3.2 Second Breach in Same Run

**Trigger:** Gate16 post-fill rejection while `breach_count_this_run >= 1`

**Automatic action:**
```python
if breach_count_this_run >= 1:
    trading_halted = True
    quarantine_mode = True
    entry_authorization_enabled = False
    record_safety_violation(...)  # Section 2, mark as SHUTDOWN
    dump_ledger_state()  # Full state dump
    generate_incident_report()
    trigger_full_shutdown()  # No recovery permitted
```

**Outcome:** Full shutdown, manual investigation required, no automatic recovery

### 3.3 Freeze on New Entries

**Actions (on first breach):**
1. Set system flag: `quarantine_mode = True`
2. Set system flag: `entry_authorization_enabled = False`
3. Reject all new `submit_order()` calls with reason: `"QUARANTINE_MODE: Gate16 breach remediation active"`
4. Do NOT cancel existing pending orders yet (see section 4)

**Duration:** Until reconciliation complete (first breach) or full shutdown (second breach)

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

Flatten the breached position and all other open positions using ONLY information known at decision time (no look-ahead).

### 5.1 Flatten Sequence (Deterministic Order)

```
For each open position (in order of entry bar index, oldest first):
  1. Identify symbol and direction
  2. Schedule flatten for next eligible bar (after breach detection)
  3. At that bar: calculate exit price per environment model (5.2 or 5.3)
  4. Calculate canonical exit cost (direction-aware)
  5. Create ExitEvent with:
     - exit_price = per execution model (not future data)
     - pnl_realized = net P&L (gross - entry_cost - exit_cost)
     - exit_reason = ExitReason.QUARANTINE_FLATTEN
  6. Call ledger.close_position(exit_event)
  7. Record CompletedTrade
  8. If ledger.close_position() fails → escalate to full shutdown (section 6.2)
```

### 5.2 Historical Replay Execution (Backtest)

**NO LOOK-AHEAD:** Use only bars/prices known at decision time.

**Exit scheduling:**
- Submit flatten order for **next eligible bar** (bar after breach detection)
- Do NOT use current bar close (may contain future information)

**Exit price model (predefined, conservative):**
```
For flatten at bar t+1:
  - Use bar(t+1).open or bid-ask midpoint
  - Apply conservative slippage buffer: -10 bps (assume execution at worst 10bps)
  - Conservative fill = midpoint - 10bps buffer
  - Never use bar(t+1).close (not yet known at decision time)
```

**Example:**
```
Breach detected: bar t, time t.close
Breach fill booked: bar t+1.open
Flatten scheduled for: bar t+2 open
Flatten executed at: bar(t+2).open - 10bps buffer (conservative)
Never: bar(t+2).close (future data)
```

**Cost model:**
- Entry cost: already paid (from original fill)
- Exit cost: canonical NSE model (brokerage + exchange + STT)
  - Direction-aware: SELL for long, BUY for short
  - Calculated at conservative fill price

### 5.3 Live Broker Execution (Production)

**Marketable exit order with bounded timeout/retries:**

```
1. Create marketable exit order:
   - Direction: opposite of position (SELL for long, BUY for short)
   - Price: current best-bid/ask - 1 tick (ensure execution)
   - Quantity: full position size
   - Timeout: 30 seconds (configurable per symbol)

2. Submit order to broker

3. Retry policy:
   - If not filled within 30s: cancel and retry
   - Max retries: 3 (total wall-clock: ~90s)
   - Back-off: 1s, 2s, 4s between attempts

4. Outcomes:
   - Filled: record actual fill, update ledger
   - Timeout after all retries: escalate to full shutdown (section 6.2)
   - Broker error: escalate to full shutdown (section 6.2)
```

**Cost model:**
- Entry cost: actual (from original fill)
- Exit cost: actual (from marketable order execution)
- Both recorded for reconciliation

---

## 6. Reconciliation After Flatten

### 6.1 Reconciliation Deadline (Bounded)

**Timeline:**
- Breach detected at timestamp T
- Flatten initiated immediately (sections 5.2 or 5.3)
- Reconciliation deadline: T + 120 seconds (2 minutes)
- If reconciliation incomplete at deadline: escalate to full shutdown

### 6.2 Reconciliation Conditions

After all positions flattened, verify atomic completion within deadline:

```python
reconciliation_ok = (
    len(ledger.positions) == 0 AND              # Zero open positions
    len(ledger.pending_orders) == 0 AND         # Zero pending orders
    ledger.reserved_cash == 0.0 AND             # Zero reserved cash
    all_completed_trades_have_net_pnl() AND     # All trades reconciled
    audit_chain_is_unbroken() AND               # SafetyViolation hash chain valid
    audit_signatures_verified()                 # All signatures cryptographically valid
)
```

**Failure conditions (any triggers shutdown):**
- Any open position remains
- Any pending order remains
- Any reserved cash remains
- Any completed trade missing net_pnl
- Audit hash chain break detected
- Audit signature verification fails
- Reconciliation deadline exceeded

### 6.3 Reconciliation Outcomes

**Success Path:**
```
1. Zero open positions
2. Zero pending orders
3. Zero reserved cash
4. All completed trades reconciled
5. Daily P&L = sum(completed_trades.net_pnl) by date
6. Audit chain unbroken, signatures verified
7. Reconciliation completed within deadline
8. Remediation status: RECONCILED
9. Manual review required to approve recovery
```

**Failure Path (any condition in 6.2 fails):**
```
1. Record failure details in audit
2. Preserve complete ledger state at failure
3. Dump incident report
4. Set trading_halted = True
5. Escalate to full shutdown (section 7.2)
6. Remediation status: SHUTDOWN
7. No automatic recovery permitted
```

---

## 7. Conditions for Manual Recovery vs. Full Shutdown

### 7.1 Manual Recovery (Permitted Only After Exact Reconciliation)

**PREREQUISITE:** First breach must be followed by exact reconciliation (section 6.2, success path)

**Conditions for manual intervention (human decision only):**

1. **Reconciliation succeeded** (section 6.3, success path)
   - Zero open positions
   - Zero pending orders
   - Zero reserved cash
   - All completed trades recorded
   - Audit chain unbroken, signatures verified
   - Deadline met

2. **Audit trail complete** (section 2)
   - SafetyViolation records hash-linked and signed
   - All remediation events recorded
   - Cryptographic integrity verified

3. **Analysis required:**
   - Root cause of slippage (market volatility, execution delay, other)
   - Whether breach was isolated or systemic
   - Impact on calibration or strategy parameters

**Manual recovery decision:**
- **Authorized by:** named human reviewer with explicit sign-off
- **Recorded in:** audit trail with timestamp and rationale
- **Options:**
  - **Resume trading:** with adjusted parameters (note: Gate16 threshold remains 0.1000%, no changes)
  - **Restart validation:** with same or adjusted config
  - **Recalibrate:** with lessons learned from breach
- **Constraint:** Gate16 threshold (0.1000%) is frozen; no ad-hoc adjustment

### 7.2 Full Shutdown (Required, No Recovery Permitted)

**Automatic shutdown on ANY of these conditions:**

1. **Second breach in same run** (section 3.2)
   - Systemic execution quality issue
   - No manual recovery permitted

2. **Reconciliation failed** (section 6.2 failure path)
   - Any open position remains
   - Any pending order remains
   - Any reserved cash remains
   - Any completed trade missing net_pnl
   - Deadline exceeded
   - Audit chain break detected
   - Audit signature verification fails

3. **Live broker timeout** (section 5.3)
   - Flatten order not filled after 3 retries (~90s)
   - Broker error or exchange halt
   - Cannot guarantee position closure

4. **Audit integrity compromised**
   - SafetyViolation record tampered
   - Hash-chain break detected
   - Cryptographic signature verification failed

**Shutdown actions:**
- Set `trading_halted = True` (fail-closed)
- Dump full ledger state to disk (encrypted, timestamped)
- Generate comprehensive incident report with:
  - Full breach sequence and audit chain
  - Ledger state at failure
  - Remediation timeline
  - Root cause hypothesis
- Notify stakeholders
- Await manual investigation and policy decision
- **No automatic recovery permitted**
- Requires explicit human authorization and new policy decision before future trading

---

## 8. Implementation Checklist

- [ ] SafetyViolation audit record: hash-linked, cryptographically signed
- [ ] SafetyViolation durable storage (append-only database or sealed file)
- [ ] Breach count tracking: first vs second in run
- [ ] Quarantine mode flag and entry authorization checks
- [ ] Pending order cancellation logic (FIFO, with audit)
- [ ] Flatten scheduling for next eligible bar (no look-ahead)
- [ ] Conservative fill model for replay (bid-ask midpoint - 10bps buffer)
- [ ] Marketable exit order with timeout/retry for live broker
- [ ] ExitEvent construction with net P&L = gross - entry_cost - exit_cost
- [ ] Reconciliation bounded deadline (120 seconds)
- [ ] Reconciliation validation: zero open/pending/reserved, audit chain intact
- [ ] Manual recovery workflow (human sign-off required)
- [ ] Full shutdown trigger conditions and actions
- [ ] **End-to-end remediation test (REQUIRED before 48-symbol)**

---

## 9. Required End-to-End Remediation Test

**Test name:** `test_gate16_breach_quarantine_flatten_reconcile_recovery`

**Must prove complete lifecycle (all stages):**

### Stage 1: Breach Detection
- Open 1 long + 1 short position
- Trigger Gate16 post-fill breach (measured_slippage > 0.1000%)
- Verify: SafetyViolation record created with run_id, config_hash, dataset_hash
- Verify: Breach count = 1
- Verify: No second breach (test only first-breach recovery path)

### Stage 2: Quarantine and Cancellation
- Verify: `quarantine_mode = True`, `entry_authorization_enabled = False`
- Verify: New submit_order() calls rejected
- Add pending orders before breach
- Verify: All pending orders cancelled (FIFO)
- Verify: Reserved cash released to available cash

### Stage 3: Flatten Execution
- Verify: Flatten scheduled for next eligible bar (no look-ahead)
- Verify: Conservative fill model applied (midpoint - 10bps for replay)
- Verify: Flatten submitted for both long and short positions
- Verify: ExitEvent.pnl_realized = net P&L (gross - entry_cost - exit_cost)
- Verify: Both positions closed via ledger.close_position()

### Stage 4: Reconciliation
- Verify: Zero open positions
- Verify: Zero pending orders
- Verify: Zero reserved cash
- Verify: All completed trades have net_pnl
- Verify: Daily P&L = sum(completed_trades.net_pnl) by date
- Verify: Reconciliation completed within 120-second deadline
- Verify: Audit hash-chain unbroken
- Verify: All signatures cryptographically valid

### Stage 5: Manual Recovery Decision
- Verify: Remediation status = "RECONCILED"
- Verify: Human reviewer can examine audit trail
- Verify: Manual approval required to resume trading (not automatic)
- Verify: Gate16 threshold remains 0.1000% (no changes)

**Test must pass before any 48-symbol validation or calibration.**

---

## 10. Governance Approval Status

### Defaults Proposed (Require Explicit Sign-Off)

- [ ] **First Gate 16 breach:** automatic quarantine immediately
- [ ] **Second breach in run:** automatic full shutdown (no recovery)
- [ ] **Replay flatten:** next eligible bar with conservative fill model (no look-ahead)
- [ ] **Live flatten:** marketable exit with bounded timeout/retries; failure → shutdown
- [ ] **Audit:** append-only, hash-linked, cryptographically signed (immutable chain)
- [ ] **Reconciliation:** bounded deadline (120s); any failure → shutdown; any success → manual recovery only

### Blockers on 48-Symbol Validation

Until explicit approval:
- ✋ Policy approval signature (named decision authority)
- ✋ Implementation begins (no code changes without approval)
- ✋ Integration test runs (no validation without green test)
- ✋ 48-symbol validation blocked
- ✋ Calibration blocked

### Approval Decision

**Awaiting explicit human authorization:**
```
To: [Authorized Decision Maker]
From: [Risk/Governance]
Re: Gate16 Remediation Policy Approval

Policy Version: 1.0 (attached)

Defaults:
1. First breach → quarantine + flatten
2. Second breach → shutdown
3. Conservative fill model (no look-ahead)
4. Bounded reconciliation (120s)
5. Manual recovery only

Approval required before implementation proceeds.
```

---

**Next Review Date:** After remediation test passes and 48-symbol validation completes
