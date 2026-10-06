"""Contract and submission gates must agree on executable share quantities."""
import pytest
from runtime.contract_validator import ContractValidator


@pytest.mark.parametrize('quantity', [True, False, 1.0, .5, float('nan'), float('inf'), None, '1', 0, -1])
def test_contract_rejects_non_positive_integer_quantities(quantity):
    validator = ContractValidator(engine='EXTERNAL')
    reasons = validator.validate_order_payload(dict(symbol='TITAN', side='BUY', quantity=quantity, order_type='MARKET'))
    assert any('quantity' in reason for reason in reasons)


@pytest.mark.parametrize('quantity', [1, 10])
def test_contract_accepts_positive_integer_quantities(quantity):
    validator = ContractValidator(engine='EXTERNAL')
    assert validator.validate_order_payload(dict(symbol='TITAN', side='SELL', quantity=quantity, order_type='MARKET')) == []


def test_submission_gate_rejects_null_quantity_even_when_field_is_present():
    from runtime.operating_mode import ExecutionGate
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    engine = Revision2ExternalEngineOrchestrator(['TITAN'])
    result = ExecutionGate().validate_pre_submit(engine.safety_contract.as_dict(),
        dict(symbol='TITAN', side='BUY', quantity=None, order_type='MARKET'))
    assert not result['passed']
    assert any('quantity' in reason for reason in result['reasons'])
