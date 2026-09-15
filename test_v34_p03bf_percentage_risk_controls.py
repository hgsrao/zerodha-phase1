"""P0-3B-F offline policy tests; no runner and no broker API."""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import json
import sys
import types

import pytest

if "kiteconnect" not in sys.modules:
    fake_module = types.ModuleType("kiteconnect")
    fake_module.KiteConnect = object
    sys.modules["kiteconnect"] = fake_module

from institutional_engine_v34_p01d_candidate import BrokerRiskSnapshot
from run_production_p01d_candidate import RunnerEntryAuthorizer, RunnerEntryStateStore


IST = ZoneInfo("Asia/Kolkata")


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 13, 10, 0, tzinfo=IST)

    def now(self):
        return self.value

    def advance(self, seconds=901):
        self.value += timedelta(seconds=seconds)


class SnapshotBroker:
    def __init__(self):
        self.snapshot = BrokerRiskSnapshot(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )

    def get_daily_risk_snapshot(self):
        return self.snapshot


def authorizer(tmp_path, clock, broker=None):
    broker = broker or SnapshotBroker()
    auth = RunnerEntryAuthorizer(
        broker=broker,
        state_store=RunnerEntryStateStore(tmp_path / "p03bf.json"),
        kill_switch_file=tmp_path / "KILL_SWITCH",
        capital_limit=Decimal("20000"),
        daily_loss_limit=Decimal("2000"),
        now_fn=clock.now,
    )
    return auth, broker


def authorize(auth, symbol="INFY", quantity=1, price="100"):
    return auth.authorize_buy(
        symbol=symbol, quantity=quantity, price=Decimal(price),
        live_trading_enabled=True,
    )


def test_live_disabled_fails_locally_without_broker_access(tmp_path):
    class NoCallBroker:
        calls = 0

        def get_daily_risk_snapshot(self):
            self.calls += 1
            raise AssertionError("broker must not be consulted while live trading is disabled")

    clock = Clock()
    broker = NoCallBroker()
    auth, _ = authorizer(tmp_path, clock, broker)

    with pytest.raises(RuntimeError, match="LIVE_TRADING_DISABLED"):
        auth.authorize_buy(
            symbol="INFY", quantity=1, price=Decimal("100"),
            live_trading_enabled=False,
        )

    assert broker.calls == 0
    saved = auth.state_store.load()
    assert saved["clearance_required"] is True
    assert saved["halt_reason"] == "LIVE_TRADING_DISABLED"


def test_percentage_limits_and_risk_based_quantity(tmp_path):
    clock = Clock()
    auth, _ = authorizer(tmp_path, clock)
    assert auth.target_risk == Decimal("100.000")
    assert auth.max_trade_risk == Decimal("150.0000")
    assert auth.daily_entry_lock == Decimal("300.000")
    assert auth.daily_hard_halt == Decimal("400.00")
    assert auth.rolling_week_halt == Decimal("800.00")
    assert auth.trial_drawdown_halt == Decimal("1000.00")
    assert auth.risk_based_quantity(Decimal("100")) == 50
    with pytest.raises(RuntimeError, match="TRADE_RISK_LIMIT"):
        authorize(auth, quantity=51)


def test_two_entries_cooldown_same_symbol_lock_and_crash_safe_reservation(tmp_path):
    clock = Clock()
    auth, _ = authorizer(tmp_path, clock)
    authorize(auth, "INFY")
    saved = json.loads((tmp_path / "p03bf.json").read_text(encoding="utf-8"))
    assert saved["entries_today"] == 1
    assert saved["symbols_today"] == ["INFY"]
    assert saved["turnover_today"] == "100"

    restarted, _ = authorizer(tmp_path, clock)
    with pytest.raises(RuntimeError, match="SAME_SYMBOL_DAILY_LOCK|ENTRY_COOLDOWN"):
        authorize(restarted, "INFY")
    clock.advance()
    authorize(restarted, "TCS")
    clock.advance()
    with pytest.raises(RuntimeError, match="DAILY_ENTRY_LIMIT"):
        authorize(restarted, "RELIANCE")


def test_one_position_and_turnover_controls(tmp_path):
    clock = Clock()
    auth, broker = authorizer(tmp_path, clock)
    broker.snapshot = BrokerRiskSnapshot(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("100")
    )
    with pytest.raises(RuntimeError, match="SIMULTANEOUS_POSITION_LIMIT"):
        authorize(auth)

    broker.snapshot = BrokerRiskSnapshot(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    )
    auth.controller.durable_state.update({
        "counter_day": clock.now().date().isoformat(),
        "entries_today": 0, "symbols_today": [], "turnover_today": "19950",
    })
    auth.state_store.save(auth.controller.durable_state)
    with pytest.raises(RuntimeError, match="DAILY_TURNOVER_LIMIT"):
        authorize(auth)


@pytest.mark.parametrize(
    "pnl,reason,clearance",
    [
        ("-300", "DAILY_ENTRY_LOCK", False),
        ("-400", "DAILY_HARD_HALT", True),
    ],
)
def test_daily_percentage_loss_boundaries(tmp_path, pnl, reason, clearance):
    clock = Clock()
    auth, broker = authorizer(tmp_path, clock)
    broker.snapshot = BrokerRiskSnapshot(
        Decimal(pnl), Decimal("0"), Decimal("0"), Decimal("0")
    )
    with pytest.raises(RuntimeError, match=reason):
        authorize(auth)
    state = auth.state_store.load()
    assert bool(state.get("clearance_required")) is clearance


@pytest.mark.parametrize("kind,reason", [
    ("week", "ROLLING_WEEK_HALT"),
    ("trial", "TRIAL_DRAWDOWN_HALT"),
])
def test_rolling_week_and_trial_drawdown_halts_are_durable(tmp_path, kind, reason):
    clock = Clock()
    auth, broker = authorizer(tmp_path, clock)
    state = {
        "counter_day": clock.now().date().isoformat(),
        "entries_today": 0, "symbols_today": [], "turnover_today": "0",
    }
    if kind == "week":
        state.update({"week_pnl_by_day": {"2026-08-12": "-700"}})
        current = "-100"
    else:
        state.update({"trial_cumulative_pnl": "-900",
                      "trial_last_recorded_day": "2026-08-12",
                      "trial_last_recorded_day_pnl": "-900"})
        current = "-100"
    auth.state_store.save(state)
    auth.controller.durable_state = state
    broker.snapshot = BrokerRiskSnapshot(
        Decimal(current), Decimal("0"), Decimal("0"), Decimal("0")
    )
    with pytest.raises(RuntimeError, match=reason):
        authorize(auth)
    assert auth.state_store.load()["clearance_required"] is True


def test_rolling_week_is_trailing_seven_days_not_iso_week(tmp_path):
    clock = Clock()
    clock.value = datetime(2026, 8, 17, 10, 0, tzinfo=IST)  # Monday
    auth, broker = authorizer(tmp_path, clock)
    state = {
        "counter_day": "2026-08-16",
        "entries_today": 0,
        "symbols_today": [],
        "turnover_today": "0",
        "week_pnl_by_day": {
            "2026-08-11": "-700",  # still inside the trailing window
            "2026-08-10": "-9999",  # expired and must not count
        },
    }
    auth.state_store.save(state)
    auth.controller.durable_state = state
    broker.snapshot = BrokerRiskSnapshot(
        Decimal("-100"), Decimal("0"), Decimal("0"), Decimal("0")
    )
    with pytest.raises(RuntimeError, match="ROLLING_WEEK_HALT"):
        authorize(auth)
    saved = auth.state_store.load()
    assert saved["rolling_week_start"] == "2026-08-11"
    assert "2026-08-10" not in saved["week_pnl_by_day"]
