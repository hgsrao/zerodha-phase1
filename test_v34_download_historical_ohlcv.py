import pytest
from kiteconnect import KiteConnect

import download_historical_ohlcv as dl


class TestAuthenticate:
    def test_reuses_an_existing_access_token_without_exchanging_anything(self, monkeypatch):
        monkeypatch.setenv("KITE_API_KEY", "fake_key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "fake_access_token")
        monkeypatch.delenv("KITE_API_SECRET", raising=False)

        def _forbidden(*args, **kwargs):
            raise AssertionError("must not exchange a request token when an access token is already set")
        monkeypatch.setattr(KiteConnect, "generate_session", _forbidden)

        kite = dl.authenticate(request_token=None)
        assert kite.access_token == "fake_access_token"

    def test_raises_when_neither_access_token_nor_request_token_available(self, monkeypatch):
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("KITE_API_KEY", "fake_key")
        monkeypatch.setenv("KITE_API_SECRET", "fake_secret")

        with pytest.raises(RuntimeError, match="Provide --request-token"):
            dl.authenticate(request_token=None)

    def test_falls_back_to_exchanging_a_request_token(self, monkeypatch):
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("KITE_API_KEY", "fake_key")
        monkeypatch.setenv("KITE_API_SECRET", "fake_secret")

        calls = []

        def _fake_generate_session(self, request_token, api_secret):
            calls.append((request_token, api_secret))
            return {"access_token": "exchanged_token"}
        monkeypatch.setattr(KiteConnect, "generate_session", _fake_generate_session)

        kite = dl.authenticate(request_token="fresh_request_token")
        assert kite.access_token == "exchanged_token"
        assert calls == [("fresh_request_token", "fake_secret")]

    def test_missing_api_key_or_secret_fails_closed_during_exchange(self, monkeypatch):
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("KITE_API_KEY", raising=False)
        monkeypatch.delenv("KITE_API_SECRET", raising=False)

        with pytest.raises(RuntimeError, match="KITE_API_KEY and KITE_API_SECRET"):
            dl.authenticate(request_token="some_token")
