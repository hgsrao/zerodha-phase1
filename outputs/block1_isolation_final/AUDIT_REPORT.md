# SECTION 1: SUBPROCESS EXECUTION & STATE VERIFICATION

| Arm ID | Configuration Description | OS Process PID | Start Time (ISO-8601 UTC) | End Time (ISO-8601 UTC) | Elapsed Runtime (s) | Exit Code |
|:---|:---|:---|:---|:---|:---|:---|
| Arm 00 | Control (d1b71eb baseline blob) | 52732 (worker reports 52732) | 2026-10-05T05:38:39.074328+00:00 | 2026-10-05T05:39:29.414258+00:00 | 50.34 (in-process 21.5) | 0 |
| Arm 10 | Hold-only (PR #7 logic) | 52948 (worker reports 52948) | 2026-10-05T05:39:29.414604+00:00 | 2026-10-05T05:40:18.567703+00:00 | 49.153 (in-process 21.1) | 0 |
| Arm 01 | PA-only (PA symmetric_direction patch; ID RR NOT patched) | 53079 (worker reports 53079) | 2026-10-05T05:40:18.568204+00:00 | 2026-10-05T05:41:07.394454+00:00 | 48.826 (in-process 21.3) | 0 |
| Arm 11 | Combined (Hold fix + PA patch) | 53176 (worker reports 53176) | 2026-10-05T05:41:07.395486+00:00 | 2026-10-05T05:41:55.802215+00:00 | 48.407 (in-process 21.1) | 0 |

PIDs distinct (incl. null process): True · all exit codes 0: True · runs strictly sequential: True

## Clean-state initialization receipt (verbatim lines from each worker's console log, evaluated at t = 0, before the first bar)

### Arm 00  (/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm00_rep1.log)
```
ZERO_STATE_RECEIPT arm=00 pid=52732 t=0 (before first bar) starting_equity=1000000.0 mtm_peak=1000000.0 checks=30 all_zero=True
ZERO_STATE_RECEIPT arm=00 GTG1_HEAVY_INDUSTRY.integral_error = 0.0
ZERO_STATE_RECEIPT arm=00 GTG1_HEAVY_INDUSTRY.last_error = 0.0
ZERO_STATE_RECEIPT arm=00 GTG1_HEAVY_INDUSTRY.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=00 GTG1_HEAVY_INDUSTRY.inner_states = 0
ZERO_STATE_RECEIPT arm=00 GTG1_HEAVY_INDUSTRY.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=00 GTG2_TECH_TELECOM.integral_error = 0.0
ZERO_STATE_RECEIPT arm=00 GTG2_TECH_TELECOM.last_error = 0.0
ZERO_STATE_RECEIPT arm=00 GTG2_TECH_TELECOM.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=00 GTG2_TECH_TELECOM.inner_states = 0
ZERO_STATE_RECEIPT arm=00 GTG2_TECH_TELECOM.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=00 CSTG1_BFSI.integral_error = 0.0
ZERO_STATE_RECEIPT arm=00 CSTG1_BFSI.last_error = 0.0
ZERO_STATE_RECEIPT arm=00 CSTG1_BFSI.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=00 CSTG1_BFSI.inner_states = 0
ZERO_STATE_RECEIPT arm=00 CSTG1_BFSI.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=00 CSTG2_CONSUMER_AUTO.integral_error = 0.0
ZERO_STATE_RECEIPT arm=00 CSTG2_CONSUMER_AUTO.last_error = 0.0
ZERO_STATE_RECEIPT arm=00 CSTG2_CONSUMER_AUTO.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=00 CSTG2_CONSUMER_AUTO.inner_states = 0
ZERO_STATE_RECEIPT arm=00 CSTG2_CONSUMER_AUTO.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=00 BPSTG_HEALTHCARE.integral_error = 0.0
ZERO_STATE_RECEIPT arm=00 BPSTG_HEALTHCARE.last_error = 0.0
ZERO_STATE_RECEIPT arm=00 BPSTG_HEALTHCARE.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=00 BPSTG_HEALTHCARE.inner_states = 0
ZERO_STATE_RECEIPT arm=00 BPSTG_HEALTHCARE.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=00 orch.open_trades = 0
ZERO_STATE_RECEIPT arm=00 orch.equity_curve_deviation_from_seed = 0.0
ZERO_STATE_RECEIPT arm=00 orch.mtm_max_drawdown_fraction = 0.0
ZERO_STATE_RECEIPT arm=00 orch.symbol_consecutive_losses = 0
ZERO_STATE_RECEIPT arm=00 orch.mtm_peak_minus_start_equity = 0.0
```
### Arm 10  (/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm10_rep1.log)
```
ZERO_STATE_RECEIPT arm=10 pid=52948 t=0 (before first bar) starting_equity=1000000.0 mtm_peak=1000000.0 checks=30 all_zero=True
ZERO_STATE_RECEIPT arm=10 GTG1_HEAVY_INDUSTRY.integral_error = 0.0
ZERO_STATE_RECEIPT arm=10 GTG1_HEAVY_INDUSTRY.last_error = 0.0
ZERO_STATE_RECEIPT arm=10 GTG1_HEAVY_INDUSTRY.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=10 GTG1_HEAVY_INDUSTRY.inner_states = 0
ZERO_STATE_RECEIPT arm=10 GTG1_HEAVY_INDUSTRY.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=10 GTG2_TECH_TELECOM.integral_error = 0.0
ZERO_STATE_RECEIPT arm=10 GTG2_TECH_TELECOM.last_error = 0.0
ZERO_STATE_RECEIPT arm=10 GTG2_TECH_TELECOM.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=10 GTG2_TECH_TELECOM.inner_states = 0
ZERO_STATE_RECEIPT arm=10 GTG2_TECH_TELECOM.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=10 CSTG1_BFSI.integral_error = 0.0
ZERO_STATE_RECEIPT arm=10 CSTG1_BFSI.last_error = 0.0
ZERO_STATE_RECEIPT arm=10 CSTG1_BFSI.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=10 CSTG1_BFSI.inner_states = 0
ZERO_STATE_RECEIPT arm=10 CSTG1_BFSI.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=10 CSTG2_CONSUMER_AUTO.integral_error = 0.0
ZERO_STATE_RECEIPT arm=10 CSTG2_CONSUMER_AUTO.last_error = 0.0
ZERO_STATE_RECEIPT arm=10 CSTG2_CONSUMER_AUTO.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=10 CSTG2_CONSUMER_AUTO.inner_states = 0
ZERO_STATE_RECEIPT arm=10 CSTG2_CONSUMER_AUTO.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=10 BPSTG_HEALTHCARE.integral_error = 0.0
ZERO_STATE_RECEIPT arm=10 BPSTG_HEALTHCARE.last_error = 0.0
ZERO_STATE_RECEIPT arm=10 BPSTG_HEALTHCARE.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=10 BPSTG_HEALTHCARE.inner_states = 0
ZERO_STATE_RECEIPT arm=10 BPSTG_HEALTHCARE.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=10 orch.open_trades = 0
ZERO_STATE_RECEIPT arm=10 orch.equity_curve_deviation_from_seed = 0.0
ZERO_STATE_RECEIPT arm=10 orch.mtm_max_drawdown_fraction = 0.0
ZERO_STATE_RECEIPT arm=10 orch.symbol_consecutive_losses = 0
ZERO_STATE_RECEIPT arm=10 orch.mtm_peak_minus_start_equity = 0.0
```
### Arm 01  (/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm01_rep1.log)
```
ZERO_STATE_RECEIPT arm=01 pid=53079 t=0 (before first bar) starting_equity=1000000.0 mtm_peak=1000000.0 checks=30 all_zero=True
ZERO_STATE_RECEIPT arm=01 GTG1_HEAVY_INDUSTRY.integral_error = 0.0
ZERO_STATE_RECEIPT arm=01 GTG1_HEAVY_INDUSTRY.last_error = 0.0
ZERO_STATE_RECEIPT arm=01 GTG1_HEAVY_INDUSTRY.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=01 GTG1_HEAVY_INDUSTRY.inner_states = 0
ZERO_STATE_RECEIPT arm=01 GTG1_HEAVY_INDUSTRY.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=01 GTG2_TECH_TELECOM.integral_error = 0.0
ZERO_STATE_RECEIPT arm=01 GTG2_TECH_TELECOM.last_error = 0.0
ZERO_STATE_RECEIPT arm=01 GTG2_TECH_TELECOM.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=01 GTG2_TECH_TELECOM.inner_states = 0
ZERO_STATE_RECEIPT arm=01 GTG2_TECH_TELECOM.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=01 CSTG1_BFSI.integral_error = 0.0
ZERO_STATE_RECEIPT arm=01 CSTG1_BFSI.last_error = 0.0
ZERO_STATE_RECEIPT arm=01 CSTG1_BFSI.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=01 CSTG1_BFSI.inner_states = 0
ZERO_STATE_RECEIPT arm=01 CSTG1_BFSI.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=01 CSTG2_CONSUMER_AUTO.integral_error = 0.0
ZERO_STATE_RECEIPT arm=01 CSTG2_CONSUMER_AUTO.last_error = 0.0
ZERO_STATE_RECEIPT arm=01 CSTG2_CONSUMER_AUTO.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=01 CSTG2_CONSUMER_AUTO.inner_states = 0
ZERO_STATE_RECEIPT arm=01 CSTG2_CONSUMER_AUTO.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=01 BPSTG_HEALTHCARE.integral_error = 0.0
ZERO_STATE_RECEIPT arm=01 BPSTG_HEALTHCARE.last_error = 0.0
ZERO_STATE_RECEIPT arm=01 BPSTG_HEALTHCARE.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=01 BPSTG_HEALTHCARE.inner_states = 0
ZERO_STATE_RECEIPT arm=01 BPSTG_HEALTHCARE.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=01 orch.open_trades = 0
ZERO_STATE_RECEIPT arm=01 orch.equity_curve_deviation_from_seed = 0.0
ZERO_STATE_RECEIPT arm=01 orch.mtm_max_drawdown_fraction = 0.0
ZERO_STATE_RECEIPT arm=01 orch.symbol_consecutive_losses = 0
ZERO_STATE_RECEIPT arm=01 orch.mtm_peak_minus_start_equity = 0.0
```
### Arm 11  (/home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm11_rep1.log)
```
ZERO_STATE_RECEIPT arm=11 pid=53176 t=0 (before first bar) starting_equity=1000000.0 mtm_peak=1000000.0 checks=30 all_zero=True
ZERO_STATE_RECEIPT arm=11 GTG1_HEAVY_INDUSTRY.integral_error = 0.0
ZERO_STATE_RECEIPT arm=11 GTG1_HEAVY_INDUSTRY.last_error = 0.0
ZERO_STATE_RECEIPT arm=11 GTG1_HEAVY_INDUSTRY.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=11 GTG1_HEAVY_INDUSTRY.inner_states = 0
ZERO_STATE_RECEIPT arm=11 GTG1_HEAVY_INDUSTRY.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=11 GTG2_TECH_TELECOM.integral_error = 0.0
ZERO_STATE_RECEIPT arm=11 GTG2_TECH_TELECOM.last_error = 0.0
ZERO_STATE_RECEIPT arm=11 GTG2_TECH_TELECOM.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=11 GTG2_TECH_TELECOM.inner_states = 0
ZERO_STATE_RECEIPT arm=11 GTG2_TECH_TELECOM.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=11 CSTG1_BFSI.integral_error = 0.0
ZERO_STATE_RECEIPT arm=11 CSTG1_BFSI.last_error = 0.0
ZERO_STATE_RECEIPT arm=11 CSTG1_BFSI.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=11 CSTG1_BFSI.inner_states = 0
ZERO_STATE_RECEIPT arm=11 CSTG1_BFSI.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=11 CSTG2_CONSUMER_AUTO.integral_error = 0.0
ZERO_STATE_RECEIPT arm=11 CSTG2_CONSUMER_AUTO.last_error = 0.0
ZERO_STATE_RECEIPT arm=11 CSTG2_CONSUMER_AUTO.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=11 CSTG2_CONSUMER_AUTO.inner_states = 0
ZERO_STATE_RECEIPT arm=11 CSTG2_CONSUMER_AUTO.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=11 BPSTG_HEALTHCARE.integral_error = 0.0
ZERO_STATE_RECEIPT arm=11 BPSTG_HEALTHCARE.last_error = 0.0
ZERO_STATE_RECEIPT arm=11 BPSTG_HEALTHCARE.last_control_u = 0.0
ZERO_STATE_RECEIPT arm=11 BPSTG_HEALTHCARE.inner_states = 0
ZERO_STATE_RECEIPT arm=11 BPSTG_HEALTHCARE.consecutive_stops = 0
ZERO_STATE_RECEIPT arm=11 orch.open_trades = 0
ZERO_STATE_RECEIPT arm=11 orch.equity_curve_deviation_from_seed = 0.0
ZERO_STATE_RECEIPT arm=11 orch.mtm_max_drawdown_fraction = 0.0
ZERO_STATE_RECEIPT arm=11 orch.symbol_consecutive_losses = 0
ZERO_STATE_RECEIPT arm=11 orch.mtm_peak_minus_start_equity = 0.0
```

Mapping of the five requested accumulators to checked fields: PID integral = `*.integral_error`; last error = `*.last_error`; control output u(t) = `*.last_control_u`; consecutive stop counter = `*.consecutive_stops`; drawdown high-water mark = `orch.mtm_peak_minus_start_equity` and `orch.mtm_max_drawdown_fraction`.  Also checked: per-position inner states, open trades, per-symbol loss counters, equity-curve seed.  No separate EMA accumulator exists on these objects.

# SECTION 2: CRYPTOGRAPHIC HASHES & PROVENANCE (sha256 recomputed when this report ran)

## Input market data (the engine loads CSV, not parquet)

| Path | sha256 | Manifest sha256 | Match |
|:---|:---|:---|:---|
| /home/srinivas/projects/zerodha-phase1/data/frozen_48_1min_20230703_20260824/NSE_TITAN_minute_2023-07-03_2026-08-24.csv | f9b34afbceee6fc7269255fc4d2363a057d1ec5b8921e75b729b440b14533a1e | f9b34afbceee6fc7269255fc4d2363a057d1ec5b8921e75b729b440b14533a1e | True |
| /home/srinivas/projects/zerodha-phase1/data/frozen_grid_15min/INDIA_VIX_15MIN_20160101_20260825.csv | 9992deda85005fdb9ee2d6684f8a84a3cabd3f591655450207aeeaacbe7c1a8d | 9992deda85005fdb9ee2d6684f8a84a3cabd3f591655450207aeeaacbe7c1a8d | True |
| /home/srinivas/projects/zerodha-phase1/data/frozen_grid_15min/NSE_NIFTY 50_15minute_2023-08-14_2026-08-13.csv | c2e78705f61b5bad66257890ed1ec0cd3703571e721c16e1b2d2128ee4ea5443 | c2e78705f61b5bad66257890ed1ec0cd3703571e721c16e1b2d2128ee4ea5443 | True |

Stock manifest /home/srinivas/projects/zerodha-r5-governor-refactor/revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json sha256 2dcb04eb74fc2abec77a838b20dfb5ce6d730c10ba359203a7fc045eccffef66; manifest_hash field f724074ca45eb9380ba6e7cf18ae1da4d9c9d48be0cf78ca51b0faa8d8d8ff16.
In-run causal slice hash (from prepare_block) c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50; identical across all 4 arms: True.

## Frozen parameter files (outputs/r5_step5_stage_a_v2_state/params/ of the zerodha-phase1 checkout)

| Path | sha256 | Used by this run |
|:---|:---|:---|
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_000.json | 7bb4a976a31ebee22b8fe8330a7520aba8d1ba4c4f564b618d6701754d59ae3d | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_001.json | 04a51abce06311f187f1dea1eefc57d9702d8ebb316774e5f2eee46d4211f6d5 | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_002.json | 36ac3729d8b512a1c3ea2d9b2925ee5d701d919dd186ec07a2efdd1f1c996a6a | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_003.json | e2a18399addd9cfed0f044cef3dd8c925650790ea44f6a1dd656a5555297d175 | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_004.json | 6669fd6c161ead5d48e1ec1cf25bc2bd17e286e7e097c6dca1eba085d00b63ab | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_005.json | af55b87cf03253a9e27f619b8cfc7aef1f9cea70d5c452b489b35cefdb3d3d12 | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_006.json | 276a12ba8c4c702cb70014f26e5b5ce7617fa84cec2e90562a15c1c230e43f29 | no |
| /home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_007.json | d563872531a950e721e86058493e489511585fc016a5c1209c486912885a6359 | YES |

Protocol: /home/srinivas/projects/zerodha-r5-governor-refactor/revision5/step5_sealed_calibration_protocol_v2.json sha256 6861cdd27d7666fc6a1bf94c21e90c330052e8137973f7f07eda538566eb1ca7

## Code blobs

| Item | Path | sha256 |
|:---|:---|:---|
| Baseline governor module as loaded (arms 00/01) | /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/generated_sources/governor_authority_d1b71eb.py | 1bb10a81496069442ee13b711effa6d6e6df4cd703e358632c037160b42a4359 |
| git blob d1b71eb0:revision5/governor_authority.py | (git object) | 1bb10a81496069442ee13b711effa6d6e6df4cd703e358632c037160b42a4359 |
| PA-symmetry patched module as loaded (arms 01/11) | /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/generated_sources/indicators_talib_pa_symmetric.py | 9dfa5a6066968e6593dc3c88255e8f6d7da2b276c1b541bd1deb35203020026b |
| PA patch file | /home/srinivas/projects/zerodha-r5-governor-refactor/scripts/diagnostics/data/pa_symmetry_indicators_talib.patch | bbae6224edcc5d04a59d2ba5266e7718eda91b23a4595644a14274590f6d8e1b |
| Unpatched revision2_external/indicators_talib.py | /home/srinivas/projects/zerodha-r5-governor-refactor/revision2_external/indicators_talib.py | 70ff1999ddf06a15ef9e650cb7db3539ee8e5379440c8bd183f544433440c549 |
| PR #7 governor_authority.py (arms 10/11) | /home/srinivas/projects/zerodha-r5-governor-refactor/revision5/governor_authority.py | 2946c5c2e86ed45b2070d112fab679a0bd39bc8f20fd7c2dd65a0fa0fe40138c |

Loaded baseline module equals the git blob: True.

## Output files

Requested names `run_manifest.json` / `ledger.csv` / `summary.md` do not exist; the harness writes `process_manifest.json`, `arm<ID>_ledger.csv` and `REPORT.md` / `summary.json`.  Directory: /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final

| Path | sha256 |
|:---|:---|
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/REPORT.md | 613dab18a79be736ae810b9eaf5b67dd0903431357c458625f3ad7f032b6c1dc |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm00_ledger.csv | 69ed691e1967e9639372e1bec342038489b7c0f476df5e64fa734ac5439ae19f |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm00_rep1.json | 0ee8c1f7aed99e799696309536e8e15e1a6577f044ada7b861f62f6833a65adf |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm00_rep1.log | 957708c81dfb5586ef43070cfad38ea8d92484e2903099ce701fa243f5eda587 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm01_ledger.csv | be98ea6f308e1747aed5045b0ca7a5bd4de77c62180082fcc11e118beb45d7c6 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm01_rep1.json | a688c0a72c991a95b2b5a5a6c66a3ea3d55afd6e7bd2d4732452be48249cf125 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm01_rep1.log | 1eb00e470bfd9b7ce4c65c564c6a217d40d95ac5157cd4b0ddcb455b75b6da1d |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm10_ledger.csv | 69ed691e1967e9639372e1bec342038489b7c0f476df5e64fa734ac5439ae19f |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm10_rep1.json | 2a8e061381d1e40e7df7ab15cdb22831bbc380d5744c08487685254197b76f9d |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm10_rep1.log | c319b329b782d4a87b29d8e6a943a0c2db927156d83d47e2dbff629e9adfae91 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm11_ledger.csv | 8eae8561ad9b3aaa0a6b0722825c003f1e2668f383b44db3b02b22dbb0c030da |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm11_rep1.json | 71111f262640fa5f9036fdb58307e091caec46e274b81768f087f0c8680e5fdd |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/arm11_rep1.log | 642ede6d46c2d86b351f1051742695a9ae41af00eb6253e8cfa6c4ff86f4e946 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/generated_sources/governor_authority_d1b71eb.py | 1bb10a81496069442ee13b711effa6d6e6df4cd703e358632c037160b42a4359 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/generated_sources/indicators_talib_pa_symmetric.py | 9dfa5a6066968e6593dc3c88255e8f6d7da2b276c1b541bd1deb35203020026b |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/null.json | 58e3853cadd5fa1527ddf784bc84e7f91d54ca543c167fc55d6bebf770e63bd8 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/null.log | 78cb0e0e8d88bdf321c52f10f8e2b2428b3c4648e30c3216a7c829a382db9d07 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/null_ledger.csv | feef6deaec6f536e35103b2258b6224a2cad6ea7f5abaad447190e240ebdb994 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/process_manifest.json | d0ec06e3b33b733ebc7354dc7965f8b3d6e0ca044d4b652d8773e897c50d8405 |
| /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation_final/summary.json | 39f29053d903daecf45f0ba2813bb3712173c7acd05d7d0e11eb757ed727552b |

## Determinism

| Arm | trade_ledger_sha256 | controller_trace_sha256 |
|:---|:---|:---|
| 00 | 051143f6bd1d5988a0bf97a5b53d23b4a165177e0b84834d5cc728a583a334c1 | 716a3997e313be3ff595789230fe78aa6d9976510b265a32e29701c8ae1b5788 |
| 10 | 051143f6bd1d5988a0bf97a5b53d23b4a165177e0b84834d5cc728a583a334c1 | 716a3997e313be3ff595789230fe78aa6d9976510b265a32e29701c8ae1b5788 |
| 01 | 78de5dd20f2d869b7b2fdf5356e12c5debdfbf6eddd2387d1081e6c1d888eec8 | 839a953969013e8ce2539d6965b8015098635bb53ef2d2c1e00f7a4e2787be30 |
| 11 | 7d0a3d451f15d6e9c0d13862fdd74e2980ca253256fe653b5708af5599fc8698 | f9e3a0bbc8a6ba15f83ca7f6f27cdd003e3414e23ca1a881f9e3b813502388db |

Reproducibility vs earlier run /home/srinivas/projects/zerodha-r5-governor-refactor/outputs/block1_isolation:
- arm 00: ledger hash equal=True, trace hash equal=True
- arm 10: ledger hash equal=True, trace hash equal=True
- arm 01: ledger hash equal=True, trace hash equal=True
- arm 11: ledger hash equal=True, trace hash equal=True

# SECTION 3: COMPLETE TRADE-BY-TRADE AUDIT LEDGER (unrounded, no aggregation)

| Arm | Trade | Symbol | Side | Entry ts | Entry fill (Rs) | Exit ts | Exit fill (Rs) | Qty | Notional (Rs) | Bars held | Clock seconds | Exit predicate | MFE (R) | MAE (R) | Gross P&L fill-to-fill (Rs) |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| 00 | trade-1 | TITAN | SELL | 2024-02-13 09:33:00 | 3559.5838 | 2024-02-13 09:36:00 | 3569.784 | 5 | 17797.918999999998 | 3 | 180 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.01816759585689061 | -0.6067197340266344 | -51.00100000000111 |
| 10 | trade-1 | TITAN | SELL | 2024-02-13 09:33:00 | 3559.5838 | 2024-02-13 09:36:00 | 3569.784 | 5 | 17797.918999999998 | 3 | 180 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.01816759585689061 | -0.6067197340266344 | -51.00100000000111 |
| 01 | trade-1 | TITAN | SELL | 2024-02-13 09:33:00 | 3559.3133 | 2024-02-13 09:36:00 | 3569.784 | 5 | 17796.5665 | 3 | 180 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.011347270542093509 | -0.6695514297542275 | -52.35350000000153 |
| 01 | trade-2 | TITAN | BUY | 2024-02-13 12:50:00 | 3596.8264 | 2024-02-13 12:56:00 | 3592.4529 | 25 | 89920.66 | 6 | 360 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.15568310611044334 | -0.1386509740076046 | -109.33749999999236 |
| 01 | trade-3 | TITAN | BUY | 2024-02-14 10:30:00 | 3542.3688 | 2024-02-14 10:38:00 | 3542.8277 | 26 | 92101.5888 | 8 | 480 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.4258979929982053 | -0.1114705735711967 | 11.93139999999039 |
| 01 | trade-4 | TITAN | BUY | 2024-02-14 12:39:00 | 3544.5557 | 2024-02-14 12:49:00 | 3541.0786 | 23 | 81524.7811 | 10 | 600 | governor_exit:GOVERNOR_PATH_ERROR | 0.021401117848691885 | -0.18090519537318378 | -79.97330000000147 |
| 01 | trade-5 | TITAN | BUY | 2024-02-15 11:43:00 | 3588.3463 | 2024-02-15 11:46:00 | 3580.7087 | 21 | 75355.2723 | 3 | 180 | governor_exit:GOVERNOR_PATH_ERROR | 0.0 | -0.3325895737766183 | -160.38960000000043 |
| 01 | trade-6 | TITAN | BUY | 2024-02-15 14:49:00 | 3608.5427 | 2024-02-15 14:55:00 | 3607.1955 | 19 | 68562.3113 | 6 | 360 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.08327788927987027 | -0.19387696079036534 | -25.596800000002986 |
| 01 | trade-7 | TITAN | BUY | 2024-02-16 11:05:00 | 3671.1372 | 2024-02-16 11:15:00 | 3669.2145 | 18 | 66080.4696 | 10 | 600 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.3709342125284315 | -0.12362160039542806 | -34.60860000000139 |
| 01 | trade-8 | TITAN | SELL | 2024-02-16 12:39:00 | 3658.1467 | 2024-02-16 12:43:00 | 3664.3313 | 17 | 62188.493899999994 | 4 | 240 | governor_exit:GOVERNOR_PATH_ERROR | 0.0 | -0.1864585782013322 | -105.13820000000078 |
| 01 | trade-9 | TITAN | BUY | 2024-02-19 10:26:00 | 3679.8431 | 2024-02-19 10:28:00 | 3674.6618 | 15 | 55197.6465 | 2 | 120 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.0 | -0.2304732279400773 | -77.71950000000288 |
| 01 | trade-10 | TITAN | BUY | 2024-02-19 11:16:00 | 3687.1464 | 2024-02-19 11:26:00 | 3685.5063 | 15 | 55307.196 | 10 | 600 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.330427531753101 | -0.06295861242173574 | -24.601500000001124 |
| 11 | trade-1 | TITAN | SELL | 2024-02-13 09:33:00 | 3559.3133 | 2024-02-13 09:36:00 | 3569.784 | 5 | 17796.5665 | 3 | 180 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.011347270542093509 | -0.6695514297542275 | -52.35350000000153 |
| 11 | trade-2 | TITAN | BUY | 2024-02-13 12:50:00 | 3596.8264 | 2024-02-13 12:56:00 | 3592.4529 | 25 | 89920.66 | 6 | 360 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.15568310611044334 | -0.1386509740076046 | -109.33749999999236 |
| 11 | trade-3 | TITAN | BUY | 2024-02-14 10:30:00 | 3542.3688 | 2024-02-14 10:38:00 | 3542.8277 | 26 | 92101.5888 | 8 | 480 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.4258979929982053 | -0.1114705735711967 | 11.93139999999039 |
| 11 | trade-4 | TITAN | BUY | 2024-02-14 12:39:00 | 3544.5557 | 2024-02-14 12:49:00 | 3541.0786 | 23 | 81524.7811 | 10 | 600 | governor_exit:GOVERNOR_PATH_ERROR | 0.021401117848691885 | -0.18090519537318378 | -79.97330000000147 |
| 11 | trade-5 | TITAN | BUY | 2024-02-15 11:43:00 | 3588.3463 | 2024-02-15 11:46:00 | 3580.7087 | 21 | 75355.2723 | 3 | 180 | governor_exit:GOVERNOR_PATH_ERROR | 0.0 | -0.3325895737766183 | -160.38960000000043 |
| 11 | trade-6 | TITAN | BUY | 2024-02-15 14:49:00 | 3608.5427 | 2024-02-15 14:55:00 | 3607.1955 | 19 | 68562.3113 | 6 | 360 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.08327788927987027 | -0.19387696079036534 | -25.596800000002986 |
| 11 | trade-7 | TITAN | BUY | 2024-02-16 11:05:00 | 3671.1372 | 2024-02-16 11:15:00 | 3669.2145 | 18 | 66080.4696 | 10 | 600 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.3709342125284315 | -0.12362160039542806 | -34.60860000000139 |
| 11 | trade-8 | TITAN | SELL | 2024-02-16 12:39:00 | 3658.1467 | 2024-02-16 12:43:00 | 3664.3313 | 17 | 62188.493899999994 | 4 | 240 | governor_exit:GOVERNOR_PATH_ERROR | 0.0 | -0.1864585782013322 | -105.13820000000078 |
| 11 | trade-9 | TITAN | BUY | 2024-02-19 10:26:00 | 3679.8431 | 2024-02-19 10:29:00 | 3675.5113 | 15 | 55197.6465 | 3 | 180 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.0 | -0.2304732279400773 | -64.97699999999895 |
| 11 | trade-10 | TITAN | BUY | 2024-02-19 11:16:00 | 3687.1464 | 2024-02-19 11:26:00 | 3685.5063 | 15 | 55307.196 | 10 | 600 | governor_exit:FSR_BELOW_EXIT:FSRN | 0.330427531753101 | -0.06295861242173574 | -24.601500000001124 |

MFE/MAE are the engine's own `mfe_r`/`mae_r` (pre-exit-bar, in units of initial risk); trade-record fields are printed unmodified.

# SECTION 4: EXACT COST RECONCILIATION & BALANCE SHEET

Engine friction model (revision2/transaction_costs.py): brokerage = min(Rs 20, 0.03% turnover); exchange turnover charge = 0.00345% turnover; STT = 0.025% on SELL legs.  **Stamp duty and GST are not modeled by the engine** and are shown as 0.00 (NOT MODELED), not computed.  Slippage = frozen paper-fill adverse fraction, recovered from fills.  Gross here is frictionless (fill-to-fill P&L + slippage) because fill prices already contain slippage.  Net Realized is the engine's own `net_pnl` (independent of this harness's arithmetic), so Drift is a real check.

| Arm | Symbol | Gross P&L (Rs) | Brokerage (Rs) | STT (Rs) | Exch Turnover (Rs) | Stamp + GST (Rs) | Slippage (Rs) | Total Friction (Rs) | Total Friction (bps) | Net Realized P&L (Rs) | Drift (Gross - Friction - Net) | Drift unrounded |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| 00 | TITAN trade-1 | -33.18 | 10.69 | 4.45 | 1.23 | 0.00 (not modeled) | 17.82 | 34.20 | 19.21 | -67.37 | 0.00 | 0.0 |
| 10 | TITAN trade-1 | -33.18 | 10.69 | 4.45 | 1.23 | 0.00 (not modeled) | 17.82 | 34.20 | 19.21 | -67.37 | 0.00 | 0.0 |
| 01 | TITAN trade-1 | -34.53 | 10.69 | 4.45 | 1.23 | 0.00 (not modeled) | 17.82 | 34.20 | 19.21 | -68.73 | 0.00 | 0.0 |
| 01 | TITAN trade-2 | -19.47 | 40.00 | 22.45 | 6.20 | 0.00 (not modeled) | 89.87 | 158.52 | 17.63 | -177.99 | 0.00 | 0.0 |
| 01 | TITAN trade-3 | 104.04 | 40.00 | 23.03 | 6.36 | 0.00 (not modeled) | 92.11 | 161.49 | 17.53 | -57.45 | -0.00 | -1.4210854715202004e-14 |
| 01 | TITAN trade-4 | 1.51 | 40.00 | 20.36 | 5.62 | 0.00 (not modeled) | 81.48 | 147.47 | 18.09 | -145.96 | 0.00 | 0.0 |
| 01 | TITAN trade-5 | -85.11 | 40.00 | 18.80 | 5.19 | 0.00 (not modeled) | 75.28 | 139.27 | 18.48 | -224.38 | 0.00 | 0.0 |
| 01 | TITAN trade-6 | 42.95 | 40.00 | 17.13 | 4.73 | 0.00 (not modeled) | 68.55 | 130.41 | 19.02 | -87.46 | 0.00 | 0.0 |
| 01 | TITAN trade-7 | 31.45 | 39.64 | 16.51 | 4.56 | 0.00 (not modeled) | 66.06 | 126.77 | 19.18 | -95.32 | 0.00 | 0.0 |
| 01 | TITAN trade-8 | -42.90 | 37.34 | 15.55 | 4.29 | 0.00 (not modeled) | 62.24 | 119.43 | 19.20 | -162.32 | 0.00 | 0.0 |
| 01 | TITAN trade-9 | -22.56 | 33.10 | 13.78 | 3.81 | 0.00 (not modeled) | 55.16 | 105.84 | 19.17 | -128.40 | 0.00 | 0.0 |
| 01 | TITAN trade-10 | 30.69 | 33.18 | 13.82 | 3.82 | 0.00 (not modeled) | 55.29 | 106.11 | 19.19 | -75.41 | 0.00 | 0.0 |
| 11 | TITAN trade-1 | -34.53 | 10.69 | 4.45 | 1.23 | 0.00 (not modeled) | 17.82 | 34.20 | 19.21 | -68.73 | 0.00 | 0.0 |
| 11 | TITAN trade-2 | -19.47 | 40.00 | 22.45 | 6.20 | 0.00 (not modeled) | 89.87 | 158.52 | 17.63 | -177.99 | 0.00 | 0.0 |
| 11 | TITAN trade-3 | 104.04 | 40.00 | 23.03 | 6.36 | 0.00 (not modeled) | 92.11 | 161.49 | 17.53 | -57.45 | -0.00 | -1.4210854715202004e-14 |
| 11 | TITAN trade-4 | 1.51 | 40.00 | 20.36 | 5.62 | 0.00 (not modeled) | 81.48 | 147.47 | 18.09 | -145.96 | 0.00 | 0.0 |
| 11 | TITAN trade-5 | -85.11 | 40.00 | 18.80 | 5.19 | 0.00 (not modeled) | 75.28 | 139.27 | 18.48 | -224.38 | 0.00 | 0.0 |
| 11 | TITAN trade-6 | 42.95 | 40.00 | 17.13 | 4.73 | 0.00 (not modeled) | 68.55 | 130.41 | 19.02 | -87.46 | 0.00 | 0.0 |
| 11 | TITAN trade-7 | 31.45 | 39.64 | 16.51 | 4.56 | 0.00 (not modeled) | 66.06 | 126.77 | 19.18 | -95.32 | 0.00 | 0.0 |
| 11 | TITAN trade-8 | -42.90 | 37.34 | 15.55 | 4.29 | 0.00 (not modeled) | 62.24 | 119.43 | 19.20 | -162.32 | 0.00 | 0.0 |
| 11 | TITAN trade-9 | -9.81 | 33.10 | 13.78 | 3.81 | 0.00 (not modeled) | 55.17 | 105.85 | 19.18 | -115.67 | -0.00 | -1.4210854715202004e-14 |
| 11 | TITAN trade-10 | 30.69 | 33.18 | 13.82 | 3.82 | 0.00 (not modeled) | 55.29 | 106.11 | 19.19 | -75.41 | 0.00 | 0.0 |

Itemized fees vs the engine's booked `costs`, per trade (Rs, unrounded error):
- arm 00 trade-1: itemized 16.373347395499998 vs engine 16.373347395499998 -> error 0.0
- arm 10 trade-1: itemized 16.373347395499998 vs engine 16.373347395499998 -> error 0.0
- arm 01 trade-1: itemized 16.37255685925 vs engine 16.37255685925 -> error 0.0
- arm 01 trade-2: itemized 68.65358402125 vs engine 68.65358402125 -> error 0.0
- arm 01 trade-3: itemized 69.3838013105 vs engine 69.38380131049999 -> error 1.4210854715202004e-14
- arm 01 trade-4: itemized 65.98365276705 vs engine 65.98365276704999 -> error 1.4210854715202004e-14
- arm 01 trade-5: itemized 63.9927010225 vs engine 63.99270102250001 -> error -7.105427357601002e-15
- arm 01 trade-6: itemized 61.8640950151 vs engine 61.8640950151 -> error 0.0
- arm 01 trade-7: itemized 60.7077228357 vs engine 60.7077228357 -> error 0.0
- arm 01 trade-8: itemized 57.186394621999995 vs engine 57.186394621999995 -> error 0.0
- arm 01 trade-9: itemized 50.681210085749996 vs engine 50.681210085749996 -> error 0.0
- arm 01 trade-10: itemized 50.81293354725 vs engine 50.81293354725 -> error 0.0
- arm 11 trade-1: itemized 16.37255685925 vs engine 16.37255685925 -> error 0.0
- arm 11 trade-2: itemized 68.65358402125 vs engine 68.65358402125 -> error 0.0
- arm 11 trade-3: itemized 69.3838013105 vs engine 69.38380131049999 -> error 1.4210854715202004e-14
- arm 11 trade-4: itemized 65.98365276705 vs engine 65.98365276704999 -> error 1.4210854715202004e-14
- arm 11 trade-5: itemized 63.9927010225 vs engine 63.99270102250001 -> error -7.105427357601002e-15
- arm 11 trade-6: itemized 61.8640950151 vs engine 61.8640950151 -> error 0.0
- arm 11 trade-7: itemized 60.7077228357 vs engine 60.7077228357 -> error 0.0
- arm 11 trade-8: itemized 57.186394621999995 vs engine 57.186394621999995 -> error 0.0
- arm 11 trade-9: itemized 50.68865807700001 vs engine 50.688658077 -> error 7.105427357601002e-15
- arm 11 trade-10: itemized 50.81293354725 vs engine 50.81293354725 -> error 0.0

# SECTION 5: 15-BAR COUNTERFACTUAL TRACKING PROOF

Forward bars = 1-minute bars of the same symbol and same scored session strictly after the exit bar; the post-block sentinel row and any later sessions are never used.  A bar touching both levels counts as stop first.

| Arm | Trade | Side | Planned stop (Rs) | Planned target (Rs) | Fwd bars used | Bars left in session after exit | 15-bar fwd extreme high (Rs) | 15-bar fwd extreme low (Rs) | Class | Justification |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| 00 | trade-1 | SELL | 3594.470089027597 | 3507.2543664586037 | 15 | 353 | 3579.05 | 3562.15 | NOISE_CHURN | neither level touched in 15 bars: max high 3579.05 < stop 3594.470089027597; min low 3562.15 > target 3507.2543664586037 |
| 10 | trade-1 | SELL | 3594.470089027597 | 3507.2543664586037 | 15 | 353 | 3579.05 | 3562.15 | NOISE_CHURN | neither level touched in 15 bars: max high 3579.05 < stop 3594.470089027597; min low 3562.15 > target 3507.2543664586037 |
| 01 | trade-1 | SELL | 3591.329809930938 | 3511.288535103592 | 15 | 353 | 3579.05 | 3562.15 | NOISE_CHURN | neither level touched in 15 bars: max high 3579.05 < stop 3591.329809930938; min low 3562.15 > target 3511.288535103592 |
| 01 | trade-2 | BUY | 3576.4414007583437 | 3627.4038988624843 | 15 | 153 | 3600.0 | 3593.1 | NOISE_CHURN | neither level touched in 15 bars: min low 3593.1 > stop 3576.4414007583437; max high 3600.0 < target 3627.4038988624843 |
| 01 | trade-3 | BUY | 3525.155288500709 | 3568.189067248937 | 15 | 291 | 3548.4 | 3528.0 | NOISE_CHURN | neither level touched in 15 bars: min low 3528.0 > stop 3525.155288500709; max high 3548.4 < target 3568.189067248937 |
| 01 | trade-4 | BUY | 3523.7951022059333 | 3575.6965966911002 | 15 | 160 | 3548.5 | 3540.7 | NOISE_CHURN | neither level touched in 15 bars: min low 3540.7 > stop 3523.7951022059333; max high 3548.5 < target 3575.6965966911002 |
| 01 | trade-5 | BUY | 3569.114488469142 | 3617.1940172962873 | 15 | 223 | 3589.9 | 3579.15 | NOISE_CHURN | neither level touched in 15 bars: min low 3579.15 > stop 3569.114488469142; max high 3589.9 < target 3617.1940172962873 |
| 01 | trade-6 | BUY | 3591.043457252594 | 3634.791564121109 | 15 | 34 | 3632.0 | 3605.0 | NOISE_CHURN | neither level touched in 15 bars: min low 3605.0 > stop 3591.043457252594; max high 3632.0 < target 3634.791564121109 |
| 01 | trade-7 | BUY | 3653.8489591653574 | 3697.0695612519644 | 15 | 254 | 3690.0 | 3671.05 | NOISE_CHURN | neither level touched in 15 bars: min low 3671.05 > stop 3653.8489591653574; max high 3690.0 < target 3697.0695612519644 |
| 01 | trade-8 | SELL | 3684.175537325788 | 3619.1034440113176 | 15 | 166 | 3664.4 | 3647.1 | NOISE_CHURN | neither level touched in 15 bars: max high 3664.4 < stop 3684.175537325788; min low 3647.1 > target 3619.1034440113176 |
| 01 | trade-9 | BUY | 3664.0360579736375 | 3703.553663039544 | 15 | 301 | 3678.2 | 3672.25 | NOISE_CHURN | neither level touched in 15 bars: min low 3672.25 > stop 3664.0360579736375; max high 3678.2 < target 3703.553663039544 |
| 01 | trade-10 | BUY | 3663.3784047656677 | 3722.7983928514986 | 15 | 243 | 3693.85 | 3682.2 | NOISE_CHURN | neither level touched in 15 bars: min low 3682.2 > stop 3663.3784047656677; max high 3693.85 < target 3722.7983928514986 |
| 11 | trade-1 | SELL | 3591.329809930938 | 3511.288535103592 | 15 | 353 | 3579.05 | 3562.15 | NOISE_CHURN | neither level touched in 15 bars: max high 3579.05 < stop 3591.329809930938; min low 3562.15 > target 3511.288535103592 |
| 11 | trade-2 | BUY | 3576.4414007583437 | 3627.4038988624843 | 15 | 153 | 3600.0 | 3593.1 | NOISE_CHURN | neither level touched in 15 bars: min low 3593.1 > stop 3576.4414007583437; max high 3600.0 < target 3627.4038988624843 |
| 11 | trade-3 | BUY | 3525.155288500709 | 3568.189067248937 | 15 | 291 | 3548.4 | 3528.0 | NOISE_CHURN | neither level touched in 15 bars: min low 3528.0 > stop 3525.155288500709; max high 3548.4 < target 3568.189067248937 |
| 11 | trade-4 | BUY | 3523.7951022059333 | 3575.6965966911002 | 15 | 160 | 3548.5 | 3540.7 | NOISE_CHURN | neither level touched in 15 bars: min low 3540.7 > stop 3523.7951022059333; max high 3548.5 < target 3575.6965966911002 |
| 11 | trade-5 | BUY | 3569.114488469142 | 3617.1940172962873 | 15 | 223 | 3589.9 | 3579.15 | NOISE_CHURN | neither level touched in 15 bars: min low 3579.15 > stop 3569.114488469142; max high 3589.9 < target 3617.1940172962873 |
| 11 | trade-6 | BUY | 3591.043457252594 | 3634.791564121109 | 15 | 34 | 3632.0 | 3605.0 | NOISE_CHURN | neither level touched in 15 bars: min low 3605.0 > stop 3591.043457252594; max high 3632.0 < target 3634.791564121109 |
| 11 | trade-7 | BUY | 3653.8489591653574 | 3697.0695612519644 | 15 | 254 | 3690.0 | 3671.05 | NOISE_CHURN | neither level touched in 15 bars: min low 3671.05 > stop 3653.8489591653574; max high 3690.0 < target 3697.0695612519644 |
| 11 | trade-8 | SELL | 3684.175537325788 | 3619.1034440113176 | 15 | 166 | 3664.4 | 3647.1 | NOISE_CHURN | neither level touched in 15 bars: max high 3664.4 < stop 3684.175537325788; min low 3647.1 > target 3619.1034440113176 |
| 11 | trade-9 | BUY | 3664.0360579736375 | 3703.553663039544 | 15 | 300 | 3678.2 | 3672.25 | NOISE_CHURN | neither level touched in 15 bars: min low 3672.25 > stop 3664.0360579736375; max high 3678.2 < target 3703.553663039544 |
| 11 | trade-10 | BUY | 3663.3784047656677 | 3722.7983928514986 | 15 | 243 | 3693.85 | 3682.2 | NOISE_CHURN | neither level touched in 15 bars: min low 3682.2 > stop 3663.3784047656677; max high 3693.85 < target 3722.7983928514986 |

## Bar-by-bar forward trajectories

### arm 00 trade-1 TITAN SELL (exit 2024-02-13 09:36:00 @ 3569.784, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-13 09:37:00+05:30 | 3568.3 | 3569.25 | 3562.15 | 3564.45 |
| 2 | 2024-02-13 09:38:00+05:30 | 3564.45 | 3567.1 | 3564.2 | 3565.8 |
| 3 | 2024-02-13 09:39:00+05:30 | 3565.8 | 3571.5 | 3565.0 | 3570.7 |
| 4 | 2024-02-13 09:40:00+05:30 | 3570.7 | 3571.8 | 3567.25 | 3570.4 |
| 5 | 2024-02-13 09:41:00+05:30 | 3568.6 | 3568.6 | 3565.0 | 3566.55 |
| 6 | 2024-02-13 09:42:00+05:30 | 3566.75 | 3569.9 | 3566.6 | 3569.25 |
| 7 | 2024-02-13 09:43:00+05:30 | 3569.9 | 3569.95 | 3563.5 | 3566.85 |
| 8 | 2024-02-13 09:44:00+05:30 | 3566.6 | 3571.95 | 3566.6 | 3571.6 |
| 9 | 2024-02-13 09:45:00+05:30 | 3571.6 | 3571.6 | 3567.4 | 3570.75 |
| 10 | 2024-02-13 09:46:00+05:30 | 3571.3 | 3579.05 | 3571.1 | 3577.15 |
| 11 | 2024-02-13 09:47:00+05:30 | 3578.45 | 3578.45 | 3573.45 | 3576.65 |
| 12 | 2024-02-13 09:48:00+05:30 | 3576.0 | 3576.9 | 3573.5 | 3573.5 |
| 13 | 2024-02-13 09:49:00+05:30 | 3574.1 | 3575.5 | 3572.25 | 3573.15 |
| 14 | 2024-02-13 09:50:00+05:30 | 3573.15 | 3578.8 | 3572.4 | 3578.45 |
| 15 | 2024-02-13 09:51:00+05:30 | 3578.35 | 3578.4 | 3571.8 | 3573.9 |

### arm 10 trade-1 TITAN SELL (exit 2024-02-13 09:36:00 @ 3569.784, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-13 09:37:00+05:30 | 3568.3 | 3569.25 | 3562.15 | 3564.45 |
| 2 | 2024-02-13 09:38:00+05:30 | 3564.45 | 3567.1 | 3564.2 | 3565.8 |
| 3 | 2024-02-13 09:39:00+05:30 | 3565.8 | 3571.5 | 3565.0 | 3570.7 |
| 4 | 2024-02-13 09:40:00+05:30 | 3570.7 | 3571.8 | 3567.25 | 3570.4 |
| 5 | 2024-02-13 09:41:00+05:30 | 3568.6 | 3568.6 | 3565.0 | 3566.55 |
| 6 | 2024-02-13 09:42:00+05:30 | 3566.75 | 3569.9 | 3566.6 | 3569.25 |
| 7 | 2024-02-13 09:43:00+05:30 | 3569.9 | 3569.95 | 3563.5 | 3566.85 |
| 8 | 2024-02-13 09:44:00+05:30 | 3566.6 | 3571.95 | 3566.6 | 3571.6 |
| 9 | 2024-02-13 09:45:00+05:30 | 3571.6 | 3571.6 | 3567.4 | 3570.75 |
| 10 | 2024-02-13 09:46:00+05:30 | 3571.3 | 3579.05 | 3571.1 | 3577.15 |
| 11 | 2024-02-13 09:47:00+05:30 | 3578.45 | 3578.45 | 3573.45 | 3576.65 |
| 12 | 2024-02-13 09:48:00+05:30 | 3576.0 | 3576.9 | 3573.5 | 3573.5 |
| 13 | 2024-02-13 09:49:00+05:30 | 3574.1 | 3575.5 | 3572.25 | 3573.15 |
| 14 | 2024-02-13 09:50:00+05:30 | 3573.15 | 3578.8 | 3572.4 | 3578.45 |
| 15 | 2024-02-13 09:51:00+05:30 | 3578.35 | 3578.4 | 3571.8 | 3573.9 |

### arm 01 trade-1 TITAN SELL (exit 2024-02-13 09:36:00 @ 3569.784, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-13 09:37:00+05:30 | 3568.3 | 3569.25 | 3562.15 | 3564.45 |
| 2 | 2024-02-13 09:38:00+05:30 | 3564.45 | 3567.1 | 3564.2 | 3565.8 |
| 3 | 2024-02-13 09:39:00+05:30 | 3565.8 | 3571.5 | 3565.0 | 3570.7 |
| 4 | 2024-02-13 09:40:00+05:30 | 3570.7 | 3571.8 | 3567.25 | 3570.4 |
| 5 | 2024-02-13 09:41:00+05:30 | 3568.6 | 3568.6 | 3565.0 | 3566.55 |
| 6 | 2024-02-13 09:42:00+05:30 | 3566.75 | 3569.9 | 3566.6 | 3569.25 |
| 7 | 2024-02-13 09:43:00+05:30 | 3569.9 | 3569.95 | 3563.5 | 3566.85 |
| 8 | 2024-02-13 09:44:00+05:30 | 3566.6 | 3571.95 | 3566.6 | 3571.6 |
| 9 | 2024-02-13 09:45:00+05:30 | 3571.6 | 3571.6 | 3567.4 | 3570.75 |
| 10 | 2024-02-13 09:46:00+05:30 | 3571.3 | 3579.05 | 3571.1 | 3577.15 |
| 11 | 2024-02-13 09:47:00+05:30 | 3578.45 | 3578.45 | 3573.45 | 3576.65 |
| 12 | 2024-02-13 09:48:00+05:30 | 3576.0 | 3576.9 | 3573.5 | 3573.5 |
| 13 | 2024-02-13 09:49:00+05:30 | 3574.1 | 3575.5 | 3572.25 | 3573.15 |
| 14 | 2024-02-13 09:50:00+05:30 | 3573.15 | 3578.8 | 3572.4 | 3578.45 |
| 15 | 2024-02-13 09:51:00+05:30 | 3578.35 | 3578.4 | 3571.8 | 3573.9 |

### arm 01 trade-2 TITAN BUY (exit 2024-02-13 12:56:00 @ 3592.4529, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-13 12:57:00+05:30 | 3594.0 | 3595.4 | 3593.1 | 3594.75 |
| 2 | 2024-02-13 12:58:00+05:30 | 3594.75 | 3595.55 | 3593.15 | 3593.15 |
| 3 | 2024-02-13 12:59:00+05:30 | 3594.5 | 3596.85 | 3593.2 | 3596.05 |
| 4 | 2024-02-13 13:00:00+05:30 | 3596.55 | 3600.0 | 3595.05 | 3600.0 |
| 5 | 2024-02-13 13:01:00+05:30 | 3600.0 | 3600.0 | 3599.75 | 3600.0 |
| 6 | 2024-02-13 13:02:00+05:30 | 3600.0 | 3600.0 | 3597.9 | 3599.55 |
| 7 | 2024-02-13 13:03:00+05:30 | 3599.3 | 3599.55 | 3596.0 | 3597.6 |
| 8 | 2024-02-13 13:04:00+05:30 | 3597.6 | 3598.7 | 3597.1 | 3597.95 |
| 9 | 2024-02-13 13:05:00+05:30 | 3597.85 | 3598.45 | 3596.05 | 3597.4 |
| 10 | 2024-02-13 13:06:00+05:30 | 3597.4 | 3598.5 | 3596.05 | 3598.2 |
| 11 | 2024-02-13 13:07:00+05:30 | 3597.75 | 3598.55 | 3595.0 | 3596.05 |
| 12 | 2024-02-13 13:08:00+05:30 | 3596.05 | 3598.0 | 3596.05 | 3597.8 |
| 13 | 2024-02-13 13:09:00+05:30 | 3597.8 | 3598.1 | 3596.0 | 3598.1 |
| 14 | 2024-02-13 13:10:00+05:30 | 3598.05 | 3598.1 | 3596.25 | 3596.95 |
| 15 | 2024-02-13 13:11:00+05:30 | 3596.95 | 3597.65 | 3595.85 | 3596.0 |

### arm 01 trade-3 TITAN BUY (exit 2024-02-14 10:38:00 @ 3542.8277, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-14 10:39:00+05:30 | 3544.25 | 3545.05 | 3544.25 | 3545.05 |
| 2 | 2024-02-14 10:40:00+05:30 | 3545.95 | 3548.4 | 3545.05 | 3545.15 |
| 3 | 2024-02-14 10:41:00+05:30 | 3544.35 | 3545.2 | 3538.0 | 3538.0 |
| 4 | 2024-02-14 10:42:00+05:30 | 3538.05 | 3538.45 | 3532.25 | 3535.55 |
| 5 | 2024-02-14 10:43:00+05:30 | 3534.55 | 3534.55 | 3528.0 | 3528.6 |
| 6 | 2024-02-14 10:44:00+05:30 | 3528.6 | 3531.6 | 3528.6 | 3530.1 |
| 7 | 2024-02-14 10:45:00+05:30 | 3530.1 | 3533.75 | 3530.0 | 3532.95 |
| 8 | 2024-02-14 10:46:00+05:30 | 3533.5 | 3533.6 | 3530.0 | 3532.05 |
| 9 | 2024-02-14 10:47:00+05:30 | 3533.0 | 3535.65 | 3532.0 | 3533.95 |
| 10 | 2024-02-14 10:48:00+05:30 | 3534.1 | 3539.0 | 3533.4 | 3538.85 |
| 11 | 2024-02-14 10:49:00+05:30 | 3538.85 | 3540.8 | 3536.65 | 3539.2 |
| 12 | 2024-02-14 10:50:00+05:30 | 3538.8 | 3540.35 | 3538.05 | 3540.1 |
| 13 | 2024-02-14 10:51:00+05:30 | 3540.0 | 3542.0 | 3537.2 | 3537.7 |
| 14 | 2024-02-14 10:52:00+05:30 | 3537.7 | 3538.9 | 3537.7 | 3538.5 |
| 15 | 2024-02-14 10:53:00+05:30 | 3538.5 | 3541.0 | 3538.5 | 3538.7 |

### arm 01 trade-4 TITAN BUY (exit 2024-02-14 12:49:00 @ 3541.0786, governor_exit:GOVERNOR_PATH_ERROR)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-14 12:50:00+05:30 | 3541.15 | 3542.0 | 3541.15 | 3542.0 |
| 2 | 2024-02-14 12:51:00+05:30 | 3542.0 | 3542.0 | 3541.45 | 3542.0 |
| 3 | 2024-02-14 12:52:00+05:30 | 3542.0 | 3542.0 | 3540.9 | 3541.7 |
| 4 | 2024-02-14 12:53:00+05:30 | 3541.7 | 3541.85 | 3540.7 | 3541.5 |
| 5 | 2024-02-14 12:54:00+05:30 | 3541.65 | 3542.0 | 3541.1 | 3541.85 |
| 6 | 2024-02-14 12:55:00+05:30 | 3541.8 | 3542.0 | 3541.8 | 3541.95 |
| 7 | 2024-02-14 12:56:00+05:30 | 3542.0 | 3543.45 | 3541.9 | 3543.4 |
| 8 | 2024-02-14 12:57:00+05:30 | 3543.4 | 3545.0 | 3543.35 | 3544.8 |
| 9 | 2024-02-14 12:58:00+05:30 | 3544.85 | 3544.85 | 3543.25 | 3544.4 |
| 10 | 2024-02-14 12:59:00+05:30 | 3543.95 | 3544.95 | 3541.85 | 3543.55 |
| 11 | 2024-02-14 13:00:00+05:30 | 3543.5 | 3547.35 | 3543.5 | 3547.1 |
| 12 | 2024-02-14 13:01:00+05:30 | 3547.2 | 3547.95 | 3546.2 | 3547.9 |
| 13 | 2024-02-14 13:02:00+05:30 | 3547.95 | 3548.35 | 3546.1 | 3547.5 |
| 14 | 2024-02-14 13:03:00+05:30 | 3547.5 | 3548.5 | 3546.8 | 3547.3 |
| 15 | 2024-02-14 13:04:00+05:30 | 3547.75 | 3547.75 | 3546.05 | 3546.1 |

### arm 01 trade-5 TITAN BUY (exit 2024-02-15 11:46:00 @ 3580.7087, governor_exit:GOVERNOR_PATH_ERROR)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-15 11:47:00+05:30 | 3581.95 | 3584.2 | 3581.25 | 3584.2 |
| 2 | 2024-02-15 11:48:00+05:30 | 3582.35 | 3584.4 | 3582.0 | 3584.4 |
| 3 | 2024-02-15 11:49:00+05:30 | 3582.8 | 3582.85 | 3579.15 | 3581.1 |
| 4 | 2024-02-15 11:50:00+05:30 | 3582.15 | 3584.8 | 3581.1 | 3582.55 |
| 5 | 2024-02-15 11:51:00+05:30 | 3582.6 | 3585.9 | 3582.6 | 3583.95 |
| 6 | 2024-02-15 11:52:00+05:30 | 3583.95 | 3586.75 | 3583.6 | 3584.95 |
| 7 | 2024-02-15 11:53:00+05:30 | 3585.85 | 3586.3 | 3583.65 | 3584.3 |
| 8 | 2024-02-15 11:54:00+05:30 | 3584.3 | 3588.35 | 3583.75 | 3586.45 |
| 9 | 2024-02-15 11:55:00+05:30 | 3587.75 | 3589.55 | 3586.35 | 3588.0 |
| 10 | 2024-02-15 11:56:00+05:30 | 3589.55 | 3589.55 | 3588.0 | 3589.0 |
| 11 | 2024-02-15 11:57:00+05:30 | 3588.3 | 3589.6 | 3588.0 | 3588.05 |
| 12 | 2024-02-15 11:58:00+05:30 | 3588.35 | 3589.9 | 3585.05 | 3585.05 |
| 13 | 2024-02-15 11:59:00+05:30 | 3585.05 | 3587.65 | 3584.2 | 3584.45 |
| 14 | 2024-02-15 12:00:00+05:30 | 3584.45 | 3586.65 | 3583.1 | 3583.2 |
| 15 | 2024-02-15 12:01:00+05:30 | 3583.25 | 3587.2 | 3583.25 | 3583.45 |

### arm 01 trade-6 TITAN BUY (exit 2024-02-15 14:55:00 @ 3607.1955, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-15 14:56:00+05:30 | 3609.95 | 3610.0 | 3608.1 | 3610.0 |
| 2 | 2024-02-15 14:57:00+05:30 | 3609.35 | 3610.0 | 3606.6 | 3609.9 |
| 3 | 2024-02-15 14:58:00+05:30 | 3610.0 | 3611.0 | 3605.0 | 3606.55 |
| 4 | 2024-02-15 14:59:00+05:30 | 3606.55 | 3610.0 | 3605.85 | 3609.95 |
| 5 | 2024-02-15 15:00:00+05:30 | 3609.95 | 3612.0 | 3607.2 | 3611.6 |
| 6 | 2024-02-15 15:01:00+05:30 | 3611.0 | 3613.0 | 3609.7 | 3613.0 |
| 7 | 2024-02-15 15:02:00+05:30 | 3612.95 | 3616.0 | 3609.7 | 3612.05 |
| 8 | 2024-02-15 15:03:00+05:30 | 3614.1 | 3623.6 | 3612.1 | 3623.0 |
| 9 | 2024-02-15 15:04:00+05:30 | 3623.0 | 3628.95 | 3622.5 | 3625.6 |
| 10 | 2024-02-15 15:05:00+05:30 | 3625.95 | 3626.0 | 3619.65 | 3622.7 |
| 11 | 2024-02-15 15:06:00+05:30 | 3623.0 | 3627.75 | 3621.05 | 3627.0 |
| 12 | 2024-02-15 15:07:00+05:30 | 3628.25 | 3632.0 | 3622.3 | 3623.0 |
| 13 | 2024-02-15 15:08:00+05:30 | 3622.25 | 3626.25 | 3620.45 | 3625.95 |
| 14 | 2024-02-15 15:09:00+05:30 | 3625.95 | 3626.45 | 3622.4 | 3625.0 |
| 15 | 2024-02-15 15:10:00+05:30 | 3625.45 | 3626.8 | 3624.0 | 3624.0 |

### arm 01 trade-7 TITAN BUY (exit 2024-02-16 11:15:00 @ 3669.2145, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-16 11:16:00+05:30 | 3674.4 | 3679.65 | 3672.8 | 3679.0 |
| 2 | 2024-02-16 11:17:00+05:30 | 3679.0 | 3684.4 | 3678.9 | 3683.8 |
| 3 | 2024-02-16 11:18:00+05:30 | 3684.05 | 3686.95 | 3682.35 | 3684.0 |
| 4 | 2024-02-16 11:19:00+05:30 | 3684.75 | 3690.0 | 3680.95 | 3681.15 |
| 5 | 2024-02-16 11:20:00+05:30 | 3680.95 | 3681.65 | 3677.05 | 3677.3 |
| 6 | 2024-02-16 11:21:00+05:30 | 3677.25 | 3678.05 | 3673.6 | 3673.6 |
| 7 | 2024-02-16 11:22:00+05:30 | 3673.6 | 3678.2 | 3673.6 | 3678.0 |
| 8 | 2024-02-16 11:23:00+05:30 | 3678.0 | 3678.0 | 3674.0 | 3675.85 |
| 9 | 2024-02-16 11:24:00+05:30 | 3674.2 | 3676.2 | 3671.45 | 3673.6 |
| 10 | 2024-02-16 11:25:00+05:30 | 3673.6 | 3674.9 | 3671.9 | 3673.0 |
| 11 | 2024-02-16 11:26:00+05:30 | 3673.0 | 3673.9 | 3671.05 | 3673.0 |
| 12 | 2024-02-16 11:27:00+05:30 | 3673.0 | 3675.5 | 3672.05 | 3672.05 |
| 13 | 2024-02-16 11:28:00+05:30 | 3672.05 | 3674.05 | 3671.65 | 3674.05 |
| 14 | 2024-02-16 11:29:00+05:30 | 3674.05 | 3676.85 | 3673.0 | 3674.3 |
| 15 | 2024-02-16 11:30:00+05:30 | 3674.3 | 3676.8 | 3674.3 | 3676.8 |

### arm 01 trade-8 TITAN SELL (exit 2024-02-16 12:43:00 @ 3664.3313, governor_exit:GOVERNOR_PATH_ERROR)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-16 12:44:00+05:30 | 3661.45 | 3662.0 | 3660.0 | 3660.0 |
| 2 | 2024-02-16 12:45:00+05:30 | 3660.0 | 3663.0 | 3659.9 | 3663.0 |
| 3 | 2024-02-16 12:46:00+05:30 | 3663.0 | 3664.0 | 3662.95 | 3663.65 |
| 4 | 2024-02-16 12:47:00+05:30 | 3663.65 | 3663.65 | 3659.1 | 3660.0 |
| 5 | 2024-02-16 12:48:00+05:30 | 3660.0 | 3660.35 | 3659.95 | 3660.0 |
| 6 | 2024-02-16 12:49:00+05:30 | 3660.0 | 3660.2 | 3660.0 | 3660.0 |
| 7 | 2024-02-16 12:50:00+05:30 | 3660.0 | 3660.7 | 3659.65 | 3660.0 |
| 8 | 2024-02-16 12:51:00+05:30 | 3660.0 | 3660.5 | 3659.0 | 3660.0 |
| 9 | 2024-02-16 12:52:00+05:30 | 3660.0 | 3660.4 | 3659.6 | 3660.1 |
| 10 | 2024-02-16 12:53:00+05:30 | 3660.1 | 3660.45 | 3659.6 | 3659.6 |
| 11 | 2024-02-16 12:54:00+05:30 | 3659.6 | 3664.4 | 3659.6 | 3660.4 |
| 12 | 2024-02-16 12:55:00+05:30 | 3660.4 | 3660.95 | 3658.0 | 3658.1 |
| 13 | 2024-02-16 12:56:00+05:30 | 3658.1 | 3658.1 | 3648.7 | 3651.75 |
| 14 | 2024-02-16 12:57:00+05:30 | 3652.95 | 3653.05 | 3647.1 | 3651.35 |
| 15 | 2024-02-16 12:58:00+05:30 | 3651.3 | 3651.45 | 3650.0 | 3650.9 |

### arm 01 trade-9 TITAN BUY (exit 2024-02-19 10:28:00 @ 3674.6618, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-19 10:29:00+05:30 | 3677.35 | 3678.15 | 3676.45 | 3678.1 |
| 2 | 2024-02-19 10:30:00+05:30 | 3678.1 | 3678.2 | 3675.85 | 3677.3 |
| 3 | 2024-02-19 10:31:00+05:30 | 3677.3 | 3677.55 | 3676.0 | 3677.0 |
| 4 | 2024-02-19 10:32:00+05:30 | 3677.0 | 3677.0 | 3675.9 | 3677.0 |
| 5 | 2024-02-19 10:33:00+05:30 | 3677.0 | 3677.55 | 3676.0 | 3677.55 |
| 6 | 2024-02-19 10:34:00+05:30 | 3677.55 | 3677.55 | 3676.1 | 3677.5 |
| 7 | 2024-02-19 10:35:00+05:30 | 3677.5 | 3677.5 | 3675.0 | 3676.95 |
| 8 | 2024-02-19 10:36:00+05:30 | 3676.95 | 3677.0 | 3675.0 | 3675.05 |
| 9 | 2024-02-19 10:37:00+05:30 | 3675.05 | 3676.0 | 3675.0 | 3676.0 |
| 10 | 2024-02-19 10:38:00+05:30 | 3675.0 | 3677.0 | 3675.0 | 3675.15 |
| 11 | 2024-02-19 10:39:00+05:30 | 3675.15 | 3677.0 | 3675.15 | 3677.0 |
| 12 | 2024-02-19 10:40:00+05:30 | 3675.85 | 3677.5 | 3675.15 | 3675.6 |
| 13 | 2024-02-19 10:41:00+05:30 | 3675.6 | 3675.95 | 3672.65 | 3675.95 |
| 14 | 2024-02-19 10:42:00+05:30 | 3675.95 | 3676.0 | 3673.55 | 3673.6 |
| 15 | 2024-02-19 10:43:00+05:30 | 3673.6 | 3675.25 | 3672.25 | 3674.8 |

### arm 01 trade-10 TITAN BUY (exit 2024-02-19 11:26:00 @ 3685.5063, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-19 11:27:00+05:30 | 3690.0 | 3690.0 | 3688.0 | 3688.75 |
| 2 | 2024-02-19 11:28:00+05:30 | 3688.75 | 3689.7 | 3686.65 | 3688.4 |
| 3 | 2024-02-19 11:29:00+05:30 | 3688.4 | 3689.45 | 3687.15 | 3688.7 |
| 4 | 2024-02-19 11:30:00+05:30 | 3688.7 | 3689.0 | 3687.15 | 3688.35 |
| 5 | 2024-02-19 11:31:00+05:30 | 3688.05 | 3689.15 | 3685.35 | 3687.5 |
| 6 | 2024-02-19 11:32:00+05:30 | 3687.45 | 3687.9 | 3683.0 | 3683.15 |
| 7 | 2024-02-19 11:33:00+05:30 | 3683.25 | 3687.4 | 3682.2 | 3685.25 |
| 8 | 2024-02-19 11:34:00+05:30 | 3685.3 | 3687.6 | 3684.45 | 3687.3 |
| 9 | 2024-02-19 11:35:00+05:30 | 3685.7 | 3687.5 | 3685.7 | 3685.75 |
| 10 | 2024-02-19 11:36:00+05:30 | 3686.05 | 3688.0 | 3686.0 | 3686.05 |
| 11 | 2024-02-19 11:37:00+05:30 | 3686.05 | 3689.0 | 3686.0 | 3689.0 |
| 12 | 2024-02-19 11:38:00+05:30 | 3689.0 | 3693.45 | 3686.6 | 3691.9 |
| 13 | 2024-02-19 11:39:00+05:30 | 3691.9 | 3693.25 | 3691.0 | 3691.3 |
| 14 | 2024-02-19 11:40:00+05:30 | 3692.0 | 3693.85 | 3690.0 | 3690.15 |
| 15 | 2024-02-19 11:41:00+05:30 | 3690.15 | 3692.0 | 3690.0 | 3691.2 |

### arm 11 trade-1 TITAN SELL (exit 2024-02-13 09:36:00 @ 3569.784, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-13 09:37:00+05:30 | 3568.3 | 3569.25 | 3562.15 | 3564.45 |
| 2 | 2024-02-13 09:38:00+05:30 | 3564.45 | 3567.1 | 3564.2 | 3565.8 |
| 3 | 2024-02-13 09:39:00+05:30 | 3565.8 | 3571.5 | 3565.0 | 3570.7 |
| 4 | 2024-02-13 09:40:00+05:30 | 3570.7 | 3571.8 | 3567.25 | 3570.4 |
| 5 | 2024-02-13 09:41:00+05:30 | 3568.6 | 3568.6 | 3565.0 | 3566.55 |
| 6 | 2024-02-13 09:42:00+05:30 | 3566.75 | 3569.9 | 3566.6 | 3569.25 |
| 7 | 2024-02-13 09:43:00+05:30 | 3569.9 | 3569.95 | 3563.5 | 3566.85 |
| 8 | 2024-02-13 09:44:00+05:30 | 3566.6 | 3571.95 | 3566.6 | 3571.6 |
| 9 | 2024-02-13 09:45:00+05:30 | 3571.6 | 3571.6 | 3567.4 | 3570.75 |
| 10 | 2024-02-13 09:46:00+05:30 | 3571.3 | 3579.05 | 3571.1 | 3577.15 |
| 11 | 2024-02-13 09:47:00+05:30 | 3578.45 | 3578.45 | 3573.45 | 3576.65 |
| 12 | 2024-02-13 09:48:00+05:30 | 3576.0 | 3576.9 | 3573.5 | 3573.5 |
| 13 | 2024-02-13 09:49:00+05:30 | 3574.1 | 3575.5 | 3572.25 | 3573.15 |
| 14 | 2024-02-13 09:50:00+05:30 | 3573.15 | 3578.8 | 3572.4 | 3578.45 |
| 15 | 2024-02-13 09:51:00+05:30 | 3578.35 | 3578.4 | 3571.8 | 3573.9 |

### arm 11 trade-2 TITAN BUY (exit 2024-02-13 12:56:00 @ 3592.4529, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-13 12:57:00+05:30 | 3594.0 | 3595.4 | 3593.1 | 3594.75 |
| 2 | 2024-02-13 12:58:00+05:30 | 3594.75 | 3595.55 | 3593.15 | 3593.15 |
| 3 | 2024-02-13 12:59:00+05:30 | 3594.5 | 3596.85 | 3593.2 | 3596.05 |
| 4 | 2024-02-13 13:00:00+05:30 | 3596.55 | 3600.0 | 3595.05 | 3600.0 |
| 5 | 2024-02-13 13:01:00+05:30 | 3600.0 | 3600.0 | 3599.75 | 3600.0 |
| 6 | 2024-02-13 13:02:00+05:30 | 3600.0 | 3600.0 | 3597.9 | 3599.55 |
| 7 | 2024-02-13 13:03:00+05:30 | 3599.3 | 3599.55 | 3596.0 | 3597.6 |
| 8 | 2024-02-13 13:04:00+05:30 | 3597.6 | 3598.7 | 3597.1 | 3597.95 |
| 9 | 2024-02-13 13:05:00+05:30 | 3597.85 | 3598.45 | 3596.05 | 3597.4 |
| 10 | 2024-02-13 13:06:00+05:30 | 3597.4 | 3598.5 | 3596.05 | 3598.2 |
| 11 | 2024-02-13 13:07:00+05:30 | 3597.75 | 3598.55 | 3595.0 | 3596.05 |
| 12 | 2024-02-13 13:08:00+05:30 | 3596.05 | 3598.0 | 3596.05 | 3597.8 |
| 13 | 2024-02-13 13:09:00+05:30 | 3597.8 | 3598.1 | 3596.0 | 3598.1 |
| 14 | 2024-02-13 13:10:00+05:30 | 3598.05 | 3598.1 | 3596.25 | 3596.95 |
| 15 | 2024-02-13 13:11:00+05:30 | 3596.95 | 3597.65 | 3595.85 | 3596.0 |

### arm 11 trade-3 TITAN BUY (exit 2024-02-14 10:38:00 @ 3542.8277, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-14 10:39:00+05:30 | 3544.25 | 3545.05 | 3544.25 | 3545.05 |
| 2 | 2024-02-14 10:40:00+05:30 | 3545.95 | 3548.4 | 3545.05 | 3545.15 |
| 3 | 2024-02-14 10:41:00+05:30 | 3544.35 | 3545.2 | 3538.0 | 3538.0 |
| 4 | 2024-02-14 10:42:00+05:30 | 3538.05 | 3538.45 | 3532.25 | 3535.55 |
| 5 | 2024-02-14 10:43:00+05:30 | 3534.55 | 3534.55 | 3528.0 | 3528.6 |
| 6 | 2024-02-14 10:44:00+05:30 | 3528.6 | 3531.6 | 3528.6 | 3530.1 |
| 7 | 2024-02-14 10:45:00+05:30 | 3530.1 | 3533.75 | 3530.0 | 3532.95 |
| 8 | 2024-02-14 10:46:00+05:30 | 3533.5 | 3533.6 | 3530.0 | 3532.05 |
| 9 | 2024-02-14 10:47:00+05:30 | 3533.0 | 3535.65 | 3532.0 | 3533.95 |
| 10 | 2024-02-14 10:48:00+05:30 | 3534.1 | 3539.0 | 3533.4 | 3538.85 |
| 11 | 2024-02-14 10:49:00+05:30 | 3538.85 | 3540.8 | 3536.65 | 3539.2 |
| 12 | 2024-02-14 10:50:00+05:30 | 3538.8 | 3540.35 | 3538.05 | 3540.1 |
| 13 | 2024-02-14 10:51:00+05:30 | 3540.0 | 3542.0 | 3537.2 | 3537.7 |
| 14 | 2024-02-14 10:52:00+05:30 | 3537.7 | 3538.9 | 3537.7 | 3538.5 |
| 15 | 2024-02-14 10:53:00+05:30 | 3538.5 | 3541.0 | 3538.5 | 3538.7 |

### arm 11 trade-4 TITAN BUY (exit 2024-02-14 12:49:00 @ 3541.0786, governor_exit:GOVERNOR_PATH_ERROR)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-14 12:50:00+05:30 | 3541.15 | 3542.0 | 3541.15 | 3542.0 |
| 2 | 2024-02-14 12:51:00+05:30 | 3542.0 | 3542.0 | 3541.45 | 3542.0 |
| 3 | 2024-02-14 12:52:00+05:30 | 3542.0 | 3542.0 | 3540.9 | 3541.7 |
| 4 | 2024-02-14 12:53:00+05:30 | 3541.7 | 3541.85 | 3540.7 | 3541.5 |
| 5 | 2024-02-14 12:54:00+05:30 | 3541.65 | 3542.0 | 3541.1 | 3541.85 |
| 6 | 2024-02-14 12:55:00+05:30 | 3541.8 | 3542.0 | 3541.8 | 3541.95 |
| 7 | 2024-02-14 12:56:00+05:30 | 3542.0 | 3543.45 | 3541.9 | 3543.4 |
| 8 | 2024-02-14 12:57:00+05:30 | 3543.4 | 3545.0 | 3543.35 | 3544.8 |
| 9 | 2024-02-14 12:58:00+05:30 | 3544.85 | 3544.85 | 3543.25 | 3544.4 |
| 10 | 2024-02-14 12:59:00+05:30 | 3543.95 | 3544.95 | 3541.85 | 3543.55 |
| 11 | 2024-02-14 13:00:00+05:30 | 3543.5 | 3547.35 | 3543.5 | 3547.1 |
| 12 | 2024-02-14 13:01:00+05:30 | 3547.2 | 3547.95 | 3546.2 | 3547.9 |
| 13 | 2024-02-14 13:02:00+05:30 | 3547.95 | 3548.35 | 3546.1 | 3547.5 |
| 14 | 2024-02-14 13:03:00+05:30 | 3547.5 | 3548.5 | 3546.8 | 3547.3 |
| 15 | 2024-02-14 13:04:00+05:30 | 3547.75 | 3547.75 | 3546.05 | 3546.1 |

### arm 11 trade-5 TITAN BUY (exit 2024-02-15 11:46:00 @ 3580.7087, governor_exit:GOVERNOR_PATH_ERROR)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-15 11:47:00+05:30 | 3581.95 | 3584.2 | 3581.25 | 3584.2 |
| 2 | 2024-02-15 11:48:00+05:30 | 3582.35 | 3584.4 | 3582.0 | 3584.4 |
| 3 | 2024-02-15 11:49:00+05:30 | 3582.8 | 3582.85 | 3579.15 | 3581.1 |
| 4 | 2024-02-15 11:50:00+05:30 | 3582.15 | 3584.8 | 3581.1 | 3582.55 |
| 5 | 2024-02-15 11:51:00+05:30 | 3582.6 | 3585.9 | 3582.6 | 3583.95 |
| 6 | 2024-02-15 11:52:00+05:30 | 3583.95 | 3586.75 | 3583.6 | 3584.95 |
| 7 | 2024-02-15 11:53:00+05:30 | 3585.85 | 3586.3 | 3583.65 | 3584.3 |
| 8 | 2024-02-15 11:54:00+05:30 | 3584.3 | 3588.35 | 3583.75 | 3586.45 |
| 9 | 2024-02-15 11:55:00+05:30 | 3587.75 | 3589.55 | 3586.35 | 3588.0 |
| 10 | 2024-02-15 11:56:00+05:30 | 3589.55 | 3589.55 | 3588.0 | 3589.0 |
| 11 | 2024-02-15 11:57:00+05:30 | 3588.3 | 3589.6 | 3588.0 | 3588.05 |
| 12 | 2024-02-15 11:58:00+05:30 | 3588.35 | 3589.9 | 3585.05 | 3585.05 |
| 13 | 2024-02-15 11:59:00+05:30 | 3585.05 | 3587.65 | 3584.2 | 3584.45 |
| 14 | 2024-02-15 12:00:00+05:30 | 3584.45 | 3586.65 | 3583.1 | 3583.2 |
| 15 | 2024-02-15 12:01:00+05:30 | 3583.25 | 3587.2 | 3583.25 | 3583.45 |

### arm 11 trade-6 TITAN BUY (exit 2024-02-15 14:55:00 @ 3607.1955, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-15 14:56:00+05:30 | 3609.95 | 3610.0 | 3608.1 | 3610.0 |
| 2 | 2024-02-15 14:57:00+05:30 | 3609.35 | 3610.0 | 3606.6 | 3609.9 |
| 3 | 2024-02-15 14:58:00+05:30 | 3610.0 | 3611.0 | 3605.0 | 3606.55 |
| 4 | 2024-02-15 14:59:00+05:30 | 3606.55 | 3610.0 | 3605.85 | 3609.95 |
| 5 | 2024-02-15 15:00:00+05:30 | 3609.95 | 3612.0 | 3607.2 | 3611.6 |
| 6 | 2024-02-15 15:01:00+05:30 | 3611.0 | 3613.0 | 3609.7 | 3613.0 |
| 7 | 2024-02-15 15:02:00+05:30 | 3612.95 | 3616.0 | 3609.7 | 3612.05 |
| 8 | 2024-02-15 15:03:00+05:30 | 3614.1 | 3623.6 | 3612.1 | 3623.0 |
| 9 | 2024-02-15 15:04:00+05:30 | 3623.0 | 3628.95 | 3622.5 | 3625.6 |
| 10 | 2024-02-15 15:05:00+05:30 | 3625.95 | 3626.0 | 3619.65 | 3622.7 |
| 11 | 2024-02-15 15:06:00+05:30 | 3623.0 | 3627.75 | 3621.05 | 3627.0 |
| 12 | 2024-02-15 15:07:00+05:30 | 3628.25 | 3632.0 | 3622.3 | 3623.0 |
| 13 | 2024-02-15 15:08:00+05:30 | 3622.25 | 3626.25 | 3620.45 | 3625.95 |
| 14 | 2024-02-15 15:09:00+05:30 | 3625.95 | 3626.45 | 3622.4 | 3625.0 |
| 15 | 2024-02-15 15:10:00+05:30 | 3625.45 | 3626.8 | 3624.0 | 3624.0 |

### arm 11 trade-7 TITAN BUY (exit 2024-02-16 11:15:00 @ 3669.2145, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-16 11:16:00+05:30 | 3674.4 | 3679.65 | 3672.8 | 3679.0 |
| 2 | 2024-02-16 11:17:00+05:30 | 3679.0 | 3684.4 | 3678.9 | 3683.8 |
| 3 | 2024-02-16 11:18:00+05:30 | 3684.05 | 3686.95 | 3682.35 | 3684.0 |
| 4 | 2024-02-16 11:19:00+05:30 | 3684.75 | 3690.0 | 3680.95 | 3681.15 |
| 5 | 2024-02-16 11:20:00+05:30 | 3680.95 | 3681.65 | 3677.05 | 3677.3 |
| 6 | 2024-02-16 11:21:00+05:30 | 3677.25 | 3678.05 | 3673.6 | 3673.6 |
| 7 | 2024-02-16 11:22:00+05:30 | 3673.6 | 3678.2 | 3673.6 | 3678.0 |
| 8 | 2024-02-16 11:23:00+05:30 | 3678.0 | 3678.0 | 3674.0 | 3675.85 |
| 9 | 2024-02-16 11:24:00+05:30 | 3674.2 | 3676.2 | 3671.45 | 3673.6 |
| 10 | 2024-02-16 11:25:00+05:30 | 3673.6 | 3674.9 | 3671.9 | 3673.0 |
| 11 | 2024-02-16 11:26:00+05:30 | 3673.0 | 3673.9 | 3671.05 | 3673.0 |
| 12 | 2024-02-16 11:27:00+05:30 | 3673.0 | 3675.5 | 3672.05 | 3672.05 |
| 13 | 2024-02-16 11:28:00+05:30 | 3672.05 | 3674.05 | 3671.65 | 3674.05 |
| 14 | 2024-02-16 11:29:00+05:30 | 3674.05 | 3676.85 | 3673.0 | 3674.3 |
| 15 | 2024-02-16 11:30:00+05:30 | 3674.3 | 3676.8 | 3674.3 | 3676.8 |

### arm 11 trade-8 TITAN SELL (exit 2024-02-16 12:43:00 @ 3664.3313, governor_exit:GOVERNOR_PATH_ERROR)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-16 12:44:00+05:30 | 3661.45 | 3662.0 | 3660.0 | 3660.0 |
| 2 | 2024-02-16 12:45:00+05:30 | 3660.0 | 3663.0 | 3659.9 | 3663.0 |
| 3 | 2024-02-16 12:46:00+05:30 | 3663.0 | 3664.0 | 3662.95 | 3663.65 |
| 4 | 2024-02-16 12:47:00+05:30 | 3663.65 | 3663.65 | 3659.1 | 3660.0 |
| 5 | 2024-02-16 12:48:00+05:30 | 3660.0 | 3660.35 | 3659.95 | 3660.0 |
| 6 | 2024-02-16 12:49:00+05:30 | 3660.0 | 3660.2 | 3660.0 | 3660.0 |
| 7 | 2024-02-16 12:50:00+05:30 | 3660.0 | 3660.7 | 3659.65 | 3660.0 |
| 8 | 2024-02-16 12:51:00+05:30 | 3660.0 | 3660.5 | 3659.0 | 3660.0 |
| 9 | 2024-02-16 12:52:00+05:30 | 3660.0 | 3660.4 | 3659.6 | 3660.1 |
| 10 | 2024-02-16 12:53:00+05:30 | 3660.1 | 3660.45 | 3659.6 | 3659.6 |
| 11 | 2024-02-16 12:54:00+05:30 | 3659.6 | 3664.4 | 3659.6 | 3660.4 |
| 12 | 2024-02-16 12:55:00+05:30 | 3660.4 | 3660.95 | 3658.0 | 3658.1 |
| 13 | 2024-02-16 12:56:00+05:30 | 3658.1 | 3658.1 | 3648.7 | 3651.75 |
| 14 | 2024-02-16 12:57:00+05:30 | 3652.95 | 3653.05 | 3647.1 | 3651.35 |
| 15 | 2024-02-16 12:58:00+05:30 | 3651.3 | 3651.45 | 3650.0 | 3650.9 |

### arm 11 trade-9 TITAN BUY (exit 2024-02-19 10:29:00 @ 3675.5113, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-19 10:30:00+05:30 | 3678.1 | 3678.2 | 3675.85 | 3677.3 |
| 2 | 2024-02-19 10:31:00+05:30 | 3677.3 | 3677.55 | 3676.0 | 3677.0 |
| 3 | 2024-02-19 10:32:00+05:30 | 3677.0 | 3677.0 | 3675.9 | 3677.0 |
| 4 | 2024-02-19 10:33:00+05:30 | 3677.0 | 3677.55 | 3676.0 | 3677.55 |
| 5 | 2024-02-19 10:34:00+05:30 | 3677.55 | 3677.55 | 3676.1 | 3677.5 |
| 6 | 2024-02-19 10:35:00+05:30 | 3677.5 | 3677.5 | 3675.0 | 3676.95 |
| 7 | 2024-02-19 10:36:00+05:30 | 3676.95 | 3677.0 | 3675.0 | 3675.05 |
| 8 | 2024-02-19 10:37:00+05:30 | 3675.05 | 3676.0 | 3675.0 | 3676.0 |
| 9 | 2024-02-19 10:38:00+05:30 | 3675.0 | 3677.0 | 3675.0 | 3675.15 |
| 10 | 2024-02-19 10:39:00+05:30 | 3675.15 | 3677.0 | 3675.15 | 3677.0 |
| 11 | 2024-02-19 10:40:00+05:30 | 3675.85 | 3677.5 | 3675.15 | 3675.6 |
| 12 | 2024-02-19 10:41:00+05:30 | 3675.6 | 3675.95 | 3672.65 | 3675.95 |
| 13 | 2024-02-19 10:42:00+05:30 | 3675.95 | 3676.0 | 3673.55 | 3673.6 |
| 14 | 2024-02-19 10:43:00+05:30 | 3673.6 | 3675.25 | 3672.25 | 3674.8 |
| 15 | 2024-02-19 10:44:00+05:30 | 3674.8 | 3676.0 | 3673.55 | 3674.7 |

### arm 11 trade-10 TITAN BUY (exit 2024-02-19 11:26:00 @ 3685.5063, governor_exit:FSR_BELOW_EXIT:FSRN)

| +bar | timestamp | open | high | low | close |
|:---|:---|:---|:---|:---|:---|
| 1 | 2024-02-19 11:27:00+05:30 | 3690.0 | 3690.0 | 3688.0 | 3688.75 |
| 2 | 2024-02-19 11:28:00+05:30 | 3688.75 | 3689.7 | 3686.65 | 3688.4 |
| 3 | 2024-02-19 11:29:00+05:30 | 3688.4 | 3689.45 | 3687.15 | 3688.7 |
| 4 | 2024-02-19 11:30:00+05:30 | 3688.7 | 3689.0 | 3687.15 | 3688.35 |
| 5 | 2024-02-19 11:31:00+05:30 | 3688.05 | 3689.15 | 3685.35 | 3687.5 |
| 6 | 2024-02-19 11:32:00+05:30 | 3687.45 | 3687.9 | 3683.0 | 3683.15 |
| 7 | 2024-02-19 11:33:00+05:30 | 3683.25 | 3687.4 | 3682.2 | 3685.25 |
| 8 | 2024-02-19 11:34:00+05:30 | 3685.3 | 3687.6 | 3684.45 | 3687.3 |
| 9 | 2024-02-19 11:35:00+05:30 | 3685.7 | 3687.5 | 3685.7 | 3685.75 |
| 10 | 2024-02-19 11:36:00+05:30 | 3686.05 | 3688.0 | 3686.0 | 3686.05 |
| 11 | 2024-02-19 11:37:00+05:30 | 3686.05 | 3689.0 | 3686.0 | 3689.0 |
| 12 | 2024-02-19 11:38:00+05:30 | 3689.0 | 3693.45 | 3686.6 | 3691.9 |
| 13 | 2024-02-19 11:39:00+05:30 | 3691.9 | 3693.25 | 3691.0 | 3691.3 |
| 14 | 2024-02-19 11:40:00+05:30 | 3692.0 | 3693.85 | 3690.0 | 3690.15 |
| 15 | 2024-02-19 11:41:00+05:30 | 3690.15 | 3692.0 | 3690.0 | 3691.2 |


# SECTION 6: GIT TREE BOUNDARY AUDIT

## `git status` (unedited)
```
On branch diagnostic/block1-isolation-harness
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	docs/experiment_outputs/steam/after/block1/
	docs/experiment_outputs/steam/before/block1/
	outputs/block1_isolation/
	outputs/block1_isolation_audit/
	outputs/block1_isolation_final/
	scripts/diagnostics/
	tests/test_block1_isolation_harness.py

nothing added to commit but untracked files present (use "git add" to track)
```
## `git diff --stat origin/diagnostic/pid-controller-authority-hierarchy` (unedited)
```

```
(An empty diff --stat means no TRACKED file differs from the PR #7 head.)

`git diff --name-only origin/diagnostic/pid-controller-authority-hierarchy -- <all engine dirs>`: (empty)
Untracked paths outside scripts/diagnostics/, tests/, outputs/block1_isolation*/ and the pre-existing docs/experiment_outputs/steam/: none
PR #7 head: local tracking ref 440ca5fac80d935c16a8c45fd489f2d3f9153503; remote now 440ca5fac80d935c16a8c45fd489f2d3f9153503; unchanged from reported 440ca5fac80d935c16a8c45fd489f2d3f9153503: True

**SELF-CHECK FAILURES: none**
