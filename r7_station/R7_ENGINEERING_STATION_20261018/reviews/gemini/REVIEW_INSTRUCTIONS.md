# Independent R7 model review

These are isolated review copies. Never edit the authoritative repository at `/home/srinivas/projects/zerodha-r6-no-adaptation` and never copy changes back automatically.

Review every supplied source file line by line, including black-box/native integration boundaries. Review each module in this order:

1. `01_core_control`: R7 state, features, AVR, governor, allocator, coordinated controller.
2. `02_engine_integration`: R7 engine factory, native admission, exits, pending orders, checkpoints, telemetry.
3. `03_native_avr_risk`: R5 authority/topology and R6 physical AVR/governor preservation.
4. `04_tests_audit`: tests, fixtures, diagnostics, and audit claims.

For every finding, give file, line, severity, evidence, impact, and a concrete proposed patch. Check all 25 audit points, all 11 governor parameters, AVR mapping, native protective exits, fail-closed behavior, causal ordering, checkpoint behavior, and data/replay claims.

You may make changes only inside your own model-specific workspace. Do not claim a change is accepted. At the end, produce `REVIEW_FINDINGS.md`, `PROPOSED_CHANGES.patch`, and `TEST_RESULTS.md`. Any proposed change must include a regression test and must preserve `LIVE_TRADING_ENABLED=False`.
