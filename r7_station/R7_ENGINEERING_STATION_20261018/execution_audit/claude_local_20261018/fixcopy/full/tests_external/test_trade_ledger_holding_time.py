from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def test_completed_trade_ledger_includes_causal_holding_time():
    orchestrator = Revision2ExternalEngineOrchestrator(["INFY"], CanonicalParameterRegistry())
    state = orchestrator.exit_controller.open_position("BUY", 100.0, 95.0, 110.0, 30)
    state.bars_held = 7
    orchestrator._exit_controller_states["INFY"] = state
    trade = {
        "symbol": "INFY", "side": "BUY", "entry_price": 100.0, "quantity": 10,
        "entry_timestamp": "2023-09-01 10:00:00", "entry_atr": 1.0,
        "planned_entry_price": 100.0, "planned_stop_price": 95.0,
        "planned_target_price": 110.0,
    }
    orchestrator.open_trades["INFY"] = trade
    # The exit's position-reconciliation guard requires the broker to actually hold the long
    # position this close believes is open (see _verify_broker_position_reconciles).
    orchestrator.broker.positions["INFY"] = {"quantity": 10, "avg_price": 100.0}
    orchestrator._execute_exit("INFY", "2023-09-01 10:07:00", trade, 101.0, "test_exit")
    assert orchestrator.completed_trades[-1]["bars_held"] == 7
