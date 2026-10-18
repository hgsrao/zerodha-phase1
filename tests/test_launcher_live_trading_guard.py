"""Fail-closed LIVE_TRADING_ENABLED enforcement for every Engine A/B/C and R6/R7 launcher.

Offline: no engine is constructed and no data is read. Refusals happen before argument parsing."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.live_trading_guard import live_trading_violation, require_live_trading_disabled

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = [
    "scripts/run_engine_a_reference.py",
    "scripts/run_engine_b_nautilus.py",
    "scripts/run_engine_c_backtrader.py",
    "revision6_avr/run_r6_native_replay.py",
    "revision7_avr/run_r7_replay.py",
]
BAD_VALUES = ["True", "true", "TRUE", "1", "0", "false", "FALSE", "no", "", " ", " False", "False ", "False\n", "\tFalse",
              "Fals", "Falsee", "None", "off", "F", "\x00", "False\x00", "ＦＡＬＳＥ", "'False'", '"False"']


def test_exactly_false_is_the_only_permitted_value():
    assert live_trading_violation({"LIVE_TRADING_ENABLED": "False"}) is None
    require_live_trading_disabled({"LIVE_TRADING_ENABLED": "False"})


def test_missing_value_refuses():
    assert "not set" in live_trading_violation({})
    with pytest.raises(SystemExit) as exc:
        require_live_trading_disabled({})
    assert exc.value.code == 78


@pytest.mark.parametrize("value", BAD_VALUES)
def test_every_other_value_refuses(value):
    assert live_trading_violation({"LIVE_TRADING_ENABLED": value}) is not None
    with pytest.raises(SystemExit) as exc:
        require_live_trading_disabled({"LIVE_TRADING_ENABLED": value})
    assert exc.value.code == 78


def test_guard_reads_but_never_sets_the_environment(monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    with pytest.raises(SystemExit):
        require_live_trading_disabled()
    assert "LIVE_TRADING_ENABLED" not in os.environ


def _run(script, env_extra):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", **env_extra}
    return subprocess.run([sys.executable, str(ROOT / script), "--help"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("script", LAUNCHERS)
def test_launcher_refuses_when_unset(script):
    r = _run(script, {})
    assert r.returncode == 78 and "REFUSED" in r.stderr and "not set" in r.stderr


@pytest.mark.parametrize("script", LAUNCHERS)
@pytest.mark.parametrize("value", ["True", "false", "FALSE", "1", "", "False "])
def test_launcher_refuses_bad_values(script, value):
    r = _run(script, {"LIVE_TRADING_ENABLED": value})
    assert r.returncode == 78 and "REFUSED" in r.stderr


@pytest.mark.parametrize("script", LAUNCHERS)
def test_launcher_proceeds_past_guard_only_for_exact_false(script):
    r = _run(script, {"LIVE_TRADING_ENABLED": "False"})
    assert r.returncode == 0 and "REFUSED" not in r.stderr          # --help is served after the guard


@pytest.mark.parametrize("script", LAUNCHERS)
def test_launchers_do_not_set_or_default_the_flag(script):
    text = (ROOT / script).read_text()
    import re
    assert not re.search(r'os\.environ\[["\']LIVE_TRADING_ENABLED["\']\]\s*=(?!=)', text)
    assert 'setdefault("LIVE_TRADING_ENABLED"' not in text
    assert "require_live_trading_disabled()" in text
