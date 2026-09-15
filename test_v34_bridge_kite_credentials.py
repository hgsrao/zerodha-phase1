"""Tests for v34_bridge_kite_credentials.py.

Moved here (not duplicated) from test_v34_bridge_trigger_main.py once
v34_bridge_runner_main.py became a second real caller of the identical
credential-loading logic.
"""

import sys
import types

import pytest

import v34_bridge_kite_credentials as creds


class FakeKiteConnect:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.access_token = None

    def set_access_token(self, token):
        self.access_token = token


class TestBuildKiteClientFromEnv:
    def test_missing_api_key_is_fail_closed(self, monkeypatch):
        monkeypatch.delenv("KITE_API_KEY", raising=False)
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "some-token")
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_API_KEY"):
            creds.build_kite_client_from_env()

    def test_missing_access_token_is_fail_closed(self, monkeypatch):
        monkeypatch.setenv("KITE_API_KEY", "some-key")
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_ACCESS_TOKEN"):
            creds.build_kite_client_from_env()

    def test_happy_path_constructs_and_authenticates_the_client(self, monkeypatch):
        monkeypatch.setenv("KITE_API_KEY", "some-key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "some-token")

        fake_module = types.ModuleType("kiteconnect")
        fake_module.KiteConnect = FakeKiteConnect
        monkeypatch.setitem(sys.modules, "kiteconnect", fake_module)

        kite = creds.build_kite_client_from_env()
        assert kite.api_key == "some-key"
        assert kite.access_token == "some-token"
