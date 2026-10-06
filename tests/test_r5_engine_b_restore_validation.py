"""Restored cached decisions must not bypass Engine B decision validation."""
from copy import deepcopy
import pytest
from revision5.engine_b_management import EngineBController, EngineBPolicy
from test_engine_b_management import record, bars, evaluate


def controller_and_receipt():
    controller = EngineBController(EngineBPolicy(enabled=True))
    evaluate(controller, record(), bars([100]))
    return controller, controller.export_state()


@pytest.mark.parametrize('field,value', [
    ('last_timestamp', 'NaT'), ('reversal_count', True),
    ('reversal_count', 1.5), ('sessions_elapsed', 0),
    ('sessions_elapsed', True), ('session_last_bar', 'false'),
    ('decision', {}),
])
def test_invalid_metadata_is_rejected_atomically(field, value):
    controller, receipt = controller_and_receipt()
    before = deepcopy(controller.export_state())
    damaged = deepcopy(receipt)
    damaged['positions']['p'][field] = value
    with pytest.raises(ValueError):
        controller.restore_state(damaged)
    assert controller.export_state() == before


@pytest.mark.parametrize('field,value', [
    ('action', 'BUY'), ('reason', 'unverified'), ('effective_from', 'CURRENT_BAR'),
    ('proposed_stop_price', float('nan')), ('proposed_stop_price', -1),
    ('current_r', float('inf')), ('structural_reference', float('nan')),
    ('reversal_count', 99),
])
def test_invalid_cached_decision_cannot_be_returned_on_duplicate_bar(field, value):
    controller, receipt = controller_and_receipt()
    damaged = deepcopy(receipt)
    damaged['positions']['p']['decision'][field] = value
    with pytest.raises(ValueError):
        controller.restore_state(damaged)


def test_valid_restore_duplicate_bar_and_export_do_not_alias_live_state():
    controller, receipt = controller_and_receipt()
    expected = evaluate(controller, record(), bars([100]))
    receipt['positions']['p']['decision']['action'] = 'EXIT'
    assert evaluate(controller, record(), bars([100])) == expected
    fresh = EngineBController(controller.policy)
    valid = controller.export_state()
    fresh.restore_state(valid)
    valid['positions']['p']['decision']['action'] = 'EXIT'
    assert evaluate(fresh, record(), bars([100])) == expected
