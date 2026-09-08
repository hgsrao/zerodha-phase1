import json

from revision4.contracts import Bar, EffectiveConfig, FillEvent, OrderIntent, SizedProposal, TradePlan
from revision4.gate16_remediation import Gate16Remediator
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.timestamp_orchestrator import RankedOrderCandidate, TimestampOrchestrator
from revision4.validate_sunpharma_sealed import _parse_rejection_reason


def _order(timestamp, index, *, order_id="gate16-order", symbol="TEST"):
    plan = TradePlan(timestamp, index, symbol, 1, 100.0, 95.0, 110.0, 5.0, 1.0)
    proposal = SizedProposal(timestamp, index, symbol, plan, 1.0, 1.0, 1.0)
    return OrderIntent(order_id, timestamp, index, symbol, 1, 1.0, 95.0, 110.0, proposal)


class _Gate16Failure:
    def evaluate_pre_submission(self, *args, **kwargs):
        return True, None

    def evaluate_post_fill(self, *args, **kwargs):
        return False, "Gate16Slippage: deliberate test breach"

    def evaluate_post_reconciliation(self, *args, **kwargs):
        return True, None


def test_breach_quarantines_flattens_and_requires_manual_recovery(tmp_path):
    config = EffectiveConfig()
    audit = tmp_path / "violations.jsonl"
    remediator = Gate16Remediator(config, dataset_hash="dataset-test", config_hash="config-test", audit_log_path=str(audit), run_id="test-run")
    first = "2024-08-02T10:00:00+00:00"
    second = "2024-08-02T10:01:00+00:00"
    third = "2024-08-02T10:02:00+00:00"

    def candidates(snapshot, bars, index):
        return [RankedOrderCandidate(_order(first, 0), 1.0, 0.8, 2.0)] if index == 0 else []

    orchestrator = TimestampOrchestrator(config, candidates, gate_evaluator=_Gate16Failure(), gate16_remediator=remediator)
    result = orchestrator.run({
        "TEST": [
            Bar(first, "TEST", 100, 101, 99, 100, 1000),
            Bar(second, "TEST", 101, 102, 100, 101, 1000),
            Bar(third, "TEST", 102, 103, 101, 102, 1000),
        ]
    })
    assert remediator.quarantine_mode
    assert not remediator.entry_authorization_enabled
    assert len(remediator.violations) == 1 and remediator.verify_chain()
    assert len(result.fills) == 1
    assert len(result.exits) == 1
    remediator.record_reconciliation(
        third, pending_orders=0, reserved_cash=0.0, open_positions=0,
        realized_pnl=orchestrator.ledger.realized_pnl, daily_pnl={},
        daily_pnl_matches_realized=True, exact=True,
    )
    assert not remediator.approve_manual_recovery("", "reviewed", third, orchestrator.ledger)
    assert remediator.approve_manual_recovery(
        "risk-reviewer", "contained breach; permit one controlled replay", third, orchestrator.ledger,
    )
    assert remediator.verify_chain()
    assert remediator.verify_persisted_chain()
    assert [event.event_type for event in remediator.audit_events] == [
        "GATE16_VIOLATION", "QUARANTINE_ACTIVATED", "ADVERSE_FLATTEN",
        "RECONCILIATION_RESULT", "MANUAL_RECOVERY_APPROVED",
    ]
    persisted = [json.loads(line) for line in audit.read_text().splitlines()]
    assert persisted[0]["run_id"] == "test-run"
    assert persisted[0]["dataset_hash"] == "dataset-test"
    assert persisted[-1]["event_type"] == "MANUAL_RECOVERY_APPROVED"


def test_persisted_chain_detects_tampering(tmp_path):
    config = EffectiveConfig()
    audit = tmp_path / "audit.jsonl"
    remediator = Gate16Remediator(
        config, dataset_hash="dataset-test", config_hash="config-test",
        audit_log_path=str(audit), run_id="tamper-test",
    )
    remediator._append_event("2024-08-02T10:00:00+00:00", "GATE16_VIOLATION", {"symbol": "TEST"})
    assert remediator.verify_chain() and remediator.verify_persisted_chain()

    lines = audit.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["symbol"] = "FORGED"
    audit.write_text(json.dumps(tampered) + "\n")

    assert remediator.verify_chain()  # Memory remains intact.
    assert not remediator.verify_persisted_chain()


def test_recovery_approval_can_only_append_to_verified_zero_state_audit(tmp_path):
    config = EffectiveConfig()
    audit = tmp_path / "approval.jsonl"
    remediator = Gate16Remediator(
        config, dataset_hash="dataset-test", config_hash="config-test",
        audit_log_path=str(audit), run_id="approval-test",
    )
    remediator._append_event("2024-08-02T10:00:00+00:00", "GATE16_VIOLATION", {"symbol": "TEST"})
    remediator.record_reconciliation(
        "2024-08-02T10:01:00+00:00", pending_orders=0, reserved_cash=0.0,
        open_positions=0, realized_pnl=0.0, daily_pnl={},
        daily_pnl_matches_realized=True, exact=True,
    )
    loaded = Gate16Remediator.from_persisted_audit(config, str(audit))
    approval = loaded.record_persisted_manual_recovery(
        "Shrinivas", "verify remediation reproducibility", "2024-08-02T10:02:00+00:00",
    )
    assert approval.event_type == "MANUAL_RECOVERY_APPROVED"
    assert loaded.verify_chain() and loaded.verify_persisted_chain()


def test_quarantine_cancellation_is_hash_linked(tmp_path):
    config = EffectiveConfig()
    audit = tmp_path / "cancellation.jsonl"
    remediator = Gate16Remediator(
        config, dataset_hash="dataset-test", config_hash="config-test",
        audit_log_path=str(audit), run_id="cancel-test",
    )
    ledger = PortfolioLedger()
    broker = PaperBroker()
    first = "2024-08-02T10:00:00+00:00"
    filled_order = _order(first, 0, order_id="filled")
    pending_order = _order(first, 0, order_id="pending", symbol="PENDING")
    assert ledger.create_order(filled_order.order_id, filled_order)[0]
    assert broker.submit_order(filled_order, config)[0]
    fill = FillEvent("fill-1", "filled", first, 0, "TEST", 1, 1.0, 100.0, 0.0, first, first)
    assert ledger.fill_order("filled", fill)[0]
    assert ledger.create_order(pending_order.order_id, pending_order)[0]
    assert broker.submit_order(pending_order, config)[0]

    remediator.handle_breach(first, filled_order, fill, ledger, broker)

    assert "pending" not in ledger.pending_orders
    assert "pending" not in broker.get_active_orders()
    cancellation = next(event for event in remediator.audit_events if event.event_type == "PENDING_ORDER_CANCELLED")
    assert cancellation.payload["order_id"] == "pending"
    assert cancellation.payload["reservation_released"] == 100.0
    assert remediator.verify_chain() and remediator.verify_persisted_chain()


def test_structured_gate_rejection_reason_does_not_parse_the_timestamp():
    event = ("2024-08-02 03:46:00+00:00", "GATE_REJECT", "375_SUNPHARMA:Gate12StrategySignals: confidence below threshold")
    assert _parse_rejection_reason(event) == "Gate12StrategySignals"
