"""Tests for v34_bridge_trigger_main.py.

Both of this module's read-only gaps are now closed via
KiteReadOnlyClient - these tests exercise the real wiring against a fake
KiteConnect-shaped object (never a real account, never real credentials).
Credential-loading itself (FAIL_CLOSED on a missing KITE_API_KEY/
KITE_ACCESS_TOKEN) is tested once, in test_v34_bridge_kite_credentials.py
- not duplicated here now that both entrypoints share
v34_bridge_kite_credentials.build_kite_client_from_env().
"""

import pytest

import v34_bridge_trigger_main as trigger_main
from v34_bridge_kite_read_only_client import KiteReadOnlyClient


class FakeKiteConnect:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.access_token = None
        self.positions_response = {"net": [], "day": []}
        self.ltp_response = {}

    def set_access_token(self, token):
        self.access_token = token

    def positions(self):
        return self.positions_response

    def ltp(self, instruments):
        return self.ltp_response


class TestFetchLiveQuotes:
    def test_requests_the_entire_momentum_universe(self):
        from external_momentum_shadow import EXTERNAL_UNIVERSE
        fake_kite = FakeKiteConnect()
        fake_kite.ltp_response = {f"NSE:{s}": {"last_price": 100.0} for s in EXTERNAL_UNIVERSE}
        client = KiteReadOnlyClient(fake_kite)

        quotes = trigger_main._fetch_live_quotes(client)
        assert set(quotes) == {f"NSE:{s}" for s in EXTERNAL_UNIVERSE}


class TestFetchCurrentPortfolio:
    def test_filters_to_cnc_only(self):
        fake_kite = FakeKiteConnect()
        fake_kite.positions_response = {
            "net": [
                {"tradingsymbol": "RELIANCE", "product": "CNC", "quantity": 50},
                {"tradingsymbol": "INFY", "product": "MIS", "quantity": 10},  # not CNC - must be ignored
            ],
            "day": [],
        }
        client = KiteReadOnlyClient(fake_kite)

        current = trigger_main._fetch_current_portfolio(client)
        assert current == {"RELIANCE": 50}

    def test_zero_quantity_cnc_positions_are_omitted(self):
        fake_kite = FakeKiteConnect()
        fake_kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "product": "CNC", "quantity": 0}], "day": []}
        client = KiteReadOnlyClient(fake_kite)

        assert trigger_main._fetch_current_portfolio(client) == {}

    def test_a_malformed_cnc_position_is_fail_closed(self):
        fake_kite = FakeKiteConnect()
        fake_kite.positions_response = {"net": [{"tradingsymbol": "RELIANCE", "product": "CNC"}], "day": []}  # no quantity
        client = KiteReadOnlyClient(fake_kite)

        with pytest.raises(RuntimeError, match="FAIL_CLOSED: malformed CNC position record"):
            trigger_main._fetch_current_portfolio(client)

    def test_no_cnc_positions_returns_an_empty_mapping(self):
        fake_kite = FakeKiteConnect()
        fake_kite.positions_response = {"net": [], "day": []}
        client = KiteReadOnlyClient(fake_kite)

        assert trigger_main._fetch_current_portfolio(client) == {}


class TestMainEndToEnd:
    def test_main_refuses_before_reaching_run_trigger_when_credentials_are_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path))
        monkeypatch.setenv("KITE_RATE_GOVERNOR_DIR", str(tmp_path / "governor"))  # EA1-R1 - required, not what this test is checking
        monkeypatch.delenv("KITE_API_KEY", raising=False)
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        called = []
        monkeypatch.setattr("v34_bridge_trigger.run_trigger", lambda **kwargs: called.append(kwargs))

        with pytest.raises(RuntimeError, match="FAIL_CLOSED"):
            trigger_main.main()
        assert called == []
        assert list(tmp_path.glob("*.json")) == []

    def test_main_writes_a_real_plan_file_end_to_end_against_a_fake_kite(self, monkeypatch, tmp_path):
        from external_momentum_shadow import EXTERNAL_UNIVERSE

        monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path))
        monkeypatch.setenv("KITE_RATE_GOVERNOR_DIR", str(tmp_path / "governor"))  # EA1-R1 - required
        monkeypatch.setenv("KITE_API_KEY", "some-key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "some-token")

        fake_kite = FakeKiteConnect()
        fake_kite.ltp_response = {f"NSE:{s}": {"last_price": 100.0 + i} for i, s in enumerate(EXTERNAL_UNIVERSE)}
        fake_kite.positions_response = {"net": [], "day": []}
        monkeypatch.setattr("v34_bridge_kite_credentials.build_kite_client_from_env", lambda: fake_kite)

        trigger_main.main()

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1


class TestKiteRateGovernorRequired:
    """EA1-R1: this script's own real Kite calls (ltp() over the whole
    universe, get_positions()) must be governed the same way the runner
    daemon's are - it runs concurrently with whichever runner shares
    this account's credentials."""

    def test_refuses_when_governor_dir_is_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path))
        monkeypatch.setenv("KITE_API_KEY", "some-key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "some-token")
        monkeypatch.delenv("KITE_RATE_GOVERNOR_DIR", raising=False)
        called = []
        monkeypatch.setattr("v34_bridge_trigger.run_trigger", lambda **kwargs: called.append(kwargs))

        with pytest.raises(RuntimeError, match="FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR"):
            trigger_main.main()
        assert called == []

    def test_a_real_governor_is_actually_used_for_the_ltp_call(self, monkeypatch, tmp_path):
        from external_momentum_shadow import EXTERNAL_UNIVERSE
        import json

        monkeypatch.setenv("PLAN_STORE_DIR", str(tmp_path / "plans"))
        gov_dir = tmp_path / "governor"
        monkeypatch.setenv("KITE_RATE_GOVERNOR_DIR", str(gov_dir))
        monkeypatch.setenv("KITE_API_KEY", "some-key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "some-token")

        fake_kite = FakeKiteConnect()
        fake_kite.ltp_response = {f"NSE:{s}": {"last_price": 100.0 + i} for i, s in enumerate(EXTERNAL_UNIVERSE)}
        fake_kite.positions_response = {"net": [], "day": []}
        monkeypatch.setattr("v34_bridge_kite_credentials.build_kite_client_from_env", lambda: fake_kite)

        trigger_main.main()

        ledger_path = gov_dir / "kite_rate_governor_ledger.json"
        assert ledger_path.exists()
        ledger = json.loads(ledger_path.read_text())
        # One ltp() call (whole universe, batched) + one get_positions() call.
        assert sum(len(v) for v in ledger.values()) == 2
