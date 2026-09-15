from decimal import Decimal
from pathlib import Path

import pytest

import local_paper_trading_simulator as paper


def test_complete_buy_creates_only_paper_position():
    broker = paper.LocalPaperBroker(seed=1)
    order = broker.submit_order(
        symbol="ABC", side="BUY", quantity=10,
        reference_price=Decimal("100"), scenario="COMPLETE",
    )
    assert order.status == "COMPLETE"
    assert order.order_id.startswith("PAPER-")
    assert broker.positions["ABC"].quantity == 10


def test_partial_and_rejected_scenarios():
    broker = paper.LocalPaperBroker(seed=2)
    partial = broker.submit_order(
        symbol="ABC", side="BUY", quantity=6,
        reference_price=Decimal("100"), scenario="PARTIAL",
    )
    rejected = broker.submit_order(
        symbol="XYZ", side="BUY", quantity=4,
        reference_price=Decimal("50"), scenario="REJECTED",
    )
    assert partial.status == "PARTIAL" and partial.filled_quantity == 3
    assert rejected.status == "REJECTED" and "XYZ" not in broker.positions


def test_sell_closes_position_and_realises_pnl():
    broker = paper.LocalPaperBroker(seed=3)
    broker.submit_order(symbol="ABC", side="BUY", quantity=5,
                        reference_price=Decimal("100"), scenario="COMPLETE")
    entry = Decimal(broker.positions["ABC"].average_price)
    broker.submit_order(symbol="ABC", side="SELL", quantity=5,
                        reference_price=entry + Decimal("10"), scenario="COMPLETE")
    assert broker.positions["ABC"].quantity == 0
    assert Decimal(broker.positions["ABC"].realised_pnl) > 0


def test_telemetry_has_strict_safety_flags():
    payload = paper.LocalPaperBroker().telemetry({})
    assert payload["mode"] == "LOCAL_PAPER_ONLY"
    assert payload["safety"] == {
        "kite_imported": False,
        "broker_network_used": False,
        "production_state_used": False,
        "live_order_possible": False,
        "live_trading_enabled": False,
        "gate_4": "LOCKED",
    }


def test_writer_refuses_production_and_shadow_targets(tmp_path: Path):
    for name in ("bot_state_v34.json", "bot_state_v34.lock", "shadow_strategy_telemetry.json"):
        with pytest.raises(ValueError):
            paper.write_paper_telemetry(tmp_path / name, {})


def test_demo_writes_separate_paper_telemetry(tmp_path: Path):
    target = tmp_path / "paper_trading_telemetry.json"
    result = paper.demo(target, seed=34036)
    assert target.is_file()
    assert len(result["orders"]) == 3
    assert result["safety"]["live_order_possible"] is False


def test_source_has_no_kite_or_order_api_operations():
    source = Path(paper.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", ".place_order(", ".modify_order(", ".cancel_order(",
        "request_entry(", "run_production_p01d_candidate",
        "institutional_engine_v34_p01d_candidate",
    )
    assert all(token not in source for token in forbidden)
