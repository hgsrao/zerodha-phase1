import pytest
from typing import Dict, List
from revision4.contracts import Bar, EffectiveConfig, SizedProposal, TradePlan, OrderIntent, ExitEvent
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.gates_proper import ProperGateEvaluator

class MockCandidateProvider:
    def __init__(self, proposals_to_emit: Dict[str, List[SizedProposal]]):
        self.proposals = proposals_to_emit

    def get_candidates(self, timestamp: str, event_index: int, bars: Dict[str, Bar]) -> List[SizedProposal]:
        return self.proposals.get(timestamp, [])

class MockExitProvider:
    def get_exits(self, timestamp, event_index, bars, ledger_snapshot) -> List[ExitEvent]:
        return []

def test_strict_cross_session_orchestration():
    """
    Prove TimestampOrchestrator correctly coordinates Gate11 cross-session 
    rejection and pending order cancellation across a weekend gap.
    """
    config = EffectiveConfig()
    
    friday_ts = "2024-08-02T15:29:00+00:00"
    monday_ts = "2024-08-05T09:15:00+00:00"

    bars_by_symbol = {
        "TEST_SYM": [
            Bar(timestamp=friday_ts, open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
            Bar(timestamp=monday_ts, open=105.0, high=105.0, low=105.0, close=105.0, volume=1000)
        ]
    }

    plan = TradePlan(
        timestamp=friday_ts,
        bar_index=0,
        symbol="TEST_SYM",
        direction=1,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        risk_per_share=5.0,
        position_size_base=10.0
    )

    proposal = SizedProposal(
        timestamp=friday_ts,
        bar_index=0,
        symbol="TEST_SYM",
        plan=plan,
        mpc_scaling_factor=1.0,
        final_quantity=10.0,
        cost_estimate=10.0
    )

    proposals = {
        friday_ts: [proposal]
    }

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=MockCandidateProvider(proposals),
        exit_provider=MockExitProvider(),
        gate_evaluator=ProperGateEvaluator(config)
    )

    fake_legacy_order = OrderIntent(
        order_id="legacy_123",
        symbol="TEST_SYM",
        timestamp_intent=friday_ts,
        bar_index_intent=0,
        signal_price=100.0,
        quantity=10,
        direction=1
    )
    orchestrator.ledger.pending_orders[fake_legacy_order.order_id] = fake_legacy_order
    orchestrator.ledger.reserved_cash += 1000.0 

    result = orchestrator.run(bars_by_symbol)

    assert len(result.orders_submitted) == 0, "Gate11 failed to block a cross-session order!"
    assert len(orchestrator.ledger.pending_orders) == 0, "Ledger failed to drop Friday's pending order!"
    assert orchestrator.ledger.reserved_cash == 0.0, "Ledger failed to release reserved cash for stale weekend order!"
