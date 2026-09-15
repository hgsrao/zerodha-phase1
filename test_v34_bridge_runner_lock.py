"""Tests for v34_bridge_runner_lock.py.

Two layers: same-process unit tests (state errors, normal
release/reacquire, metadata shape, corrupted-metadata-degrades-
diagnostics-only), and real cross-process tests using an actual child
process holding the OS lock - the only way to genuinely prove "second
runner refused" and "OS auto-releases on abnormal death" rather than
assuming it.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from v34_bridge_runner_lock import RunnerLock, RunnerLockHeldError, RunnerLockStateError

_CHILD_HOLDER_SCRIPT = """
import sys
import time
sys.path.insert(0, {module_dir!r})
from v34_bridge_runner_lock import RunnerLock

lock = RunnerLock(sys.argv[1])
lock.acquire()
print("ACQUIRED", flush=True)
time.sleep(60)
"""


def _start_child_holder(tmp_path: Path, lock_path: Path) -> subprocess.Popen:
    script_path = tmp_path / "_child_holder.py"
    script_path.write_text(
        _CHILD_HOLDER_SCRIPT.format(module_dir=str(Path(__file__).parent)),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(script_path), str(lock_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    line = proc.stdout.readline()
    assert line.strip() == "ACQUIRED", f"child holder failed to acquire: stderr={proc.stderr.read()}"
    return proc


class TestSameProcessUnit:
    def test_acquire_then_release_then_reacquire_succeeds(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.acquire()
        lock.release()
        lock2 = RunnerLock(path)
        lock2.acquire()  # must not raise - normal release freed the OS lock
        lock2.release()

    def test_double_acquire_on_the_same_instance_is_a_state_error(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.acquire()
        with pytest.raises(RunnerLockStateError):
            lock.acquire()
        lock.release()

    def test_repeated_release_is_idempotent(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.acquire()
        lock.release()
        lock.release()  # must not raise
        lock.release()  # still must not raise

    def test_release_without_ever_acquiring_is_a_no_op(self, tmp_path):
        lock = RunnerLock(tmp_path / "runner.lock")
        lock.release()  # must not raise

    def test_metadata_is_written_and_readable_after_acquire(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.acquire()
        meta = lock.read_metadata()
        assert meta["pid"] == lock.pid
        assert meta["instance_id"] == lock.instance_id
        assert "acquired_at" in meta
        lock.release()

    def test_a_fresh_instance_gets_a_different_instance_id(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock1 = RunnerLock(path)
        lock1.acquire()
        lock1.release()
        lock2 = RunnerLock(path)
        assert lock2.instance_id != lock1.instance_id  # PID alone is never the identity
        lock2.acquire()
        lock2.release()

    def test_reacquire_overwrites_metadata_with_the_new_holders_identity(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock1 = RunnerLock(path)
        lock1.acquire()
        first_instance_id = lock1.instance_id
        lock1.release()

        lock2 = RunnerLock(path)
        lock2.acquire()
        meta = lock2.read_metadata()
        assert meta["instance_id"] != first_instance_id
        assert meta["instance_id"] == lock2.instance_id
        lock2.release()


class TestCorruptedMetadataDegradesDiagnosticsOnlyNeverTheDecision:
    def test_unreadable_metadata_falls_back_to_a_generic_refusal_message(self, tmp_path, monkeypatch):
        # The OS lock function is the one thing allowed to decide
        # acquire()/refuse - simulate it refusing, independent of
        # whatever garbage is sitting in the metadata file.
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.metadata_path.write_text("{not valid json at all", encoding="utf-8")

        def fake_refusal(fh):
            raise RunnerLockHeldError("simulated: OS lock already held")
        monkeypatch.setattr("v34_bridge_runner_lock._lock_exclusive_nonblocking", fake_refusal)

        with pytest.raises(RunnerLockHeldError, match="diagnostic metadata unavailable"):
            lock.acquire()

    def test_valid_metadata_is_included_in_the_refusal_message(self, tmp_path, monkeypatch):
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.metadata_path.write_text(json.dumps({"pid": 999999, "instance_id": "abc-123", "acquired_at": "2026-08-15T00:00:00+00:00"}), encoding="utf-8")

        def fake_refusal(fh):
            raise RunnerLockHeldError("simulated: OS lock already held")
        monkeypatch.setattr("v34_bridge_runner_lock._lock_exclusive_nonblocking", fake_refusal)

        with pytest.raises(RunnerLockHeldError, match="999999"):
            lock.acquire()

    def test_read_metadata_never_raises_on_garbage_content(self, tmp_path):
        path = tmp_path / "runner.lock"
        lock = RunnerLock(path)
        lock.metadata_path.write_text("not json { at all", encoding="utf-8")
        assert lock.read_metadata() is None  # degrades to None, never raises

    def test_read_metadata_returns_none_for_a_missing_file(self, tmp_path):
        lock = RunnerLock(tmp_path / "does_not_exist.lock")
        assert lock.read_metadata() is None


class TestRealCrossProcessLocking:
    """The only tests in this file that actually prove the OS-level
    guarantee this module depends on, rather than assuming it - a real
    child process holds the lock; this process attempts to acquire it."""

    def test_second_runner_acquisition_is_refused_while_the_first_is_alive(self, tmp_path):
        lock_path = tmp_path / "runner.lock"
        child = _start_child_holder(tmp_path, lock_path)
        try:
            lock = RunnerLock(lock_path)
            with pytest.raises(RunnerLockHeldError):
                lock.acquire()
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_refusal_message_reports_the_holders_diagnostic_metadata(self, tmp_path):
        lock_path = tmp_path / "runner.lock"
        child = _start_child_holder(tmp_path, lock_path)
        try:
            lock = RunnerLock(lock_path)
            with pytest.raises(RunnerLockHeldError, match=str(child.pid)):
                lock.acquire()
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_stale_lock_after_abnormal_process_death_is_auto_released_by_the_os(self, tmp_path):
        # No release() call happens anywhere in this test for the child -
        # it is hard-killed, exactly like SIGKILL/OOM-kill/power-loss-
        # then-restart from the OS's point of view. Proving a fresh
        # acquire() succeeds afterward proves the OS released the lock
        # itself; no PID-liveness heuristic was consulted anywhere in
        # this module to reach that conclusion.
        lock_path = tmp_path / "runner.lock"
        child = _start_child_holder(tmp_path, lock_path)
        dead_pid = child.pid
        child.kill()  # hard kill - no cleanup code in the child ever runs
        child.wait(timeout=10)

        lock = RunnerLock(lock_path)
        # A brief retry loop only to absorb OS scheduling delay in
        # releasing kernel resources after process teardown - not a
        # staleness heuristic of this module's own.
        last_exc = None
        for _ in range(50):
            try:
                lock.acquire()
                break
            except RunnerLockHeldError as exc:
                last_exc = exc
                time.sleep(0.1)
        else:
            pytest.fail(f"lock was never released after the holder (pid={dead_pid}) was hard-killed: {last_exc}")
        lock.release()

    def test_crash_without_explicit_release_still_frees_the_lock_for_reacquisition(self, tmp_path):
        # Same guarantee as above, phrased as the user's own named
        # adversarial scenario: "crash without explicit release."
        lock_path = tmp_path / "runner.lock"
        child = _start_child_holder(tmp_path, lock_path)
        child.kill()
        child.wait(timeout=10)

        lock = RunnerLock(lock_path)
        for _ in range(50):
            try:
                lock.acquire()
                lock.release()
                return
            except RunnerLockHeldError:
                time.sleep(0.1)
        pytest.fail("lock was never freed after the holder crashed without releasing")


class TestNoBrokerCallsAnywhereInThisModule:
    def test_module_source_contains_no_broker_credential_or_order_references(self):
        source = Path("v34_bridge_runner_lock.py").read_text(encoding="utf-8").lower()
        forbidden = [
            "kite", "broker", "access_token", "api_key", "api_secret",
            "place_order", "modify_order", "cancel_order", "request_token",
        ]
        hits = [token for token in forbidden if token in source]
        assert not hits, f"unexpected broker/credential references found in the lock module: {hits}"
