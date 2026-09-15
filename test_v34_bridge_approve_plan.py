"""Tests for v34_bridge_approve_plan.py (the human-in-the-loop approval CLI).

No real stdin/terminal - the interactive prompt is exercised by
monkeypatching builtins.input, and --yes covers the non-interactive path.
No real broker/network. Real R1/Step A/RebalancePlan machinery throughout.
"""

from datetime import date

import pytest

import v34_bridge_approve_plan as cli
from v34_bridge_rebalance_plan import RebalancePlanStatus, RebalancePlanStore
from v34_bridge_trigger import run_trigger

SIGNAL_DATE = date(2026, 8, 14)
# Regenerated 2026-08-16 alongside test_v34_bridge_target_portfolio.py's
# own fixture - see that file's docstring for why (50-symbol universe).
QUOTES = {
    "NSE:LAURUSLABS": {"last_price": 1815.0},
    "NSE:SHRIRAMFIN": {"last_price": 1046.4},
}


def _created_plan(tmp_path):
    store = RebalancePlanStore(tmp_path)
    plan, outcome = run_trigger(
        quotes=QUOTES, current_portfolio={"RELIANCE": 50},
        signal_date=SIGNAL_DATE, current_trading_day=SIGNAL_DATE, store=store, positions=2,
    )
    assert outcome == "CREATED"
    return plan, store


class TestNonInteractiveApproval:
    def test_yes_flag_approves_without_a_prompt(self, tmp_path, capsys):
        plan, store = _created_plan(tmp_path)
        exit_code = cli.main([plan.target_id, "--plans-dir", str(tmp_path), "--yes"], today=SIGNAL_DATE)
        assert exit_code == 0

        reloaded = store.load(plan.target_id)
        assert reloaded.status == RebalancePlanStatus.APPROVED
        assert reloaded.approved_at is not None

        out = capsys.readouterr().out
        assert "Approved." in out

    def test_prints_the_exit_enter_keep_breakdown_before_approving(self, tmp_path, capsys):
        plan, store = _created_plan(tmp_path)
        cli.main([plan.target_id, "--plans-dir", str(tmp_path), "--yes"], today=SIGNAL_DATE)

        out = capsys.readouterr().out
        assert "SELL RELIANCE" in out
        for symbol, qty in plan.diff.enters.items():
            assert f"BUY  {symbol}" in out
            assert f"qty={qty}" in out


class TestInteractiveApproval:
    def test_y_answer_approves(self, tmp_path, monkeypatch):
        plan, store = _created_plan(tmp_path)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        exit_code = cli.main([plan.target_id, "--plans-dir", str(tmp_path)], today=SIGNAL_DATE)
        assert exit_code == 0
        assert store.load(plan.target_id).status == RebalancePlanStatus.APPROVED

    def test_n_answer_does_not_approve(self, tmp_path, monkeypatch):
        plan, store = _created_plan(tmp_path)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        exit_code = cli.main([plan.target_id, "--plans-dir", str(tmp_path)], today=SIGNAL_DATE)
        assert exit_code == 1
        assert store.load(plan.target_id).status == RebalancePlanStatus.CREATED  # unchanged

    def test_anything_other_than_y_is_treated_as_no(self, tmp_path, monkeypatch):
        plan, store = _created_plan(tmp_path)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")  # bare Enter
        exit_code = cli.main([plan.target_id, "--plans-dir", str(tmp_path)], today=SIGNAL_DATE)
        assert exit_code == 1
        assert store.load(plan.target_id).status == RebalancePlanStatus.CREATED


class TestRefusals:
    def test_unknown_target_id_is_refused(self, tmp_path, capsys):
        exit_code = cli.main(["V11_2099-01-01_deadbeefdeadbeef", "--plans-dir", str(tmp_path), "--yes"], today=SIGNAL_DATE)
        assert exit_code == 1
        assert "No plan found" in capsys.readouterr().err

    def test_a_non_created_plan_is_refused(self, tmp_path, capsys):
        plan, store = _created_plan(tmp_path)
        cli.main([plan.target_id, "--plans-dir", str(tmp_path), "--yes"], today=SIGNAL_DATE)  # first approval succeeds

        exit_code = cli.main([plan.target_id, "--plans-dir", str(tmp_path), "--yes"], today=SIGNAL_DATE)  # second attempt
        assert exit_code == 1
        assert "not CREATED" in capsys.readouterr().err

    def test_a_stale_plan_is_refused_and_left_untouched(self, tmp_path, capsys):
        plan, store = _created_plan(tmp_path)
        next_day = date(2026, 8, 15)

        exit_code = cli.main([plan.target_id, "--plans-dir", str(tmp_path), "--yes"], today=next_day)
        assert exit_code == 1
        assert "Refused to approve" in capsys.readouterr().err

        reloaded = store.load(plan.target_id)
        assert reloaded.status == RebalancePlanStatus.CREATED
        assert reloaded.approved_at is None
