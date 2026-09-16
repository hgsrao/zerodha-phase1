# P01D R0 Post-Closure Live Observation

**Status: OWNER-AUTHORIZED EXCEPTION — not R0 Stage 2, not a validation,
not a qualification path.**
**Date:** 2026-08-19

---

## 1. What this explicitly is, and is not

The owner explicitly asked to connect Pillar I, Pillar II, and V2-C's
already-frozen, already-**CLOSED/FAIL** signal logic to a real, live
Zerodha (Kite) market-data feed and observe how they behave against
real current prices for an observation window — after being told
precisely what this bypasses, and choosing to proceed anyway.

**What it bypasses, named plainly, not glossed over:**
- R0's own Stage 2 ("Dry Run A — Scientific Shadow Observer") is
  restricted, by R0's own frozen architecture, to *"qualified standalone
  candidates only."* Pillar I, Pillar II, and V2-C are not qualified —
  they are `CLOSED/FAIL`. This document authorizes watching them anyway,
  as a **named, one-off exception**, not a redefinition of Stage 2's
  rule for anything else.
- `P01D_FOUNDATION_CALIBRATION_F0_20260819.md` paused all further
  candidate work pending platform calibration. This observation is not
  F0 work and does not substitute for it — it runs alongside, explicitly
  outside F0's own scope.

**What this is NOT, and structurally cannot become:**
- **Not a rescue.** Nothing observed here can change Pillar I, Pillar
  II, or V2-C's `CLOSED/FAIL` verdicts. R0 remains frozen exactly as
  written, regardless of what this observation shows.
- **Not a qualification path.** A good-looking week of live observation
  cannot promote any candidate to Stage 3, Stage 4, or any form of
  execution authority. There is no PASS state this exercise can produce.
- **Not live trading.** Zero broker writes, zero orders, zero
  `request_entry()` calls, anywhere in this document's scope.
  `LIVE_TRADING_ENABLED` remains `False` throughout, structurally, not
  by promise.
- **Not F0.** It doesn't reconcile data against official NSE truth,
  doesn't test the engine, doesn't touch the economic-geometry
  precheck. F0 remains open and unaffected by this running or not
  running.

## 2. What actually runs

Real-time Kite market data (quotes/1-minute bars for Pillar I/II,
15-minute bars for V2-C's own resolution) feeds each track's **exact,
already-frozen** detection logic — no retuning, no parameter changes,
the identical rules that were tested and closed in R0:

- **Pillar I**: all 5 D1 candidates (C1–C5), unmodified.
- **Pillar II**: all 5 D1 candidates (C1–C5), unmodified.
- **V2-C**: the one frozen classifier (9 features, threshold
  0.77278226), unmodified.

Every model opinion is logged to an **Evidence Ledger** — the same
concept R0's own architecture already specified for Stage 2, reused
here by design rather than invented fresh: timestamp, symbol, each
candidate's eligibility/signal/score/reason code, whether a signal
fired or why it didn't, and — only as a hypothetical, paper
calculation using real observed prices, never a real order — what
entry/exit/P&L a fire would have produced. Nothing here is submitted
to a broker at any point.

## 3. Credentials and connectivity

Real Kite access requires the owner's own authenticated session. I do
not handle raw credentials — the existing project pattern
(`v34_bridge_generate_access_token.py`-style exchange, already used
successfully elsewhere in this project) applies unchanged: the owner
runs the token exchange themselves. The observer reuses the existing
read-only client pattern already proven in this project
(`run_p02_live_scan.py`'s live, continuous, read-only connection), not
a new, unaudited broker-write path.

## 4. Duration and scope — needs your confirmation

Proposed default, adjustable: **one week**, reviewed at the end, extend
only by explicit decision, not automatically. Running all 5 candidates
per Pillar (rather than picking a "best" one) costs nothing extra here,
since nothing is executing — more observation data, not less risk.

## 5. Status

```
R0:                    FROZEN / CLOSED — unaffected, unchanged by anything
                        observed here
F0:                     PROPOSED — unaffected, this document is not F0
This observation:       OWNER-AUTHORIZED EXCEPTION — read-only, no
                        broker writes, no promotion path, no qualification
Duration:               proposed 1 week, pending confirmation
LIVE_TRADING_ENABLED:   False, structurally, throughout
```
