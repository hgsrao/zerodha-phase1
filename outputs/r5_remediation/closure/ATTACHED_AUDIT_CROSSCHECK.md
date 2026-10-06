# Attached five-pillar audit cross-check

Source note: /home/srinivas/.codex/attachments/663b03bf-af15-4ffb-a528-c71f509bd55c/Pasted text.txt.
Cross-check is against the current worktree, not an assumed identical archive. No engine source edits were made.

1. Durable handoff intent before broker submission is supported by the implementation. This is intent/confirmation coordination, not an atomic distributed transaction across SQLite and the broker. Full integrated/live readiness remains unproven.
2. Delivery authorization classification and explicit failure receipts exist. Adapter findings do not prove live caller propagation: the current selected orchestrator is paper-only. The assertion of no pass in placement-related handling is too broad: broker_adapter_kite.py contains a pass after failed read-only ambiguity lookup, followed by an explicit ambiguous/no-retry return. Blind order-placement retries are not the modern contract.
3. Decimal tick-flooring of the 0.95 sell limit exists. Runtime ensure_protection is a paper contingent SL-M record, not the live GTT adapter. Shared helper math does not verify end-to-end live GTT ratchet/re-arming. GTT triggering does not guarantee a fill; official Zerodha terms https://zerodha.com/tos/gtt explicitly describe unfilled gap-breached limit orders.
4. Feedback snapshot plus receipt are atomically committed when the optional paper journal is attached. Atomic SQLite state/receipt storage is scoped durability, not universal live process recovery. The close-feedback method also relies on its in-memory DONE guard before applying its two consumers.
5. Engine B _states has no terminal eviction; runtime _history restored entries have no terminal cleanup. These are genuine retention candidates. The actual per-position causal history in protection is truncated in handle_bar to max(100, structural_window+1), so the note's implication of unlimited bars for every trade is inaccurate. Receipt retention is intentional deduplication; deleting it is a contract change. Eventual OOM is not demonstrated: no memory profile, rate or resource budget was supplied.

## Proposed Patch A: rejected, demonstrated counterexample

Using the production close-feedback method and existing fixture builder, no source mutation:
- Apply feedback twice for the same trade with DONE retained: merit outcomes 1, governor outcomes 1.
- Remove the DONE receipt exactly as proposed and repeat the same trade: merit outcomes 2, governor outcomes 2.
- Journal attached: false, a supported default path.

See feedback_eviction_counterexample.json. This proves the note's claim that the in-memory key has no further purpose is false. With a journal attached, post-mutation checkpoint validation is also not a replacement for a pre-mutation duplicate guard.

## Correct remediation package (C04 retention/recovery)

- Preserve duplicate suppression. Before any cache compaction, consult a durable processed-event index using a stable trade identity, or establish a proved bounded replay horizon. Do not blindly evict DONE keys or substitute arbitrary LRU expiry.
- Evict terminal Engine B state and runtime restored history only after successful durable CLOSED save; failed saves must retain recovery state.
- Update restoration/checkpoint policy so a restart cannot resurrect terminal controller state from an older position snapshot.
- Test duplicate feedback after compaction and restart, closure-save fault preservation, active-position state continuity and long-run live-state cardinality. Define which completed ledgers/history are intentionally retained or streamed; measure actual memory growth before calling OOM.

## Disposition

Reject the proposed unconditional receipt pop. Accept terminal-state retention as a targeted implementation activity with the above constraints. The supplied production-certification conclusion is not supported. C01 experimental runner, complete C03/C04 lifecycle/recovery, full Block1 acceptance and live execution boundary remain open; two cleanup edits do not close them.
