# Qwen 3.8 R7 independent engineering review

You are working only inside this directory. This is an isolated copy. Never access, modify, or copy files into the main repository at `/home/srinivas/projects/zerodha-r6-no-adaptation`.

You have permission to inspect, edit, and test this isolated workspace. Work block by block through all ten black-box areas:

1. core R7 controller and state transitions
2. governor parameters and closed-loop control
3. AVR/reference model and signal handling
4. FSM anti-churn and emergency reversal behavior
5. demand-aware allocator and capacity/risk bounds
6. native risk, protective exits, and pending fills
7. Engine B/C integration and class wiring
8. checkpoint, restore, idempotency, and causality
9. telemetry, diagnostics, audit mapping, and fail-closed behavior
10. tests, replay harnesses, dependency boundaries, and operational safety

For every block:

- inspect the implementation line by line and trace callers/callees;
- identify defects, unsafe assumptions, missing tests, and false claims;
- write targeted tests before changing behavior where practical;
- implement fixes in this isolated workspace when evidence supports them;
- run the narrowest relevant tests, then the broader available regression suite;
- record exact files, line numbers, commands, outputs, and remaining blockers.

Hard safety constraints:

- keep `LIVE_TRADING_ENABLED=False`;
- never connect to Zerodha, place orders, or use live credentials;
- do not alter frozen datasets or baseline/native risk behavior;
- do not weaken protective exits, fail-closed behavior, or audit evidence;
- do not claim replay success unless it actually ran and produced evidence;
- do not copy changes back to the main repository.

Deliverables in this directory:

- `REVIEW_FINDINGS.md`: block-by-block findings classified PASS, FAIL, BLOCKED, or UNVERIFIED;
- `PROPOSED_CHANGES.patch`: complete diff of every source/test change;
- `TEST_RESULTS.md`: commands, pass/fail output, dependency blockers, and hashes;
- `REVIEW_SUMMARY.md`: concise recommendation for the human audit.

At completion, show `git diff --stat`, `git diff --check`, the test summary, and the SHA-256 hashes of all deliverables.
