import pytest
from broker_exposure_scope_p02_multipos import classify_positions


def test_default_managed_product_is_cnc_and_mis_is_ignored():
    managed, ignored = classify_positions([
        {"tradingsymbol": "RELIANCE", "product": "CNC", "quantity": 10},
        {"tradingsymbol": "SOMEOTHER", "product": "MIS", "quantity": 1},
    ])
    assert [p["tradingsymbol"] for p in managed] == ["RELIANCE"]
    assert [p["tradingsymbol"] for p in ignored] == ["SOMEOTHER"]


def test_roles_are_the_mirror_of_the_original_mis_engine_module():
    # The original broker_exposure_scope.py treats MIS as blocking and CNC
    # as ignored. This fork's default is the inverse - CNC is what the
    # P02 engine manages.
    managed, ignored = classify_positions([{"tradingsymbol": "X", "product": "MIS", "quantity": 1}])
    assert managed == []
    assert ignored[0]["tradingsymbol"] == "X"


def test_managed_product_is_configurable():
    managed, ignored = classify_positions(
        [{"tradingsymbol": "X", "product": "MIS", "quantity": 1}],
        managed_product="MIS",
    )
    assert managed[0]["tradingsymbol"] == "X"
    assert ignored == []


@pytest.mark.parametrize("product", [None, "", "NRML", "BO"])
def test_unknown_nonzero_product_fails_closed(product):
    with pytest.raises(RuntimeError, match="unsupported or missing product"):
        classify_positions([{"product": product, "quantity": 1}])


def test_invalid_managed_product_argument_fails_closed():
    with pytest.raises(RuntimeError, match="Unsupported managed_product"):
        classify_positions([], managed_product="NRML")


def test_zero_quantity_does_not_require_product():
    assert classify_positions([{"quantity": 0}]) == ([], [])


def test_malformed_quantity_fails_closed():
    with pytest.raises(RuntimeError, match="quantity is malformed"):
        classify_positions([{"product": "CNC", "quantity": "not-a-number"}])


def test_non_list_positions_fails_closed():
    with pytest.raises(RuntimeError, match="not a list"):
        classify_positions({"not": "a list"})
