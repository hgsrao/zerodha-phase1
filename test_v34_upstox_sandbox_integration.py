import json
from pathlib import Path

import pytest

import upstox_sandbox_integration as sbx


BASE = Path(__file__).resolve().parent

# Isolation fix (2026-08-14): same root cause as test_v34_gate4_one_shot_safety.py -
# recording_path/production_state_path used to point directly at the live,
# currently-running shadow_strategy_telemetry.json / bot_state_v34.json, so
# a test asserting a specific hardcoded price broke once the real read-only
# collectors started updating those files with real market data today.
# runner_path is deliberately NOT isolated - checking the real production
# runner's actual safety-flag source text is the intended check there.


def _write_fixture_recording(path: Path, *, symbol="HDFCBANK", last_price=725.0, decision="BLOCK"):
    path.write_text(json.dumps({
        "mode": "READ_ONLY_SHADOW",
        "source": "KITE_QUOTE_READ_ONLY",
        "decision": decision,
        "observed_at_utc": "2026-08-14T00:00:00+00:00",
        "candidate": {"symbol": symbol, "last_price": last_price},
    }), encoding="utf-8")


def _write_fixture_state(path: Path):
    path.write_text(json.dumps({"status": "FLAT"}), encoding="utf-8")


def paths(tmp_path):
    return {
        "recording_path": tmp_path / "shadow_strategy_telemetry.json",
        "production_state_path": tmp_path / "bot_state_v34.json",
        "runner_path": BASE / "run_production_p01d_candidate.py",
    }


def test_real_recorded_block_produces_no_order_and_no_network(tmp_path):
    p = paths(tmp_path)
    _write_fixture_recording(p["recording_path"])
    _write_fixture_state(p["production_state_path"])

    result = sbx.run(
        **p, instrument_token="", execute=False, confirmation="",
        simulate_authorized=False,
    )
    assert result["recording"]["decision"] == "BLOCK"
    assert result["order_generated"] is False
    assert result["network_calls"] == 0
    assert result["production_state_unchanged"] is True


def test_simulated_authorization_maps_recorded_price_to_quantity_one(tmp_path):
    p = paths(tmp_path)
    _write_fixture_recording(p["recording_path"], symbol="HDFCBANK", last_price=725.0)
    _write_fixture_state(p["production_state_path"])

    result = sbx.run(
        **p, instrument_token="NSE_EQ|TEST_HDFCBANK", execute=False,
        confirmation="", simulate_authorized=True,
    )
    assert result["order_payload"]["quantity"] == 1
    assert result["order_payload"]["price"] == 725.0
    assert result["order_payload"]["order_type"] == "LIMIT"
    assert result["network_calls"] == 0


def test_execution_requires_exact_confirmation(tmp_path, monkeypatch):
    p = paths(tmp_path)
    _write_fixture_recording(p["recording_path"])
    _write_fixture_state(p["production_state_path"])
    monkeypatch.setenv(sbx.TOKEN_ENV, "sandbox-token")
    with pytest.raises(sbx.SandboxSafetyError, match="confirmation"):
        sbx.run(
            **p, instrument_token="NSE_EQ|TEST", execute=True,
            confirmation="yes", simulate_authorized=True,
        )


def test_client_rejects_production_and_non_upstox_hosts():
    for root in ("https://api.upstox.com", "https://api.kite.trade", "http://sandbox.upstox.com"):
        with pytest.raises(sbx.SandboxSafetyError, match="endpoint"):
            sbx.UpstoxSandboxClient("token", lambda *args: {}, root=root)


def test_fake_sandbox_place_modify_cancel_lifecycle(tmp_path, monkeypatch):
    p = paths(tmp_path)
    _write_fixture_recording(p["recording_path"])
    _write_fixture_state(p["production_state_path"])
    calls = []

    def fake_transport(method, url, headers, body):
        calls.append((method, url, json.loads(body) if body else None))
        return {"status": "success", "data": {"order_id": "SBX-1"}}

    monkeypatch.setenv(sbx.TOKEN_ENV, "sandbox-token")
    result = sbx.run(
        **p, instrument_token="NSE_EQ|TEST", execute=True,
        confirmation=sbx.EXECUTION_CONFIRMATION, simulate_authorized=True,
        transport=fake_transport,
    )
    assert [call[0] for call in calls] == ["POST", "PUT", "DELETE"]
    assert all(call[1].startswith("https://sandbox.upstox.com/v3/order/") for call in calls)
    assert calls[0][2]["quantity"] == 1
    assert result["network_calls"] == 3
    assert result["production_state_unchanged"] is True


def test_source_cannot_import_or_start_zerodha_production_runner():
    source = Path(sbx.__file__).read_text(encoding="utf-8").lower()
    assert "run_production_p01d_candidate import" not in source
    assert "kiteconnect" not in source
    assert "live_trading_enabled = true" not in source
