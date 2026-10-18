# R6 AVR — Standalone implementation package

**Status: offline research implementation, not qualified for live trading.**

Files:
- `r6_avr_core.py` — positional PI, independent voltage/field AVR, financial exposure-feedback AVR, governor/AVR/safety unit master, deterministic bench model, checkpoint payloads.
- `test_r6_avr_core.py` — standalone unit tests (31 currently passing).
- `run_r6_avr_bench.py` — no broker or market feed; proves independent simulated actuator/voltage process response and demonstrates vote arbitration.
- `CLAUDE_IMPLEMENTATION_PROMPT.md` — precise integration specification with preservation and acceptance criteria.

This implementation deliberately separates two operating worlds:

1. **VoltageAVR** regulates modeled/measured per-unit terminal voltage using independently measured/reactive `Q` and actual field response. The `BenchExciterPlant` creates clearly labeled physical-equivalent simulations. Never pass NIFTY, OHLC or churn as measured electrical voltage/MVAR.
2. **ExposureAVR** regulates actual *financial* bay utilization from fill and acknowledged-order ledgers. The outer and inner loops get separately sourced occupancy measurements. It neither assumes nor fabricates MVAR. It returns bounded exposure votes but cannot submit orders.

The same immutable `Vote` protocol is used by `UnitMaster`. ENTRY requires Mark V + AVR + existing safety permission; authorized EXIT or mandatory TRIP cannot be vetoed by the other controller; REDUCE closes only the difference. Commercial minimum notional is applied **once** after bounded lot sizing. No global FullR5Adaptation import occurs anywhere in this package.

## Tests

```bash
python3 -m unittest -v test_r6_avr_core.py
python3 run_r6_avr_bench.py
```

## Important design and validation restrictions

- This is **not a drop-in replacement** for `revision5/ccpp_protection_cubicles.py`. Do not overwrite that class. The actual repository's orchestrator, Mark V return schema, safety gates, position lifecycle, fill hooks and checkpoint implementation need a thin, audited adapter.
- UnitMaster returns **intents**, not broker orders. The existing execution ledger must enforce idempotency, deduplicate, handle partial fills/cancel/rejections, and serialize the resultant actual state. An `event_id` is a stable identifier, not an exactly-once guarantee.
- `ExposureAVR` needs credible real fills and **broker-acknowledged pending entry** notional, including every symbol sharing the bay. It cannot assume submissions are fills or that unacknowledged orders were accepted. The adapter must handle pending exits and uncertain orders fail-closed.
- Financial `max_total_quantity` is explicitly the maximum *total held shares of this one symbol*. Its allocation must be consistent with portfolio-wide/bay-wide existing exposure and separately authorized risk limits. Do not interpret the legacy governor's `size_multiplier` as total quantity without an audited conversion.
- The droop percentages 7.5% GTG and 10% CSTG/BPSTG are **experimental**. Physical reactive Q base/sign and generator-sharing dynamics must be validated in the bench. The actual trading feedback does not apply physical MVAR droop.
- The example PI gains and bench plant constants are placeholders requiring parameter approval and calibration. Safety ceilings must be no less restrictive than the sealed contracts; do not use this prototype to relax protections.
- Trip reset requires explicit operator authorization and a healthy measured state. Trip state persists until reset; do not clear on daily session reset.
- All snapshots/restore operations require coordinated atomic checkpointing with the existing portfolio/governor/execution state. Restored controllers demand fresh causal measurements before voting.
- Unit tests only cover modeled scenarios. No historical Engine A/B/C or Git commit golden tests have been run here.

## Expected repository integration destination

Import the file as an isolated R6 module; keep existing R5/R6 production code unchanged until the approval gate. After evidence, add a versioned, opt-in adapter with explicit mode selection; no implicit full-adaptation enablement.
