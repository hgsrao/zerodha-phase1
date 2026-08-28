# V3.4 P0-3 — Capital & Operational Safety Gate

**Status:** RED / Specification Frozen for Test-First Implementation  
**Live capital:** LOCKED  
**Scope:** Capital ceiling, daily-loss circuit, runtime kill switch, durable halt state, and preservation of emergency-exit capability.

## 1. Purpose

P0-3 closes the safety gap identified by the Production Runner & Broker Adapter Integration Audit.

P0-3 does **not** modify the P0 emergency-exit contract. It adds an independent risk-control layer around new capital deployment.

The governing rule is:

> **Risk controls may prevent new entry side effects, but they must never prevent an already-required emergency exit from being evaluated or submitted.**

No P0-3 control may silently modify an order. A blocked entry must produce a deterministic state/action and durable audit evidence.

---

## 2. Frozen constants

| Control | Frozen value |
|---|---:|
| Hard bot capital ceiling | ₹20,000 |
| Daily loss threshold | ₹2,000 |
| Currency | INR |
| Trading scope | Bot-owned NSE/MIS exposure |
| Emergency exit | Independent of entry permission |
| Live deployment | Disabled until P0-3 GREEN |

These values must be represented as `Decimal`, not binary floating-point.

---

## 3. Capital-ceiling invariant

### 3.1 Definition

`deployed_capital` is the maximum capital that could become committed by the bot if all currently authorized BUY exposure executes.

For each bot-owned open position:

```text
position_notional = max(quantity, 0) × authoritative broker average/entry price
```

For each bot-owned active/pending BUY order:

```text
pending_buy_notional = remaining BUY quantity × authoritative order price
```

The risk layer must reject an entry if:

```text
deployed_capital + proposed_entry_notional > ₹20,000
```

Equality is permitted:

```text
deployed_capital + proposed_entry_notional == ₹20,000
```

Any malformed broker position/order payload required for this calculation is a **contract violation** and causes `RECONCILIATION_HALT`; it is never estimated or ignored.

### 3.2 Emergency-exit exclusion

SELL emergency exits do not consume additional capital-ceiling capacity.

An emergency exit is a liquidation side effect against an already-established position and remains available even when the capital ceiling is exhausted.

---

## 4. Daily-loss circuit invariant

The circuit uses a broker-authoritative daily risk snapshot.

The frozen contract is:

```text
daily_net_pnl = broker-authoritative realized P&L
              + broker-authoritative unrealized MTM
              - broker-authoritative charges/fees
```

If the broker adapter cannot provide a complete, structurally valid snapshot, the system must fail closed.

### Threshold

The circuit trips when:

```text
daily_net_pnl <= -₹2,000
```

It must also trip if the value crosses the threshold between polling cycles and the next authoritative snapshot confirms the breach.

### Action

On breach:

1. Persist `TRADING_HALTED_RISK`.
2. Set `clearance_required = True`.
3. Record reason, timestamp, source and observed P&L.
4. Block all **new entry** submissions.
5. Do not cancel or disable the emergency-exit pathway.
6. Require explicit operator clearance after broker reconciliation before new entries can resume.

There is **no automatic reset** merely because P&L later improves.

---

## 5. Runtime kill switch

The runner must support an external operator-controlled kill mechanism.

The first implementation should use a filesystem control artifact because it is deterministic and easy to audit.

Frozen contract:

```text
KILL_SWITCH_FILE exists
        ↓
new entry submission prohibited
        ↓
durable TRADING_HALTED_KILL_SWITCH state
        ↓
clearance_required = True
```

The running process must re-check the kill switch before every new entry side effect.

A kill switch appearing after the previous loop iteration but before the actual broker submission must therefore still block that submission.

### Important distinction

The kill switch blocks **new entry side effects**.

It must not block:

- broker observation,
- reconciliation,
- existing protective-stop management,
- emergency liquidation required by an established exit invariant.

---

## 6. Crash persistence

The following state must survive process death:

- risk-halt status;
- halt reason;
- halt source;
- halt timestamp;
- observed daily P&L;
- capital-exposure snapshot used for the decision;
- `clearance_required`;
- kill-switch state, if persisted by the runner.

After restart, the engine must not silently return to entry-enabled operation.

Startup must reconcile broker reality first.

---

## 7. Broker-authority rule

The risk layer must never infer capital or P&L from local assumptions when broker reality is available.

Forbidden:

- assuming a local order was not filled;
- assuming a pending BUY has zero exposure;
- estimating malformed prices;
- ignoring an unknown broker order;
- replacing missing P&L with zero;
- silently rounding quantities or prices;
- clearing a risk halt because the local process restarted.

Observation failure and contract violation remain distinct:

| Condition | Required action |
|---|---|
| Known transport ambiguity | bounded observation/reconciliation |
| Malformed broker payload | immediate `RECONCILIATION_HALT` |
| Capital > ₹20,000 | durable entry block |
| Daily loss ≤ -₹2,000 | durable entry block |
| Kill switch active | durable entry block |

---

## 8. Required state model

P0-3 introduces the following entry-control states without altering the emergency-exit states:

- `TRADING_HALTED_RISK`
- `TRADING_HALTED_KILL_SWITCH`

These states are **entry gates**, not liquidation states.

If an active position simultaneously requires emergency liquidation, the engine must continue through the existing P0 emergency-exit lifecycle.

---

## 9. Operator clearance

Clearance must require:

1. non-empty operator acknowledgement;
2. broker position reconciliation;
3. broker order reconciliation;
4. confirmation that the kill switch is no longer active;
5. confirmation that the current daily loss is above the frozen threshold;
6. confirmation that proposed future entry remains within the ₹20,000 ceiling.

A restart is never equivalent to clearance.

---

## 10. Acceptance matrix

### P03-01 — Capital

- exact ₹20,000 exposure is accepted;
- ₹20,000.01 exposure is rejected;
- existing exposure + pending BUY is included;
- multiple positions/orders are aggregated;
- malformed quantity fails closed;
- malformed price fails closed;
- unexpected broker order cannot be ignored;
- emergency SELL remains available while ceiling is exhausted.

### P03-02 — Daily loss

- `-₹1,999.99` remains entry-eligible if all other controls pass;
- `-₹2,000.00` trips the circuit;
- loss beyond threshold trips the circuit;
- circuit is durable across restart;
- no new entry after trip;
- improving P&L does not automatically clear the halt;
- emergency exit remains available.

### P03-03 — Kill switch

- absent file permits normal entry evaluation;
- present file blocks entry;
- file appearing between observation and submission blocks submission;
- kill state persists across restart;
- removing the file does not automatically clear the durable halt;
- emergency exit remains available.

### P03-04 — Combined controls

The entry permission predicate is:

```text
ENTRY_ALLOWED =
    LIVE_TRADING_ENABLED
    AND NOT clearance_required
    AND NOT risk_halt
    AND NOT kill_switch_active
    AND daily_net_pnl > -₹2,000
    AND projected_deployed_capital <= ₹20,000
    AND broker_observations_valid
```

Emergency-exit permission is deliberately **not** part of this predicate.

---

## 11. Production authorization gate

P0-3 is GREEN only when:

- every P03 test passes;
- existing E06, E20 and P0-2 suites remain GREEN;
- the installed Kite Connect SDK is verified to support the exact production order parameters;
- real broker response semantics for `market_protection=-1` are captured and reconciled;
- no hard-coded credentials are present;
- live trading remains disabled until a separate authorization step.

**Until then: ₹20,000 live capital remains LOCKED.**
