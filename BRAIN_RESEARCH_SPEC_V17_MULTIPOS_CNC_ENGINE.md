# V17 / P02-I — Multi-Position CNC Engine: Freeze Certificate

**Status: FROZEN.** This document is the administrative and cryptographic
freeze record for the P02 multi-position CNC execution-engine candidate.
It changes no trading behavior. It certifies that the candidate, as of
the hashes recorded below, matches the invariants frozen in
`V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` and passed the release-verification
sweep recorded in `V34_P02_H_RELEASE_VERIFICATION_REPORT.md`.

> **P02-I certifies the execution-engine candidate against its frozen
> software invariants. It does not certify future profitability,
> eliminate market risk, or certify the not-yet-built
> portfolio-rebalance bridge.**

## Why this project exists

The production engine (`institutional_engine_v34_p01d_candidate.py` /
`run_production_p01d_candidate.py`) was built for the original V9
strategy shape: one position, MIS (intraday, same-day), daily entry
semantics. The only strategy actually validated in this session (V11,
12-1 cross-sectional momentum, 17-fold anchored walk-forward, real costs)
holds up to 4-6 concurrent positions for multiple weeks, rebalanced
monthly — a structurally different shape the production engine cannot
run. This project built and froze a separate, isolated candidate engine
capable of that shape, without ever touching the production engine or
enabling live trading at any point.

## What is frozen

1. **The portfolio/risk constitution** — `V34_P02_PORTFOLIO_INVARIANTS_SPEC.md`
   (frozen at Revision 2 + the valuation-cadence pin, per its own status
   header). Every accounting formula, control-class definition, and gate
   ordering the candidate implements traces back to this document. No
   further architectural changes to it except in response to an actual
   contradiction found during future work — and any such change would
   itself define a new, separately-versioned candidate, not an edit to P02.
2. **The complete P02 source and test inventory** — table below, one
   SHA-256 hash per file, computed at freeze time.
3. **The verification result** — full repository suite **577/577**
   passed; P02 family **196/196** passed; **0 skipped, 0 xfail** anywhere
   in the P02 family. Full detail in `V34_P02_H_RELEASE_VERIFICATION_REPORT.md`.

## Frozen file inventory (SHA-256)

**Spec**

| Hash | File |
|---|---|
| `5a07cb621bc90ddca2c0b1e47c7bd73b76986c13df114bb76221b05de19afa99` | `V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` |

**Production modules**

| Hash | File |
|---|---|
| `f30551d223dc250d429530137b2c5cc9136fc9614f8347e0399b2a06c6131217` | `v34_p02_state.py` |
| `d23b8b0d72929cbe5368ba4c141246f4c29a38a8a3f12962eec5f71397e509b6` | `broker_exposure_scope_p02_multipos.py` |
| `cedb510b69776306c3b1bd109875e0a8a3ea04d2fbeb1300f14fb6b96fb82280` | `institutional_engine_v34_p02_multipos_candidate.py` |
| `a947e07dd46e89aa9f880115fbaef2b59cd85d7af9fc4a567c52783ccd78974f` | `v34_p02_accounting.py` |
| `b16501dd8ffd70331dfd89470394a7aa145a2b1e90203c174bd3622efcd4cc54` | `v34_p02_authorizer.py` |
| `6ba5de54fb10db275182593d6805cec20760f67c241cc7a52eeba7eab47bcaad` | `v34_p02_broker_adapter.py` |

**Tests**

| Hash | File |
|---|---|
| `1c9accbc7611e4d82161ecca8a0b8556f3273caa5b7135323663ad9e659d7d1b` | `test_v34_p02_state.py` |
| `2bee62ee56b85cd905033a8f44a1305c28ef064c94489e95af4be1517fcb2e34` | `test_v34_broker_exposure_scope_p02_multipos.py` |
| `688a25ae6f1c547c4711c0db33ebb51e24c732df041ca528617558b713101fe3` | `test_v34_p02_multipos_engine.py` |
| `ba86195e85a1cca9478e702258e7a6ddf913d67c72ad2c93c66e7b0cf0945c4b` | `test_v34_p02_accounting.py` |
| `98b25f77cec12b4b5612e76b7aa58e6f51585957bff792c2b392c8ef15d3d5aa` | `test_v34_p02_authorizer.py` |
| `124815c48174ff4cc8b5a7cca24054efb23c1a378f0cd245eed85b802d0fca95` | `test_v34_p02_lifecycle_integration.py` |
| `c05b293ee373277dca725d35eac1c02f94816452555c2d1c488f5cc7c2473b14` | `test_v34_p02_adversarial_matrix_multipos.py` |

**Verification record**

| Hash | File |
|---|---|
| `60ee65634f921790d40e5eaf60bc9e3104c9af12ed7ac11ee30906690e7571c0` | `V34_P02_H_RELEASE_VERIFICATION_REPORT.md` |

Recomputable with:
```
python -c "import hashlib; print(hashlib.sha256(open(PATH,'rb').read()).hexdigest())"
```

## Verification summary (see the full report for detail)

- Full repository: **577/577** passed.
- P02 family: **196/196** passed, **0** skipped/xfail, across P02-B
  through P02-G's test files.
- **Isolation from P01D**: zero import statements anywhere in the P02
  family referencing `institutional_engine_v34_p01d_candidate.py` or
  `run_production_p01d_candidate.py`; production files confirmed
  unmodified.
- **No raw-order bypass**: exactly one code path reaches a real
  `place_order` call for a BUY, and it is unconditionally preceded by the
  full authorization gate stack (snapshot → authorize → durable
  fingerprint mark → only then the call).
- **No `ENTRY_LOCK`/`ENGINE_HALT` exception-class corruption**: the
  specific `except EntryPolicyDeclinedError` clause precedes every
  generic `except Exception` it could otherwise fall through to.
- **No credential or network reachability**: no SDK import, no network
  import, no credential-shaped strings, and no filesystem I/O anywhere in
  the P02 production modules — this candidate is structurally incapable
  of reaching a real broker in its current form, not merely gated by a
  flag.
- **No state-file collision**: the candidate performs zero real file I/O
  today; its two reserved future state-file names don't collide with any
  existing production/shadow `.json` file in the repository.
- **`LIVE_TRADING_ENABLED`**: not defined anywhere in the P02 family (no
  live entry point exists yet to need it); the production runner's flag
  is confirmed unchanged at `False`.
- **One production fix made during verification, disclosed rather than
  hidden**: `_step_managing`'s unprotected branch previously adopted any
  nonzero broker-reported quantity unconditionally; fixed to require it
  match the locally recorded quantity or halt. Recorded in full in the
  P02-H report.

## Recorded design decision: `SL_TAGS` compatibility

`SL_TAGS = {"V3.3_SL", "V3.4_SL", "V3.4_P02_SL"}` deliberately includes
the production engine's own protective-stop tags alongside the new one,
so a discretionary stop placed under either tagging convention can be
adopted by this candidate as its own. This is a reviewed, intentional
broadening, recorded here so it is never mistaken for an unnoticed
cross-engine assumption. It does not create any risk of the two engines'
own *orders* being confused with each other: entry/exit tags themselves
(`V3.4_P02_ENTRY`/`V3.4_P02_EXIT`) are distinct from the production
engine's (`V3.4_ENTRY`/`V3.4_EXIT`).

## Known-risk register

Carried forward from the P02-H report, unchanged — a software-safety
freeze is not a claim that the strategy cannot lose money:

| Risk | Status |
|---|---|
| Overnight gap losses on carried CNC positions | Accepted strategy risk — no halt reacts to it; matches how V11 was validated |
| No mandatory per-position stop-loss | Intentional — V11/V12 semantics; capital protection is portfolio-level only |
| Partial rebalance-leg failure (future bridge) | Not yet solved — explicitly out of scope |
| Live strategy-to-engine bridge | Not built — `request_entry`/`request_exit` have no live caller anywhere in this project |
| Real-money operational validation | Not done — every test runs against a mocked broker |
| Broker outage/API failure during a market event | Fail-closed (halts rather than guesses), but does not eliminate market risk on already-held positions during the outage |
| Correlation/factor concentration beyond sector-match | Not modeled — sector-match is a proxy, not a true correlation limit |
| Multi-process/multi-threaded concurrent authorization | Not built or needed yet — current design is single-process sequential; would need file-locking if that ever changes |

## What this freeze explicitly does NOT authorize

- **Live trading of any kind.** `LIVE_TRADING_ENABLED` is not defined in
  this candidate and there is no live entry point to it. This freeze does
  not create one.
- **Building or activating the V11 signal bridge** (turning the validated
  monthly-rebalance momentum ranking into live calls to `request_entry`/
  `request_exit`). That is a materially different class of problem —
  translating "these are the 4-6 stocks to own this month" into safe
  exits, holds, and entries without partially corrupting the intended
  portfolio — and requires its own design gate before any code is
  written, exactly as this engine did.
- Any claim about the underlying V11/V16 trading edge. This freeze
  concerns the execution engine only.

## Modification policy

**A frozen P02 file is not edited.** Any future change to
`V34_P02_PORTFOLIO_INVARIANTS_SPEC.md` or any file listed in the hash
table above — for any reason, including a bug fix — creates a **new,
separately-versioned candidate** (e.g. a P03 line), not a silent update
to this one. The new candidate requires its own regression run against
the full corpus and its own release-verification pass before it can be
considered for its own freeze. This freeze certificate, and the hashes in
it, describe exactly one immutable point in time.

## Owner-level verdict

At the start of this project the multi-position proposal had genuine
conceptual holes: daily P&L semantics, drawdown semantics, CNC capital
accounting, reservation races, integrity-vs-policy halt semantics, and
multi-position reconciliation. These were resolved before and during
implementation (P02-0's two review rounds), and implementation then
surfaced further real defects under test: startup quantity
re-verification (P02-C), UNKNOWN-state progress reporting (P02-F), and
unexplained quantity shrink during ongoing `MANAGING` (P02-G/H). Each was
found, fixed, and disclosed as it was found.

**P02 ENGINE: RELEASE-CANDIDATE QUALITY — FROZEN.**
**LIVE SYSTEM: NOT AUTHORIZED.**

This branch of work is closed as of this freeze. The next serious
architectural discussion is the V11 → target-portfolio → rebalance
bridge — a new, separate project with its own design gate — not a
further modification to P02.

## Release boundary

`LIVE_TRADING_ENABLED` remains `False`, unconditionally, in every file
where it exists (the production runner only). No broker calls capable of
a real order were made producing this document or anything in the
project it certifies. This freeze does not authorize live trading, the
V11 rebalance bridge, or any change to `LIVE_TRADING_ENABLED`.
