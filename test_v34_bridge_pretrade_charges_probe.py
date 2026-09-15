"""Tests for v34_bridge_pretrade_charges_probe.py.

This script exists specifically for use with REAL Kite credentials -
these tests never supply any; they prove the probe's own structural-
check logic against fake, offline responses (well-formed and several
malformed shapes), and that nothing credential-shaped ever reaches
stdout, regardless of outcome.
"""

import sys
import types

import v34_bridge_pretrade_charges_probe as probe


class FakeKiteConnect:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.access_token = None
        self.response = [{"charges": {"total": 0.58}}]
        self.raise_exc = None

    def set_access_token(self, token):
        self.access_token = token

    def get_virtual_contract_note(self, params):
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _install_fake_kite(monkeypatch, *, response=None, raise_exc=None):
    monkeypatch.setenv("KITE_API_KEY", "fake-key-never-printed")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "fake-token-never-printed")
    fake_module = types.ModuleType("kiteconnect")
    holder = {}

    def factory(api_key=None):
        instance = FakeKiteConnect(api_key=api_key)
        if response is not None:
            instance.response = response
        if raise_exc is not None:
            instance.raise_exc = raise_exc
        holder["instance"] = instance
        return instance

    fake_module.KiteConnect = factory
    monkeypatch.setitem(sys.modules, "kiteconnect", fake_module)
    return holder


class TestWellFormedResponse:
    def test_all_checks_pass(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": 0.5826968}}])
        checks = probe._run_probe()
        assert all(ok for _, ok in checks)

    def test_main_returns_exit_code_zero(self, monkeypatch, capsys):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": 0.58}}])
        assert probe.main() == 0


class TestMalformedResponses:
    def test_non_list_response_fails_the_list_check(self, monkeypatch):
        # This probe calls the raw SDK method directly (not through
        # KiteReadOnlyClient's own validation) precisely so this fact is
        # independently observable, not collapsed into a generic
        # "SDK call succeeded: False" outcome.
        _install_fake_kite(monkeypatch, response={"not": "a list"})
        checks = probe._run_probe()
        assert dict(checks)["SDK call succeeded"] is True
        assert dict(checks)["returned object is a list"] is False
        assert not all(ok for _, ok in checks)

    def test_wrong_length_fails_the_length_check(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": 1}}, {"charges": {"total": 2}}])
        checks = probe._run_probe()
        assert dict(checks)["returned object is a list"] is True
        assert dict(checks)["list length == 1"] is False

    def test_missing_charges_key_fails_that_check(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[{"no_charges_here": True}])
        checks = probe._run_probe()
        assert dict(checks)["item['charges'] is a mapping"] is False

    def test_missing_total_key_fails_that_check(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[{"charges": {"brokerage": 0}}])
        checks = probe._run_probe()
        assert dict(checks)["charges['total'] key exists"] is False

    def test_non_numeric_total_fails_the_numeric_check(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": "not-a-number"}}])
        checks = probe._run_probe()
        assert dict(checks)["charges['total'] is finite/non-negative numeric"] is False

    def test_negative_total_fails_the_numeric_check(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": -5}}])
        checks = probe._run_probe()
        assert dict(checks)["charges['total'] is finite/non-negative numeric"] is False

    def test_a_raised_exception_is_reported_without_its_message_by_default(self, monkeypatch):
        _install_fake_kite(monkeypatch, raise_exc=RuntimeError("some message that might leak account detail"))
        monkeypatch.delenv("PRETRADE_PROBE_SHOW_ERROR_DETAIL", raising=False)
        checks = probe._run_probe()
        assert dict(checks)["SDK call succeeded"] is False
        assert not any("some message that might leak account detail" in label for label, _ in checks)

    def test_main_returns_exit_code_one_on_any_failure(self, monkeypatch):
        _install_fake_kite(monkeypatch, response=[])
        assert probe.main() == 1


class TestOptInErrorDetail:
    def test_the_message_is_withheld_when_the_flag_is_unset(self, monkeypatch):
        _install_fake_kite(monkeypatch, raise_exc=RuntimeError("diagnostic detail"))
        monkeypatch.delenv("PRETRADE_PROBE_SHOW_ERROR_DETAIL", raising=False)
        checks = probe._run_probe()
        assert not any("diagnostic detail" in label for label, _ in checks)

    def test_the_message_is_shown_when_the_flag_is_set_to_1(self, monkeypatch):
        _install_fake_kite(monkeypatch, raise_exc=RuntimeError("diagnostic detail"))
        monkeypatch.setenv("PRETRADE_PROBE_SHOW_ERROR_DETAIL", "1")
        checks = probe._run_probe()
        assert any("diagnostic detail" in label for label, _ in checks)

    def test_any_other_value_still_withholds_the_message(self, monkeypatch):
        _install_fake_kite(monkeypatch, raise_exc=RuntimeError("diagnostic detail"))
        monkeypatch.setenv("PRETRADE_PROBE_SHOW_ERROR_DETAIL", "true")  # only the literal "1" opts in
        checks = probe._run_probe()
        assert not any("diagnostic detail" in label for label, _ in checks)


class TestNeverPrintsCredentials:
    def test_stdout_never_contains_the_api_key_or_access_token(self, monkeypatch, capsys):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": 0.58}}])
        probe.main()
        out = capsys.readouterr().out
        assert "fake-key-never-printed" not in out
        assert "fake-token-never-printed" not in out

    def test_stdout_never_contains_the_full_response_body_on_failure(self, monkeypatch, capsys):
        _install_fake_kite(monkeypatch, response=[{"charges": {"total": "not-a-number"}, "secret_account_field": "XYZ123"}])
        probe.main()
        out = capsys.readouterr().out
        assert "XYZ123" not in out
