from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def test_compact_telemetry_aggregates_events_but_keeps_sizing_decisions():
    orchestrator = Revision2ExternalEngineOrchestrator(
        ["INFY"], CanonicalParameterRegistry(), telemetry_mode="compact",
    )
    orchestrator._record_controller_event("EXIT_PROTECTION_UPDATE", "2023-07-03 09:30", "INFY", {})
    orchestrator._record_controller_event(
        "POSITION_SIZING", "2023-07-03 09:31", "INFY",
        {"base_risk_budget": 100.0, "derated_risk_budget": 80.0, "final_quantity": 2},
    )

    assert orchestrator.controller_telemetry == []
    assert orchestrator._controller_event_counts["EXIT_PROTECTION_UPDATE"] == 1
    assert orchestrator._controller_event_counts["POSITION_SIZING"] == 1
    assert len(orchestrator._position_sizing_events) == 1
    assert orchestrator._position_sizing_events[0]["final_quantity"] == 2

    orchestrator._last_close["INFY"] = 90.0
    orchestrator.open_trades["INFY"] = {
        "side": "BUY", "entry_price": 100.0, "quantity": 10,
    }
    orchestrator._record_mtm("2023-07-03 09:32")
    assert orchestrator._mtm_equity_curve == [("", 1_000_000.0)]
    assert orchestrator._mtm_max_drawdown_fraction > 0.0
