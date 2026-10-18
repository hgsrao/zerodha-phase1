# Incident: rate governor hang on reboot-stale ledger entries — 2026-08-24

## Symptom (live, real)

Monday 2026-08-24, market open. First `r1c_live_observer.py --once` dry run
of the day hung with zero output past `fetching AXISBANK...` for 9+ minutes
(process PID 11316, started 09:26:03, still alive/unresponsive at 09:35:07
per `Get-CimInstance Win32_Process`). No traceback on Ctrl+C — the script's
own `KeyboardInterrupt` handler swallows it, printing only
`[SYSTEM] Live observer stopped by user.`

## Root cause

`kite_request_governor.py`'s `KiteRequestGovernor` persists per-endpoint-
class request timestamps to a shared JSON ledger
(`C:\zerodha_data\kite_rate_governor\kite_rate_governor_ledger.json`) so
that multiple processes sharing one Kite account coordinate rate limits.
`now_fn` defaults to `time.monotonic()`.

`time.monotonic()` is only meaningful within one continuous OS session —
it resets near-zero on every reboot. The ledger file survives reboots.
Real ledger content pulled mid-incident:

```json
{"default": [41990.98, 41991.03, 41991.14, 1330.25],
 "historical": [42497.31, 42498.18, 42498.25],
 "quote": [42041.70]}
```

Three clusters at wildly different magnitudes in one file — proof the
machine rebooted between writes. On the new (post-reboot) session, the
governor's window filter (`t > window_start`) treated the old, numerically
huge entries as "within the last second" indefinitely, since the new
session's low-valued clock would need ~11.6 hours to numerically catch up
to them. `acquire()` for the `HISTORICAL` class (already at its 3/s
"occupied" limit per the stale entries) then computed
`wait_seconds ≈ 42497 - (new session's now) ≈ tens of thousands of
seconds` and called `sleep_fn(wait_seconds)` once — a single,
uninterruptible multi-hour sleep. The intended safety bound,
`_MAX_ACQUIRE_WAIT_SECONDS = 30.0`, could never fire: the deadline is only
re-checked *between* `while True` loop iterations, never during an
in-progress `sleep_fn` call.

## Fix

One-line change in `acquire()`: the recency filter gained an upper bound —
`window_start < t <= now` instead of `t > window_start`. A legitimate
ledger entry can never be from the future relative to the current
process's own clock reading, so this discards exactly the cross-boot-stale
case (and nothing else) — same fail-safe direction as the existing
corrupt-JSON handling in `_read_ledger` (more permissive on a bad read,
never less).

`kite_request_governor.py` was not under any existing hash-freeze
manifest, so no SUPERSEDED_ copy was required before editing (unlike
`r1c_live_observer.py`, which is frozen under
`R1C_LIVE_OBSERVER_HASH_MANIFEST_*`).

- File hash after fix (sha256): `b2afd333244b04e87cf4313090d5375f19ee90cfa28af1f65695519663e81f8e`

## Immediate unblock (operator action taken live)

`kite_rate_governor_ledger.json` reset to `{}` by the user directly (pure
rate-limit bookkeeping, no financial/position state — safe to discard).

## Verification

- New regression test added:
  `test_stale_cross_boot_ledger_entry_is_not_treated_as_recent` in
  `test_kite_request_governor.py`, reproducing the exact real ledger
  values above. 15/15 governor tests pass.
- Downstream regression check: `test_r1c_live_observer.py` (12/12) and
  `test_v34_bridge_trigger_main.py` unaffected, zero failures.
- Real-world confirmation: same-morning re-run of
  `python r1c_live_observer.py --once` after the fix completed cleanly —
  all 19 symbols + NIFTY 50 fetched (11,425 / 11,650 bars respectively),
  `poll complete - fetched_new_bars=True`, no hang.

## Note on scope

This poll ran at 09:39 IST, before `MARKET_CLOSE` — by design,
`run_one_poll()` only runs composite evaluation and writes to the
Evidence Ledger once `now.time() >= MARKET_CLOSE`. No new Evidence Ledger
entry was expected or written by this run; that is correct, not a symptom
of anything. A post-close `--once` run is still needed today to see a real
end-of-day composite evaluation.
