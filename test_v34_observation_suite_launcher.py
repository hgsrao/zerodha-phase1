from pathlib import Path

import pytest

import observation_suite_launcher as launcher


def make_sources(base: Path, runner_guard: str = launcher.LIVE_FALSE_GUARD):
    (base / launcher.RUNNER).write_text(runner_guard + "\n", encoding="utf-8")
    (base / launcher.COLLECTOR).write_text("reader.quote([])\n", encoding="utf-8")
    (base / launcher.DASHBOARD).write_text("READ_ONLY = True\n", encoding="utf-8")
    (base / "shadow_strategy_evaluator.py").write_text("PURE = True\n", encoding="utf-8")


def test_validate_sources_accepts_false_guard_and_read_only_collector(tmp_path: Path):
    make_sources(tmp_path)
    launcher.validate_sources(tmp_path)


def test_validate_sources_rejects_missing_false_guard(tmp_path: Path):
    make_sources(tmp_path, "LIVE_TRADING_ENABLED = True")
    with pytest.raises(RuntimeError, match="exact LIVE_TRADING_ENABLED = False"):
        launcher.validate_sources(tmp_path)


def test_validate_sources_rejects_collector_order_operation(tmp_path: Path):
    make_sources(tmp_path)
    (tmp_path / launcher.COLLECTOR).write_text("broker.place_order()\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="forbidden trading operation"):
        launcher.validate_sources(tmp_path)


def test_child_environment_adds_token_without_mutating_parent(monkeypatch):
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    result = launcher.child_environment("secret-in-memory")
    assert result["KITE_ACCESS_TOKEN"] == "secret-in-memory"
    assert "KITE_ACCESS_TOKEN" not in launcher.os.environ


def test_request_token_environment_is_removed_by_source_contract():
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert 'os.environ.pop("KITE_REQUEST_TOKEN", "")' in source


def test_interactive_fallback_uses_normal_visible_input():
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert 'input("Paste fresh Zerodha request token or redirect URL: ")' in source
    assert "getpass" not in source


def test_launcher_source_does_not_print_or_persist_token():
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "print(access_token" not in source
    assert "write_text(access_token" not in source


class CleanKite:
    def positions(self):
        return {"net": []}

    def orders(self):
        return []


class DirtyKite(CleanKite):
    def positions(self):
        return {"net": [{"tradingsymbol": "ABC", "product": "MIS", "quantity": 1}]}


class CncHoldingsKite(CleanKite):
    def positions(self):
        return {"net": [{"tradingsymbol": "NIFTYBEES", "product": "CNC", "quantity": 5}]}


def test_broker_clean_preflight_passes_only_clean_account():
    assert launcher.broker_clean_preflight(CleanKite())["broker_clean"] is True
    cnc = launcher.broker_clean_preflight(CncHoldingsKite())
    assert cnc["broker_clean"] is True
    assert cnc["ignored_cnc_holdings"][0]["symbol"] == "NIFTYBEES"
    with pytest.raises(RuntimeError, match="BROKER_STATE") as caught:
        launcher.broker_clean_preflight(DirtyKite())
    assert '"symbol": "ABC"' in str(caught.value)


def test_pin_new_session_writes_manifest(tmp_path: Path):
    sessions = tmp_path / "session_logs" / "new"
    sessions.mkdir(parents=True)
    log = sessions / "bot_production.log"
    log.write_text("started", encoding="utf-8")
    selected = launcher.pin_new_session(tmp_path, set(), 123)
    assert selected == log
    manifest = launcher.json.loads(
        (tmp_path / "observation_suite_runtime.json").read_text(encoding="utf-8")
    )
    assert manifest["runner_pid"] == 123
    assert manifest["live_trading_enabled"] is False
