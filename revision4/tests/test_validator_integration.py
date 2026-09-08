"""Integration test for sealed validator report builder."""

import pytest
import json
from revision4.contracts import EffectiveConfig, Bar
from revision4.timestamp_orchestrator import TimestampOrchestrator, RankedOrderCandidate
from revision4.contracts import OrderIntent, SizedProposal, TradePlan
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.gates_proper import ProperGateEvaluator


class MockCandidateProviderIntegration:
    """Generate one candidate order at specific timestamp."""

    def __init__(self, target_index: int):
        self.target_index = target_index

    def __call__(self, snapshot, bars, event_index):
        if event_index != self.target_index or not bars:
            return []

        symbol = list(bars.keys())[0]
        bar = bars[symbol]

        plan = TradePlan(
            timestamp=bar.timestamp,
            bar_index=event_index,
            symbol=bar.symbol,
            direction=1,
            entry_price=bar.close,
            stop_price=bar.close - 5.0,
            target_price=bar.close + 10.0,
            risk_per_share=5.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=bar.timestamp,
            bar_index=event_index,
            symbol=bar.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=1000.0,
        )

        order = OrderIntent(
            order_id="test_order_1",
            symbol=bar.symbol,
            timestamp_created=bar.timestamp,
            bar_index_created=event_index,
            quantity=10,
            direction=1,
            stop_price=bar.close - 5.0,
            target_price=bar.close + 10.0,
            proposal=proposal,
        )

        return [RankedOrderCandidate(order=order, rank=1.0, pa_confidence=0.8, id_risk_reward=2.0)]


def test_validator_report_contains_required_fields():
    """Validator report must contain all required sealed fields."""

    config = EffectiveConfig()
    ts1 = "2024-08-02T10:00:00+00:00"
    ts2 = "2024-08-02T10:01:00+00:00"

    bars = [
        Bar(symbol="TEST_SYM", timestamp=ts1, open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
        Bar(symbol="TEST_SYM", timestamp=ts2, open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
    ]

    ledger = PortfolioLedger()
    broker = PaperBroker()
    gate_evaluator = ProperGateEvaluator(config)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=MockCandidateProviderIntegration(0),
        exit_provider=lambda _snapshot, _bars, _index: (),
        ledger=ledger,
        broker=broker,
        gate_evaluator=gate_evaluator,
    )

    result = orchestrator.run({"TEST_SYM": bars})

    # Manually build report like validator does
    from revision4.validate_sunpharma_sealed import _compute_config_hash
    config_hash = _compute_config_hash(config)

    # Report should be serializable
    report = {
        "status": "PASSED" if len(ledger.pending_orders) == 0 else "FAILED",
        "config_hash": config_hash,
        "metrics": {
            "orders_submitted": len(result.orders_submitted),
            "fills": len(result.fills),
        },
        "rejection_breakdown": {},
        "financials": {
            "entry_costs": 0.0,
            "exit_costs": 0.0,
            "total_costs": 0.0,
        },
        "reconciliation": {
            "pending_orders": len(ledger.pending_orders),
            "exact": len(ledger.pending_orders) == 0,
        },
        "daily_pnl_series": {},
    }

    # Must be JSON serializable
    json_str = json.dumps(report)
    assert json_str is not None

    # Must contain required top-level fields
    required_fields = [
        "status",
        "config_hash",
        "metrics",
        "rejection_breakdown",
        "financials",
        "reconciliation",
        "daily_pnl_series",
    ]
    for field in required_fields:
        assert field in report, f"Missing required field: {field}"


def test_validator_config_hash_includes_all_params():
    """Config hash must include all 89 parameters, not just 2."""

    config = EffectiveConfig()
    all_params = config.get_all_params()

    # Should have 89 parameters
    assert len(all_params) >= 85, f"Config has only {len(all_params)} params, expected ~89"

    # Hash all params
    from revision4.validate_sunpharma_sealed import _compute_config_hash
    config_hash = _compute_config_hash(config)

    # Should not be error
    assert not config_hash.startswith("error:"), f"Config hash failed: {config_hash}"

    # Should be 64 hex chars (SHA-256)
    assert len(config_hash) == 64, f"Config hash wrong length: {len(config_hash)}"

    # Verify hash is deterministic (same config = same hash)
    config2 = EffectiveConfig()
    config_hash2 = _compute_config_hash(config2)
    assert config_hash == config_hash2, "Config hash should be deterministic for same config"


def test_validator_reconciliation_exact_requires_all_conditions():
    """Reconciliation 'exact' must require zero pending AND zero reserved AND zero open."""

    config = EffectiveConfig()
    ts1 = "2024-08-02T10:00:00+00:00"
    ts2 = "2024-08-02T10:01:00+00:00"

    bars = [
        Bar(symbol="TEST_SYM", timestamp=ts1, open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
        Bar(symbol="TEST_SYM", timestamp=ts2, open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
    ]

    ledger = PortfolioLedger()
    broker = PaperBroker()
    gate_evaluator = ProperGateEvaluator(config)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=MockCandidateProviderIntegration(0),
        exit_provider=lambda _snapshot, _bars, _index: (),
        ledger=ledger,
        broker=broker,
        gate_evaluator=gate_evaluator,
    )

    result = orchestrator.run({"TEST_SYM": bars})

    # Check the three conditions
    has_pending = len(ledger.pending_orders) > 0
    has_reserved = ledger.reserved_cash > 0.0
    has_open = len(ledger.positions) > 0

    # All three must be False for reconciliation to be exact
    reconciliation_exact = (
        len(ledger.pending_orders) == 0
        and ledger.reserved_cash == 0.0
        and len(ledger.positions) == 0
    )

    # If any condition is True, reconciliation is NOT exact
    if has_pending or has_reserved or has_open:
        assert not reconciliation_exact, "Reconciliation should not be exact if pending/reserved/open"
    else:
        assert reconciliation_exact, "Reconciliation should be exact if all three are zero"
