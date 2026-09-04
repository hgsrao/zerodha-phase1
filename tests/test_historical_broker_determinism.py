from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import SafetyContract
from runtime.operating_mode import PaperBrokerAdapter


def test_historical_event_time_and_order_ids_are_deterministic():
    registry = CanonicalParameterRegistry()
    contract = SafetyContract.from_registry(registry).as_dict()

    def run_once():
        broker = PaperBrokerAdapter(account_id="HIST")
        return broker.place_order(
            "INFY", "BUY", 2, "MARKET", 100.0, contract, registry,
            event_time="2026-01-02T10:01:00+05:30",
        ), broker

    first, first_broker = run_once()
    second, second_broker = run_once()
    assert first["order_id"] == second["order_id"] == "HIST-000000001"
    assert first_broker.fills == second_broker.fills
    assert first_broker.fills[0]["filled_at"] == "2026-01-02T10:01:00+05:30"
