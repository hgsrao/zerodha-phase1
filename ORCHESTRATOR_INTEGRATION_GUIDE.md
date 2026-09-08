# Revision2 Portfolio Orchestrator Integration Guide

**Purpose:** Detailed implementation steps to wire cross-session rejection and Gate16 remediation into the orchestrator lifecycle.

**Status:** Implementation blueprint (do not execute yet—requires careful integration)

---

## 1. Add Lifecycle Dependencies to `__init__`

**Location:** `revision2/portfolio_orchestrator.py`, lines 92-151

**Add after line 122 (after self.broker initialization):**

```python
# Safety control lifecycle dependencies (wired during run())
self.cross_session_policy = None  # Set by caller before run()
self.gate16_remediator = None     # Set by caller before run()
self.event_ledger = []            # Authoritative event record
self.quarantine_mode = False
self.trading_halted = False
```

**Acceptance Criteria:**
- Attributes exist and initialize to None
- No errors on startup
- __init__ does not instantiate these (they're passed by caller)

---

## 2. Add Cross-Session Pre-Submission Check

**Location:** `revision2/portfolio_orchestrator.py`, lines 496-499

**Before creating order, add check:**

```python
# Line 495: Before order creation, validate cross-session
if self.cross_session_policy is not None:
    ok, reason = self.cross_session_policy.check_pre_submission(
        symbol=symbol,
        decision_timestamp=str(timestamp),
        current_bar_index=bar_idx,
        all_bars=symbol_bars,  # Pass real DataFrames
    )
    if not ok:
        # Record rejection event
        self.event_ledger.append({
            "event_type": "ORDER_REJECTED",
            "timestamp": str(timestamp),
            "symbol": symbol,
            "order_id": None,
            "reason": reason,  # "CROSS_SESSION_REJECTED" or "NO_ELIGIBLE_FILL_BAR"
        })
        funnel["cross_session_rejections"] = funnel.get("cross_session_rejections", 0) + 1
        continue  # Skip this candidate, don't queue
```

**Acceptance Criteria:**
- Cross-session rejections recorded in event ledger
- Candidates with cross-session issues never reach order creation
- Funnel counts cross-session rejections separately

---

## 3. Add Gate16 Post-Fill Check

**Location:** `revision2/portfolio_orchestrator.py`, lines 372-385 (inside fill processing)

**After fill is recorded, add check:**

```python
# Line 385: After fill["passed"] is True, check Gate16
if fill["passed"] and self.gate16_remediator is not None:
    # Check slippage: intended price vs actual fill price
    is_breach = self.gate16_remediator.detect_breach(
        timestamp=str(timestamp),
        symbol=event.symbol,
        order_id=pending["order"].order_id,  # Need to track this
        fill_id=f"fill_{event.bar_idx}_{event.symbol}",
        intended_entry_price=pending["order"].entry_price,  # From plan
        actual_fill_price=fill["filled_price"],
    )
    
    if is_breach:
        # Record Gate16 breach event
        self.event_ledger.append({
            "event_type": "GATE16_BREACH",
            "timestamp": str(timestamp),
            "symbol": event.symbol,
            "fill_id": f"fill_{event.bar_idx}_{event.symbol}",
            "measured_slippage_pct": abs(fill["filled_price"] - pending["order"].entry_price) / pending["order"].entry_price * 100,
        })
        
        # Enter quarantine mode
        self.quarantine_mode = True
        self.event_ledger.append({
            "event_type": "QUARANTINE_STARTED",
            "timestamp": str(timestamp),
        })
        
        # DON'T reject the fill—it's already in the book
        # Instead, proceed to cancellation and flatten logic below
```

**Acceptance Criteria:**
- Gate16 breaches detected AFTER fill is recorded
- Breach events recorded in ledger
- Quarantine flag activated (blocks new entries)
- Fill is not reversed (quarantine happens post-fill)

---

## 4. Implement Pending Order Cancellation (Quarantine)

**Location:** Same fill-processing section, after Gate16 check

**Add after quarantine activation:**

```python
# Line ~395: If in quarantine, cancel all pending orders
if self.quarantine_mode and not self.trading_halted:
    symbols_to_cancel = list(self.pending_entries.keys())
    for cancel_symbol in symbols_to_cancel:
        if cancel_symbol == event.symbol:
            continue  # Don't cancel the order we just filled
        
        pending = self.pending_entries[cancel_symbol]
        # Record cancellation
        self.event_ledger.append({
            "event_type": "ORDER_CANCELLED",
            "timestamp": str(timestamp),
            "symbol": cancel_symbol,
            "order_id": pending["order"].order_id,
            "reason": "QUARANTINE_MODE_GATE16",
        })
        # Release reserved cash
        del self.pending_entries[cancel_symbol]
        funnel["pending_orders_cancelled"] += 1
```

**Acceptance Criteria:**
- All pending orders for other symbols cancelled
- Cancellations recorded in event ledger
- Reserved cash implicitly released (orders removed from pending)
- Original breached fill remains in open_trades

---

## 5. Schedule Adverse Flattening (Next Eligible Bar)

**Location:** Still in fill processing, after cancellations

**Track positions to flatten:**

```python
# Line ~410: Schedule all open positions for adverse flatten
if self.quarantine_mode and not self.trading_halted:
    # Mark all current open_trades for next-bar flatten
    scheduled_flattens = {}
    for flatten_symbol, trade in self.open_trades.items():
        scheduled_flattens[flatten_symbol] = {
            "entry_price": trade["entry_price"],
            "entry_bar_idx": trade["entry_bar_idx"],
            "side": trade["side"],
            "quantity": trade["quantity"],
        }
    
    # Store for later execution (need to track across bars)
    if not hasattr(self, '_scheduled_flattens'):
        self._scheduled_flattens = {}
    self._scheduled_flattens.update(scheduled_flattens)
```

**Acceptance Criteria:**
- All positions marked for next-bar flatten
- Flatten scheduled when next eligible bar arrives for each symbol

---

## 6. Execute Adverse Flattens (At Next Eligible Bar)

**Location:** `revision2/portfolio_orchestrator.py`, before signal generation (around line 426)

**Add flatten execution loop:**

```python
# Line 426: Before exit checking, execute any scheduled flattens
if self.quarantine_mode and hasattr(self, '_scheduled_flattens') and event.symbol in self._scheduled_flattens:
    flatten_plan = self._scheduled_flattens[event.symbol]
    bars = symbol_bars[event.symbol]
    
    # Use next bar open with adverse adjustment
    bar_open = float(bars.iloc[event.bar_idx]["open"])
    adverse_factor = 0.999 if flatten_plan["side"] == "BUY" else 1.001
    exit_price = bar_open * adverse_factor
    
    # Record flatten event
    self.event_ledger.append({
        "event_type": "POSITION_FLATTENED",
        "timestamp": str(timestamp),
        "symbol": event.symbol,
        "exit_price": exit_price,
        "adverse_factor": adverse_factor,
    })
    
    # Execute adverse flatten
    self._execute_exit(
        symbol=event.symbol,
        timestamp=timestamp,
        trade={
            "side": flatten_plan["side"],
            "entry_price": flatten_plan["entry_price"],
            "quantity": flatten_plan["quantity"],
        },
        exit_price=exit_price,
        reason="quarantine_flatten",
        exit_bar_idx=event.bar_idx,
    )
    
    # Remove from scheduled
    del self._scheduled_flattens[event.symbol]
```

**Acceptance Criteria:**
- Flattens execute at next eligible bar for each symbol
- Exit price uses bar open × adverse factor (no future data)
- Flatten events recorded in ledger
- Positions close exactly (no open positions remain)

---

## 7. Add Authoritative Event Emissions

**Location:** Throughout run() where key actions happen

**Events to emit (minimal set):**

```python
# Existing fill processing (line ~373):
if fill["passed"]:
    self.event_ledger.append({
        "event_type": "FILL",
        "timestamp": str(timestamp),
        "symbol": event.symbol,
        "fill_price": fill["filled_price"],
        "quantity": pending["quantity"],
    })

# Order submission (line ~496):
self.event_ledger.append({
    "event_type": "ORDER_SUBMITTED",
    "timestamp": str(timestamp),
    "symbol": symbol,
    "order_id": order.order_id,
})

# At run() end, reconciliation:
self.event_ledger.append({
    "event_type": "RECONCILIATION_COMPLETED",
    "timestamp": str(datetime.now().isoformat()),
    "open_positions": len(self.open_trades),
    "pending_orders": len(self.pending_entries),
    "realized_pnl": self.broker.realized_pnl,
})
```

**Acceptance Criteria:**
- All critical actions recorded in event_ledger
- Events include timestamp, symbol, order ID, prices
- Ledger grows monotonically (append-only)
- Events can be replayed to reconstruct state

---

## 8. Implement Final Reconciliation Check

**Location:** `revision2/portfolio_orchestrator.py`, end of run() method

**Add reconciliation enforcement:**

```python
# Line ~XXX: At run() end, enforce reconciliation
unrealized_pnl = self._mark_to_market_equity() - self.starting_equity - self.broker.realized_pnl

reconciliation_exact = (
    len(self.open_trades) == 0 and
    len(self.pending_entries) == 0 and
    abs(unrealized_pnl) < 0.01  # Should be zero after EOD flatten
)

if self.quarantine_mode and not reconciliation_exact:
    self.trading_halted = True
    self.event_ledger.append({
        "event_type": "RECONCILIATION_FAILED",
        "open_positions": len(self.open_trades),
        "pending_orders": len(self.pending_entries),
        "unrealized_pnl": unrealized_pnl,
    })
    raise RuntimeError("Reconciliation failed after quarantine flatten")

self.event_ledger.append({
    "event_type": "RECONCILIATION_COMPLETED",
    "exact": reconciliation_exact,
    "open_positions": len(self.open_trades),
    "pending_orders": len(self.pending_entries),
    "realized_pnl": self.broker.realized_pnl,
})

return {
    "status": "REMEDIATION_REQUIRED" if (self.quarantine_mode and reconciliation_exact) else "PASSED",
    "quarantine_mode": self.quarantine_mode,
    "trading_halted": self.trading_halted,
    "event_ledger": self.event_ledger,
}
```

**Acceptance Criteria:**
- Reconciliation is checked and enforced
- REMEDIATION_REQUIRED if breach occurred but recovered
- FAILED if reconciliation is incomplete
- Event ledger returned with results

---

## Testing Strategy

### Unit Tests (Before Integration)
1. Cross-session pre-submission with DataFrame bars
2. Gate16 detection with real slippage values
3. Quarantine mode prevents new entries
4. Cancellations release orders from pending
5. Adverse flatten uses bar open × adverse factor

### Integration Tests (After Implementation)
1. Real orchestrator.run() with small bars
2. Friday → Monday order rejection
3. Gate16 breach → quarantine → flatten → reconciliation
4. SUNPHARMA one-month sealed replay

---

## Critical Notes

- **Do NOT reverse fills**: Gate16 breach happens AFTER fill is recorded; remediation is quarantine/flatten, not unfill
- **Use next eligible bar**: Flattens must use next bar's OPEN, not current bar close
- **Event ledger is authoritative**: All metrics derive from events, not broker state estimates
- **Reconciliation is binary**: Either exact (PASSED) or incomplete (FAILED)—no partial credit
- **Second breach triggers shutdown**: If quarantine is active and another breach occurs, set trading_halted = True

---

## Implementation Order (Do NOT deviate)

1. ✅ Add lifecycle dependencies to `__init__`
2. ✅ Add cross-session pre-submission check (before order creation)
3. ✅ Add Gate16 post-fill check (after fill recorded)
4. ✅ Add pending order cancellation (in quarantine)
5. ✅ Add flatten scheduling (mark for next bar)
6. ✅ Add flatten execution (when next bar arrives)
7. ✅ Add event emissions (all critical points)
8. ✅ Add reconciliation check (at run end)
9. ✅ Write unit tests (for each step)
10. ✅ Write integration tests (real orchestrator.run())

---

**Do NOT attempt full-run validation until all 10 steps are complete and tested.**

**Status:** Ready for implementation by next coder. This document is the blueprint.
