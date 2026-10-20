# R8 Consensus Governor

R8 is a separate experimental implementation. It does not modify `revision7_avr`.

The contract is:

```text
four independent evidence values
    -> explicit BUY/HOLD/SELL consensus
    -> action intent (entry, maintain, reduce, exit candidate)
    -> separate bounded execution ramp
    -> native safety/execution adapter
```

No AVR, dispatch, studies, or governor evidence is silently combined using
portfolio weightages. A conflicting source produces HOLD unless the explicit
long-exit or short-exit rule is satisfied.
