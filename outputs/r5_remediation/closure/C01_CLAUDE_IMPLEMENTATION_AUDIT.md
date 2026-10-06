# C01 Claude implementation audit — EXPERIMENTAL_PAPER_LIFECYCLE runner

Scope: C01 paper wiring only (historical, offline, Stage B Block 1, 48 symbols). No live readiness and no
qualifying-BUY claim. Not a sealed CANDIDATE_EVALUATION.

## Files changed (only these three)
- `scripts/run_r5_paper_lifecycle.py` (new)
- `tests/test_r5_paper_lifecycle_entry.py` (new)
- `outputs/C01_CLAUDE_IMPLEMENTATION_AUDIT.md` (this file)

No edits to the worker, engine, sealed protocol, parameters, holdout/reference outputs or controller logic.

## Tests: NOT RUN
Nothing was executed by the implementer (no commands were permitted). All code and tests are unverified.
Suggested: `python -m pytest tests/test_r5_paper_lifecycle_entry.py tests/test_r5_worker_runtime_wiring.py -q`.

## API
- CLI: `python scripts/run_r5_paper_lifecycle.py --mode EXPERIMENTAL_PAPER_LIFECYCLE --expected-protocol-sha <sha> --params <json> --stock-root <dir> --grid-root <dir> --output-dir <new dir> [--protocol <json>]`
  - `--protocol` defaults to the checkout's `revision5/step5_sealed_calibration_protocol_v2.json`.
  - Parser has `allow_abbrev=False` and exposes exactly those 7 options: no `--root`, stage, block, session, symbol, live, resume, audit-only or tuning flag. Code root is always the checkout containing the script.
- `main(argv=None) -> int` (0 ok, 1 failed/refused, 2 usage, 4 completed with safety violations).
- `run_lifecycle(args, *, code_root, executor, snapshot_fn, _test_loader_substitute) -> (exit_code, manifest)`; the last three are test seams.
- Helpers: `hash_source_tree`, `capture_source_identity`, `require_source_unchanged`, `critical_module_report`, `load_pinned_protocol`, `load_params`, `select_stage_b_block1`, `validate_universe`, `validate_grid_inputs`, `build_data_view`, `validate_output_dir`, `evaluate_engine_identity`, `summarize_result`.

## Behaviour
- Protocol SHA pinned (64 lowercase hex, bytes hashed once and parsed from the same bytes); `protocol_id` must be V2.
- Params keys must equal `protocol.search_surface` exactly, then `registry.validate_calibration_payload(engine="EXTERNAL")`. Registry frozen identity is verified; whether the registry identity equals `protocol.registry_identity_sha256` is recorded (not enforced, to avoid a false refusal if the registry has moved on).
- Block: exactly `sampling_plan.stage_b` block 1.
- Universe: on `DatasetManifest` metadata of the manifest seen through the view: protocol says 48, manifest hash equals the protocol's, `symbol_count == len(files) == 48`, unique, trimmed uppercase names, absolute `data_dir` inside `--stock-root`; protocol must forbid synthetic data (loader already uses `synthetic_if_missing=False`). Grid manifest feed set must match the protocol and every feed path must be absolute and inside `--grid-root`.
- Data view: `TemporaryDirectory` with symlinks `revision2 -> stock_root/revision2`, `local_workspace -> grid_root/local_workspace`; passed as the data `root` to the unchanged `worker.execute_block`. Imports come from the code checkout; every critical module (list `CRITICAL_MODULES`) is imported up front, must live under the code root, and its SHA-256 is recorded and re-checked after the run.
- Identity: `worker.verify_engine_identity` is called; only a `SystemExit` starting `ENGINE_PARENT_DRIFT` is tolerated, and the full message is stored as `engine_identity.engine_parent_drift` with `sealed_source_claim: false`. Ancestry failure (CalledProcessError) and any other SystemExit fail the run.
- Source identity (dirty and untracked included): aggregate SHA-256 over sorted `relpath\0sha256` of root `*.py`, all `*.py` under top-level packages (dirs with `__init__.py`, excluding tests*) plus `blocks/` and `scripts/`, and a short list of known registry/protocol JSON files. Excludes outputs, docs, data, tests, local_workspace, caches, venvs, non-py files other than the known list (secret-like names are rejected from that list). Captured before the output dir is created and after the run; any change, or a HEAD change, fails the run. Also records git HEAD, branch, dirty flag and `status --porcelain` entries (capped at 2000).
- Output dir must not exist (checked with `lexists`), parent must exist, must not be inside either data root, and the path must not contain `sealed`, `holdout` or `r5_step5` (case-insensitive substrings). Created with `mkdir(exist_ok=False)`. WAL is `<out>/paper_lifecycle_wal.sqlite3`, created by the worker factory with `'xb'`; the runner also refuses a pre-existing WAL path. Nothing resumes.
- On any engine/guard error (Exception or SystemExit) a `run_manifest.json` with `status: FAILED`, error and traceback plus `SUMMARY.md` are written in the fresh dir, no `result.json`/ledger, exit 1.
- Result validation requires the runtime receipt, `live_admissions is False`, and `end_of_run_disposition == "CLOSE"` (the factory default; the runner never sets it). For non-substituted runs the executed symbol set must equal the manifest's 48 and only then is `actual48_certified` true.
- Outputs: `result.json` (full worker result + summary), `ledger.json` and `ledger.csv` (engine completed trades), `run_manifest.json`, `SUMMARY.md`, the WAL.
- Metrics all from the engine result: gross/net P&L, MTM max drawdown fraction (and percent = fraction x 100), total fills, completed trades, BUY/SELL trade counts, total costs (sum of engine per-trade `costs`), safety violations, governor entry/position decision counts, MiCOM trip counts. Labelled `UNAVAILABLE_FROM_WORKER_RESULT` rather than invented: fills by side, ID counts, Gate12 counts (`execute_block` does not return them).
- Non-finite floats (e.g. profit factor inf) are written as strings so the JSON is strict.

## Tests (NOT RUN) — coverage
CLI surface and override rejection; wrong/malformed SHA; params surface/registry; Stage B Block 1 selection; 48-universe checks (47, duplicate, bad name, count, outside data_dir, hash mismatch) on test-built valid manifest metadata; output reuse and WAL not truncated (runner and worker factory); restricted output paths create nothing; symlink view with separate roots; drift recorded / non-drift identity failures on a temporary git repo using the real `verify_engine_identity`; current required dirty/untracked source files hashed (real checkout); exclusion rules and mid-run change detection; mid-run change and engine error produce FAILED receipts; critical modules load from checkout; alternate code root refused; integration through `run_lifecycle` + real `worker.execute_block` on the derived TITAN fixture (only `worker.prepare_block` substituted, labelled test, `actual48_certified: false`), asserting trades/metrics equal the baseline worker run, default worker fingerprint unchanged, and re-run into the same output refused.

## Limitations / risks to verify
- Several tests (mid-run, engine error, integration) run the real `verify_engine_identity`, which requires the sealed parent `c0b4672…` to be an ancestor of the checkout's HEAD; if it is not, those tests (and real runs) correctly fail.
- Drift detail is only the worker's `ENGINE_PARENT_DRIFT: ...` message (tracked diff vs the parent); untracked files are covered by the source hash, not by that message.
- Real runs require the stock manifest's absolute `data_dir` to lie under `--stock-root`, and grid manifest absolute paths under `--grid-root`; a relocated copy with stale absolute paths will be refused rather than read from elsewhere.
- Registry-identity-vs-protocol match is recorded, not enforced.
- `run_manifest.json` is written once at the end; a hard kill leaves no receipt (only the WAL and dir).
- The source set is a deliberate heuristic (top-level packages + root py + scripts/blocks + listed JSON); modules imported from outside it are caught only if in `CRITICAL_MODULES`.
- Multi-session PERSIST and morning hydration are not covered; the integration uses a single TITAN session and certifies nothing about the 48-symbol Block 1.
- The real 48-symbol run needs ~48 symbols of data and was not attempted.
