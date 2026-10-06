"""C01 EXPERIMENTAL_PAPER_LIFECYCLE runner tests.

The integration test drives the runner and the real ``worker.execute_block`` on the derived real
TITAN fixture.  Only the data loader (``worker.prepare_block``) is substituted; that is a TEST
substitution and NOT a 48-symbol Block 1 certification.  The 48-symbol universe checks run on a
test-built but schema-valid DatasetManifest.

Note: test names avoid the words the runner treats as restricted output locations, because
pytest derives tmp_path directory names from them.
"""
import hashlib
import inspect
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from revision2.dataset_manifest import FileRecord
from scripts import run_r5_paper_lifecycle as c01
from scripts import run_r5_step5_candidate as worker

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/r5_d01"
REAL_PROTOCOL = ROOT / "revision5/step5_sealed_calibration_protocol_v2.json"
PARAMS = FIXTURE / "trial_007_params.json"
NAMES = ["TITAN"] + [f"SYM{i:02d}" for i in range(47)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_stock_manifest(stock_root, names=NAMES, *, data_dir=None, symbol_count=None, hash_value=None):
    records = [FileRecord(symbol=n, filename=f"{n}.csv", sha256="0" * 64, size_bytes=1, row_count=1,
                          first_timestamp="a", last_timestamp="b") for n in names]
    payload = json.dumps([asdict(r) for r in records], sort_keys=True, default=str)
    manifest_hash = hash_value or hashlib.sha256(payload.encode()).hexdigest()
    path = Path(stock_root) / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "data_dir": str(data_dir or Path(stock_root) / "data"), "built_at": "test",
        "symbol_count": len(names) if symbol_count is None else symbol_count,
        "manifest_hash": manifest_hash, "files": [asdict(r) for r in records]}))
    return manifest_hash


def make_env(tmp_path, **manifest_kwargs):
    stock_root, grid_root = tmp_path / "stock_root", tmp_path / "grid_root"
    manifest_hash = write_stock_manifest(stock_root, **manifest_kwargs)
    feeds = grid_root / "local_workspace/feeds"
    feeds.mkdir(parents=True)
    records = []
    for name in ("NIFTY_50_15MIN", "INDIA_VIX_15MIN"):
        (feeds / f"{name}.csv").write_text("x\n")
        records.append({"name": name, "path": str(feeds / f"{name}.csv"), "sha256": "0" * 64})
    (grid_root / "local_workspace/records").mkdir()
    (grid_root / "local_workspace/records/grid-manifest-local.json").write_text(json.dumps({"files": records}))
    protocol = json.loads(REAL_PROTOCOL.read_text())
    protocol["stock_dataset"]["manifest_hash"] = manifest_hash
    protocol_path = tmp_path / "protocol_copy.json"
    protocol_path.write_text(json.dumps(protocol))
    return SimpleNamespace(stock_root=stock_root, grid_root=grid_root, protocol=protocol_path,
                           sha=sha(protocol_path), out=tmp_path / "run_out", tmp=tmp_path)


def make_args(env, **overrides):
    values = {"--mode": c01.MODE, "--protocol": str(env.protocol), "--expected-protocol-sha": env.sha,
              "--params": str(PARAMS), "--stock-root": str(env.stock_root), "--grid-root": str(env.grid_root),
              "--output-dir": str(env.out)}
    values.update(overrides)
    argv = [x for pair in values.items() for x in pair]
    return c01.build_parser().parse_args(argv)


def must_not_execute(*args, **kwargs):
    raise AssertionError("engine must not be reached")


def loader_substitute(*args):
    stock = pd.read_csv(FIXTURE / "titan_1min_block1_session1.csv")
    stock["timestamp"] = pd.to_datetime(stock["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    feeds = {}
    for name, filename in [("NIFTY_50_15MIN", "nifty_50_15min_prefix.csv"),
                           ("INDIA_VIX_15MIN", "india_vix_15min_prefix.csv")]:
        frame = pd.read_csv(FIXTURE / filename)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        feeds[name] = frame
    audit = {"slice_sha256": sha(FIXTURE / "titan_1min_block1_session1.csv"), "stock": {"TITAN": {}}, "grid": {}}
    return {"TITAN": stock}, feeds, audit


# ------------------------------------------------------------------ CLI surface

def test_cli_exposes_only_the_allowed_options():
    options = {o for a in c01.build_parser()._actions for o in a.option_strings} - {"-h", "--help"}
    assert options == {"--mode", "--protocol", "--expected-protocol-sha", "--params", "--stock-root",
                       "--grid-root", "--output-dir", "--acceptance", "--fleet-loading", "--feedback-compaction"}


@pytest.mark.parametrize("extra", [
    ["--stage", "B"], ["--stage", "C"], ["--block", "2"], ["--symbols", "TITAN"], ["--symbol", "TITAN"],
    ["--live"], ["--resume"], ["--root", "/tmp"], ["--audit-only"], ["--session", "2024-02-13"],
    ["--paper-combined-cycle-state", "/tmp/x"], ["--tune"], ["--output-dir", "/tmp/a", "--output-d", "x"],
])
def test_cli_rejects_overrides(tmp_path, extra, capsys):
    env = make_env(tmp_path)
    argv = ["--mode", c01.MODE, "--expected-protocol-sha", env.sha, "--params", str(PARAMS),
            "--stock-root", str(env.stock_root), "--grid-root", str(env.grid_root),
            "--output-dir", str(env.out)] + extra
    with pytest.raises(SystemExit) as exc:
        c01.build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_cli_requires_explicit_mode():
    with pytest.raises(SystemExit):
        c01.build_parser().parse_args(["--mode", "LIVE"])


def test_default_protocol_is_the_actual_checkout_file():
    assert Path(c01.build_parser().get_default("protocol")) == REAL_PROTOCOL


# ------------------------------------------------------------------ pins and surface

def test_wrong_protocol_sha_fails_with_receipt_and_no_engine(tmp_path):
    env = make_env(tmp_path)
    code, manifest = c01.run_lifecycle(make_args(env, **{"--expected-protocol-sha": "0" * 64}),
                                       executor=must_not_execute)
    assert code == c01.EXIT_FAILED and manifest["status"] == "FAILED"
    assert "PROTOCOL_SHA_MISMATCH" in manifest["error"]["message"]
    assert json.loads((env.out / "run_manifest.json").read_text())["status"] == "FAILED"
    assert not (env.out / "result.json").exists() and not (env.out / c01.WAL_NAME).exists()


def test_malformed_protocol_sha_rejected(tmp_path):
    env = make_env(tmp_path)
    code, manifest = c01.run_lifecycle(make_args(env, **{"--expected-protocol-sha": env.sha.upper()}),
                                       executor=must_not_execute)
    assert code == c01.EXIT_FAILED and "lowercase hex" in manifest["error"]["message"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("max_hold_bars"),
    lambda p: p.update({"extra_knob": 1}),
    lambda p: p.update({"entry_confidence_threshold": 99.0}),
])
def test_parameter_surface_and_registry_enforced(tmp_path, mutate):
    env = make_env(tmp_path)
    params = json.loads(PARAMS.read_text())
    mutate(params)
    bad = tmp_path / "bad_params.json"
    bad.write_text(json.dumps(params))
    code, manifest = c01.run_lifecycle(make_args(env, **{"--params": str(bad)}), executor=must_not_execute)
    assert code == c01.EXIT_FAILED and manifest["status"] == "FAILED"
    assert not (env.out / "result.json").exists()


def test_only_stage_b_block1_is_selected():
    protocol = json.loads(REAL_PROTOCOL.read_text())
    block = c01.select_stage_b_block1(protocol)
    assert block == protocol["sampling_plan"]["stage_b"][0] and block["block"] == 1
    assert block != protocol["sampling_plan"]["stage_a"][0]
    protocol["sampling_plan"]["stage_b"].append(dict(block))
    with pytest.raises(c01.LifecycleError):
        c01.select_stage_b_block1(protocol)


# ------------------------------------------------------------------ 48-symbol universe on manifest metadata

def _universe(tmp_path, **kwargs):
    env = make_env(tmp_path, **kwargs)
    protocol = json.loads(env.protocol.read_text())
    return c01.validate_universe(protocol, env.stock_root / protocol["stock_dataset"]["manifest"], env.stock_root)


def test_universe_accepts_valid_48(tmp_path):
    universe = _universe(tmp_path)
    assert universe["symbol_count"] == 48 and universe["symbols"] == sorted(NAMES)


@pytest.mark.parametrize("kwargs", [
    {"names": NAMES[:47]},
    {"names": NAMES[:47] + [NAMES[0]]},
    {"names": NAMES[:47] + ["bad name"]},
    {"symbol_count": 47},
    {"data_dir": "/elsewhere/data"},
])
def test_universe_rejects_invalid_manifest_metadata(tmp_path, kwargs):
    with pytest.raises(c01.LifecycleError):
        _universe(tmp_path, **kwargs)


def test_universe_rejects_manifest_hash_mismatch(tmp_path):
    env = make_env(tmp_path)
    protocol = json.loads(env.protocol.read_text())
    protocol["stock_dataset"]["manifest_hash"] = "f" * 64
    with pytest.raises(c01.LifecycleError, match="identity mismatch"):
        c01.validate_universe(protocol, env.stock_root / protocol["stock_dataset"]["manifest"], env.stock_root)


# ------------------------------------------------------------------ output directory / WAL

def test_existing_output_dir_never_reused_and_wal_not_truncated(tmp_path):
    env = make_env(tmp_path)
    env.out.mkdir()
    wal = env.out / c01.WAL_NAME
    wal.write_bytes(b"EXISTING-WAL-BYTES")
    with pytest.raises(c01.LifecycleError, match="already exists"):
        c01.run_lifecycle(make_args(env), executor=must_not_execute)
    assert wal.read_bytes() == b"EXISTING-WAL-BYTES"
    assert sorted(p.name for p in env.out.iterdir()) == [c01.WAL_NAME]
    assert c01.main(["--mode", c01.MODE, "--expected-protocol-sha", env.sha, "--params", str(PARAMS),
                     "--protocol", str(env.protocol), "--stock-root", str(env.stock_root),
                     "--grid-root", str(env.grid_root), "--output-dir", str(env.out)]) != 0
    assert wal.read_bytes() == b"EXISTING-WAL-BYTES"


def test_worker_factory_refuses_existing_wal_without_truncation(tmp_path):
    wal = tmp_path / "w.sqlite3"
    wal.write_bytes(b"KEEP")
    with pytest.raises(FileExistsError):
        worker.build_paper_combined_cycle_runtime(wal)
    assert wal.read_bytes() == b"KEEP"


def test_output_dir_restrictions(tmp_path):
    env = make_env(tmp_path)
    for bad in (tmp_path / "r5_step5_out", tmp_path / "x_sealed_out", env.stock_root / "out",
                env.grid_root / "local_workspace" / "out"):
        with pytest.raises(c01.LifecycleError):
            c01.validate_output_dir(str(bad), code_root=ROOT, stock_root=env.stock_root, grid_root=env.grid_root)
    with pytest.raises(c01.LifecycleError, match="parent"):
        c01.validate_output_dir(str(tmp_path / "missing_parent" / "o"), code_root=ROOT,
                                stock_root=env.stock_root, grid_root=env.grid_root)


def test_restricted_output_creates_nothing(tmp_path):
    env = make_env(tmp_path)
    bad = tmp_path / "r5_step5_out"
    with pytest.raises(c01.LifecycleError):
        c01.run_lifecycle(make_args(env, **{"--output-dir": str(bad)}), executor=must_not_execute)
    assert not bad.exists()


# ------------------------------------------------------------------ data view

def test_data_view_links_separate_roots(tmp_path):
    env = make_env(tmp_path)
    view = tmp_path / "view"
    view.mkdir()
    c01.build_data_view(env.stock_root, env.grid_root, view)
    assert os.readlink(view / "revision2") == str((env.stock_root / "revision2").resolve())
    assert os.readlink(view / "local_workspace") == str((env.grid_root / "local_workspace").resolve())
    assert (view / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json").is_file()
    assert (view / "local_workspace/records/grid-manifest-local.json").is_file()
    assert sorted(p.name for p in view.iterdir()) == ["local_workspace", "revision2"]


def test_data_view_requires_both_subtrees(tmp_path):
    env = make_env(tmp_path)
    (env.grid_root / "local_workspace/records/grid-manifest-local.json").unlink()
    (env.grid_root / "local_workspace/records").rmdir()
    (env.grid_root / "local_workspace/feeds/NIFTY_50_15MIN.csv").unlink()
    (env.grid_root / "local_workspace/feeds/INDIA_VIX_15MIN.csv").unlink()
    (env.grid_root / "local_workspace/feeds").rmdir()
    (env.grid_root / "local_workspace").rmdir()
    view = tmp_path / "view"
    view.mkdir()
    with pytest.raises(c01.LifecycleError, match="local_workspace"):
        c01.build_data_view(env.stock_root, env.grid_root, view)


# ------------------------------------------------------------------ engine identity

def _git(cwd, *args):
    return subprocess.check_output(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t",
                                    "-c", "commit.gpgsign=false", *args], text=True).strip()


@pytest.fixture
def mini_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "revision2_external").mkdir(parents=True)
    (repo / "revision2_external/orchestrator.py").write_text("x = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "parent")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_engine_identity_clean_is_not_a_sealed_claim(mini_repo):
    repo, parent = mini_repo
    record = c01.evaluate_engine_identity(worker, repo, {"distributed_execution": {"engine_parent_commit": parent}})
    assert record["status"] == "NO_DRIFT_VS_PARENT" and record["engine_parent_drift"] is None
    assert record["sealed_source_claim"] is False


def test_engine_drift_recorded_not_blessed(mini_repo):
    repo, parent = mini_repo
    (repo / "revision2_external/orchestrator.py").write_text("x = 2\n")
    record = c01.evaluate_engine_identity(worker, repo, {"distributed_execution": {"engine_parent_commit": parent}})
    assert record["status"] == "ENGINE_PARENT_DRIFT_RECORDED"
    assert record["engine_parent_drift"] == "ENGINE_PARENT_DRIFT: revision2_external/orchestrator.py"
    assert record["sealed_source_claim"] is False


def test_other_identity_errors_still_fail(mini_repo):
    repo, _ = mini_repo
    with pytest.raises(c01.LifecycleError, match="ancestry"):
        c01.evaluate_engine_identity(worker, repo, {"distributed_execution": {"engine_parent_commit": "0" * 40}})

    class Stub:
        @staticmethod
        def verify_engine_identity(*a):
            raise SystemExit("SOMETHING_ELSE: nope")
    with pytest.raises(c01.LifecycleError, match="SOMETHING_ELSE"):
        c01.evaluate_engine_identity(Stub, repo, {"distributed_execution": {"engine_parent_commit": "p"}})


# ------------------------------------------------------------------ source identity

REQUIRED_SOURCE = [
    "revision2_external/orchestrator.py", "revision2_external/broker_reconciliation.py",
    "revision2_external/protection_policy.py", "revision5/combined_cycle_runtime.py",
    "revision5/combined_cycle_store.py", "revision5/engine_b_management.py",
    "revision5/handoff_manager.py", "revision5/morning_recovery.py", "revision5/state_recovery.py",
    "scripts/run_r5_step5_candidate.py", "scripts/run_r5_paper_lifecycle.py",
    "revision5/step5_sealed_calibration_protocol_v2.json",
]


def test_source_identity_covers_dirty_and_untracked_modules_with_sha256():
    tree = c01.hash_source_tree(ROOT)
    for rel in REQUIRED_SOURCE:
        assert rel in tree["files"], rel
        assert tree["files"][rel] == sha(ROOT / rel), rel
    assert not any(rel.startswith(("tests/", "outputs/", "docs/")) for rel in tree["files"])
    assert c01.hash_source_tree(ROOT)["aggregate_sha256"] == tree["aggregate_sha256"]


def test_source_identity_records_git_state():
    identity = c01.capture_source_identity(ROOT)
    assert len(identity["git_head"]) == 40 and "git_branch" in identity and "git_dirty" in identity
    assert identity["includes_untracked_and_dirty_source"] is True


def test_source_tree_selection_and_change_detection(tmp_path):
    (tmp_path / "revision5").mkdir()
    (tmp_path / "revision5/__init__.py").write_text("")
    (tmp_path / "revision5/mod.py").write_text("a = 1\n")
    (tmp_path / "root_mod.py").write_text("b = 1\n")
    for excluded in ("outputs", "docs", "data", "tests"):
        (tmp_path / excluded).mkdir()
        (tmp_path / excluded / "ignored.py").write_text("z = 1\n")
    (tmp_path / "revision5/.env").write_text("SECRET=1\n")
    (tmp_path / "revision5/raw.csv").write_text("1,2\n")
    before = c01.hash_source_tree(tmp_path)
    assert set(before["files"]) == {"revision5/__init__.py", "revision5/mod.py", "root_mod.py"}
    (tmp_path / "outputs/ignored.py").write_text("z = 2\n")
    assert c01.hash_source_tree(tmp_path)["aggregate_sha256"] == before["aggregate_sha256"]
    (tmp_path / "revision5/new_untracked.py").write_text("c = 1\n")
    after = c01.hash_source_tree(tmp_path)
    assert after["aggregate_sha256"] != before["aggregate_sha256"]
    with pytest.raises(c01.LifecycleError, match="SOURCE_CHANGED_MIDRUN: revision5/new_untracked.py"):
        c01.require_source_unchanged({**before, "git_head": "h"}, {**after, "git_head": "h"})
    (tmp_path / "revision5/new_untracked.py").unlink()
    (tmp_path / "revision5/mod.py").write_text("a = 2\n")
    assert c01.hash_source_tree(tmp_path)["aggregate_sha256"] != before["aggregate_sha256"]


def test_midrun_source_change_fails_run_with_receipt(tmp_path):
    env = make_env(tmp_path)
    calls = []

    def snapshot(root):
        identity = c01.capture_source_identity(root)
        calls.append(1)
        if len(calls) > 1:
            identity = {**identity, "aggregate_sha256": "0" * 64}
        return identity

    code, manifest = c01.run_lifecycle(make_args(env), executor=lambda *a, **k: {}, snapshot_fn=snapshot)
    assert code == c01.EXIT_FAILED and manifest["status"] == "FAILED"
    assert "SOURCE_CHANGED_MIDRUN" in manifest["error"]["message"]
    assert not (env.out / "result.json").exists() and not (env.out / "ledger.csv").exists()
    assert "FAILED" in (env.out / "SUMMARY.md").read_text()


def test_engine_error_is_failure_not_success(tmp_path):
    env = make_env(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("engine exploded")

    code, manifest = c01.run_lifecycle(make_args(env), executor=boom)
    assert code == c01.EXIT_FAILED and manifest["error"]["type"] == "RuntimeError"
    assert not (env.out / "result.json").exists()
    assert json.loads((env.out / "run_manifest.json").read_text())["status"] == "FAILED"


def test_critical_modules_load_from_the_actual_checkout():
    report = c01.critical_module_report(ROOT)
    assert set(report) == set(c01.CRITICAL_MODULES)
    for entry in report.values():
        assert Path(entry["path"]).is_relative_to(ROOT) and entry["sha256"] == sha(entry["path"])


def test_alternate_code_root_refused(tmp_path):
    with pytest.raises(c01.LifecycleError):
        c01.run_lifecycle(SimpleNamespace(), code_root=tmp_path)


# ------------------------------------------------------------------ integration through runner + execute_block

def test_integration_titan_fixture_through_runner_and_worker(tmp_path):
    env = make_env(tmp_path)
    protocol = json.loads(env.protocol.read_text())
    block = protocol["sampling_plan"]["stage_b"][0]
    params = json.loads(PARAMS.read_text())
    original_loader = worker.prepare_block
    try:
        worker.prepare_block = loader_substitute
        baseline = worker.execute_block(ROOT, protocol, block, params)
    finally:
        worker.prepare_block = original_loader

    code, manifest = c01.run_lifecycle(make_args(env), _test_loader_substitute=loader_substitute)
    assert worker.prepare_block is original_loader
    assert code == c01.EXIT_OK and manifest["status"] == "COMPLETED", manifest.get("error")

    out = env.out
    for name in ("result.json", "ledger.csv", "ledger.json", "run_manifest.json", "SUMMARY.md", c01.WAL_NAME):
        assert (out / name).is_file(), name
    result = json.loads((out / "result.json").read_text())
    wired = result["worker_result"]
    assert wired["trades"] == json.loads(json.dumps(baseline["trades"]))
    assert len(wired["trades"]) == 1 and wired["trades"][0]["side"] == "SELL"
    assert wired["metrics"]["net_pnl"] == baseline["metrics"]["net_pnl"]
    assert wired["block_fingerprint"] != baseline["block_fingerprint"]
    assert "combined_cycle_runtime" not in baseline
    assert wired["combined_cycle_runtime"]["live_admissions"] is False
    assert wired["combined_cycle_runtime"]["runtime"]["end_of_run_disposition"] == "CLOSE"

    summary = result["summary"]
    assert summary["net_pnl"] == baseline["metrics"]["net_pnl"]
    assert summary["gross_pnl"] == baseline["metrics"]["gross_pnl"]
    assert summary["trades_by_side"] == {"BUY": 0, "SELL": 1}
    assert summary["total_costs"] == pytest.approx(sum(t["costs"] for t in baseline["trades"]))
    assert summary["mtm_max_drawdown_percent"] == pytest.approx(
        baseline["metrics"]["mtm_max_drawdown_fraction"] * 100.0)
    assert summary["id_counts"] == c01.UNAVAILABLE and summary["gate12_counts"] == c01.UNAVAILABLE
    assert summary["fills_by_side"] == c01.UNAVAILABLE

    ledger = json.loads((out / "ledger.json").read_text())
    assert ledger == wired["trades"]
    header = (out / "ledger.csv").read_text().splitlines()[0].split(",")
    assert {"symbol", "side", "net_pnl", "costs"} <= set(header)

    saved = json.loads((out / "run_manifest.json").read_text())
    assert saved["classification"] == c01.CLASSIFICATION
    assert saved["labels"] == {"experimental": True, "sealed_candidate_evaluation": False, "live": False,
                               "loader_substituted": True, "actual48_certified": False,
                               "stage": "B", "block": 1}
    assert saved["universe"]["symbol_count"] == 48 and saved["universe"]["executed_symbols"] == ["TITAN"]
    assert saved["engine_identity"]["sealed_source_claim"] is False
    assert saved["engine_identity"]["status"] in ("NO_DRIFT_VS_PARENT", "ENGINE_PARENT_DRIFT_RECORDED")
    assert saved["source_identity"]["aggregate_sha256"] == saved["source_identity_after"]["aggregate_sha256"]
    assert saved["source_identity"]["aggregate_sha256"] == c01.hash_source_tree(ROOT)["aggregate_sha256"]
    assert saved["protocol"]["sha256"] == env.sha and saved["params"]["sha256"] == sha(PARAMS)
    assert saved["data_view"]["separate_roots"] is True
    assert set(saved["critical_modules"]) == set(c01.CRITICAL_MODULES)
    assert saved["wal"]["exists"] and saved["wal"]["sha256"] == sha(out / c01.WAL_NAME)
    text = (out / "SUMMARY.md").read_text()
    assert "no live readiness" in text and "UNAVAILABLE_FROM_WORKER_RESULT" in text

    # Default worker behaviour is untouched by the runner: same fingerprint, same signature default.
    try:
        worker.prepare_block = loader_substitute
        again = worker.execute_block(ROOT, protocol, block, params)
    finally:
        worker.prepare_block = original_loader
    assert again["block_fingerprint"] == baseline["block_fingerprint"]
    assert inspect.signature(worker.execute_block).parameters["combined_cycle_state_path"].default is None

    # A second run into the same output directory is refused and leaves the WAL byte-identical.
    wal_hash = sha(out / c01.WAL_NAME)
    with pytest.raises(c01.LifecycleError):
        c01.run_lifecycle(make_args(env), _test_loader_substitute=loader_substitute)
    assert sha(out / c01.WAL_NAME) == wal_hash


def test_real_entry_refuses_altered_sampling_protocol_even_with_matching_supplied_hash(tmp_path):
    env = make_env(tmp_path)
    code, manifest = c01.run_lifecycle(make_args(env))
    assert code == c01.EXIT_FAILED
    assert 'fixed v2 sampling/holdout protocol' in manifest['error']['message']
    assert not (env.out / c01.WAL_NAME).exists()


def test_interrupted_execution_leaves_running_receipt_without_success_outputs(tmp_path):
    env = make_env(tmp_path)
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt('simulated termination')
    with pytest.raises(KeyboardInterrupt):
        c01.run_lifecycle(make_args(env), executor=interrupted)
    receipt = json.loads((env.out / 'run_manifest.json').read_text())
    assert receipt['status'] == 'RUNNING'
    assert not (env.out / 'result.json').exists()
    assert not (env.out / 'ledger.json').exists()


def test_data_view_uses_current_source_manifest_when_stock_data_checkout_lacks_it(tmp_path):
    stock = tmp_path / 'physical_stock_root'
    stock.mkdir()
    grid = tmp_path / 'grid_root'
    (grid / 'local_workspace').mkdir(parents=True)
    view = tmp_path / 'view'
    view.mkdir()
    c01.build_data_view(stock, grid, view)
    assert (view / 'revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json').resolve() == (ROOT / 'revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json').resolve()
    assert (view / 'local_workspace').resolve() == (grid / 'local_workspace').resolve()

@pytest.mark.parametrize('option',['--fleet-loading','--feedback-compaction'])
def test_prototype_options_require_acceptance(tmp_path,option):
    env=make_env(tmp_path)
    args=make_args(env)
    setattr(args,option[2:].replace('-','_'),True)
    with pytest.raises(c01.LifecycleError,match='require --acceptance'):
        c01.run_lifecycle(args)
    assert not env.out.exists()

def test_acceptance_options_parse_without_live_switch(tmp_path):
    env=make_env(tmp_path)
    args=make_args(env)
    assert not args.acceptance and not args.fleet_loading and not args.feedback_compaction
    parsed=c01.build_parser().parse_args(['--mode',c01.MODE,'--expected-protocol-sha',env.sha,'--params',str(PARAMS),'--stock-root',str(env.stock_root),'--grid-root',str(env.grid_root),'--output-dir',str(env.out),'--acceptance','--fleet-loading','--feedback-compaction'])
    assert parsed.acceptance and parsed.fleet_loading and parsed.feedback_compaction

def test_acceptance_factory_cli_seam_real_fixture_summary(tmp_path):
    env=make_env(tmp_path)
    args=make_args(env); args.acceptance=True
    code,manifest=c01.run_lifecycle(args,_test_loader_substitute=loader_substitute)
    assert code==0, manifest.get('error')
    summary=manifest['summary']
    assert summary['id_counts']!=c01.UNAVAILABLE
    assert summary['holding_duration_bars']['samples']==1
    assert summary['gross_r_samples']==1
    assert summary['fees_and_friction_total']==summary['total_costs']
    assert (env.out/'acceptance/native_report.json').is_file()
    assert not manifest['labels']['actual48_certified']

def test_acceptance_cannot_bypass_fixed_protocol_hash(tmp_path):
    env=make_env(tmp_path)
    args=make_args(env); args.acceptance=True
    code,manifest=c01.run_lifecycle(args)
    assert code==c01.EXIT_FAILED
    assert 'fixed v2 sampling/holdout protocol' in manifest['error']['message']
    assert not (env.out/'paper_lifecycle_wal.sqlite3').exists()
