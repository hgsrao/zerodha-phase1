# V34-P02-H — Release Verification Report

The question this document answers: **can the P02 multi-position CNC
candidate be frozen with confidence that the implementation matches
`V34_P02_PORTFOLIO_INVARIANTS_SPEC.md`?** Not "is it profitable," not
"can it go live" — those are separate, later gates. No new features were
added in this pass; every item below is verification only, and the one
production change made here was applied because verification surfaced a
genuine defect against the frozen rules (noted explicitly, §5).

## 1. Regression corpus

| Suite | Result |
|---|---|
| Full repo (`pytest --ignore=work`) | **577 passed**, exit 0 |
| P02 family, combined | **196 passed**, exit 0 |

P02 family breakdown:

| File | Tests | Phase |
|---|---|---|
| `test_v34_p02_state.py` | 37 | P02-B |
| `test_v34_broker_exposure_scope_p02_multipos.py` | 11 | P02-B |
| `test_v34_p02_multipos_engine.py` | 32 | P02-C |
| `test_v34_p02_accounting.py` | 48 | P02-D |
| `test_v34_p02_authorizer.py` | 41 | P02-E |
| `test_v34_p02_lifecycle_integration.py` | 11 | P02-F |
| `test_v34_p02_adversarial_matrix_multipos.py` | 16 | P02-G |
| **Total** | **196** | |

No skipped, xfail, or conditionally-omitted tests anywhere in the P02
family (`grep -n "pytest.mark.skip\|pytest.skip(\|pytest.mark.xfail"` →
zero matches). Every test that exists actually ran and passed.

## 2. Isolation from the production engine/runner

```
grep -n "^from institutional_engine_v34_p01d_candidate\|^from run_production_p01d_candidate\|
          ^import institutional_engine_v34_p01d_candidate\|^import run_production_p01d_candidate\|
          from institutional_engine_v34_p01d_candidate import\|from run_production_p01d_candidate import"
    <all 13 P02 production+test files>
→ zero matches
```

```
grep -n "^from broker_exposure_scope import\|^import broker_exposure_scope$"
    <all 13 P02 production+test files>
→ zero matches (the P02 family imports only its own fork, broker_exposure_scope_p02_multipos)
```

Production files confirmed byte-identical to their pre-P02 state via
SHA-256 (spot-checked, no diff tooling available outside git):
`institutional_engine_v34_p01d_candidate.py`,
`run_production_p01d_candidate.py`, `broker_exposure_scope.py` — all
present, all unmodified by this project.

## 3. Single BUY dispatch path — no bypass of the authorizer

Every `place_order(` call site in the P02 production files was traced by
hand, not assumed:

- **Engine** (`institutional_engine_v34_p02_multipos_candidate.py`): exactly
  one call site, `self.broker.place_order(...)` inside `_step_entry_submit`
  (line ~978). No other method in the engine can create a BUY.
- **Adapter** (`v34_p02_broker_adapter.py`): exactly one call site that
  reaches the raw broker, `self.raw_broker.place_order(...)`
  (`KiteBrokerAdapterMultiPos.place_order`, line 159) — and it is
  unconditionally preceded, in the same method, by
  `build_portfolio_snapshot` → `authorize_and_persist` → a check on
  `decision.allowed` → `mark_reservation_submitted` (durable) → *only
  then* the raw call. There is no code path in this file that reaches the
  raw broker's `place_order` without passing through the full gate stack
  first.
- `submit_emergency_exit` has exactly one call site in each of the engine
  and adapter, both unconditional pass-throughs — correct, per spec §2/§5,
  since exits are never gated.

No raw order pathway bypassing the authorizer exists.

## 4. `ENTRY_LOCK` / `ENGINE_HALT` exception ordering

`_step_entry_submit`'s `try` block around the `place_order` call has
`except EntryPolicyDeclinedError as exc:` **before** the generic
`except Exception as exc:` in the same block (lines 983 and 992) — Python
matches `except` clauses in source order, so a policy decline can never
fall through to the generic handler and be misclassified as
`ENGINE_HALT`. Verified by direct inspection of every `except Exception`
in the file (25 occurrences) — none of them precede the specific
`EntryPolicyDeclinedError` clause, and no other `except Exception` in the
file sits between `place_order` and its dedicated handler.

## 5. Production fix applied during this verification pass

One genuine defect against the frozen spec was found and fixed here, not
during P02-G: `_step_managing`'s unprotected branch adopted any nonzero
broker-reported quantity unconditionally, with no check against the
locally recorded `filled_qty`. An unexplained change on a position with
no stop order and no exit in flight — nothing that could legitimately
alter its quantity — would have been silently absorbed instead of halted,
in direct tension with spec §5's "unexplained equity change is an
integrity event" principle, generalized to the ongoing poll loop rather
than only the daily accounting checkpoint. Fixed to require
`broker_qty == ctx.filled_qty` or halt. Covered by
`TestUnexplainedQuantityChange` (P02-G).

## 6. State-file collision

The P02 candidate has **zero `.json` path literals anywhere in its
production files** — every test in the P02 family uses `InMemoryStore`;
no `open(`, `pathlib`, or `os` file-I/O call exists in any P02 production
module (`broker_exposure_scope_p02_multipos.py`,
`institutional_engine_v34_p02_multipos_candidate.py`,
`v34_p02_accounting.py`, `v34_p02_authorizer.py`,
`v34_p02_broker_adapter.py`, `v34_p02_state.py`). This candidate cannot
touch a real file at all today — collision is not just avoided, it is
structurally impossible in the current build.

The two state-file names reserved in the V17 rewrite plan for future
wiring — `bot_state_v34_p02_multipos.json` and
`bot_state_v34_p02_multipos_entry_control.json` — were checked against
every `.json` literal actually used across the production/shadow file set
(`bot_state_v34.json`, `bot_state_v34_p03_entry_control.json`,
`entry_gate_dry_run_*.json`, `orb_shadow_*.json`,
`shadow_entry_exit_state.json`, `shadow_*_telemetry.json`): no collision.

## 7. No credential leakage, no real broker reachability

- No `kiteconnect`, `requests`, `socket`, or `urllib` import anywhere in
  the P02 family. The one string `"kiteconnect.exceptions"` in the engine
  (`_is_transient_observation_exception`) is a **module-name string
  comparison** against an already-caught exception's `__module__` — the
  same decoupled classification pattern the original production engine
  uses, explicitly to avoid importing the SDK. No `import kiteconnect`
  exists.
- No `api_key`, `api_secret`, `access_token`, `request_token`, or
  `password=` pattern anywhere in the P02 family.
- Combined with §6 (no file I/O at all), there is no mechanism by which
  running the P02 test suite — or constructing this candidate at all in
  its current form — could reach a real broker, network, or credential
  store.

## 8. `LIVE_TRADING_ENABLED`

- Zero assignments (`LIVE_TRADING_ENABLED\s*=`) anywhere in the P02
  family — the flag is not defined in this candidate at all, because
  there is currently no runner or live entry point that would need it (no
  live signal source calls `request_entry`/`request_exit` — that bridge
  is explicitly future, separately-gated work).
- The three prose mentions that do appear (in module docstrings) all
  state the flag's *absence* here and point to the production runner as
  its home — verified these are comments, not code.
- Production runner's actual flag confirmed unchanged: `LIVE_TRADING_ENABLED = False` at
  `run_production_p01d_candidate.py:50`.

## 9. A design note surfaced during this pass, not a defect

`SL_TAGS = {"V3.3_SL", "V3.4_SL", "V3.4_P02_SL"}` in the engine
deliberately includes the *old* engine's protective-stop tags alongside
the new one, so a discretionary stop placed under either tagging
convention can be adopted as this engine's own. This is a reviewed,
intentional broadening (adopt any operator-placed stop regardless of
which convention placed it) — flagged here explicitly so it's a recorded
decision, not a latent cross-engine assumption discovered later. Entry/
exit tags themselves (`V3.4_P02_ENTRY`/`V3.4_P02_EXIT`) are distinct from
the production engine's own (`V3.4_ENTRY`/`V3.4_EXIT`), so the two
engines' own *orders* could never be confused with each other even if
both existed against the same account.

## 10. Known-risk register

Software-safety verification is not a claim that the strategy cannot
lose money. What this candidate does and does not protect against,
stated plainly so it cannot be mistaken for something it isn't six months
from now:

| Risk | Status |
|---|---|
| Overnight gap losses on carried CNC positions | **Accepted strategy risk** — no halt reacts to it; matches how V11 was validated (spec §5) |
| No mandatory per-position stop-loss | **Intentional** — V11/V12 semantics; capital protection is portfolio-level only (spec §2/§5) |
| Partial rebalance-leg failure (future bridge) | **Not yet solved** — explicitly out of scope (spec §8/§11) |
| Live strategy-to-engine bridge | **Not built** — `request_entry`/`request_exit` have no live caller anywhere in this project |
| Real-money operational validation | **Not done** — every test in this project runs against a mocked broker; nothing here has touched a live account |
| Broker outage / API failure during a market event | **Fail-closed** (halts rather than guesses), but this does not eliminate market risk on already-held positions during the outage |
| Correlation/factor concentration beyond sector-match | **Not modeled** — sector-match (spec §8) is a proxy, not a true correlation limit |
| Multi-process/multi-threaded concurrent authorization | **Not built or needed yet** — current design is single-process sequential (spec §9); would require added file-locking if that ever changes |

## Verdict

Clean. No defect found in this pass beyond the one fixed and documented
in §5. Recommend proceeding to P02-I (freeze).

## Release boundary

`LIVE_TRADING_ENABLED` remains `False`, unconditionally, everywhere it
exists (the production runner only — see §8). No broker calls capable of
a real order were made producing this report; the P02 candidate remains
structurally incapable of making one at all (§6/§7). This report
verifies internal consistency against the frozen P02-0 spec — it does
not authorize live trading, the future rebalance bridge, or any change
to `LIVE_TRADING_ENABLED`.
