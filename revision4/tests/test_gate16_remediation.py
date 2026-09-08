import json

from revision4.contracts import Bar, EffectiveConfig, OrderIntent, SizedProposal, TradePlan
from revision4.gate16_remediation import Gate16Remediator
from revision4.timestamp_orchestrator import RankedOrderCandidate, TimestampOrchestrator


def _order(timestamp, index):
    plan = TradePlan(timestamp, index, "TEST", 1, 100.0, 95.0, 110.0, 5.0, 1.0)
    proposal = SizedProposal(timestamp, index, "TEST", plan, 1.0, 1.0, 1.0)
    return OrderIntent("gate16-order", timestamp, index, "TEST", 1, 1.0, 95.0, 110.0, proposal)


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
    remediator = Gate16Remediator(config, str(audit), run_id="test-run")
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
    assert not remediator.approve_manual_recovery("", orchestrator.ledger)
    assert remediator.approve_manual_recovery("risk-reviewer", orchestrator.ledger)
    # The real run ledger is clean after the scheduled adverse next-bar flatten.
    assert json.loads(audit.read_text().splitlines()[0])["run_id"] == "test-run"
