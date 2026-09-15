"""Tests for v34_bridge_gate3_startup_validation_probe.py.

This script exists specifically to be run with REAL Kite credentials and
a real DP_CHARGE_PER_SYMBOL against the real production entrypoint - these
tests never supply any of that. They prove the script's own audit-record
reading/reporting logic offline, by monkeypatching v34_bridge_runner_
main._build_engine() itself (the exact function this script calls),
exactly like test_v34_bridge_runner_main.py's own tests do for other
scenarios.
"""

import json

import pytest

import v34_bridge_gate3_startup_validation_probe as gate3_probe
import v34_bridge_runner_main as runner_main
from v34_bridge_runner_startup import ProductionRunnerPaths


class FakeEngine:
    def __init__(self, *, halted=False, reason=None, status_value="RUNNING"):
        self.terminator = _FakeTerminator(halted=halted, reason=reason)
        self.state = _FakeState(status_value=status_value)
        self.lock_provider = _FakeLockProvider()


class _FakeTerminator:
    def __init__(self, *, halted, reason):
        self.halted = halted
        self.reason = reason


class _FakeState:
    def __init__(self, *, status_value):
        self.status = _FakeStatus(status_value)


class _FakeStatus:
    def __init__(self, value):
        self.value = value


class _FakeLockProvider:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


def _write_dp_charge_configured(tmp_path, monkeypatch, *, configured=True, amount="15.34"):
    monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
    paths = ProductionRunnerPaths(data_dir=tmp_path / "runner_data")
    paths.audit_log.parent.mkdir(parents=True, exist_ok=True)
    record = {"event_type": "DP_CHARGE_CONFIGURED", "fields": {"configured": configured, "amount": amount}}
    paths.audit_log.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return paths


class TestHappyPath:
    def test_returns_zero_and_reports_the_configured_amount(self, tmp_path, monkeypatch):
        _write_dp_charge_configured(tmp_path, monkeypatch, configured=True, amount="15.34")
        fake_engine = FakeEngine(halted=False)
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        assert gate3_probe.main() == 0
        assert fake_engine.lock_provider.released is True

    def test_a_halted_engine_still_returns_zero_if_dp_charge_was_configured(self, tmp_path, monkeypatch):
        # A halt is a separate, honest outcome of startup reconciliation -
        # this script's own job is only to verify the configuration
        # chain, not to re-litigate whether the engine happened to halt.
        _write_dp_charge_configured(tmp_path, monkeypatch, configured=True, amount="15.05")
        fake_engine = FakeEngine(halted=True, reason="some unrelated reconciliation issue", status_value="RECONCILIATION_HALT")
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        assert gate3_probe.main() == 0
        assert fake_engine.lock_provider.released is True


class TestFailurePaths:
    def test_build_engine_raising_is_reported_and_returns_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
        def boom(**kwargs):
            raise RuntimeError("FAIL_CLOSED: DP_CHARGE_PER_SYMBOL environment variable is missing or empty.")
        monkeypatch.setattr(runner_main, "_build_engine", boom)

        assert gate3_probe.main() == 1

    def test_missing_audit_record_returns_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
        paths = ProductionRunnerPaths(data_dir=tmp_path / "runner_data")
        paths.audit_log.parent.mkdir(parents=True, exist_ok=True)
        paths.audit_log.write_text('{"event_type": "SOME_OTHER_EVENT", "fields": {}}\n', encoding="utf-8")
        fake_engine = FakeEngine(halted=False)
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        assert gate3_probe.main() == 1
        assert fake_engine.lock_provider.released is True  # still released even on this failure

    def test_configured_false_returns_one(self, tmp_path, monkeypatch):
        _write_dp_charge_configured(tmp_path, monkeypatch, configured=False, amount=None)
        fake_engine = FakeEngine(halted=False)
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        assert gate3_probe.main() == 1
        assert fake_engine.lock_provider.released is True

    def test_missing_audit_file_entirely_returns_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
        fake_engine = FakeEngine(halted=False)
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        assert gate3_probe.main() == 1
        assert fake_engine.lock_provider.released is True


class TestLockAlwaysReleased:
    def test_the_lock_is_released_even_when_the_audit_check_itself_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_DATA_DIR", str(tmp_path / "runner_data"))
        fake_engine = FakeEngine(halted=False)
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        gate3_probe.main()
        assert fake_engine.lock_provider.released is True


class TestNeverPrintsCredentials:
    def test_stdout_never_contains_credential_shaped_values(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("KITE_API_KEY", "fake-key-never-printed")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "fake-token-never-printed")
        _write_dp_charge_configured(tmp_path, monkeypatch, configured=True, amount="15.34")
        fake_engine = FakeEngine(halted=False)
        monkeypatch.setattr(runner_main, "_build_engine", lambda **kwargs: fake_engine)

        gate3_probe.main()
        out = capsys.readouterr().out
        assert "fake-key-never-printed" not in out
        assert "fake-token-never-printed" not in out
