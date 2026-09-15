import pytest
from broker_exposure_scope import classify_positions


def test_explicit_cnc_holdings_are_reported_but_not_blocking():
    blocking, ignored = classify_positions([
        {"tradingsymbol": "NIFTYBEES", "product": "CNC", "quantity": 5}
    ])
    assert blocking == []
    assert ignored[0]["tradingsymbol"] == "NIFTYBEES"


def test_nonzero_mis_position_remains_blocking():
    blocking, ignored = classify_positions([
        {"tradingsymbol": "RELIANCE", "product": "MIS", "quantity": 1}
    ])
    assert len(blocking) == 1 and ignored == []


@pytest.mark.parametrize("product", [None, "", "NRML", "BO"])
def test_unknown_nonzero_product_fails_closed(product):
    with pytest.raises(RuntimeError, match="unsupported or missing product"):
        classify_positions([{"product": product, "quantity": 1}])


def test_zero_quantity_does_not_require_product():
    assert classify_positions([{"quantity": 0}]) == ([], [])
