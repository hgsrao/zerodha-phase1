"""C03 integrated mock-paper qualification, explicitly constructed BUY seed."""
import pytest
from scripts.diagnostics.r5_lifecycle_acceptance import execute

@pytest.mark.parametrize('outcome,protective', [('ACKNOWLEDGED',False),
    ('ACKNOWLEDGED',True), ('REJECTED',False), ('UNKNOWN',False)])
def test_integrated_lifecycle_and_feedback(tmp_path, outcome, protective):
    receipt = execute(tmp_path, outcome, protective)
    assert receipt['durable_intent_before_submit']
    assert receipt['exactly_once_feedback_in_process']
    assert len(receipt['ledger']) == 1
