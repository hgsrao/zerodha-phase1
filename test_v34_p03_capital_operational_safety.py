"""
V3.4 P0-3 RED acceptance harness.

This file intentionally targets the production candidate's missing P0-3
risk-control contract. It does NOT contain placeholder assertions such as
pytest.raises(NotImplementedError), because those create false-green tests.

The tests should initially fail against the current candidate because the
required P0-3 risk-control API does not yet exist.

Expected implementation seam:

    engine.evaluate_entry_risk(...)
    engine.submit_entry_guarded(...)
    engine.poll_runtime_safety_controls(...)
    engine.clear_risk_halt(...)

The exact production method names may be changed during implementation,
but the behavioral contracts below are frozen.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import json
import pytest


CAPITAL_LIMIT = Decimal("20000")
DAILY_LOSS_LIMIT = Decimal("2000")


@dataclass
class RiskSnapshot:
    realized_pnl: Decimal
    unrealized_mtm: Decimal
    charges: Decimal
    deployed_capital: Decimal


@dataclass
class FakeBroker:
    positions: list
    orders: list
    risk_snapshot: RiskSnapshot
    sell_submissions: int = 0
    buy_submissions: int = 0

    def get_positions(self):
        return self.positions

    def get_orders(self):
        return self.orders

    def get_daily_risk_snapshot(self):
        return self.risk_snapshot

    def submit_buy(self, **kwargs):
        self.buy_submissions += 1
        return f"BUY-{self.buy_submissions}"

    def submit_emergency_exit(self, **kwargs):
        self.sell_submissions += 1
        return f"EXIT-{self.sell_submissions}"


class JsonRiskStore:
    """Small real JSON store used to prove crash persistence semantics."""

    def __init__(self, path: Path):
        self.path = path

    def save(self, state):
        self.path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


def make_candidate(broker, store, kill_switch_file):
    """
    Integration seam.

    This assertion is deliberately concrete: the current candidate has no
    P0-3 risk controller yet, so the RED baseline must fail here rather than
    passing through a placeholder exception.
    """
    from institutional_engine_v34_p01d_candidate import TradingEngineV34

    # The production implementation must expose a P0-3-capable constructor
    # or factory. Until then this is an explicit contract failure.
    assert hasattr(TradingEngineV34, "create_p03_risk_controller"), (
        "P0-3 contract missing: TradingEngineV34.create_p03_risk_controller "
        "must be implemented before capital controls can be tested."
    )

    return TradingEngineV34.create_p03_risk_controller(
        broker=broker,
        store=store,
        kill_switch_file=kill_switch_file,
        capital_limit=CAPITAL_LIMIT,
        daily_loss_limit=DAILY_LOSS_LIMIT,
    )


def test_P03_01_exact_capital_ceiling_is_allowed(tmp_path):
    broker = FakeBroker(
        positions=[],
        orders=[],
        risk_snapshot=RiskSnapshot(
            realized_pnl=Decimal("0"),
            unrealized_mtm=Decimal("0"),
            charges=Decimal("0"),
            deployed_capital=Decimal("15000"),
        ),
    )
    store = JsonRiskStore(tmp_path / "state.json")
    sut = make_candidate(broker, store, tmp_path / "KILL_SWITCH")

    result = sut.evaluate_entry(
        symbol="RELIANCE",
        quantity=5,
        price=Decimal("1000"),
    )

    assert result.allowed is True
    assert result.projected_deployed_capital == CAPITAL_LIMIT


def test_P03_02_capital_one_paisa_over_limit_is_blocked(tmp_path):
    broker = FakeBroker(
        positions=[],
        orders=[],
        risk_snapshot=RiskSnapshot(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("19999.99")
        ),
    )
    store = JsonRiskStore(tmp_path / "state.json")
    sut = make_candidate(broker, store, tmp_path / "KILL_SWITCH")

    result = sut.evaluate_entry(
        symbol="RELIANCE",
        quantity=1,
        price=Decimal("0.02"),
    )

    assert result.allowed is False
    assert result.reason == "CAPITAL_CEILING"


def test_P03_03_pending_buy_exposure_is_counted(tmp_path):
    broker = FakeBroker(
        positions=[{
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": 10,
            "average_price": "1500.00",
        }],
        orders=[{
            "order_id": "PENDING1",
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "product": "MIS",
            "transaction_type": "BUY",
            "status": "OPEN",
            "quantity": 5,
            "price": "900.00",
        }],
        risk_snapshot=RiskSnapshot(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        ),
    )
    store = JsonRiskStore(tmp_path / "state.json")
    sut = make_candidate(broker, store, tmp_path / "KILL_SWITCH")

    result = sut.evaluate_entry(
        symbol="TCS",
        quantity=1,
        price=Decimal("1"),
    )

    assert result.allowed is False
    assert result.reason == "CAPITAL_CEILING"


def test_P03_04_malformed_broker_quantity_fails_closed(tmp_path):
    broker = FakeBroker(
        positions=[{
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": "NOT_A_NUMBER",
            "average_price": "1500.00",
        }],
        orders=[],
        risk_snapshot=RiskSnapshot(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        ),
    )
    sut = make_candidate(
        broker,
        JsonRiskStore(tmp_path / "state.json"),
        tmp_path / "KILL_SWITCH",
    )

    result = sut.evaluate_entry("INFY", 1, Decimal("100"))

    assert result.allowed is False
    assert result.reason == "BROKER_CONTRACT_VIOLATION"


def test_P03_05_daily_loss_at_exact_threshold_trips(tmp_path):
    broker = FakeBroker(
        [], [],
        RiskSnapshot(
            Decimal("-1800"),
            Decimal("-150"),
            Decimal("-50"),
            Decimal("0"),
        ),
    )
    store = JsonRiskStore(tmp_path / "state.json")
    sut = make_candidate(broker, store, tmp_path / "KILL_SWITCH")

    result = sut.evaluate_entry("INFY", 1, Decimal("100"))

    assert result.allowed is False
    assert result.reason == "DAILY_LOSS_LIMIT"
    assert result.clearance_required is True


def test_P03_06_daily_loss_just_above_threshold_remains_eligible(tmp_path):
    broker = FakeBroker(
        [], [],
        RiskSnapshot(
            Decimal("-1800"),
            Decimal("-149.99"),
            Decimal("-50"),
            Decimal("0"),
        ),
    )
    sut = make_candidate(
        broker,
        JsonRiskStore(tmp_path / "state.json"),
        tmp_path / "KILL_SWITCH",
    )

    result = sut.evaluate_entry("INFY", 1, Decimal("100"))

    assert result.allowed is True


def test_P03_07_risk_halt_survives_process_restart(tmp_path):
    state_file = tmp_path / "state.json"

    broker = FakeBroker(
        [], [],
        RiskSnapshot(
            Decimal("-2000"), Decimal("0"), Decimal("0"), Decimal("0")
        ),
    )

    store_a = JsonRiskStore(state_file)
    sut_a = make_candidate(broker, store_a, tmp_path / "KILL_SWITCH")
    first = sut_a.evaluate_entry("INFY", 1, Decimal("100"))

    assert first.allowed is False
    assert store_a.load()["clearance_required"] is True

    # Fresh store/object: no in-memory state is reused.
    store_b = JsonRiskStore(state_file)
    sut_b = make_candidate(broker, store_b, tmp_path / "KILL_SWITCH")
    second = sut_b.evaluate_entry("INFY", 1, Decimal("100"))

    assert second.allowed is False
    assert second.reason in {"DAILY_LOSS_LIMIT", "DURABLE_HALT"}


def test_P03_08_kill_switch_blocks_new_entry(tmp_path):
    kill = tmp_path / "KILL_SWITCH"
    kill.write_text("operator stop\n", encoding="utf-8")

    broker = FakeBroker(
        [], [],
        RiskSnapshot(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
    )
    sut = make_candidate(
        broker,
        JsonRiskStore(tmp_path / "state.json"),
        kill,
    )

    result = sut.evaluate_entry("INFY", 1, Decimal("100"))

    assert result.allowed is False
    assert result.reason == "KILL_SWITCH"


def test_P03_09_kill_switch_appearing_before_submission_blocks_buy(tmp_path):
    kill = tmp_path / "KILL_SWITCH"

    broker = FakeBroker(
        [], [],
        RiskSnapshot(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
    )
    sut = make_candidate(
        broker,
        JsonRiskStore(tmp_path / "state.json"),
        kill,
    )

    preflight = sut.evaluate_entry("INFY", 1, Decimal("100"))
    assert preflight.allowed is True

    # Adversarial race: operator creates the kill switch after observation
    # but before the broker side effect.
    kill.write_text("operator stop\n", encoding="utf-8")

    with pytest.raises(Exception):
        sut.submit_entry_guarded(
            symbol="INFY",
            quantity=1,
            price=Decimal("100"),
        )

    assert broker.buy_submissions == 0


def test_P03_10_removing_kill_switch_does_not_auto_clear_durable_halt(tmp_path):
    kill = tmp_path / "KILL_SWITCH"
    kill.write_text("operator stop\n", encoding="utf-8")

    state_file = tmp_path / "state.json"
    broker = FakeBroker(
        [], [],
        RiskSnapshot(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
    )

    sut = make_candidate(broker, JsonRiskStore(state_file), kill)
    blocked = sut.evaluate_entry("INFY", 1, Decimal("100"))
    assert blocked.allowed is False

    kill.unlink()

    fresh = make_candidate(
        broker,
        JsonRiskStore(state_file),
        kill,
    )
    still_blocked = fresh.evaluate_entry("INFY", 1, Decimal("100"))

    assert still_blocked.allowed is False
    assert still_blocked.clearance_required is True


def test_P03_11_emergency_exit_remains_available_when_entry_is_halted(tmp_path):
    kill = tmp_path / "KILL_SWITCH"
    kill.write_text("operator stop\n", encoding="utf-8")

    broker = FakeBroker(
        [{
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": 10,
            "average_price": "100.00",
        }],
        [],
        RiskSnapshot(
            Decimal("-2000"), Decimal("0"), Decimal("0"), Decimal("1000")
        ),
    )

    sut = make_candidate(
        broker,
        JsonRiskStore(tmp_path / "state.json"),
        kill,
    )

    assert sut.evaluate_entry("TCS", 1, Decimal("100")).allowed is False

    exit_result = sut.evaluate_emergency_exit(
        symbol="INFY",
        quantity=10,
        trigger_price=Decimal("99.95"),
    )

    assert exit_result.allowed is True


def test_P03_12_malformed_pnl_snapshot_fails_closed(tmp_path):
    class BadBroker(FakeBroker):
        def get_daily_risk_snapshot(self):
            return {"realized_pnl": "not numeric"}

    broker = BadBroker(
        [], [],
        RiskSnapshot(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
    )
    sut = make_candidate(
        broker,
        JsonRiskStore(tmp_path / "state.json"),
        tmp_path / "KILL_SWITCH",
    )

    result = sut.evaluate_entry("INFY", 1, Decimal("100"))

    assert result.allowed is False
    assert result.reason == "BROKER_CONTRACT_VIOLATION"
