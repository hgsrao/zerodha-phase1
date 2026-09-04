from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.boxes import P01DBox
from revision2.contracts import EffectiveConfig, SafetyContract, TradePlan
from runtime.operating_mode import PaperBrokerAdapter


def setup():
    registry = CanonicalParameterRegistry()
    safety = SafetyContract.from_registry(registry).as_dict()
    return registry, safety, PaperBrokerAdapter("ORDER-TEST", slippage_fraction=0.0005)


def test_limit_price_is_enforced_and_caps_adverse_slippage():
    registry, safety, broker = setup()
    pending = broker.place_order("INFY", "BUY", 1, "LIMIT", 101.0, safety, registry, limit_price=100.0)
    assert pending["state"] == "pending"
    assert broker.get_position("INFY")["quantity"] == 0
    filled = broker.place_order("INFY", "BUY", 1, "LIMIT", 99.99, safety, registry, limit_price=100.0)
    assert filled["passed"]
    assert filled["filled_price"] <= 100.0


def test_timeout_retry_count_and_retry_delay_are_enforced():
    registry, safety, broker = setup()
    timeout = broker.place_order("INFY", "BUY", 1, "MARKET", 100.0, safety, registry, timeout_seconds=3, elapsed_seconds=4)
    assert timeout["state"] == "cancelled"
    too_many = broker.place_order("INFY", "BUY", 1, "MARKET", 100.0, safety, registry, max_retries=1, attempt=3)
    assert too_many["state"] == "rejected"
    too_soon = broker.place_order(
        "INFY", "BUY", 1, "MARKET", 100.0, safety, registry,
        max_retries=2, attempt=2, retry_delay_seconds=5, elapsed_seconds=4,
    )
    assert too_soon["state"] == "pending"


def test_p01d_carries_every_lifecycle_parameter_to_order():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    values.update({
        "order_type": "LIMIT", "limit_order_offset_percent": 0.01,
        "order_timeout_seconds": 12, "max_retry_attempts": 3,
        "retry_delay_seconds": 4, "slippage_tolerance_percent": 0.08,
    })
    config = EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)
    order, trace = P01DBox().create_order("INFY", TradePlan("BUY", 100, 99, 102, 2, 20), 5, config)
    assert order.limit_price == 101.0
    assert order.timeout_seconds == 12
    assert order.max_retries == 3
    assert order.retry_delay_seconds == 4
    assert order.slippage_tolerance_fraction == 0.0008
    assert {use.parameter for use in trace} >= {
        "limit_order_offset_percent", "order_timeout_seconds", "max_retry_attempts",
        "retry_delay_seconds", "slippage_tolerance_percent",
    }


def test_partial_fill_is_real_state_and_reports_actual_quantity():
    registry, safety, broker = setup()
    result = broker.place_order(
        "INFY", "BUY", 10, "MARKET", 100.0, safety, registry, actual_fill_quantity=4,
    )
    assert result["state"] == "partial"
    assert result["filled_quantity"] == 4
    assert broker.get_position("INFY")["quantity"] == 4


def test_every_order_terminal_or_pending_state_can_be_persisted(tmp_path):
    registry = CanonicalParameterRegistry()
    safety = SafetyContract.from_registry(registry).as_dict()
    path = tmp_path / "orders.jsonl"
    broker = PaperBrokerAdapter("AUDIT", audit_path=str(path))
    broker.place_order("INFY", "BUY", 1, "LIMIT", 101.0, safety, registry, limit_price=100.0, event_time="2026-01-02 10:00+05:30")
    broker.place_order("INFY", "BUY", 1, "MARKET", 100.0, safety, registry, event_time="2026-01-02 10:01+05:30")
    assert len(broker.audit_events) == 2
    text = path.read_text(encoding="utf-8")
    assert '"state": "pending"' in text
    assert '"state": "filled"' in text
